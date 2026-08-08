"""Pure data model of the Adaptive Ventilation engine.

This module must never import Home Assistant. Everything in ``engine`` is a
plain Python package so the whole decision logic can be unit tested and
replayed offline (see ``scripts/replay.py``).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, time, timedelta
from enum import IntEnum, StrEnum
from functools import cached_property
import math
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import psychrometrics as psy

# --------------------------------------------------------------------------
# Enumerations
# --------------------------------------------------------------------------


class Action(StrEnum):
    """Action a recommendation asks the user (or an automation) to perform."""

    OPEN_WIDE = "open_wide"
    OPEN_TILT = "open_tilt"
    PURGE = "purge"
    CROSS_VENTILATE = "cross_ventilate"
    CLOSE = "close"
    KEEP_CLOSED = "keep_closed"
    KEEP_OPEN = "keep_open"
    COVER_DOWN = "cover_down"
    COVER_UP = "cover_up"
    COVER_SLAT = "cover_slat"
    FAN_ON = "fan_on"
    NO_ACTION = "no_action"


#: Actions that require the window to end up open.
OPENING_ACTIONS: frozenset[Action] = frozenset(
    {Action.OPEN_WIDE, Action.OPEN_TILT, Action.PURGE, Action.CROSS_VENTILATE, Action.KEEP_OPEN}
)
#: Actions that require the window to end up closed.
CLOSING_ACTIONS: frozenset[Action] = frozenset({Action.CLOSE, Action.KEEP_CLOSED})
#: Actions that address a cover instead of a window.
COVER_ACTIONS: frozenset[Action] = frozenset(
    {Action.COVER_DOWN, Action.COVER_UP, Action.COVER_SLAT}
)


def local_day(moment: datetime, timezone: str | None) -> str:
    """ISO calendar day of ``moment`` on ``timezone``'s clock.

    Everything in the engine is UTC, but "today" is a thing the user sees on
    their own wall clock: the daily push budget, "ignore today" and the manual
    hold all rolled over at 02:00 local in central European summer.
    """
    if timezone is not None and moment.tzinfo is not None:
        try:
            return moment.astimezone(ZoneInfo(timezone)).date().isoformat()
        except (ZoneInfoNotFoundError, ValueError):
            pass
    return moment.date().isoformat()


class Priority(IntEnum):
    """Recommendation class. Higher wins, SAFETY always wins."""

    OPTIMIZATION = 0
    COMFORT = 1
    HEALTH = 2
    SAFETY = 3


class Mode(StrEnum):
    """Operating mode of the integration."""

    AUTO = "auto"
    SUMMER = "summer"
    WINTER = "winter"
    AWAY = "away"
    OFF = "off"


class Season(StrEnum):
    """Resolved season used by the rules (``AUTO`` is resolved to one of these)."""

    SUMMER = "summer"
    WINTER = "winter"
    TRANSITIONAL = "transitional"


class GlobalState(StrEnum):
    """State machine of ``sensor.adaptive_ventilation_status``."""

    IDLE = "idle"
    VENTILATE_NOW = "ventilate_now"
    KEEP_CLOSED = "keep_closed"
    HEAT_PROTECTION = "heat_protection"
    NIGHT_FLUSH = "night_flush"
    PURGE_RUNNING = "purge_running"
    STORM = "storm"
    AIR_QUALITY = "air_quality"
    AWAY = "away"
    OFF = "off"
    UNAVAILABLE_DATA = "unavailable_data"


class MoldRisk(StrEnum):
    """Mold risk classes derived from the surface relative humidity."""

    NONE = "none"
    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"
    CONDENSATION = "condensation"


class BuildingType(StrEnum):
    """Coarse building classification, used only to seed the thermal model."""

    OLD_MASSIVE = "old_massive"
    OLD_RENOVATED = "old_renovated"
    NEW_INSULATED = "new_insulated"
    LIGHTWEIGHT = "lightweight"
    HALF_TIMBERED = "half_timbered"
    UNKNOWN = "unknown"


class SlotQuality(StrEnum):
    """Quality rating of a slot in the 24 h ventilation schedule."""

    GOOD = "good"
    FAIR = "fair"
    BAD = "bad"
    BLOCKED = "blocked"


EstimationMethod = Literal["measured", "reference_offset", "learned_offset", "model", "average"]
AlertKind = Literal["storm", "hail", "thunderstorm", "rain", "heat", "frost", "snow", "other"]
AlertSeverity = Literal["minor", "moderate", "severe", "extreme"]

#: τ (hours) and night cool-down rate (K/h at ΔT = 10 K, window wide open).
BUILDING_DEFAULTS: dict[BuildingType, tuple[float, float]] = {
    BuildingType.OLD_MASSIVE: (60.0, 1.2),
    BuildingType.OLD_RENOVATED: (80.0, 1.0),
    BuildingType.NEW_INSULATED: (110.0, 0.8),
    BuildingType.LIGHTWEIGHT: (20.0, 2.0),
    BuildingType.HALF_TIMBERED: (45.0, 1.2),
    BuildingType.UNKNOWN: (60.0, 1.0),
}

#: Thermal bridge temperature factor f_Rsi per building type (DIN 4108-2).
F_RT_DEFAULTS: dict[BuildingType, float] = {
    BuildingType.OLD_MASSIVE: 0.35,
    BuildingType.OLD_RENOVATED: 0.20,
    BuildingType.NEW_INSULATED: 0.12,
    BuildingType.LIGHTWEIGHT: 0.18,
    BuildingType.HALF_TIMBERED: 0.35,
    BuildingType.UNKNOWN: 0.25,
}


# --------------------------------------------------------------------------
# World state
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class OutdoorState:
    """Everything we know about the air outside."""

    temperature: float
    humidity: float | None = None
    absolute_humidity: float | None = None
    dew_point: float | None = None
    pm25: float | None = None
    pm10: float | None = None
    wind_speed: float | None = None
    wind_bearing: float | None = None
    precipitation: float | None = None
    precipitation_probability: float | None = None
    cloud_coverage: float | None = None
    illuminance: float | None = None
    pressure: float | None = None
    source: Literal["sensor", "weather", "mixed"] = "weather"
    is_stale: bool = False

    @classmethod
    def create(
        cls, temperature: float, humidity: float | None = None, **kwargs: Any
    ) -> OutdoorState:
        """Build an :class:`OutdoorState` and derive the psychrometric values."""
        derived: dict[str, Any] = {}
        if humidity is not None:
            derived["absolute_humidity"] = psy.absolute_humidity(temperature, humidity)
            derived["dew_point"] = psy.dew_point(temperature, humidity)
        derived.update(kwargs)
        return cls(temperature=temperature, humidity=humidity, **derived)

    @cached_property
    def enthalpy(self) -> float | None:
        """Specific enthalpy in kJ/kg (``None`` without humidity)."""
        if self.humidity is None:
            return None
        return psy.enthalpy(self.temperature, self.humidity, self.pressure or 1013.25)

    @cached_property
    def is_raining(self) -> bool:
        return (self.precipitation or 0.0) > 0.1


@dataclass(frozen=True)
class ForecastHour:
    """One hour of the weather forecast."""

    time: datetime
    temperature: float
    humidity: float | None = None
    apparent_temperature: float | None = None
    precipitation: float | None = None
    precipitation_probability: float | None = None
    cloud_coverage: float | None = None
    wind_speed: float | None = None
    wind_bearing: float | None = None
    pressure: float | None = None
    condition: str | None = None

    @cached_property
    def absolute_humidity(self) -> float | None:
        if self.humidity is None:
            return None
        return psy.absolute_humidity(self.temperature, self.humidity)

    @cached_property
    def dew_point(self) -> float | None:
        if self.humidity is None:
            return None
        return psy.dew_point(self.temperature, self.humidity)

    @cached_property
    def enthalpy(self) -> float | None:
        if self.humidity is None:
            return None
        return psy.enthalpy(self.temperature, self.humidity, self.pressure or 1013.25)


@dataclass(frozen=True)
class SunState:
    """Sun position and the coordinates needed to extrapolate it."""

    azimuth: float
    elevation: float
    next_rising: datetime | None = None
    next_setting: datetime | None = None
    latitude: float = 50.0
    longitude: float = 8.0

    @property
    def is_up(self) -> bool:
        return self.elevation > 0.0


@dataclass(frozen=True)
class WeatherAlert:
    """A weather warning from DWD / Meteoalarm or a comparable source."""

    event: str
    severity: AlertSeverity
    kind: AlertKind = "other"
    start: datetime | None = None
    end: datetime | None = None
    headline: str | None = None

    def is_active(self, now: datetime) -> bool:
        if self.start is not None and now < self.start:
            return False
        return not (self.end is not None and now > self.end)


@dataclass(frozen=True)
class BuildingProfile:
    """Coarse building description, only used to seed the thermal model."""

    building_type: BuildingType = BuildingType.UNKNOWN
    construction_year: int | None = None
    floor: int = 0
    top_floor: bool = False
    f_rt_override: float | None = None
    latitude: float = 50.0
    longitude: float = 8.0
    elevation_m: float = 100.0

    @property
    def f_rt(self) -> float:
        """Temperature factor of the coldest wall surface."""
        if self.f_rt_override is not None:
            return self.f_rt_override
        return F_RT_DEFAULTS[self.building_type]

    @property
    def default_tau_hours(self) -> float:
        return BUILDING_DEFAULTS[self.building_type][0]

    @property
    def default_night_cooling_k_per_h(self) -> float:
        return BUILDING_DEFAULTS[self.building_type][1]


@dataclass(frozen=True)
class Preferences:
    """User weights, thresholds and quiet times.

    The four weights are the heart of section 10.4 of the specification: every
    one of them can *demand* or *forbid* ventilation, the arbiter balances them.
    All weights are 0..100 with 50 = neutral.
    """

    # -- the four axes -----------------------------------------------------
    weight_temperature: int = 60
    weight_humidity: int = 50
    weight_co2: int = 60
    weight_particulate: int = 50
    # -- meta axes ---------------------------------------------------------
    weight_energy: int = 50
    notification_restraint: int = 50

    # -- comfort bands -----------------------------------------------------
    summer_target_min: float = 22.0
    summer_target_max: float = 26.0
    winter_target_min: float = 19.0
    winter_target_max: float = 23.0
    humidity_target_min: float = 40.0
    humidity_target_max: float = 60.0

    # -- thresholds --------------------------------------------------------
    co2_threshold: int = 1000
    co2_urgent: int = 1400
    voc_threshold: float = 250.0
    pm25_indoor_threshold: float = 25.0
    pm25_outdoor_threshold: float = 25.0
    delta_t_hysteresis: float = 0.5
    co2_hysteresis: int = 150
    storm_wind_kmh: float = 60.0
    frost_temperature: float = 3.0
    tropical_night_threshold: float = 20.0
    heatwave_threshold: float = 32.0
    dry_air_absolute: float = 4.0

    # -- calmness ----------------------------------------------------------
    min_state_duration_minutes: int = 15
    cooldown_minutes: int = 60
    quiet_hours_start: time = time(22, 0)
    quiet_hours_end: time = time(7, 0)
    max_pushes_per_day: int = 6
    close_lead_time_minutes: int = 30
    window_forgotten_hours: float = 3.0
    #: IANA name of the household's timezone. Quiet hours are wall-clock times
    #: the user typed while looking at their own clock, but every timestamp in
    #: the engine is UTC - without this they were compared directly, so "22:00
    #: to 07:00" silenced 00:00 to 09:00 local in central European summer and
    #: swallowed exactly the two pushes the integration exists for: the night
    #: flush and the morning close.
    timezone: str | None = None

    # -- behaviour ---------------------------------------------------------
    allow_cover_automation: bool = False
    notifications_enabled: bool = True
    actionable_notifications: bool = True
    min_confidence_for_push: float = 0.7

    def __post_init__(self) -> None:
        """Accept ``"22:00"`` as well as ``time(22, 0)``.

        Preferences arrive from three directions - the options flow, a YAML
        scenario and the replay map file - and only the first of those hands
        over real ``time`` objects. Coercing here beats a ``TypeError`` deep
        inside a quiet-hours comparison.
        """
        for field_name in ("quiet_hours_start", "quiet_hours_end"):
            value = getattr(self, field_name)
            if isinstance(value, str):
                object.__setattr__(self, field_name, _parse_clock(value))

    def weight(self, axis: Literal["temperature", "humidity", "co2", "particulate"]) -> float:
        """Return an axis weight normalised to 0.0 .. 2.0 (1.0 = neutral)."""
        raw = {
            "temperature": self.weight_temperature,
            "humidity": self.weight_humidity,
            "co2": self.weight_co2,
            "particulate": self.weight_particulate,
        }[axis]
        return raw / 50.0

    def local_time(self, moment: datetime) -> time:
        """``moment`` on the household's own clock."""
        if self.timezone is None or moment.tzinfo is None:
            return moment.time()
        try:
            return moment.astimezone(ZoneInfo(self.timezone)).time()
        except (ZoneInfoNotFoundError, ValueError):
            return moment.time()

    def in_quiet_hours(self, moment: datetime) -> bool:
        """Whether ``moment`` falls into the configured quiet hours."""
        current = self.local_time(moment)
        start, end = self.quiet_hours_start, self.quiet_hours_end
        if start == end:
            return False
        if start < end:
            return start <= current < end
        return current >= start or current < end

    def target_band(self, season: Season) -> tuple[float, float]:
        if season is Season.WINTER:
            return self.winter_target_min, self.winter_target_max
        return self.summer_target_min, self.summer_target_max


