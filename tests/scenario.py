"""Declarative scenario loader.

A scenario is a YAML file describing one snapshot of the world plus the
expectations the engine has to satisfy. Keeping them declarative means tuning
thresholds is a diff in a YAML file rather than a rewrite of a test.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

from adaptive_ventilation.engine import (
    BuildingProfile,
    EngineMemory,
    ForecastHour,
    Mode,
    OutdoorState,
    Preferences,
    RoomState,
    SunState,
    WeatherAlert,
    WindowState,
    WorldState,
)
from adaptive_ventilation.engine.solar import sun_position
from adaptive_ventilation.engine.state import BuildingType, LearnedParameters, LearnedRoom

SCENARIO_DIR = Path(__file__).parent / "fixtures" / "scenarios"


@dataclass
class Expectation:
    """What a scenario asserts about the engine output."""

    global_state: str | None = None
    global_state_in: list[str] = field(default_factory=list)
    actions: dict[str, list[str]] = field(default_factory=dict)
    rules_present: list[str] = field(default_factory=list)
    rules_absent: list[str] = field(default_factory=list)
    notify_rules: list[str] = field(default_factory=list)
    silent_rules: list[str] = field(default_factory=list)
    silent_targets: list[str] = field(default_factory=list)
    no_notifications: bool = False
    max_urgency: dict[str, int] = field(default_factory=dict)
    min_urgency: dict[str, int] = field(default_factory=dict)
    min_duration: dict[str, int] = field(default_factory=dict)
    max_duration: dict[str, int] = field(default_factory=dict)
    max_confidence: dict[str, float] = field(default_factory=dict)
    mold_risk: dict[str, str] = field(default_factory=dict)
    cooling_verdict: str | None = None


@dataclass
class Scenario:
    """One loaded scenario file."""

    name: str
    description: str
    state: WorldState
    memory: EngineMemory
    expect: Expectation
    path: Path


def load_all(directory: Path | None = None) -> list[Scenario]:
    """Load every ``*.yaml`` scenario in ``directory``."""
    target = directory or SCENARIO_DIR
    return [load(path) for path in sorted(target.glob("*.yaml"))]


def load(path: Path) -> Scenario:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return Scenario(
        name=raw.get("name", path.stem),
        description=raw.get("description", ""),
        state=build_state(raw),
        memory=build_memory(raw),
        expect=build_expectation(raw.get("expect", {})),
        path=path,
    )


# --------------------------------------------------------------------------
# World state construction
# --------------------------------------------------------------------------


def build_state(raw: dict[str, Any]) -> WorldState:
    now = _dt(raw["now"])
    building = _building(raw.get("building", {}))
    outdoor = _outdoor(raw.get("outdoor", {}))
    forecast = _forecast(raw.get("forecast", {}), now, outdoor)
    rooms = tuple(_room(entry) for entry in raw.get("rooms", []))
    windows = tuple(_window(entry) for entry in raw.get("windows", []))
    return WorldState(
        now=now,
        outdoor=outdoor,
        rooms=rooms,
        windows=windows,
        forecast=forecast,
        sun=_sun(raw.get("sun"), now, building),
        mode=Mode(raw.get("mode", "auto")),
        presence=raw.get("presence", True),
        weather_alerts=tuple(_alert(entry) for entry in raw.get("alerts", [])),
        building=building,
        preferences=_preferences(raw.get("preferences", {})),
        learned=_learned(raw.get("learned", {})),
        purge_active={k: _dt(v) for k, v in (raw.get("purge_active") or {}).items()},
    )


def build_memory(raw: dict[str, Any]) -> EngineMemory:
    memory = EngineMemory()
    section = raw.get("memory") or {}
    for room_id, moment in (section.get("last_purge") or {}).items():
        memory.last_purge[room_id] = _dt(moment)
    for rec_id, moment in (section.get("cooldowns") or {}).items():
        memory.cooldowns[rec_id] = _dt(moment)
    for target, action in (section.get("actions") or {}).items():
        from adaptive_ventilation.engine.state import Action

        tracked = memory.target(target)
        tracked.action = Action(action)
        tracked.since = _dt(section.get("since", raw["now"]))
    for day_value in section.get("budget_history", []):
        memory.budget_history.append((day_value["day"], float(day_value["net_k"])))
    return memory


def _dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


def _building(raw: dict[str, Any]) -> BuildingProfile:
    return BuildingProfile(
        building_type=BuildingType(raw.get("type", "old_renovated")),
        construction_year=raw.get("year"),
        floor=raw.get("floor", 1),
        top_floor=raw.get("top_floor", False),
        f_rt_override=raw.get("f_rt"),
        latitude=raw.get("latitude", 50.11),
        longitude=raw.get("longitude", 8.68),
    )


def _outdoor(raw: dict[str, Any]) -> OutdoorState:
    return OutdoorState.create(
        temperature=float(raw.get("temperature", 15.0)),
        humidity=_opt_float(raw.get("humidity")),
        pm25=_opt_float(raw.get("pm25")),
        pm10=_opt_float(raw.get("pm10")),
        wind_speed=_opt_float(raw.get("wind_speed")),
        wind_bearing=_opt_float(raw.get("wind_bearing")),
        precipitation=_opt_float(raw.get("precipitation")),
        precipitation_probability=_opt_float(raw.get("precipitation_probability")),
        cloud_coverage=_opt_float(raw.get("cloud_coverage")),
        illuminance=_opt_float(raw.get("illuminance")),
        pressure=_opt_float(raw.get("pressure")),
        source=raw.get("source", "sensor"),
        is_stale=raw.get("stale", False),
    )


def _forecast(
    raw: dict[str, Any], now: datetime, outdoor: OutdoorState
) -> tuple[ForecastHour, ...]:
    if not raw:
        return ()
    start = _dt(raw["start"]) if "start" in raw else now
    if "hours" in raw:
        return tuple(
            ForecastHour(
                time=_dt(entry["time"]) if "time" in entry else start + timedelta(hours=i),
                temperature=float(entry["temperature"]),
                humidity=_opt_float(entry.get("humidity", raw.get("humidity"))),
                precipitation=_opt_float(entry.get("precipitation")),
                precipitation_probability=_opt_float(entry.get("precipitation_probability")),
                cloud_coverage=_opt_float(entry.get("cloud_coverage", raw.get("cloud_coverage"))),
                wind_speed=_opt_float(entry.get("wind_speed")),
                wind_bearing=_opt_float(entry.get("wind_bearing")),
                pressure=_opt_float(entry.get("pressure")),
                condition=entry.get("condition"),
            )
            for i, entry in enumerate(raw["hours"])
        )

    temperatures = raw.get("temperatures")
    if temperatures is None:
        temperatures = _sine_day(
            raw.get("min", outdoor.temperature - 5.0),
            raw.get("max", outdoor.temperature + 5.0),
            start,
            raw.get("count", 36),
        )
    humidity = raw.get("humidity", outdoor.humidity)
    return tuple(
        ForecastHour(
            time=start + timedelta(hours=i),
            temperature=float(temperature),
            humidity=_opt_float(humidity),
            cloud_coverage=_opt_float(raw.get("cloud_coverage")),
            precipitation=_opt_float(raw.get("precipitation")),
            precipitation_probability=_opt_float(raw.get("precipitation_probability")),
            wind_speed=_opt_float(raw.get("wind_speed")),
            pressure=_opt_float(raw.get("pressure")),
        )
        for i, temperature in enumerate(temperatures)
    )


def _sine_day(low: float, high: float, start: datetime, count: int) -> list[float]:
    """Synthesise a plausible daily curve: minimum at 05:00, maximum at 16:00."""
    mean = (low + high) / 2.0
    amplitude = (high - low) / 2.0
    values = []
    for i in range(count):
        hour = (start + timedelta(hours=i)).hour
        phase = (hour - 5.0) / 24.0 * 2.0 * math.pi
        values.append(round(mean - amplitude * math.cos(phase), 2))
    return values


def _sun(raw: dict[str, Any] | None, now: datetime, building: BuildingProfile) -> SunState:
    if raw and "elevation" in raw:
        return SunState(
            azimuth=float(raw.get("azimuth", 180.0)),
            elevation=float(raw["elevation"]),
            latitude=building.latitude,
            longitude=building.longitude,
        )
    elevation, azimuth = sun_position(now, building.latitude, building.longitude)
    return SunState(
        azimuth=azimuth,
        elevation=elevation,
        latitude=building.latitude,
        longitude=building.longitude,
    )


def _room(raw: dict[str, Any]) -> RoomState:
    return RoomState.create(
        id=raw["id"],
        name=raw.get("name", raw["id"].replace("_", " ").title()),
        temperature=_opt_float(raw.get("temperature")),
        humidity=_opt_float(raw.get("humidity")),
        co2=_opt_int(raw.get("co2")),
        voc=_opt_float(raw.get("voc")),
        pm25=_opt_float(raw.get("pm25")),
        volume_m3=_opt_float(raw.get("volume", 40.0)),
        occupied=raw.get("occupied"),
        heating_active=raw.get("heating"),
        fan_available=raw.get("fan", False),
        priority=int(raw.get("priority", 5)),
        confidence=float(raw.get("confidence", 1.0)),
        estimation_method=raw.get("estimation_method", "measured"),
        tau_hours=_opt_float(raw.get("tau")),
        is_basement=raw.get("basement", False),
        is_moisture_source=raw.get("moisture_source", False),
        is_unheated=raw.get("unheated", False),
        is_top_floor=raw.get("top_floor", False),
        is_bedroom=raw.get("bedroom", False),
        connected_rooms=tuple(raw.get("connected", [])),
        target_min=_opt_float(raw.get("target_min")),
        target_max=_opt_float(raw.get("target_max")),
        laundry_drying=raw.get("laundry", False),
        internal_load_w=_opt_float(raw.get("internal_load")),
    )


def _window(raw: dict[str, Any]) -> WindowState:
    horizon = raw.get("horizon")
    return WindowState(
        id=raw["id"],
        name=raw.get("name", raw["id"].replace("_", " ").title()),
        room_id=raw["room"],
        is_open=raw.get("open", False),
        is_tilted=raw.get("tilted", False),
        open_since=_dt(raw["open_since"]) if raw.get("open_since") else None,
        azimuth=float(raw.get("azimuth", 180.0)),
        area_m2=_opt_float(raw.get("area", 1.5)),
        tilt_capable=raw.get("tilt_capable", True),
        is_ground_floor=raw.get("ground_floor", False),
        rain_safe=raw.get("rain_safe", False),
        ok_when_away=raw.get("ok_when_away", False),
        g_value=float(raw.get("g_value", 0.6)),
        cover_entity=raw.get("cover"),
        cover_position=_opt_int(raw.get("cover_position")),
        cover_external=raw.get("cover_external", True),
        cover_auto_allowed=raw.get("cover_auto", False),
        horizon_profile=tuple(float(v) for v in horizon) if horizon else None,
    )


def _alert(raw: dict[str, Any]) -> WeatherAlert:
    return WeatherAlert(
        event=raw.get("event", "storm"),
        severity=raw.get("severity", "severe"),
        kind=raw.get("kind", "storm"),
        start=_dt(raw["start"]) if raw.get("start") else None,
        end=_dt(raw["end"]) if raw.get("end") else None,
        headline=raw.get("headline"),
    )


def _preferences(raw: dict[str, Any]) -> Preferences:
    from datetime import time as _time

    kwargs: dict[str, Any] = {}
    for key, value in raw.items():
        if key in ("quiet_hours_start", "quiet_hours_end"):
            hour, minute = str(value).split(":")[:2]
            kwargs[key] = _time(int(hour), int(minute))
        else:
            kwargs[key] = value
    return Preferences(**kwargs)


def _learned(raw: dict[str, Any]) -> LearnedParameters:
    rooms = {
        room_id: LearnedRoom(
            tau_hours=_opt_float(values.get("tau")),
            night_cooling_k_per_h=_opt_float(values.get("night_cooling")),
            solar_gain_coefficient=_opt_float(values.get("solar_gain")),
            air_changes_per_hour=_opt_float(values.get("ach")),
            samples=int(values.get("samples", 0)),
            confidence=float(values.get("confidence", 0.0)),
        )
        for room_id, values in (raw.get("rooms") or {}).items()
    }
    return LearnedParameters(rooms=rooms)


def build_expectation(raw: dict[str, Any]) -> Expectation:
    return Expectation(
        global_state=raw.get("global_state"),
        global_state_in=list(raw.get("global_state_in", [])),
        actions={k: _as_list(v) for k, v in (raw.get("actions") or {}).items()},
        rules_present=list(raw.get("rules_present", [])),
        rules_absent=list(raw.get("rules_absent", [])),
        notify_rules=list(raw.get("notify_rules", [])),
        silent_rules=list(raw.get("silent_rules", [])),
        silent_targets=list(raw.get("silent_targets", [])),
        no_notifications=raw.get("no_notifications", False),
        max_urgency={k: int(v) for k, v in (raw.get("max_urgency") or {}).items()},
        min_urgency={k: int(v) for k, v in (raw.get("min_urgency") or {}).items()},
        min_duration={k: int(v) for k, v in (raw.get("min_duration") or {}).items()},
        max_duration={k: int(v) for k, v in (raw.get("max_duration") or {}).items()},
        max_confidence={k: float(v) for k, v in (raw.get("max_confidence") or {}).items()},
        mold_risk=dict(raw.get("mold_risk") or {}),
        cooling_verdict=raw.get("cooling_verdict"),
    )


def _as_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(v) for v in value]
    return [str(value)]


def _opt_float(value: Any) -> float | None:
    return None if value is None else float(value)


def _opt_int(value: Any) -> int | None:
    return None if value is None else int(value)
