"""Everything a rule needs, computed exactly once per evaluation.

Rules receive an :class:`EvaluationContext` rather than the raw
:class:`~.state.WorldState` so that expensive derived values (solar loads,
tipping points, cooling potential, per-room assessments) are shared instead of
recomputed thirty times.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from . import psychrometrics as psy
from . import solar as solar_mod
from . import thermal
from .state import (
    CoolingBudget,
    EngineMemory,
    LearnedRoom,
    MoldRisk,
    OutdoorState,
    Preferences,
    RoomAssessment,
    RoomState,
    Season,
    TippingPoints,
    WindowState,
    WorldState,
    clamp,
)


@dataclass
class EvaluationContext:
    """Shared, pre-computed view of the world for one engine run."""

    state: WorldState
    memory: EngineMemory
    assessments: dict[str, RoomAssessment] = field(default_factory=dict)
    solar_loads: dict[str, float] = field(default_factory=dict)
    cover_savings: dict[str, float] = field(default_factory=dict)
    next_sun_hit: dict[str, datetime | None] = field(default_factory=dict)
    cross_pairs: list[tuple[WindowState, WindowState]] = field(default_factory=list)
    tipping: TippingPoints = field(default_factory=TippingPoints)
    cooling_budget: CoolingBudget = field(default_factory=CoolingBudget)
    night_potential: dict[str, float] = field(default_factory=dict)
    notes: dict[str, object] = field(default_factory=dict)

    # -- shortcuts ---------------------------------------------------------
    @property
    def now(self) -> datetime:
        return self.state.now

    @property
    def outdoor(self) -> OutdoorState:
        return self.state.outdoor

    @property
    def preferences(self) -> Preferences:
        return self.state.preferences

    @property
    def season(self) -> Season:
        return self.state.season

    @property
    def rooms(self) -> tuple[RoomState, ...]:
        return self.state.rooms

    @property
    def windows(self) -> tuple[WindowState, ...]:
        return self.state.windows

    def learned(self, room_id: str) -> LearnedRoom:
        return self.state.learned.room(room_id)

    def room_of(self, window: WindowState) -> RoomState | None:
        return self.state.room(window.room_id)

    def windows_of(self, room: RoomState) -> list[WindowState]:
        return self.state.windows_of(room.id)

    def assessment(self, room_id: str) -> RoomAssessment:
        return self.assessments.get(room_id, RoomAssessment(room_id=room_id))

    def is_cross_partner(self, window: WindowState) -> WindowState | None:
        """A window that would form a cross draught together with ``window``."""
        for first, second in self.cross_pairs:
            if first.id == window.id:
                return second
            if second.id == window.id:
                return first
        return None

    def purge_minutes(self, room: RoomState, *, tilted: bool = False, cross: bool = False) -> int:
        return thermal.purge_duration(
            room,
            self.outdoor.temperature,
            windows=self.windows_of(room),
            tilted=tilted,
            cross=cross,
            wind_speed=self.outdoor.wind_speed,
            wind_bearing=self.outdoor.wind_bearing,
            learned=self.learned(room.id),
        )

    def ventilation_dries(self, room: RoomState) -> bool | None:
        """``True`` when opening lowers the indoor absolute humidity."""
        delta = self.state.delta_ah(room)
        if delta is None:
            return None
        return delta > 0.3

    def enthalpy_delta(self, room: RoomState) -> float | None:
        """Indoor minus outdoor enthalpy (positive = outdoor air is the cooler one)."""
        indoor = room.enthalpy(self.outdoor.pressure or 1013.25)
        outdoor = self.outdoor.enthalpy
        if indoor is None or outdoor is None:
            return None
        return float(indoor - outdoor)

    def sun_ahead(self, window: WindowState) -> datetime | None:
        return self.next_sun_hit.get(window.id)

    def minutes_until(self, moment: datetime | None) -> float | None:
        if moment is None:
            return None
        return (moment - self.now).total_seconds() / 60.0

    def targets_all_windows(
        self, predicate: Callable[[WindowState], bool] | None = None
    ) -> list[WindowState]:
        if predicate is None:
            return list(self.windows)
        return [w for w in self.windows if predicate(w)]


# --------------------------------------------------------------------------
# Context construction
# --------------------------------------------------------------------------


def build_context(state: WorldState, memory: EngineMemory) -> EvaluationContext:
    """Compute every derived value the rules and entities need."""
    ctx = EvaluationContext(state=state, memory=memory)

    ctx.cross_pairs = thermal.cross_ventilation_pairs(state.windows, state.rooms)

    cloud = state.outdoor.cloud_coverage
    for window in state.windows:
        ctx.solar_loads[window.id] = solar_mod.solar_load(
            window, state.sun.elevation, state.sun.azimuth, cloud
        )
        ctx.cover_savings[window.id] = solar_mod.cover_saving_w(
            window, state.sun.elevation, state.sun.azimuth, cloud
        )
        ctx.next_sun_hit[window.id] = solar_mod.next_sun_hit(
            window, state.now, state.building.latitude, state.building.longitude
        )

    for room in state.rooms:
        ctx.assessments[room.id] = _assess_room(ctx, room)
        usable = [w for w in state.windows_of(room.id) if w.ok_when_away or state.presence]
        # Promise at most a two window (cross draught) flush, never "all of them".
        usable.sort(key=lambda w: w.area_m2 or 1.0, reverse=True)
        ctx.night_potential[room.id] = thermal.night_cooling_potential_k(
            room,
            usable[:2],
            state.forecast,
            state.building,
            state.learned.room(room.id),
            state.now,
        )

    ctx.tipping = _tipping_points(ctx)
    ctx.cooling_budget = _cooling_budget(ctx)
    return ctx


def _assess_room(ctx: EvaluationContext, room: RoomState) -> RoomAssessment:
    state = ctx.state
    learned = state.learned.room(room.id)
    windows = state.windows_of(room.id)
    pressure = state.outdoor.pressure or 1013.25

    surface_temp: float | None = None
    surface_rh: float | None = None
    risk = MoldRisk.NONE
    if room.temperature is not None and room.humidity is not None:
        surface_temp = psy.wall_surface_temperature(
            room.temperature, state.outdoor.temperature, state.building.f_rt
        )
        surface_rh = psy.surface_relative_humidity(room.temperature, room.humidity, surface_temp)
        risk = MoldRisk(psy.mold_risk_level(surface_rh))
        if state.building.building_type.value == "half_timbered" and risk is MoldRisk.LOW:
            # Timber framing is moisture sensitive: escalate one step.
            risk = MoldRisk.MODERATE

    rate = thermal.heating_rate_k_per_h(
        room,
        windows,
        state.outdoor.temperature,
        state.sun,
        state.building,
        learned,
        state.outdoor.cloud_coverage,
    )
    projected = thermal.projected_temperature(room, rate, 4.0)

    open_windows = [w for w in windows if w.is_open] or windows
    effect = thermal.cooling_rate_k_per_h(
        room,
        open_windows,
        state.outdoor.temperature,
        state.building,
        learned,
        wind_speed=state.outdoor.wind_speed,
        cross=len(open_windows) >= 2,
    )

    score, breakdown = _air_quality_score(ctx, room, risk)

    return RoomAssessment(
        room_id=room.id,
        absolute_humidity=room.absolute_humidity,
        dew_point=room.dew_point,
        enthalpy=room.enthalpy(pressure),
        wall_surface_temperature=None if surface_temp is None else round(surface_temp, 1),
        surface_humidity=None if surface_rh is None else round(surface_rh, 1),
        mold_risk=risk,
        heating_rate_k_per_h=round(rate, 2),
        projected_temperature=None if projected is None else round(projected, 1),
        projected_time=state.now + timedelta(hours=4),
        air_quality_score=score,
        score_breakdown=breakdown,
        ventilation_effect_k_per_h=round(effect, 2),
        confidence=room.confidence,
    )


def _air_quality_score(
    ctx: EvaluationContext, room: RoomState, risk: MoldRisk
) -> tuple[int, dict[str, int]]:
    """0-100 score built from the four axes of section 10.4."""
    prefs = ctx.preferences
    season = ctx.season
    low, high = prefs.target_band(season)
    if room.target_min is not None:
        low = room.target_min
    if room.target_max is not None:
        high = room.target_max

    breakdown: dict[str, int] = {}

    if room.temperature is None:
        breakdown["temperature"] = 100
    else:
        if room.temperature < low:
            deviation = low - room.temperature
        elif room.temperature > high:
            deviation = room.temperature - high
        else:
            deviation = 0.0
        breakdown["temperature"] = int(clamp(100.0 - deviation * 12.0, 0.0, 100.0))

    if room.humidity is None:
        breakdown["humidity"] = 100
    else:
        if room.humidity < prefs.humidity_target_min:
            deviation = prefs.humidity_target_min - room.humidity
        elif room.humidity > prefs.humidity_target_max:
            deviation = room.humidity - prefs.humidity_target_max
        else:
            deviation = 0.0
        penalty = deviation * 2.0
        if risk in (MoldRisk.HIGH, MoldRisk.CONDENSATION):
            penalty += 40.0
        elif risk is MoldRisk.MODERATE:
            penalty += 20.0
        breakdown["humidity"] = int(clamp(100.0 - penalty, 0.0, 100.0))

    if room.co2 is None:
        breakdown["co2"] = 100
    else:
        excess = max(0, room.co2 - 600)
        breakdown["co2"] = int(clamp(100.0 - excess / 10.0, 0.0, 100.0))

    if room.pm25 is None:
        breakdown["particulate"] = 100
    else:
        breakdown["particulate"] = int(clamp(100.0 - room.pm25 * 2.5, 0.0, 100.0))

    weights = {
        "temperature": prefs.weight("temperature"),
        "humidity": prefs.weight("humidity"),
        "co2": prefs.weight("co2"),
        "particulate": prefs.weight("particulate"),
    }
    total_weight = sum(weights.values()) or 1.0
    score = sum(breakdown[k] * weights[k] for k in breakdown) / total_weight
    return round(clamp(score, 0.0, 100.0)), breakdown


def _tipping_points(ctx: EvaluationContext) -> TippingPoints:
    state = ctx.state
    reference = _reference_indoor_temperature(state.rooms)
    if reference is None or not state.forecast:
        return TippingPoints()

    morning, evening = thermal.find_tipping_points(
        reference,
        state.forecast,
        state.now,
        hysteresis=state.preferences.delta_t_hysteresis,
    )
    cooler_outside = state.outdoor.temperature < reference - state.preferences.delta_t_hysteresis
    upcoming = [t for t in (morning, evening) if t is not None]
    minutes = min((t - state.now).total_seconds() / 60.0 for t in upcoming) if upcoming else None

    # Confidence drops with distance and with the number of estimated rooms.
    measured = sum(1 for r in state.rooms if not r.is_estimated and r.temperature is not None)
    base_confidence = clamp(0.4 + 0.2 * measured, 0.3, 0.95)
    return TippingPoints(
        morning=morning,
        evening=evening,
        morning_confidence=base_confidence if morning else 0.0,
        evening_confidence=base_confidence * 0.9 if evening else 0.0,
        cooler_outside=cooler_outside,
        minutes_to_next=minutes,
    )


def _reference_indoor_temperature(rooms: Sequence[RoomState]) -> float | None:
    """Indoor temperature the tipping points refer to: the highest priority room."""
    candidates = [r for r in rooms if r.temperature is not None]
    if not candidates:
        return None
    best = min(candidates, key=lambda r: (r.priority, -(r.temperature or 0.0)))
    return best.temperature


def _cooling_budget(ctx: EvaluationContext) -> CoolingBudget:
    state = ctx.state
    if state.season is not Season.SUMMER:
        return CoolingBudget(verdict_key="not_applicable")

    rooms = [r for r in state.rooms if r.temperature is not None]
    if not rooms:
        return CoolingBudget(verdict_key="unknown")

    achievable = sum(ctx.night_potential.get(r.id, 0.0) for r in rooms) / len(rooms)
    storage = sum(
        thermal.storable_cooling_k(r, state.building, state.learned.room(r.id)) for r in rooms
    ) / len(rooms)

    # What we need: get the warmest priority room back into its comfort band.
    prefs = state.preferences
    required = 0.0
    for room in rooms:
        high = room.target_max if room.target_max is not None else prefs.summer_target_max
        required = max(required, (room.temperature or 0.0) - high)

    rates = [a.heating_rate_k_per_h or 0.0 for a in ctx.assessments.values()]
    gained = sum(-r for r in rates if r < 0.0)
    lost = sum(r for r in rates if r > 0.0)
    balance = ctx.memory.balance(3)

    tropical = thermal.is_tropical_night(state.forecast, state.now, prefs.tropical_night_threshold)
    heatwave, peak, _peak_time = thermal.heatwave_ahead(
        state.forecast, state.now, prefs.heatwave_threshold
    )

    if tropical and achievable < required - 0.5:
        verdict = "insufficient"
    elif heatwave and achievable < required:
        verdict = "precool_needed"
    elif achievable >= required:
        verdict = "sufficient"
    else:
        verdict = "tight"

    return CoolingBudget(
        gained_k=round(gained, 2),
        lost_k=round(lost, 2),
        achievable_tonight_k=round(achievable, 2),
        required_tonight_k=round(required, 2),
        storage_k=round(storage, 2),
        balance_3d=round(balance, 2),
        verdict_key=verdict,
        verdict_data={
            "achievable": round(achievable, 1),
            "required": round(required, 1),
            "storage": round(storage, 1),
            "peak": round(peak, 1) if heatwave else None,
        },
    )