@dataclass(frozen=True)
class LearnedRoom:
    """Self-calibrated parameters for one room."""

    tau_hours: float | None = None
    night_cooling_k_per_h: float | None = None
    solar_gain_coefficient: float | None = None
    internal_gain_w: float | None = None
    air_changes_per_hour: float | None = None
    co2_decay_per_hour: float | None = None
    samples: int = 0
    confidence: float = 0.0
    updated: datetime | None = None
    overridden: frozenset[str] = frozenset()


@dataclass(frozen=True)
class LearnedParameters:
    """Container of all self-calibrated values, persisted through HA ``Store``."""

    version: int = 1
    rooms: dict[str, LearnedRoom] = field(default_factory=dict)
    window_azimuth: dict[str, float] = field(default_factory=dict)

    def room(self, room_id: str) -> LearnedRoom:
        return self.rooms.get(room_id, LearnedRoom())


@dataclass(frozen=True)
class RoomState:
    """Everything we know about one room."""

    id: str
    name: str
    temperature: float | None = None
    humidity: float | None = None
    absolute_humidity: float | None = None
    dew_point: float | None = None
    co2: int | None = None
    voc: float | None = None
    pm25: float | None = None
    volume_m3: float | None = None
    occupied: bool | None = None
    heating_active: bool | None = None
    fan_available: bool = False
    priority: int = 5
    confidence: float = 1.0
    estimation_method: EstimationMethod | None = "measured"
    tau_hours: float | None = None
    heating_rate_k_per_h: float | None = None
    is_basement: bool = False
    is_moisture_source: bool = False
    is_unheated: bool = False
    is_top_floor: bool = False
    is_bedroom: bool = False
    connected_rooms: tuple[str, ...] = ()
    target_min: float | None = None
    target_max: float | None = None
    laundry_drying: bool = False
    internal_load_w: float | None = None

    @classmethod
    def create(
        cls,
        id: str,
        name: str,
        temperature: float | None = None,
        humidity: float | None = None,
        **kwargs: Any,
    ) -> RoomState:
        """Build a :class:`RoomState` and derive the psychrometric values."""
        derived: dict[str, Any] = {}
        if temperature is not None and humidity is not None:
            derived["absolute_humidity"] = psy.absolute_humidity(temperature, humidity)
            derived["dew_point"] = psy.dew_point(temperature, humidity)
        derived.update(kwargs)
        return cls(id=id, name=name, temperature=temperature, humidity=humidity, **derived)

    @property
    def has_climate_data(self) -> bool:
        return self.temperature is not None

    @property
    def is_estimated(self) -> bool:
        return self.estimation_method not in (None, "measured")

    def enthalpy(self, pressure: float = 1013.25) -> float | None:
        if self.temperature is None or self.humidity is None:
            return None
        return psy.enthalpy(self.temperature, self.humidity, pressure)


