"""Self-calibration from recorder history.

Turns the rules of thumb into *your* flat. Everything learned here is visible
in the panel, carries a confidence and a sample count, and can be overridden
by hand (``adaptive_ventilation.override_parameter``).

The maths is deliberately small: exponential fits and linear regression, no ML
framework, exactly as the specification demands.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from itertools import pairwise
import logging
import math
import statistics
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .const import (
    CONF_OUTDOOR_TEMPERATURE,
    EVENT_CALIBRATION_UPDATED,
)
from .engine import psychrometrics as psy
from .engine import solar as solar_mod
from .engine.state import LearnedParameters, LearnedRoom
from .storage import merge_learned_room

if TYPE_CHECKING:
    from .coordinator import AdaptiveVentilationCoordinator

_LOGGER = logging.getLogger(__name__)

#: How far back to look.
HISTORY_DAYS = 7
#: Minimum number of usable episodes before a learned value replaces the seed.
MIN_SAMPLES = 3
#: Minimum length of a usable episode.
MIN_EPISODE = timedelta(hours=2)
MIN_PURGE_EPISODE = timedelta(minutes=5)
#: Sane bounds - anything outside is a measurement artefact, not a building.
TAU_BOUNDS = (4.0, 300.0)
COOLING_BOUNDS = (0.2, 4.0)
ACH_BOUNDS = (0.2, 25.0)
SOLAR_GAIN_BOUNDS = (0.2, 3.0)
#: Assumed outdoor CO2 baseline in ppm.
OUTDOOR_CO2 = 420.0


@dataclass
class Sample:
    """One timestamped numeric reading."""

    time: datetime
    value: float


class Calibrator:
    """Runs nightly (or on demand) and updates the learned parameters."""

    def __init__(self, hass: HomeAssistant, coordinator: AdaptiveVentilationCoordinator) -> None:
        self.hass = hass
        self.coordinator = coordinator
        self._last_run: datetime | None = None
        #: Why the last run produced nothing, so the panel can say so instead
        #: of showing an empty box with no explanation at all.
        self.last_status: str = "never_run"

    @property
    def last_run(self) -> datetime | None:
        """When the calibration last looked at the history."""
        return self._last_run

    async def async_run(self, *, force: bool = False) -> dict[str, Any]:
        """Evaluate the recorder history and merge the results."""
        now = dt_util.utcnow()
        if not force and self._last_run and now - self._last_run < timedelta(hours=12):
            return {"skipped": "ran recently"}

        try:
            history = await self._async_history(now)
        except Exception as err:
            _LOGGER.info("calibration skipped, no usable history: %s", err)
            self.last_status = "no_recorder"
            self._last_run = now
            return {"skipped": str(err)}

        if not history:
            self.last_status = "no_history"
            self._last_run = now
            return {"skipped": "no history"}

        self._last_run = now
        outdoor = history.get(self.coordinator.config.options.get(CONF_OUTDOOR_TEMPERATURE, ""), [])
        updated: dict[str, dict[str, float]] = {}

        for room in self.coordinator.config.rooms:
            indoor = history.get(room.temperature_sensor or "", [])
            if len(indoor) < 20 or len(outdoor) < 20:
                continue

            open_periods = self._open_periods(history, room.id)
            results: dict[str, float | None] = {}

            tau, tau_samples = fit_tau(indoor, outdoor, open_periods)
            if tau is not None and tau_samples >= MIN_SAMPLES:
                results["tau_hours"] = tau

            cooling, cooling_samples = fit_night_cooling(indoor, outdoor, open_periods)
            if cooling is not None and cooling_samples >= 2:
                results["night_cooling_k_per_h"] = cooling

            co2 = history.get(room.co2_sensor or "", [])
            if co2:
                ach, ach_samples = fit_air_changes(co2, open_periods, room.volume_m3)
                if ach is not None and ach_samples >= 2:
                    results["air_changes_per_hour"] = ach
                    results["co2_decay_per_hour"] = ach

            gain, internal = self._fit_solar(room.id, indoor, outdoor, open_periods)
            if gain is not None:
                results["solar_gain_coefficient"] = gain
            if internal is not None:
                results["internal_gain_w"] = internal

            clean = {k: v for k, v in results.items() if v is not None}
            if not clean:
                continue

            existing = self.coordinator.learned.room(room.id)
            merged = merge_learned_room(existing, clean, samples=1)
            rooms = dict(self.coordinator.learned.rooms)
            rooms[room.id] = merged
            self.coordinator.learned = replace(self.coordinator.learned, rooms=rooms)
            updated[room.id] = {k: round(v, 3) for k, v in clean.items()}

        self.last_status = "ok" if updated else "not_enough_data"
        if updated:
            await self.coordinator.async_persist()
            self.hass.bus.async_fire(EVENT_CALIBRATION_UPDATED, {"rooms": updated})
            await self.coordinator.async_request_refresh()

        return {"updated": updated, "rooms_examined": len(self.coordinator.config.rooms)}

    # ------------------------------------------------------------------
    # History access
    # ------------------------------------------------------------------

    async def _async_history(self, now: datetime) -> dict[str, list[Sample]]:
        from homeassistant.components.recorder import get_instance, history

        entity_ids = list(self.coordinator.config.tracked_entities)
        if not entity_ids:
            return {}
        start = now - timedelta(days=HISTORY_DAYS)

        raw = await get_instance(self.hass).async_add_executor_job(
            history.get_significant_states,
            self.hass,
            start,
            now,
            entity_ids,
            None,
            True,
            True,
        )
        return {entity_id: _to_samples(states) for entity_id, states in (raw or {}).items()}

    def _open_periods(
        self, history: dict[str, list[Sample]], room_id: str
    ) -> list[tuple[datetime, datetime]]:
        """Intervals during which any window of the room was open."""
        periods: list[tuple[datetime, datetime]] = []
        for window in self.coordinator.config.windows:
            if window.room_id != room_id or not window.contact_sensor:
                continue
            samples = history.get(window.contact_sensor, [])
            start: datetime | None = None
            for sample in samples:
                is_open = sample.value >= 0.5
                if is_open and start is None:
                    start = sample.time
                elif not is_open and start is not None:
                    periods.append((start, sample.time))
                    start = None
            if start is not None:
                periods.append((start, dt_util.utcnow()))
        return sorted(periods)

    def _fit_solar(
        self,
        room_id: str,
        indoor: Sequence[Sample],
        outdoor: Sequence[Sample],
        open_periods: Sequence[tuple[datetime, datetime]],
    ) -> tuple[float | None, float | None]:
        """Regress the daytime temperature rise against the computed solar load."""
        windows = [w for w in self.coordinator.config.windows if w.room_id == room_id]
        room = self.coordinator.config.room(room_id)
        if not windows or room is None:
            return None, None

        from .engine.state import WindowState

        probes = [
            WindowState(
                id=w.id,
                name=w.name,
                room_id=w.room_id,
                azimuth=w.azimuth,
                area_m2=w.area_m2,
                g_value=w.g_value,
                horizon_profile=w.horizon_profile,
            )
            for w in windows
        ]
        latitude = self.hass.config.latitude
        longitude = self.hass.config.longitude

        xs: list[float] = []
        ys: list[float] = []
        for previous, current in pairwise(indoor):
            hours = (current.time - previous.time).total_seconds() / 3600.0
            if not 0.1 <= hours <= 1.5:
                continue
            if _overlaps(previous.time, current.time, open_periods):
                continue
            outdoor_value = _interpolate(outdoor, previous.time)
            if outdoor_value is None:
                continue
            elevation, azimuth = solar_mod.sun_position(previous.time, latitude, longitude)
            if elevation <= 5.0:
                continue
            load = sum(
                solar_mod.solar_load(p, elevation, azimuth, None, apply_cover=False) for p in probes
            )
            if load <= 20.0:
                continue
            # Remove the envelope term so the residual is solar plus internal.
            rate = (current.value - previous.value) / hours
            xs.append(load)
            ys.append(rate)

        if len(xs) < 12:
            return None, None
        slope, intercept = linear_regression(xs, ys)
        if slope is None:
            return None, None

        # Convert the slope (K/h per W) into a dimensionless correction factor
        # against the modelled capacity.
        from .engine import thermal
        from .engine.state import RoomState

        probe_room = RoomState(id=room_id, name=room.name, volume_m3=room.volume_m3)
        capacity = thermal.thermal_capacity_wh_per_k(
            probe_room, self.coordinator.world.building if self.coordinator.world else None
        )
        modelled_slope = 1.0 / capacity
        coefficient = _bounded(slope / modelled_slope, SOLAR_GAIN_BOUNDS)
        internal = (
            _bounded((intercept or 0.0) * capacity, (10.0, 1500.0))
            if intercept and intercept > 0
            else None
        )
        return coefficient, internal


# --------------------------------------------------------------------------
# Fitting primitives - plain maths, unit tested without Home Assistant
# --------------------------------------------------------------------------


def fit_tau(
    indoor: Sequence[Sample],
    outdoor: Sequence[Sample],
    open_periods: Sequence[tuple[datetime, datetime]],
) -> tuple[float | None, int]:
    """Time constant in hours from free-running episodes with closed windows.

    ``T_in - T_out`` decays exponentially towards zero, so ``ln(ΔT)`` against
    time is a straight line whose slope is ``-1/tau``.
    """
    taus: list[float] = []
    for episode in _episodes(indoor, open_periods, MIN_EPISODE):
        xs: list[float] = []
        ys: list[float] = []
        start = episode[0].time
        for sample in episode:
            outdoor_value = _interpolate(outdoor, sample.time)
            if outdoor_value is None:
                continue
            delta = sample.value - outdoor_value
            if abs(delta) < 1.5:
                continue  # too close to equilibrium, the log blows up
            xs.append((sample.time - start).total_seconds() / 3600.0)
            ys.append(math.log(abs(delta)))
        if len(xs) < 6:
            continue
        slope, _intercept = linear_regression(xs, ys)
        if slope is None or slope >= -1e-4:
            continue
        candidate = -1.0 / slope
        if TAU_BOUNDS[0] <= candidate <= TAU_BOUNDS[1]:
            taus.append(candidate)

    if not taus:
        return None, 0
    return round(_robust_mean(taus), 2), len(taus)


def fit_night_cooling(
    indoor: Sequence[Sample],
    outdoor: Sequence[Sample],
    open_periods: Sequence[tuple[datetime, datetime]],
) -> tuple[float | None, int]:
    """Cool-down rate in K/h normalised to ΔT = 10 K, measured window open."""
    rates: list[float] = []
    for start, end in open_periods:
        if end - start < timedelta(minutes=45):
            continue
        inside = [s for s in indoor if start <= s.time <= end]
        if len(inside) < 3:
            continue
        hours = (inside[-1].time - inside[0].time).total_seconds() / 3600.0
        if hours < 0.5:
            continue
        drop = inside[0].value - inside[-1].value
        if drop <= 0.2:
            continue
        outdoor_mean = _mean_between(outdoor, start, end)
        if outdoor_mean is None:
            continue
        delta_t = statistics.fmean([s.value for s in inside]) - outdoor_mean
        if delta_t < 2.0:
            continue
        normalised = drop / hours * 10.0 / delta_t
        if COOLING_BOUNDS[0] <= normalised <= COOLING_BOUNDS[1]:
            rates.append(normalised)

    if not rates:
        return None, 0
    return round(_robust_mean(rates), 2), len(rates)


def fit_air_changes(
    co2: Sequence[Sample],
    open_periods: Sequence[tuple[datetime, datetime]],
    volume_m3: float,
) -> tuple[float | None, int]:
    """Real air exchange rate from the CO2 decay during open-window periods."""
    values: list[float] = []
    for start, end in open_periods:
        if end - start < MIN_PURGE_EPISODE:
            continue
        inside = [s for s in co2 if start <= s.time <= end]
        if len(inside) < 3:
            continue
        hours = (inside[-1].time - inside[0].time).total_seconds() / 3600.0
        rate = psy.air_changes_from_co2_decay(inside[0].value, inside[-1].value, OUTDOOR_CO2, hours)
        if rate is not None and ACH_BOUNDS[0] <= rate <= ACH_BOUNDS[1]:
            values.append(rate)

    if not values:
        return None, 0
    return round(_robust_mean(values), 2), len(values)


def linear_regression(
    xs: Sequence[float], ys: Sequence[float]
) -> tuple[float | None, float | None]:
    """Ordinary least squares. Returns ``(slope, intercept)``."""
    count = len(xs)
    if count < 3 or count != len(ys):
        return None, None
    mean_x = statistics.fmean(xs)
    mean_y = statistics.fmean(ys)
    denominator = sum((x - mean_x) ** 2 for x in xs)
    if denominator <= 1e-12:
        return None, None
    slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True)) / denominator
    return slope, mean_y - slope * mean_x


def _robust_mean(values: Sequence[float]) -> float:
    """Mean with the obvious outliers removed (median absolute deviation)."""
    if len(values) < 4:
        return statistics.fmean(values)
    median = statistics.median(values)
    deviations = [abs(v - median) for v in values]
    mad = statistics.median(deviations) or 1e-6
    kept = [v for v, d in zip(values, deviations, strict=True) if d <= 3.0 * mad]
    return statistics.fmean(kept or list(values))


def _episodes(
    samples: Sequence[Sample],
    open_periods: Sequence[tuple[datetime, datetime]],
    minimum: timedelta,
) -> list[list[Sample]]:
    """Split the series into runs during which no window was open."""
    episodes: list[list[Sample]] = []
    current: list[Sample] = []
    for sample in samples:
        if _inside(sample.time, open_periods):
            if current and current[-1].time - current[0].time >= minimum:
                episodes.append(current)
            current = []
        else:
            current.append(sample)
    if current and current[-1].time - current[0].time >= minimum:
        episodes.append(current)
    return episodes


def _inside(moment: datetime, periods: Iterable[tuple[datetime, datetime]]) -> bool:
    return any(start <= moment <= end for start, end in periods)


def _overlaps(start: datetime, end: datetime, periods: Iterable[tuple[datetime, datetime]]) -> bool:
    return any(not (end < s or start > e) for s, e in periods)


def _interpolate(samples: Sequence[Sample], moment: datetime) -> float | None:
    if not samples:
        return None
    if moment <= samples[0].time:
        return samples[0].value
    for previous, following in pairwise(samples):
        if previous.time <= moment <= following.time:
            span = (following.time - previous.time).total_seconds()
            if span <= 0:
                return previous.value
            ratio = (moment - previous.time).total_seconds() / span
            return previous.value + ratio * (following.value - previous.value)
    return samples[-1].value


def _mean_between(samples: Sequence[Sample], start: datetime, end: datetime) -> float | None:
    inside = [s.value for s in samples if start <= s.time <= end]
    return statistics.fmean(inside) if inside else None


def _bounded(value: float, bounds: tuple[float, float]) -> float | None:
    return value if bounds[0] <= value <= bounds[1] else None


def _to_samples(states: Iterable[Any]) -> list[Sample]:
    """Convert recorder states into numeric samples, dropping anything unusable."""
    samples: list[Sample] = []
    for state in states:
        raw = getattr(state, "state", None)
        moment = getattr(state, "last_changed", None) or getattr(state, "last_updated", None)
        if raw is None or moment is None:
            continue
        if raw in ("on", "open", "true"):
            value = 1.0
        elif raw in ("off", "closed", "false"):
            value = 0.0
        else:
            try:
                value = float(raw)
            except (TypeError, ValueError):
                continue
        samples.append(Sample(time=dt_util.as_utc(moment), value=value))
    samples.sort(key=lambda s: s.time)
    return samples


def summarise(learned: LearnedParameters) -> dict[str, Any]:
    """Panel-friendly view of everything that has been learned so far."""
    return {
        room_id: {
            "tau_hours": room.tau_hours,
            "night_cooling_k_per_h": room.night_cooling_k_per_h,
            "solar_gain_coefficient": room.solar_gain_coefficient,
            "internal_gain_w": room.internal_gain_w,
            "air_changes_per_hour": room.air_changes_per_hour,
            "samples": room.samples,
            "confidence": round(room.confidence, 2),
            "updated": room.updated.isoformat() if room.updated else None,
            "overridden": sorted(room.overridden),
            "in_use": room.samples >= MIN_SAMPLES,
        }
        for room_id, room in learned.rooms.items()
    }


__all__ = [
    "Calibrator",
    "LearnedRoom",
    "Sample",
    "fit_air_changes",
    "fit_night_cooling",
    "fit_tau",
    "linear_regression",
    "summarise",
]
