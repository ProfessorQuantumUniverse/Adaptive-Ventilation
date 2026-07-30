"""Thermal building model, purge duration and 24 h simulation.

A single RC node per room is enough in practice::

    dT_in/dt = ( (T_out - T_in)/R + Q_solar + Q_internal
                 + n_ach * V * rho * c_p * (T_out - T_in) / 3600 ) / C
    tau = R * C

Two learned parameters carry the whole model: the time constant ``tau`` and the
night cool-down rate at a given ΔT with the window open. Everything else is
seeded from the building type and then overwritten by measurements.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final, Iterable, Sequence

from . import psychrometrics as psy
from . import solar as solar_mod
from .state import (
    BuildingProfile,
    BuildingType,
    ForecastHour,
    LearnedRoom,
    RoomState,
    SunState,
    WindowState,
    bearing_difference,
    clamp,
)

#: Volumetric heat capacity of air in Wh/(m³·K).
AIR_HEAT_CAPACITY: Final = 0.34
#: Effective thermal capacity per m³ of room, including the share of walls,
#: floor and furniture that actually participates over a few hours, in
#: Wh/(m³·K). Calibrated together with ``BUILDING_DEFAULTS`` so that an open
#: window reproduces the cool-down rates of the table in SPEC.md section 7.
ROOM_CAPACITY_WH_PER_M3_K: Final[dict[BuildingType, float]] = {
    BuildingType.OLD_MASSIVE: 60.0,
    BuildingType.OLD_RENOVATED: 60.0,
    BuildingType.NEW_INSULATED: 65.0,
    BuildingType.LIGHTWEIGHT: 26.0,
    BuildingType.HALF_TIMBERED: 55.0,
    BuildingType.UNKNOWN: 55.0,
}
#: Assumed room volume when the user did not configure one.
DEFAULT_VOLUME_M3: Final = 40.0
#: Base internal gains per room in W (people, fridge, standby).
DEFAULT_INTERNAL_GAIN_W: Final = 80.0

#: Purge duration in minutes for a wide-open window, by outdoor temperature.
#: Table from specification section 6.
PURGE_TABLE: Final[tuple[tuple[float, float], ...]] = (
    (-10.0, 4.0),
    (0.0, 5.0),
    (10.0, 10.0),
    (18.0, 15.0),
    (100.0, 25.0),
)
#: Tilted windows exchange far less air; factor on the duration.
TILT_FACTOR: Final = 3.5
#: Cross ventilation raises the air exchange by 3-5x.
CROSS_VENTILATION_FACTOR: Final = 4.0
#: Damping between the (fast) room air and the (slow) building mass. Measured
#: night flush rates are roughly 80 % of what a single-node model predicts.
MASS_COUPLING: Final = 0.9
#: Geometry the purge duration table implicitly assumes: one 1.4 m² window in
#: a 40 m³ room. Real rooms are scaled against this reference.
REFERENCE_WINDOW_AREA: Final = 1.4
REFERENCE_ROOM_VOLUME: Final = 40.0


# --------------------------------------------------------------------------
# Basic parameters
# --------------------------------------------------------------------------


def room_volume(room: RoomState) -> float:
    return room.volume_m3 or DEFAULT_VOLUME_M3


def effective_tau(room: RoomState, building: BuildingProfile, learned: LearnedRoom) -> float:
    """Time constant in hours: learned > configured > building default."""
    if learned.tau_hours is not None and learned.samples >= 3:
        return learned.tau_hours
    if room.tau_hours is not None:
        return room.tau_hours
    if room.is_basement:
        return 200.0
    tau = building.default_tau_hours
    if room.is_top_floor or building.top_floor:
        tau *= 0.55
    return tau


def thermal_capacity_wh_per_k(
    room: RoomState, building: BuildingProfile | None = None
) -> float:
    """Effective heat capacity of the room in Wh/K."""
    building_type = building.building_type if building else BuildingType.UNKNOWN
    specific = ROOM_CAPACITY_WH_PER_M3_K[building_type]
    if room.is_basement:
        specific *= 1.6
    return room_volume(room) * specific


def envelope_conductance_w_per_k(
    room: RoomState, tau_hours: float, building: BuildingProfile | None = None
) -> float:
    """UA value of the room envelope in W/K, derived from tau and capacity."""
    return thermal_capacity_wh_per_k(room, building) / max(tau_hours, 1.0)


# --------------------------------------------------------------------------
# Air exchange
# --------------------------------------------------------------------------


def window_air_changes(
    window: WindowState,
    room: RoomState,
    delta_t: float,
    wind_speed: float | None = None,
    wind_bearing: float | None = None,
) -> float:
    """Air changes per hour through one open window.

    Combines the stack effect (driven by ΔT and window height) with wind
    pressure on the windward side. Deliberately coarse — calibration replaces
    it with a measured value as soon as one CO₂ decay has been observed.
    """
    if not window.is_open:
        return 0.0
    area = window.area_m2 or 1.4
    effective_area = area * (0.12 if window.is_tilted else 1.0)
    volume = room_volume(room)

    # Stack effect: volume flow scales with sqrt(|ΔT| * height). The prefactor
    # is calibrated so a 2 m² window in a 60 m³ room at ΔT = 10 K gives the
    # ~15 h⁻¹ that tracer gas measurements report for single sided ventilation.
    height = math.sqrt(max(area, 0.3))
    stack_flow = 0.20 * effective_area * math.sqrt(max(abs(delta_t), 0.2) * height * 9.81 / 293.0)

    wind_flow = 0.0
    if wind_speed:
        exposure = 1.0
        if wind_bearing is not None:
            # Windward windows get the full pressure, leeward ones about 40 %.
            angle = bearing_difference(wind_bearing, window.azimuth)
            exposure = 1.0 - 0.6 * (angle / 180.0)
        wind_flow = 0.05 * effective_area * wind_speed * exposure

    flow_m3_per_s = stack_flow + wind_flow
    return clamp(flow_m3_per_s * 3600.0 / volume, 0.0, 25.0)


def cross_ventilation_pairs(
    windows: Sequence[WindowState], rooms: Sequence[RoomState]
) -> list[tuple[WindowState, WindowState]]:
    """Pairs of windows that would form a real cross draught.

    Two windows qualify when their azimuths differ by more than 90° and their
    rooms are connected (or it is the same room). This is the feature that
    turns "12 minutes" into "4 minutes".
    """
    connections: dict[str, set[str]] = {}
    for room in rooms:
        connections.setdefault(room.id, set()).update(room.connected_rooms)
        for other in room.connected_rooms:
            connections.setdefault(other, set()).add(room.id)

    pairs: list[tuple[WindowState, WindowState]] = []
    for i, first in enumerate(windows):
        for second in windows[i + 1 :]:
            if bearing_difference(first.azimuth, second.azimuth) <= 90.0:
                continue
            same_room = first.room_id == second.room_id
            connected = second.room_id in connections.get(first.room_id, set())
            if same_room or connected:
                pairs.append((first, second))
    return pairs


def total_air_changes(
    room: RoomState,
    windows: Sequence[WindowState],
    delta_t: float,
    wind_speed: float | None = None,
    wind_bearing: float | None = None,
    *,
    cross: bool = False,
    learned: LearnedRoom | None = None,
) -> float:
    """Air changes per hour for a room with a given set of open windows."""
    base = 0.15 if not any(w.is_open for w in windows) else 0.0
    total = base + sum(
        window_air_changes(w, room, delta_t, wind_speed, wind_bearing) for w in windows
    )
    if cross and sum(1 for w in windows if w.is_open) >= 2:
        total *= CROSS_VENTILATION_FACTOR / 2.0
    if learned is not None and learned.air_changes_per_hour and learned.samples >= 3:
        # Scale the model towards the measured value instead of replacing it,
        # so the geometry dependency survives.
        modelled = max(total, 0.05)
        total *= clamp(learned.air_changes_per_hour / modelled, 0.4, 2.5)
    return total


# --------------------------------------------------------------------------
# Purge duration
# --------------------------------------------------------------------------


def base_purge_minutes(outdoor_temperature: float) -> float:
    """Wide-open purge duration in minutes from the ΔT table."""
    for limit, minutes in PURGE_TABLE:
        if outdoor_temperature < limit:
            return minutes
    return PURGE_TABLE[-1][1]


def reference_air_changes(delta_t: float) -> float:
    """Air exchange the duration table implicitly assumes, in h⁻¹."""
    reference_room = RoomState(id="_ref", name="reference", volume_m3=REFERENCE_ROOM_VOLUME)
    reference_window = WindowState(
        id="_ref", name="reference", room_id="_ref", is_open=True, area_m2=REFERENCE_WINDOW_AREA
    )
    return window_air_changes(reference_window, reference_room, delta_t)


def purge_duration(
    room: RoomState,
    outdoor_temperature: float,
    *,
    windows: Sequence[WindowState] = (),
    tilted: bool = False,
    cross: bool = False,
    wind_speed: float | None = None,
    wind_bearing: float | None = None,
    learned: LearnedRoom | None = None,
) -> int:
    """Recommended purge duration in whole minutes.

    The table from SPEC.md section 6 sets the thermal budget (how long you can
    afford to have the window open at this outdoor temperature). The air
    exchange model then scales it for *this* geometry: a big window, wind on
    the windward side or a real cross draught all shorten it, a tilted window
    stretches it.
    """
    minutes = base_purge_minutes(outdoor_temperature)
    delta_t = (room.temperature or 20.0) - outdoor_temperature

    candidates = list(windows)
    if candidates:
        probe = [_as_open(w) for w in candidates]
        ach = total_air_changes(
            room, probe, delta_t, wind_speed, wind_bearing, cross=cross, learned=learned
        )
        reference = reference_air_changes(delta_t)
        if ach > 0.1 and reference > 0.1:
            minutes *= clamp(reference / ach, 0.25, 2.5)

    if tilted:
        minutes *= TILT_FACTOR

    return int(round(clamp(minutes, 3.0, 60.0)))


def _as_open(window: WindowState, *, tilted: bool = False) -> WindowState:
    from dataclasses import replace as _replace

    return _replace(window, is_open=True, is_tilted=tilted)


# --------------------------------------------------------------------------
# RC simulation
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class SimulationPoint:
    """One step of a simulated indoor temperature curve."""

    time: datetime
    indoor_temperature: float
    outdoor_temperature: float
    solar_w: float
    air_changes: float
    windows_open: bool


@dataclass(frozen=True)
class SimulationResult:
    """Result of one what-if simulation variant."""

    label: str
    points: tuple[SimulationPoint, ...]
    peak_temperature: float
    peak_time: datetime | None
    end_temperature: float
    min_temperature: float
    delta_k: float

    def temperature_at(self, moment: datetime) -> float | None:
        best: SimulationPoint | None = None
        best_delta = timedelta(minutes=45)
        for point in self.points:
            delta = abs(point.time - moment)
            if delta <= best_delta:
                best, best_delta = point, delta
        return None if best is None else best.indoor_temperature


@dataclass(frozen=True)
class VentilationPlan:
    """A what-if plan: which windows are open when, and when covers close."""

    label: str
    open_from: datetime | None = None
    open_until: datetime | None = None
    window_ids: tuple[str, ...] = ()
    tilted: bool = False
    shading_from: datetime | None = None

    def is_open_at(self, moment: datetime) -> bool:
        if self.open_from is None or self.open_until is None:
            return False
        return self.open_from <= moment < self.open_until

    def is_shaded_at(self, moment: datetime) -> bool:
        return self.shading_from is not None and moment >= self.shading_from


def simulate(
    room: RoomState,
    windows: Sequence[WindowState],
    forecast: Sequence[ForecastHour],
    building: BuildingProfile,
    learned: LearnedRoom,
    plan: VentilationPlan,
    *,
    start: datetime | None = None,
    hours: float = 24.0,
    step_minutes: int = 15,
) -> SimulationResult:
    """Run the RC model forward through the forecast under ``plan``."""
    if room.temperature is None or not forecast:
        return SimulationResult(plan.label, (), 0.0, None, 0.0, 0.0, 0.0)

    begin = start or forecast[0].time
    tau = effective_tau(room, building, learned)
    capacity = thermal_capacity_wh_per_k(room, building)
    ua = envelope_conductance_w_per_k(room, tau, building)
    solar_coefficient = learned.solar_gain_coefficient or 1.0
    internal = learned.internal_gain_w or DEFAULT_INTERNAL_GAIN_W
    if room.internal_load_w:
        internal += room.internal_load_w

    plan_windows = [w for w in windows if not plan.window_ids or w.id in plan.window_ids]
    indoor = room.temperature
    points: list[SimulationPoint] = []
    steps = int(hours * 60 / step_minutes)
    dt_hours = step_minutes / 60.0

    for i in range(steps + 1):
        moment = begin + timedelta(minutes=i * step_minutes)
        outdoor = _interpolate_forecast(forecast, moment)
        if outdoor is None:
            break
        cloud = _interpolate_cloud(forecast, moment)

        elevation, azimuth = solar_mod.sun_position(
            moment, building.latitude, building.longitude
        )
        shaded = plan.is_shaded_at(moment)
        solar_w = 0.0
        for window in windows:
            gain = solar_mod.solar_load(window, elevation, azimuth, cloud, apply_cover=False)
            if shaded and window.has_cover:
                gain *= 1.0 - window.cover_shading_efficiency
            solar_w += gain
        solar_w *= solar_coefficient

        is_open = plan.is_open_at(moment)
        if is_open and plan_windows:
            probe = [_as_open(w, tilted=plan.tilted) for w in plan_windows]
            ach = total_air_changes(
                room,
                probe,
                indoor - outdoor,
                None,
                None,
                cross=len(probe) >= 2,
                learned=learned,
            )
        else:
            ach = 0.15

        ventilation_w = AIR_HEAT_CAPACITY * room_volume(room) * ach * (outdoor - indoor)
        envelope_w = ua * (outdoor - indoor)
        net_w = envelope_w + ventilation_w + solar_w + internal
        indoor += net_w / capacity * dt_hours

        points.append(
            SimulationPoint(moment, indoor, outdoor, solar_w, ach, is_open)
        )

    if not points:
        return SimulationResult(plan.label, (), 0.0, None, 0.0, 0.0, 0.0)

    peak = max(points, key=lambda p: p.indoor_temperature)
    low = min(p.indoor_temperature for p in points)
    return SimulationResult(
        label=plan.label,
        points=tuple(points),
        peak_temperature=peak.indoor_temperature,
        peak_time=peak.time,
        end_temperature=points[-1].indoor_temperature,
        min_temperature=low,
        delta_k=points[-1].indoor_temperature - room.temperature,
    )


def _interpolate_forecast(forecast: Sequence[ForecastHour], moment: datetime) -> float | None:
    """Linear interpolation of the forecast temperature at ``moment``."""
    if not forecast:
        return None
    if moment <= forecast[0].time:
        return forecast[0].temperature
    for previous, following in zip(forecast, forecast[1:]):
        if previous.time <= moment <= following.time:
            span = (following.time - previous.time).total_seconds()
            if span <= 0:
                return previous.temperature
            ratio = (moment - previous.time).total_seconds() / span
            return previous.temperature + ratio * (
                following.temperature - previous.temperature
            )
    return forecast[-1].temperature


def _interpolate_cloud(forecast: Sequence[ForecastHour], moment: datetime) -> float | None:
    best: ForecastHour | None = None
    best_delta = timedelta(minutes=90)
    for entry in forecast:
        delta = abs(entry.time - moment)
        if delta <= best_delta:
            best, best_delta = entry, delta
    return None if best is None else best.cloud_coverage


# --------------------------------------------------------------------------
# Tipping points and cooling potential
# --------------------------------------------------------------------------


def find_tipping_points(
    indoor_temperature: float,
    forecast: Sequence[ForecastHour],
    now: datetime,
    *,
    hysteresis: float = 0.5,
    hours: float = 30.0,
) -> tuple[datetime | None, datetime | None]:
    """Return ``(morning_close, evening_open)`` crossings of T_out and T_in.

    Morning = outdoor rises above indoor (time to shut everything).
    Evening = outdoor drops below indoor (time to open up again).
    """
    horizon = now + timedelta(hours=hours)
    entries = [f for f in forecast if now - timedelta(hours=1) <= f.time <= horizon]
    morning: datetime | None = None
    evening: datetime | None = None

    for previous, following in zip(entries, entries[1:]):
        before = previous.temperature - indoor_temperature
        after = following.temperature - indoor_temperature
        if before < -hysteresis <= after or (before < 0.0 <= after):
            crossing = _crossing_time(previous, following, indoor_temperature)
            if morning is None and crossing >= now:
                morning = crossing
        if before > hysteresis >= after or (before > 0.0 >= after):
            crossing = _crossing_time(previous, following, indoor_temperature)
            if evening is None and crossing >= now:
                evening = crossing
    return morning, evening


def _crossing_time(
    previous: ForecastHour, following: ForecastHour, target: float
) -> datetime:
    span_k = following.temperature - previous.temperature
    if abs(span_k) < 1e-6:
        return previous.time
    ratio = clamp((target - previous.temperature) / span_k, 0.0, 1.0)
    span = following.time - previous.time
    return previous.time + timedelta(seconds=span.total_seconds() * ratio)


def cooling_rate_k_per_h(
    room: RoomState,
    windows: Sequence[WindowState],
    outdoor_temperature: float,
    building: BuildingProfile,
    learned: LearnedRoom,
    *,
    wind_speed: float | None = None,
    cross: bool = False,
) -> float:
    """How fast the room cools right now with these windows open (K/h)."""
    if room.temperature is None:
        return 0.0
    delta_t = room.temperature - outdoor_temperature
    if delta_t <= 0.0:
        return 0.0

    if learned.night_cooling_k_per_h is not None and learned.samples >= 3:
        return learned.night_cooling_k_per_h * delta_t / 10.0

    probe = [_as_open(w) for w in windows]
    ach = total_air_changes(room, probe, delta_t, wind_speed, None, cross=cross, learned=learned)
    tau = effective_tau(room, building, learned)
    capacity = thermal_capacity_wh_per_k(room, building)
    ua = envelope_conductance_w_per_k(room, tau, building)
    watts = (AIR_HEAT_CAPACITY * room_volume(room) * ach + ua) * delta_t
    # The air cools almost instantly; the structure lags behind and keeps
    # feeding heat back. Without this coupling factor the model promises
    # cool-down rates that no real flat has ever delivered.
    return watts / capacity * MASS_COUPLING


def storable_cooling_k(room: RoomState, building: BuildingProfile, learned: LearnedRoom) -> float:
    """How much of tonight's cooling still exists tomorrow evening (K).

    The honest version of "my flat stores 4 K": a deep night flush is only
    worth something if the structure still holds it twelve hours later, and
    that retention is exactly ``exp(-12/tau)``.
    """
    tau = effective_tau(room, building, learned)
    retention = math.exp(-12.0 / max(tau, 2.0))
    return round(clamp(6.0 * retention, 0.4, 6.0), 2)


def night_cooling_potential_k(
    room: RoomState,
    windows: Sequence[WindowState],
    forecast: Sequence[ForecastHour],
    building: BuildingProfile,
    learned: LearnedRoom,
    now: datetime,
    *,
    hours: float = 12.0,
) -> float:
    """Kelvin obtainable tonight by keeping the given windows open.

    Integrates the cooling rate over every forecast hour that is actually
    cooler than the (dropping) indoor temperature.
    """
    if room.temperature is None:
        return 0.0
    indoor = room.temperature
    gained = 0.0
    horizon = now + timedelta(hours=hours)
    entries = [f for f in forecast if now <= f.time <= horizon]
    for entry in entries:
        if entry.temperature >= indoor - 0.3:
            continue
        probe = RoomState(
            id=room.id,
            name=room.name,
            temperature=indoor,
            volume_m3=room.volume_m3,
            tau_hours=room.tau_hours,
            is_basement=room.is_basement,
            is_top_floor=room.is_top_floor,
        )
        rate = cooling_rate_k_per_h(
            probe, windows, entry.temperature, building, learned, cross=len(windows) >= 2
        )
        step = min(rate, max(0.0, indoor - entry.temperature))
        indoor -= step
        gained += step
    return round(gained, 2)


def heating_rate_k_per_h(
    room: RoomState,
    windows: Sequence[WindowState],
    outdoor_temperature: float,
    sun: SunState,
    building: BuildingProfile,
    learned: LearnedRoom,
    cloud_coverage: float | None = None,
) -> float:
    """Current rate of change of the indoor temperature in K/h (signed)."""
    if room.temperature is None:
        return 0.0
    tau = effective_tau(room, building, learned)
    capacity = thermal_capacity_wh_per_k(room, building)
    ua = envelope_conductance_w_per_k(room, tau, building)
    solar_w = sum(
        solar_mod.solar_load(w, sun.elevation, sun.azimuth, cloud_coverage) for w in windows
    ) * (learned.solar_gain_coefficient or 1.0)
    internal = (learned.internal_gain_w or DEFAULT_INTERNAL_GAIN_W) + (room.internal_load_w or 0.0)
    delta_t = outdoor_temperature - room.temperature
    ach = total_air_changes(room, windows, -delta_t, learned=learned)
    ventilation_w = AIR_HEAT_CAPACITY * room_volume(room) * ach * delta_t
    return (ua * delta_t + ventilation_w + solar_w + internal) / capacity


def projected_temperature(
    room: RoomState, rate_k_per_h: float, hours: float
) -> float | None:
    """Naive linear projection, damped so it does not run away."""
    if room.temperature is None:
        return None
    damping = 1.0 / (1.0 + 0.08 * hours)
    return room.temperature + rate_k_per_h * hours * damping


def is_tropical_night(forecast: Sequence[ForecastHour], now: datetime, threshold: float = 20.0) -> bool:
    """Tropical night = the overnight minimum stays above ``threshold``."""
    night = [
        f
        for f in forecast
        if now <= f.time <= now + timedelta(hours=14) and (f.time.hour >= 22 or f.time.hour <= 6)
    ]
    if not night:
        return False
    return min(f.temperature for f in night) > threshold


def heatwave_ahead(
    forecast: Sequence[ForecastHour], now: datetime, threshold: float = 32.0
) -> tuple[bool, float, datetime | None]:
    """Detect a heat peak 12-72 h out: ``(found, peak_temperature, peak_time)``."""
    window = [
        f
        for f in forecast
        if now + timedelta(hours=12) <= f.time <= now + timedelta(hours=72)
    ]
    if not window:
        return False, 0.0, None
    peak = max(window, key=lambda f: f.temperature)
    return peak.temperature >= threshold, peak.temperature, peak.time


def find_temperature_drop(
    forecast: Sequence[ForecastHour],
    now: datetime,
    *,
    hours: float = 8.0,
    min_drop_k: float = 5.0,
) -> tuple[datetime, datetime, float] | None:
    """Find a sharp cold front (thunderstorm outflow) in the near forecast."""
    entries = [f for f in forecast if now <= f.time <= now + timedelta(hours=hours)]
    for i, start in enumerate(entries):
        for end in entries[i + 1 : i + 4]:
            drop = start.temperature - end.temperature
            if drop >= min_drop_k:
                return start.time, end.time, drop
    return None


def mixing_after_purge(
    room: RoomState, outdoor_temperature: float, outdoor_humidity: float | None, minutes: int,
    air_changes: float,
) -> tuple[float, float | None]:
    """Indoor temperature and RH expected right after a purge of ``minutes``."""
    if room.temperature is None:
        return outdoor_temperature, outdoor_humidity
    exchanged = 1.0 - math.exp(-air_changes * minutes / 60.0)
    if room.humidity is None or outdoor_humidity is None:
        return (
            room.temperature + exchanged * (outdoor_temperature - room.temperature),
            room.humidity,
        )
    return psy.humidity_after_mixing(
        room.temperature, room.humidity, outdoor_temperature, outdoor_humidity, exchanged
    )


def sum_solar_load(
    windows: Iterable[WindowState], sun: SunState, cloud_coverage: float | None = None
) -> float:
    """Total solar power through a set of windows in W."""
    return sum(
        solar_mod.solar_load(w, sun.elevation, sun.azimuth, cloud_coverage) for w in windows
    )