@dataclass(frozen=True)
class WindowState:
    """Everything we know about one window (and its cover)."""

    id: str
    name: str
    room_id: str
    is_open: bool = False
    is_tilted: bool = False
    #: Whether a contact sensor actually answers the question. Without one
    #: ``is_open`` is a guess ("closed"), and treating that guess as knowledge
    #: silently swallowed every "close it" push and made the contradiction
    #: detection read an unanswerable open recommendation as the user saying no.
    contact_known: bool = False
    open_since: datetime | None = None
    #: When the contact sensor last changed, open or closed. ``open_since``
    #: only survives while the window is open, but telling "the user shut this
    #: on purpose" apart from "it has been shut all along" needs the closing
    #: edge too.
    contact_changed: datetime | None = None
    azimuth: float = 180.0
    area_m2: float | None = None
    tilt_capable: bool = True
    is_ground_floor: bool = False
    rain_safe: bool = False
    ok_when_away: bool = False
    g_value: float = 0.6
    cover_entity: str | None = None
    cover_position: int | None = None
    cover_external: bool = True
    cover_auto_allowed: bool = False
    #: A blind that exists but that Home Assistant cannot see or move. It still
    #: shapes every shading recommendation - it just gets operated by hand.
    manual_cover: bool = False
    horizon_profile: tuple[float, ...] | None = None
    solar_load_w: float | None = None

    @property
    def may_be_open(self) -> bool:
        """Open, or open for all we know.

        The gate for "should we bother telling the user to close this". With a
        contact sensor it is the sensor; without one the user is the sensor and
        has to be asked.
        """
        return self.is_open or not self.contact_known

    @property
    def has_cover(self) -> bool:
        """Whether anything can shade this window, motorised or not."""
        return self.cover_entity is not None or self.manual_cover

    @property
    def cover_is_manual(self) -> bool:
        """A blind we can only ever advise about, never move."""
        return self.cover_entity is None and self.manual_cover

    @property
    def cover_shading_efficiency(self) -> float:
        """External blinds stop ~80 % of the gain, internal ones only ~30 %."""
        return 0.8 if self.cover_external else 0.3

    def open_minutes(self, now: datetime) -> float | None:
        if not self.is_open or self.open_since is None:
            return None
        return max(0.0, (now - self.open_since).total_seconds() / 60.0)


