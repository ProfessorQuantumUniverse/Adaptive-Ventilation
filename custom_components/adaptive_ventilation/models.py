"""Mapping between Home Assistant configuration/state and the engine dataclasses.

This is the only place that knows about both worlds. Everything below
``engine/`` stays Home Assistant free, everything above here never touches a
raw state object.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from typing import Any, Iterable, Mapping

from homeassistant.const import STATE_ON, STATE_OPEN, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant, State
from homeassistant.util import dt as dt_util

from .const import (
    CONF_AZIMUTH,
    CONF_BUILDING_TYPE,
    CONF_CEILING_HEIGHT,
    CONF_CLIMATE_ENTITY,
    CONF_CO2_SENSOR,
    CONF_CONNECTED_ROOMS,
    CONF_CONSTRUCTION_YEAR,
    CONF_CONTACT_SENSOR,
    CONF_COVER_AUTO,
    CONF_COVER_ENTITY,
    CONF_COVER_EXTERNAL,
    CONF_ESTIMATION,
    CONF_F_RT,
    CONF_FAN_ENTITY,
    CONF_FLOOR,
    CONF_G_VALUE,
    CONF_GROUND_FLOOR,
    CONF_HORIZON,
    CONF_HUMIDITY_SENSOR,
    CONF_ILLUMINANCE,
    CONF_IS_BASEMENT,
    CONF_IS_BEDROOM,
    CONF_IS_MOISTURE_SOURCE,
    CONF_IS_TOP_FLOOR,
    CONF_IS_UNHEATED,
    CONF_OCCUPANCY_SENSOR,
    CONF_OK_WHEN_AWAY,
    CONF_OUTDOOR_HUMIDITY,
    CONF_OUTDOOR_PM10,
    CONF_OUTDOOR_PM25,
    CONF_OUTDOOR_TEMPERATURE,
    CONF_PM25_SENSOR,
    CONF_POWER_SENSOR,
    CONF_PRIORITY,
    CONF_QUIET_END,
    CONF_QUIET_START,
    CONF_RAIN_SAFE,
    CONF_REFERENCE_OFFSET,
    CONF_REFERENCE_ROOM,
    CONF_ROOM,
    CONF_ROOM_AREA,
    CONF_ROOM_NAME,
    CONF_TARGET_MAX,
    CONF_TARGET_MIN,
    CONF_TEMPERATURE_SENSOR,
    CONF_TILT_CAPABLE,
    CONF_TILT_SENSOR,
    CONF_TOP_FLOOR,
    CONF_VOC_SENSOR,
    CONF_VOLUME,
    CONF_WINDOW_AREA,
    CONF_WINDOW_NAME,
    DEFAULT_CEILING_HEIGHT,
    DEFAULT_G_VALUE,
    DEFAULT_PRIORITY,
    DEFAULT_VOLUME,
    DEFAULT_WINDOW_AREA,
    ESTIMATION_CONFIDENCE,
    ESTIMATION_NONE,
    ESTIMATION_REFERENCE,
    STALE_AFTER,
)
from .engine.state import (
    BuildingProfile,
    BuildingType,
    ForecastHour,
    OutdoorState,
    Preferences,
    RoomState,
    SunState,
    WeatherAlert,
    WindowState,
)

_LOGGER = logging.getLogger(__name__)

INVALID_STATES = {STATE_UNAVAILABLE, STATE_UNKNOWN, None, ""}

#: Keyword -> alert kind, matched case insensitively against the event text.
#: Ordered most specific first: "hail storm" has to come out as hail, because
#: hail is what makes the difference between "close the window" and "also get
#: the shutter down".
ALERT_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("hagel", "hail"),
    ("hail", "hail"),
    ("gewitter", "thunderstorm"),
    ("thunder", "thunderstorm"),
    ("orkan", "storm"),
    ("sturm", "storm"),
    ("storm", "storm"),
    ("böe", "storm"),
    ("boe", "storm"),
    ("gust", "storm"),
    ("starkregen", "rain"),
    ("regen", "rain"),
    ("rain", "rain"),
    ("hitze", "heat"),
    ("heat", "heat"),
    ("glätte", "frost"),
    ("frost", "frost"),
    ("schnee", "snow"),
    ("snow", "snow"),
    ("wind", "storm"),
)

#: DWD warning level -> severity.
DWD_LEVELS = {0: "minor", 1: "minor", 2: "moderate", 3: "severe", 4: "extreme"}


# --------------------------------------------------------------------------
# Parsed configuration
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class RoomConfig:
    """One ``room`` subentry, already normalised."""

    id: str
    name: str
    temperature_sensor: str | None = None
    humidity_sensor: str | None = None
    co2_sensor: str | None = None
    voc_sensor: str | None = None
    pm25_sensor: str | None = None
    occupancy_sensor: str | None = None
    climate_entity: str | None = None
    fan_entity: str | None = None
    power_sensor: str | None = None
    volume_m3: float = DEFAULT_VOLUME
    priority: int = DEFAULT_PRIORITY
    target_min: float | None = None
    target_max: float | None = None
    is_basement: bool = False
    is_moisture_source: bool = False
    is_unheated: bool = False
    is_top_floor: bool = False
    is_bedroom: bool = False
    connected_rooms: tuple[str, ...] = ()
    estimation: str = ESTIMATION_NONE
    reference_room: str | None = None
    reference_offset: float = 0.0

    @classmethod
    def from_subentry(cls, subentry_id: str, data: Mapping[str, Any]) -> RoomConfig:
        volume = data.get(CONF_VOLUME)
        if not volume:
            area = data.get(CONF_ROOM_AREA)
            height = data.get(CONF_CEILING_HEIGHT, DEFAULT_CEILING_HEIGHT)
            volume = float(area) * float(height) if area else DEFAULT_VOLUME
        return cls(
            id=subentry_id,
            name=data.get(CONF_ROOM_NAME, "Room"),
            temperature_sensor=data.get(CONF_TEMPERATURE_SENSOR),
            humidity_sensor=data.get(CONF_HUMIDITY_SENSOR),
            co2_sensor=data.get(CONF_CO2_SENSOR),
            voc_sensor=data.get(CONF_VOC_SENSOR),
            pm25_sensor=data.get(CONF_PM25_SENSOR),
            occupancy_sensor=data.get(CONF_OCCUPANCY_SENSOR),
            climate_entity=data.get(CONF_CLIMATE_ENTITY),
            fan_entity=data.get(CONF_FAN_ENTITY),
            power_sensor=data.get(CONF_POWER_SENSOR),
            volume_m3=float(volume),
            priority=int(data.get(CONF_PRIORITY, DEFAULT_PRIORITY)),
            target_min=_optional_float(data.get(CONF_TARGET_MIN)),
            target_max=_optional_float(data.get(CONF_TARGET_MAX)),
            is_basement=bool(data.get(CONF_IS_BASEMENT, False)),
            is_moisture_source=bool(data.get(CONF_IS_MOISTURE_SOURCE, False)),
            is_unheated=bool(data.get(CONF_IS_UNHEATED, False)),
            is_top_floor=bool(data.get(CONF_IS_TOP_FLOOR, False)),
            is_bedroom=bool(data.get(CONF_IS_BEDROOM, False)),
            connected_rooms=tuple(data.get(CONF_CONNECTED_ROOMS, []) or []),
            estimation=data.get(CONF_ESTIMATION, ESTIMATION_NONE),
            reference_room=data.get(CONF_REFERENCE_ROOM),
            reference_offset=float(data.get(CONF_REFERENCE_OFFSET, 0.0) or 0.0),
        )

    @property
    def entity_ids(self) -> list[str]:
        """Every entity this room listens to."""
        return [
            e
            for e in (
                self.temperature_sensor,
                self.humidity_sensor,
                self.co2_sensor,
                self.voc_sensor,
                self.pm25_sensor,
                self.occupancy_sensor,
                self.climate_entity,
                self.fan_entity,
                self.power_sensor,
            )
            if e
        ]


@dataclass(frozen=True)
class WindowConfig:
    """One ``window`` subentry, already normalised."""

    id: str
    name: str
    room_id: str
    contact_sensor: str | None = None
    tilt_sensor: str | None = None
    azimuth: float = 180.0
    area_m2: float = DEFAULT_WINDOW_AREA
    tilt_capable: bool = True
    g_value: float = DEFAULT_G_VALUE
    cover_entity: str | None = None
    cover_external: bool = True
    cover_auto_allowed: bool = False
    is_ground_floor: bool = False
    ok_when_away: bool = False
    rain_safe: bool = False
    horizon_profile: tuple[float, ...] | None = None

    @classmethod
    def from_subentry(cls, subentry_id: str, data: Mapping[str, Any]) -> WindowConfig:
        horizon = data.get(CONF_HORIZON)
        return cls(
            id=subentry_id,
            name=data.get(CONF_WINDOW_NAME, "Window"),
            room_id=data.get(CONF_ROOM, ""),
            contact_sensor=data.get(CONF_CONTACT_SENSOR),
            tilt_sensor=data.get(CONF_TILT_SENSOR),
            azimuth=float(data.get(CONF_AZIMUTH, 180.0)),
            area_m2=float(data.get(CONF_WINDOW_AREA, DEFAULT_WINDOW_AREA)),
            tilt_capable=bool(data.get(CONF_TILT_CAPABLE, True)),
            g_value=float(data.get(CONF_G_VALUE, DEFAULT_G_VALUE)),
            cover_entity=data.get(CONF_COVER_ENTITY),
            cover_external=bool(data.get(CONF_COVER_EXTERNAL, True)),
            cover_auto_allowed=bool(data.get(CONF_COVER_AUTO, False)),
            is_ground_floor=bool(data.get(CONF_GROUND_FLOOR, False)),
            ok_when_away=bool(data.get(CONF_OK_WHEN_AWAY, False)),
            rain_safe=bool(data.get(CONF_RAIN_SAFE, False)),
            horizon_profile=tuple(float(v) for v in horizon) if horizon else None,
        )

    @property
    def entity_ids(self) -> list[str]:
        return [e for e in (self.contact_sensor, self.tilt_sensor, self.cover_entity) if e]


@dataclass
class ParsedConfig:
    """Everything the coordinator needs from the config entry."""

    rooms: list[RoomConfig] = field(default_factory=list)
    windows: list[WindowConfig] = field(default_factory=list)
    options: dict[str, Any] = field(default_factory=dict)

    def room(self, room_id: str) -> RoomConfig | None:
        return next((r for r in self.rooms if r.id == room_id), None)

    def window(self, window_id: str) -> WindowConfig | None:
        return next((w for w in self.windows if w.id == window_id), None)

    @property
    def tracked_entities(self) -> list[str]:
        entities: list[str] = []
        for room in self.rooms:
            entities.extend(room.entity_ids)
        for window in self.windows:
            entities.extend(window.entity_ids)
        for key in (
            CONF_OUTDOOR_TEMPERATURE,
            CONF_OUTDOOR_HUMIDITY,
            CONF_OUTDOOR_PM25,
            CONF_OUTDOOR_PM10,
            CONF_ILLUMINANCE,
        ):
            value = self.options.get(key)
            if value:
                entities.append(value)
        return sorted(set(entities))


# --------------------------------------------------------------------------
# Options -> engine configuration
# --------------------------------------------------------------------------


def build_preferences(options: Mapping[str, Any]) -> Preferences:
    """Turn the options flow result into engine :class:`Preferences`."""
    kwargs: dict[str, Any] = {}
    for name in Preferences.__dataclass_fields__:
        if name in (CONF_QUIET_START, CONF_QUIET_END):
            continue
        if name in options and options[name] is not None:
            kwargs[name] = options[name]

    for key in (CONF_QUIET_START, CONF_QUIET_END):
        raw = options.get(key)
        parsed = _parse_time(raw)
        if parsed is not None:
            kwargs[key] = parsed

    try:
        return Preferences(**kwargs)
    except TypeError:  # pragma: no cover - defensive against stale options
        _LOGGER.warning("Ignoring unknown preference keys in %s", sorted(kwargs))
        valid = {k: v for k, v in kwargs.items() if k in Preferences.__dataclass_fields__}
        return Preferences(**valid)


def build_building(options: Mapping[str, Any], latitude: float, longitude: float) -> BuildingProfile:
    raw_type = options.get(CONF_BUILDING_TYPE, BuildingType.UNKNOWN.value)
    try:
        building_type = BuildingType(raw_type)
    except ValueError:
        building_type = BuildingType.UNKNOWN
    return BuildingProfile(
        building_type=building_type,
        construction_year=_optional_int(options.get(CONF_CONSTRUCTION_YEAR)),
        floor=int(options.get(CONF_FLOOR, 0) or 0),
        top_floor=bool(options.get(CONF_TOP_FLOOR, False)),
        f_rt_override=_optional_float(options.get(CONF_F_RT)),
        latitude=latitude,
        longitude=longitude,
    )


# --------------------------------------------------------------------------
# State readers
# --------------------------------------------------------------------------


def read_float(hass: HomeAssistant, entity_id: str | None) -> float | None:
    """Numeric state of an entity, or ``None`` if it is not usable."""
    if not entity_id:
        return None
    state = hass.states.get(entity_id)
    if state is None or state.state in INVALID_STATES:
        return None
    try:
        return float(state.state)
    except (TypeError, ValueError):
        return None


def read_bool(hass: HomeAssistant, entity_id: str | None) -> bool | None:
    if not entity_id:
        return None
    state = hass.states.get(entity_id)
    if state is None or state.state in INVALID_STATES:
        return None
    return state.state in (STATE_ON, STATE_OPEN, "home", "heat", "heating")


def read_attribute(hass: HomeAssistant, entity_id: str | None, attribute: str) -> Any:
    if not entity_id:
        return None
    state = hass.states.get(entity_id)
    if state is None:
        return None
    return state.attributes.get(attribute)


def last_changed(hass: HomeAssistant, entity_id: str | None) -> datetime | None:
    if not entity_id:
        return None
    state = hass.states.get(entity_id)
    return None if state is None else state.last_changed


def build_outdoor(
    hass: HomeAssistant, options: Mapping[str, Any], weather: State | None
) -> OutdoorState:
    """Outdoor conditions: own sensors first, weather entity as the fallback."""
    now = dt_util.utcnow()

    temperature = read_float(hass, options.get(CONF_OUTDOOR_TEMPERATURE))
    humidity = read_float(hass, options.get(CONF_OUTDOOR_HUMIDITY))
    sensor_used = temperature is not None

    weather_attrs: Mapping[str, Any] = weather.attributes if weather else {}
    if temperature is None:
        temperature = _as_float(weather_attrs.get("temperature"))
    if humidity is None:
        humidity = _as_float(weather_attrs.get("humidity"))

    if temperature is None:
        # Nothing usable at all: report stale rather than invent a number.
        return OutdoorState(temperature=0.0, source="weather", is_stale=True)

    source = "mixed" if sensor_used and weather is not None else (
        "sensor" if sensor_used else "weather"
    )

    stale = False
    for entity_id in (options.get(CONF_OUTDOOR_TEMPERATURE), options.get(CONF_OUTDOOR_HUMIDITY)):
        changed = last_changed(hass, entity_id)
        if changed is not None and now - changed > STALE_AFTER:
            stale = True

    return OutdoorState.create(
        temperature=temperature,
        humidity=humidity,
        pm25=read_float(hass, options.get(CONF_OUTDOOR_PM25)),
        pm10=read_float(hass, options.get(CONF_OUTDOOR_PM10)),
        wind_speed=_wind_ms(weather_attrs),
        wind_bearing=_as_float(weather_attrs.get("wind_bearing")),
        precipitation=_as_float(weather_attrs.get("precipitation")),
        precipitation_probability=_as_float(weather_attrs.get("precipitation_probability")),
        cloud_coverage=_as_float(weather_attrs.get("cloud_coverage")),
        illuminance=read_float(hass, options.get(CONF_ILLUMINANCE)),
        pressure=_as_float(weather_attrs.get("pressure")),
        source=source,  # type: ignore[arg-type]
        is_stale=stale,
    )


def build_sun(hass: HomeAssistant, entity_id: str, building: BuildingProfile) -> SunState:
    state = hass.states.get(entity_id)
    if state is None:
        from .engine.solar import sun_position

        elevation, azimuth = sun_position(
            dt_util.utcnow(), building.latitude, building.longitude
        )
        return SunState(
            azimuth=azimuth,
            elevation=elevation,
            latitude=building.latitude,
            longitude=building.longitude,
        )
    return SunState(
        azimuth=_as_float(state.attributes.get("azimuth")) or 180.0,
        elevation=_as_float(state.attributes.get("elevation")) or 0.0,
        next_rising=_as_datetime(state.attributes.get("next_rising")),
        next_setting=_as_datetime(state.attributes.get("next_setting")),
        latitude=building.latitude,
        longitude=building.longitude,
    )


def build_rooms(
    hass: HomeAssistant, configs: Iterable[RoomConfig]
) -> tuple[RoomState, ...]:
    """Build every room, then fill the sensorless ones from the fallback chain."""
    configs = list(configs)
    rooms: dict[str, RoomState] = {}

    for config in configs:
        rooms[config.id] = _build_measured_room(hass, config)

    measured = [r for r in rooms.values() if r.temperature is not None]
    average = (
        sum(r.temperature or 0.0 for r in measured) / len(measured) if measured else None
    )
    average_humidity = _average(
        [r.humidity for r in measured if r.humidity is not None]
    )

    for config in configs:
        room = rooms[config.id]
        if room.temperature is not None:
            continue
        rooms[config.id] = _estimate_room(config, room, rooms, average, average_humidity)

    return tuple(rooms[config.id] for config in configs)


def _build_measured_room(hass: HomeAssistant, config: RoomConfig) -> RoomState:
    temperature = read_float(hass, config.temperature_sensor)
    humidity = read_float(hass, config.humidity_sensor)
    heating = None
    if config.climate_entity:
        state = hass.states.get(config.climate_entity)
        if state is not None:
            action = state.attributes.get("hvac_action")
            heating = (
                action in ("heating", "preheating")
                if action is not None
                else state.state in ("heat", "auto")
            )

    return RoomState.create(
        id=config.id,
        name=config.name,
        temperature=temperature,
        humidity=humidity,
        co2=_optional_int(read_float(hass, config.co2_sensor)),
        voc=read_float(hass, config.voc_sensor),
        pm25=read_float(hass, config.pm25_sensor),
        volume_m3=config.volume_m3,
        occupied=read_bool(hass, config.occupancy_sensor),
        heating_active=heating,
        fan_available=bool(config.fan_entity),
        priority=config.priority,
        confidence=1.0 if temperature is not None else 0.0,
        estimation_method="measured" if temperature is not None else None,
        is_basement=config.is_basement,
        is_moisture_source=config.is_moisture_source,
        is_unheated=config.is_unheated,
        is_top_floor=config.is_top_floor,
        is_bedroom=config.is_bedroom,
        connected_rooms=config.connected_rooms,
        target_min=config.target_min,
        target_max=config.target_max,
        internal_load_w=read_float(hass, config.power_sensor),
    )


def _estimate_room(
    config: RoomConfig,
    room: RoomState,
    rooms: Mapping[str, RoomState],
    average: float | None,
    average_humidity: float | None,
) -> RoomState:
    """Fallback chain: reference room + offset -> model -> flat average -> unknown.

    Never guesses silently: every step carries its own confidence, and the
    lowest levels are excluded from push notifications by the arbiter.
    """
    from dataclasses import replace

    from .engine import psychrometrics as psy

    reference = rooms.get(config.reference_room or "")
    if (
        config.estimation == ESTIMATION_REFERENCE
        and reference is not None
        and reference.temperature is not None
    ):
        temperature = reference.temperature + config.reference_offset
        humidity = reference.humidity
        if humidity is not None:
            # Keep the absolute humidity of the reference room, not its RH.
            absolute = psy.absolute_humidity(reference.temperature, humidity)
            humidity = psy.relative_humidity_from_absolute(temperature, absolute)
        return _with_estimate(room, temperature, humidity, "reference_offset")

    if average is not None:
        method = "model" if config.estimation != ESTIMATION_NONE else "average"
        offset = 0.0
        if config.is_top_floor:
            offset += 2.0
        if config.is_basement:
            offset -= 4.0
        if config.is_unheated:
            offset -= 2.0
        return _with_estimate(room, average + offset, average_humidity, method)

    return replace(room, confidence=0.0, estimation_method=None)


def _with_estimate(
    room: RoomState, temperature: float, humidity: float | None, method: str
) -> RoomState:
    from dataclasses import replace

    from .engine import psychrometrics as psy

    absolute = dew = None
    if humidity is not None:
        absolute = psy.absolute_humidity(temperature, humidity)
        dew = psy.dew_point(temperature, humidity)
    return replace(
        room,
        temperature=round(temperature, 2),
        humidity=None if humidity is None else round(humidity, 1),
        absolute_humidity=absolute,
        dew_point=dew,
        confidence=ESTIMATION_CONFIDENCE.get(method, 0.5),
        estimation_method=method,  # type: ignore[arg-type]
    )


def build_windows(
    hass: HomeAssistant, configs: Iterable[WindowConfig]
) -> tuple[WindowState, ...]:
    windows: list[WindowState] = []
    for config in configs:
        contact = hass.states.get(config.contact_sensor) if config.contact_sensor else None
        is_open = bool(contact and contact.state in (STATE_ON, STATE_OPEN))
        open_since = contact.last_changed if (contact and is_open) else None

        is_tilted = False
        if config.tilt_sensor:
            tilt = hass.states.get(config.tilt_sensor)
            is_tilted = bool(tilt and tilt.state in (STATE_ON, STATE_OPEN, "tilt", "tilted"))
        elif contact is not None and contact.state == "tilt":
            is_tilted = True

        cover_position: int | None = None
        if config.cover_entity:
            raw = read_attribute(hass, config.cover_entity, "current_position")
            if raw is None:
                cover_state = hass.states.get(config.cover_entity)
                if cover_state is not None and cover_state.state in ("open", "closed"):
                    raw = 100 if cover_state.state == "open" else 0
            cover_position = _optional_int(raw)

        windows.append(
            WindowState(
                id=config.id,
                name=config.name,
                room_id=config.room_id,
                is_open=is_open,
                is_tilted=is_tilted,
                open_since=open_since,
                azimuth=config.azimuth,
                area_m2=config.area_m2,
                tilt_capable=config.tilt_capable,
                is_ground_floor=config.is_ground_floor,
                rain_safe=config.rain_safe,
                ok_when_away=config.ok_when_away,
                g_value=config.g_value,
                cover_entity=config.cover_entity,
                cover_position=cover_position,
                cover_external=config.cover_external,
                cover_auto_allowed=config.cover_auto_allowed,
                horizon_profile=config.horizon_profile,
            )
        )
    return tuple(windows)


def build_forecast(raw: Iterable[Mapping[str, Any]]) -> tuple[ForecastHour, ...]:
    """Convert the ``weather.get_forecasts`` payload into engine forecast hours."""
    hours: list[ForecastHour] = []
    for entry in raw:
        moment = _as_datetime(entry.get("datetime"))
        temperature = _as_float(entry.get("temperature") or entry.get("native_temperature"))
        if moment is None or temperature is None:
            continue
        hours.append(
            ForecastHour(
                time=moment,
                temperature=temperature,
                humidity=_as_float(entry.get("humidity")),
                apparent_temperature=_as_float(entry.get("apparent_temperature")),
                precipitation=_as_float(entry.get("precipitation")),
                precipitation_probability=_as_float(entry.get("precipitation_probability")),
                cloud_coverage=_as_float(entry.get("cloud_coverage")),
                wind_speed=_wind_ms(entry),
                wind_bearing=_as_float(entry.get("wind_bearing")),
                pressure=_as_float(entry.get("pressure")),
                condition=entry.get("condition"),
            )
        )
    hours.sort(key=lambda h: h.time)
    return tuple(hours)


def build_alerts(hass: HomeAssistant, entity_id: str | None) -> tuple[WeatherAlert, ...]:
    """Parse DWD, Meteoalarm or a generic warning entity into alerts.

    Deliberately tolerant: a warning entity that we fail to parse must never
    take the integration down, it just means one fewer safety input.
    """
    if not entity_id:
        return ()
    state = hass.states.get(entity_id)
    if state is None or state.state in INVALID_STATES:
        return ()

    alerts: list[WeatherAlert] = []
    attributes = state.attributes

    # DWD Weather Warnings: warning_1_name / warning_1_level / ...
    count = _optional_int(attributes.get("warning_count")) or 0
    for index in range(1, count + 1):
        event = attributes.get(f"warning_{index}_name") or attributes.get(
            f"warning_{index}_headline"
        )
        if not event:
            continue
        level = _optional_int(attributes.get(f"warning_{index}_level")) or 1
        alerts.append(
            WeatherAlert(
                event=str(event),
                severity=DWD_LEVELS.get(level, "moderate"),  # type: ignore[arg-type]
                kind=_alert_kind(str(event)),
                start=_as_datetime(attributes.get(f"warning_{index}_start")),
                end=_as_datetime(attributes.get(f"warning_{index}_end")),
                headline=attributes.get(f"warning_{index}_description"),
            )
        )

    # Meteoalarm and generic list-shaped attributes.
    for entry in attributes.get("warnings", []) or attributes.get("attribution_list", []) or []:
        if not isinstance(entry, Mapping):
            continue
        event = entry.get("event") or entry.get("headline") or entry.get("name")
        if not event:
            continue
        alerts.append(
            WeatherAlert(
                event=str(event),
                severity=_severity(entry.get("severity") or entry.get("level")),
                kind=_alert_kind(str(event)),
                start=_as_datetime(entry.get("start") or entry.get("onset")),
                end=_as_datetime(entry.get("end") or entry.get("expires")),
                headline=entry.get("description") or entry.get("headline"),
            )
        )

    if not alerts and state.state == STATE_ON:
        event = attributes.get("event") or attributes.get("friendly_name") or "weather warning"
        alerts.append(
            WeatherAlert(
                event=str(event),
                severity=_severity(attributes.get("severity")),
                kind=_alert_kind(str(event)),
            )
        )
    return tuple(alerts)


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------


def _alert_kind(event: str) -> str:
    lowered = event.lower()
    for keyword, kind in ALERT_KEYWORDS:
        if keyword in lowered:
            return kind
    return "other"


def _severity(raw: Any) -> str:
    if isinstance(raw, (int, float)):
        return DWD_LEVELS.get(int(raw), "moderate")
    text = str(raw or "").lower()
    if text in ("minor", "moderate", "severe", "extreme"):
        return text
    return "moderate"


def _wind_ms(attributes: Mapping[str, Any]) -> float | None:
    """Wind speed in m/s. Home Assistant reports km/h by default."""
    value = _as_float(attributes.get("wind_speed"))
    if value is None:
        return None
    unit = attributes.get("wind_speed_unit", "km/h")
    if unit in ("km/h", "kph"):
        return value / 3.6
    if unit == "mph":
        return value * 0.44704
    if unit in ("kn", "kt"):
        return value * 0.514444
    return value


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_float(value: Any) -> float | None:
    return _as_float(value)


def _optional_int(value: Any) -> int | None:
    number = _as_float(value)
    return None if number is None else int(round(number))


def _as_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return dt_util.as_utc(value)
    if not value:
        return None
    parsed = dt_util.parse_datetime(str(value))
    return None if parsed is None else dt_util.as_utc(parsed)


def _parse_time(value: Any) -> time | None:
    if isinstance(value, time):
        return value
    if not value:
        return None
    parsed = dt_util.parse_time(str(value))
    return parsed


def _average(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def next_occurrence(reference: datetime, target: time) -> datetime:
    """Next wall-clock occurrence of ``target`` after ``reference``."""
    candidate = reference.replace(
        hour=target.hour, minute=target.minute, second=0, microsecond=0
    )
    if candidate <= reference:
        candidate += timedelta(days=1)
    return candidate