@dataclass(frozen=True)
class WorldState:
    """Immutable snapshot handed to the engine."""

    now: datetime
    outdoor: OutdoorState
    rooms: tuple[RoomState, ...] = ()
    windows: tuple[WindowState, ...] = ()
    forecast: tuple[ForecastHour, ...] = ()
    sun: SunState = field(default_factory=lambda: SunState(azimuth=180.0, elevation=0.0))
    mode: Mode = Mode.AUTO
    presence: bool = True
    weather_alerts: tuple[WeatherAlert, ...] = ()
    building: BuildingProfile = field(default_factory=BuildingProfile)
    preferences: Preferences = field(default_factory=Preferences)
    learned: LearnedParameters = field(default_factory=LearnedParameters)
    purge_active: dict[str, datetime] = field(default_factory=dict)

    # -- lookups -----------------------------------------------------------
    def room(self, room_id: str) -> RoomState | None:
        for room in self.rooms:
            if room.id == room_id:
                return room
        return None

    def window(self, window_id: str) -> WindowState | None:
        for window in self.windows:
            if window.id == window_id:
                return window
        return None

    def windows_of(self, room_id: str) -> list[WindowState]:
        return [w for w in self.windows if w.room_id == room_id]

    @cached_property
    def open_windows(self) -> tuple[WindowState, ...]:
        return tuple(w for w in self.windows if w.is_open)

    # -- derived context ---------------------------------------------------
    @cached_property
    def season(self) -> Season:
        """Resolve ``Mode.AUTO`` into a season from the forecast, not the calendar."""
        if self.mode is Mode.SUMMER:
            return Season.SUMMER
        if self.mode is Mode.WINTER:
            return Season.WINTER
        day_max = self.forecast_max(24)
        day_min = self.forecast_min(24)
        reference_max = day_max if day_max is not None else self.outdoor.temperature
        reference_min = day_min if day_min is not None else self.outdoor.temperature
        heating = any(r.heating_active for r in self.rooms if r.heating_active is not None)
        if reference_max >= 24.0 and not heating:
            return Season.SUMMER
        if reference_max <= 15.0 or reference_min <= 5.0 or heating:
            return Season.WINTER
        return Season.TRANSITIONAL

    @cached_property
    def is_night(self) -> bool:
        """Night = sun below the horizon (with a small civil-twilight margin)."""
        return self.sun.elevation < -3.0

    @cached_property
    def is_dark(self) -> bool:
        if self.outdoor.illuminance is not None:
            return self.outdoor.illuminance < 50.0
        return self.sun.elevation < -6.0

    @cached_property
    def mean_indoor_temperature(self) -> float | None:
        values = [r.temperature for r in self.rooms if r.temperature is not None]
        if not values:
            return None
        return sum(values) / len(values)

    @cached_property
    def mean_indoor_absolute_humidity(self) -> float | None:
        values = [r.absolute_humidity for r in self.rooms if r.absolute_humidity is not None]
        if not values:
            return None
        return sum(values) / len(values)

    @cached_property
    def active_alerts(self) -> tuple[WeatherAlert, ...]:
        return tuple(a for a in self.weather_alerts if a.is_active(self.now))

    @cached_property
    def is_away(self) -> bool:
        return self.mode is Mode.AWAY or not self.presence

    def forecast_slice(self, hours: float) -> list[ForecastHour]:
        """Forecast entries within the next ``hours`` hours."""
        horizon = self.now + timedelta(hours=hours)
        return [f for f in self.forecast if self.now <= f.time <= horizon]

    def forecast_max(self, hours: float) -> float | None:
        entries = self.forecast_slice(hours)
        return max((f.temperature for f in entries), default=None)

    def forecast_min(self, hours: float) -> float | None:
        entries = self.forecast_slice(hours)
        return min((f.temperature for f in entries), default=None)

    def forecast_at(self, moment: datetime) -> ForecastHour | None:
        """Closest forecast entry to ``moment`` (within 90 minutes)."""
        best: ForecastHour | None = None
        best_delta = timedelta(minutes=90)
        for entry in self.forecast:
            delta = abs(entry.time - moment)
            if delta <= best_delta:
                best, best_delta = entry, delta
        return best

    def delta_t(self, room: RoomState) -> float | None:
        """Indoor minus outdoor temperature (positive = warmer inside)."""
        if room.temperature is None:
            return None
        return room.temperature - self.outdoor.temperature

    def delta_ah(self, room: RoomState) -> float | None:
        """Indoor minus outdoor absolute humidity (positive = drier outside)."""
        if room.absolute_humidity is None or self.outdoor.absolute_humidity is None:
            return None
        return room.absolute_humidity - self.outdoor.absolute_humidity

    def with_overrides(self, **kwargs: Any) -> WorldState:
        return replace(self, **kwargs)


# --------------------------------------------------------------------------
# Engine output
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Recommendation:
    """A single, explainable piece of advice."""

    id: str
    target: str
    action: Action
    priority: Priority
    urgency: int = 50
    reason_key: str = "generic"
    reason_data: dict[str, Any] = field(default_factory=dict)
    expected_benefit: str | None = None
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    duration_minutes: int | None = None
    confidence: float = 1.0
    notify: bool = True
    rule_id: str = ""
    room_id: str | None = None
    veto: bool = False
    blocks: frozenset[Action] = frozenset()

    @property
    def sort_key(self) -> tuple[int, int, float]:
        return (int(self.priority), self.urgency, self.confidence)

    def is_valid(self, now: datetime) -> bool:
        if self.valid_from is not None and now < self.valid_from:
            return False
        return not (self.valid_until is not None and now > self.valid_until)


@dataclass(frozen=True)
class ScheduleSlot:
    """One slot of the 24 h ventilation plan (also feeds the panel timeline)."""

    start: datetime
    end: datetime
    quality: SlotQuality
    reason_key: str
    outdoor_temperature: float
    predicted_indoor_temperature: float | None = None
    delta_k: float = 0.0
    blocked_by: str | None = None

    @property
    def duration_hours(self) -> float:
        return (self.end - self.start).total_seconds() / 3600.0


@dataclass(frozen=True)
class VentilationSchedule:
    """The 24 h plan plus the single best ventilation window."""

    slots: tuple[ScheduleSlot, ...] = ()
    best_start: datetime | None = None
    best_end: datetime | None = None
    best_delta_k: float = 0.0
    #: What ``best_delta_k`` and ``ScheduleSlot.delta_k`` actually measure.
    #: In summer it is Kelvin of cooling, in winter grams of water per m³ that
    #: a purge would remove - labelling both as Kelvin produced nonsense like
    #: "best window 16:00-06:00, 29 K".
    metric: Literal["kelvin", "grams"] = "kelvin"
    summary_key: str = "no_plan"
    summary_data: dict[str, Any] = field(default_factory=dict)

    def slot_at(self, moment: datetime) -> ScheduleSlot | None:
        for slot in self.slots:
            if slot.start <= moment < slot.end:
                return slot
        return None


@dataclass(frozen=True)
class TippingPoints:
    """Times at which indoor and outdoor temperature cross."""

    morning: datetime | None = None
    evening: datetime | None = None
    morning_confidence: float = 0.0
    evening_confidence: float = 0.0
    cooler_outside: bool = False
    minutes_to_next: float | None = None


@dataclass(frozen=True)
class CoolingBudget:
    """Kelvin gained versus lost — the honesty metric during a heatwave."""

    gained_k: float = 0.0
    lost_k: float = 0.0
    achievable_tonight_k: float = 0.0
    required_tonight_k: float = 0.0
    storage_k: float = 0.0
    balance_3d: float = 0.0
    verdict_key: str = "unknown"
    verdict_data: dict[str, Any] = field(default_factory=dict)

    @property
    def net_k(self) -> float:
        return self.gained_k - self.lost_k

    @property
    def is_sufficient(self) -> bool:
        return self.achievable_tonight_k >= self.required_tonight_k - 0.2


@dataclass(frozen=True)
class RoomAssessment:
    """Per-room derived values that the HA entities publish directly."""

    room_id: str
    absolute_humidity: float | None = None
    dew_point: float | None = None
    enthalpy: float | None = None
    wall_surface_temperature: float | None = None
    surface_humidity: float | None = None
    mold_risk: MoldRisk = MoldRisk.NONE
    heating_rate_k_per_h: float | None = None
    projected_temperature: float | None = None
    projected_time: datetime | None = None
    air_quality_score: int = 100
    score_breakdown: dict[str, int] = field(default_factory=dict)
    ventilation_effect_k_per_h: float | None = None
    confidence: float = 1.0


@dataclass(frozen=True)
class EvaluationResult:
    """Everything one engine run produces."""

    now: datetime
    recommendations: tuple[Recommendation, ...] = ()
    suppressed: tuple[Recommendation, ...] = ()
    global_state: GlobalState = GlobalState.IDLE
    schedule: VentilationSchedule = field(default_factory=VentilationSchedule)
    tipping_points: TippingPoints = field(default_factory=TippingPoints)
    cooling_budget: CoolingBudget = field(default_factory=CoolingBudget)
    rooms: dict[str, RoomAssessment] = field(default_factory=dict)
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def for_window(self, window_id: str) -> Recommendation | None:
        for rec in self.recommendations:
            if rec.target == window_id:
                return rec
        return None

    def for_room(self, room_id: str) -> list[Recommendation]:
        return [r for r in self.recommendations if room_id in (r.room_id, r.target)]

    @property
    def primary(self) -> Recommendation | None:
        actionable = [r for r in self.recommendations if r.action is not Action.NO_ACTION]
        if not actionable:
            return None
        return max(actionable, key=lambda r: r.sort_key)

    @property
    def action_required(self) -> bool:
        return any(
            r.action not in (Action.NO_ACTION, Action.KEEP_CLOSED, Action.KEEP_OPEN)
            for r in self.recommendations
        )


# --------------------------------------------------------------------------
# Engine memory (mutable, persisted between runs)
# --------------------------------------------------------------------------


@dataclass
class TargetMemory:
    """Per-target history needed for hysteresis and minimum dwell time."""

    action: Action = Action.NO_ACTION
    since: datetime | None = None
    last_notified: datetime | None = None
    contradictions: int = 0
    contradiction_day: str | None = None


@dataclass
class EngineMemory:
    """Mutable state carried between evaluations.

    Kept deliberately small and JSON-serialisable so the coordinator can
    persist it through :class:`homeassistant.helpers.storage.Store`.
    """

    targets: dict[str, TargetMemory] = field(default_factory=dict)
    cooldowns: dict[str, datetime] = field(default_factory=dict)
    snoozed_until: dict[str, datetime] = field(default_factory=dict)
    ignored_today: dict[str, str] = field(default_factory=dict)
    manual_hold: dict[str, str] = field(default_factory=dict)
    last_purge: dict[str, datetime] = field(default_factory=dict)
    pushes_today: int = 0
    push_day: str | None = None
    budget_history: list[tuple[str, float]] = field(default_factory=list)
    #: Household timezone, set by the coordinator on every evaluation and
    #: deliberately not persisted. "Today" has to mean the user's today: every
    #: timestamp here is UTC, so a plain ``now.date()`` rolled the push budget
    #: and "ignore today" over at 02:00 local in central European summer.
    timezone: str | None = None

    def target(self, target_id: str) -> TargetMemory:
        return self.targets.setdefault(target_id, TargetMemory())

    def day_of(self, moment: datetime) -> str:
        """The calendar day ``moment`` falls on, on the household's clock."""
        return local_day(moment, self.timezone)

    def is_snoozed(self, rec_id: str, now: datetime) -> bool:
        until = self.snoozed_until.get(rec_id)
        if until is not None and now < until:
            return True
        if until is not None:
            del self.snoozed_until[rec_id]
        return False

    def is_ignored_today(self, rec_id: str, now: datetime) -> bool:
        return self.ignored_today.get(rec_id) == self.day_of(now)

    def is_held(self, target_id: str, now: datetime) -> bool:
        return self.manual_hold.get(target_id) == self.day_of(now)

    def snooze(self, rec_id: str, until: datetime) -> None:
        self.snoozed_until[rec_id] = until

    def ignore_today(self, rec_id: str, now: datetime) -> None:
        self.ignored_today[rec_id] = self.day_of(now)

    def hold(self, target_id: str, now: datetime) -> None:
        self.manual_hold[target_id] = self.day_of(now)

    def note_push(self, now: datetime) -> None:
        day = self.day_of(now)
        if self.push_day != day:
            self.push_day, self.pushes_today = day, 0
        self.pushes_today += 1

    def pushes_left(self, now: datetime, limit: int) -> int:
        if self.push_day != self.day_of(now):
            return limit
        return max(0, limit - self.pushes_today)

    def may_push(self, priority: Priority, now: datetime, limit: int) -> bool:
        """Whether the daily notification budget still covers this priority.

        Not first-come-first-served: without a reserve, a shading tip at 09:00
        spends the whole day's budget and the CO2 warning at 23:00 never
        arrives. Comfort and optimisation therefore cannot touch the last
        third; health can spend everything; safety ignores the budget.
        """
        if priority is Priority.SAFETY:
            return True
        left = self.pushes_left(now, limit)
        if priority is Priority.HEALTH:
            return left > 0
        return left > max(1, limit // 3)

    def register_push(
        self, recommendation_id: str, target: str, now: datetime, cooldown_minutes: int
    ) -> None:
        """Record that a recommendation actually went out.

        Everything that decides "may this be pushed again" reads from here, so
        it has to be one method: the daily counter, the per-recommendation
        cooldown the arbiter checks, and the timestamp the contradiction
        detection uses. Keeping it in the persisted memory means a restart does
        not reset the cooldown and re-notify everything.
        """
        self.note_push(now)
        self.cooldowns[recommendation_id] = now + timedelta(minutes=cooldown_minutes)
        self.target(target).last_notified = now

    def hours_since_purge(self, room_id: str, now: datetime) -> float | None:
        """Hours since the last completed purge, ``None`` if never seen."""
        last = self.last_purge.get(room_id)
        if last is None:
            return None
        return (now - last).total_seconds() / 3600.0

    def note_purge(self, room_id: str, now: datetime) -> None:
        self.last_purge[room_id] = now

    def record_budget(self, now: datetime, net_k: float) -> None:
        day = self.day_of(now)
        self.budget_history = [(d, v) for d, v in self.budget_history if d != day][-6:]
        self.budget_history.append((day, net_k))

    def balance(self, days: int = 3) -> float:
        return sum(v for _, v in self.budget_history[-days:])

    def prune(self, now: datetime) -> None:
        """Drop expired bookkeeping so the persisted blob stays small.

        Every dict is snapshotted with ``list()`` first. The engine runs in an
        executor thread while the event loop may be handling a snooze or an
        acknowledgement on the very same memory, and rebuilding a dict straight
        from its own ``.items()`` raises "dictionary changed size during
        iteration" if that happens to land in between. Pruning against a
        snapshot can at worst miss one entry until the next run.
        """
        today = self.day_of(now)
        self.cooldowns = {k: v for k, v in list(self.cooldowns.items()) if v > now}
        self.snoozed_until = {k: v for k, v in list(self.snoozed_until.items()) if v > now}
        self.ignored_today = {k: v for k, v in list(self.ignored_today.items()) if v == today}
        self.manual_hold = {k: v for k, v in list(self.manual_hold.items()) if v == today}
        cutoff = now - timedelta(days=2)
        self.targets = {
            k: v for k, v in list(self.targets.items()) if v.since is None or v.since > cutoff
        }


def _parse_clock(raw: str) -> time:
    """Parse ``HH:MM`` or ``HH:MM:SS`` into a :class:`~datetime.time`."""
    parts = raw.strip().split(":")
    hour = int(parts[0])
    minute = int(parts[1]) if len(parts) > 1 else 0
    return time(hour % 24, minute % 60)


def bearing_difference(a: float, b: float) -> float:
    """Smallest absolute angle between two compass bearings in degrees."""
    return abs((a - b + 180.0) % 360.0 - 180.0)


def compass_point(azimuth: float) -> str:
    """Translate an azimuth into one of the eight compass points."""
    points = ("n", "ne", "e", "se", "s", "sw", "w", "nw")
    return points[int((azimuth % 360.0) / 45.0 + 0.5) % 8]


def clamp(value: float, low: float, high: float) -> float:
    """Constrain a value to a range."""
    return max(low, min(high, value))


def sigmoid(x: float) -> float:
    """Numerically safe logistic function, used to shape urgency values."""
    if x < -30.0:
        return 0.0
    if x > 30.0:
        return 1.0
    return 1.0 / (1.0 + math.exp(-x))
