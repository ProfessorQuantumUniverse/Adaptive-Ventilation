"""Diagnostics with redaction.

Worth its weight in gold for bug reports: the complete world state, the engine
output and every intermediate value, with entity ids and coordinates removed.
"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import datetime, time
from enum import Enum
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .calibration import summarise as summarise_learned
from .const import (
    CONF_ILLUMINANCE,
    CONF_NOTIFY_TARGETS,
    CONF_OUTDOOR_HUMIDITY,
    CONF_OUTDOOR_PM10,
    CONF_OUTDOOR_PM25,
    CONF_OUTDOOR_TEMPERATURE,
    CONF_PRESENCE_ENTITY,
    CONF_WEATHER_ALERT_ENTITY,
    CONF_WEATHER_ENTITY,
)
from .coordinator import AdaptiveVentilationConfigEntry, AdaptiveVentilationCoordinator
from .engine import ENGINE_VERSION
from .engine.rules import REGISTRY
from .presentation import schedule_payload

TO_REDACT = {
    "latitude",
    "longitude",
    "elevation_m",
    CONF_NOTIFY_TARGETS,
    CONF_WEATHER_ENTITY,
    CONF_WEATHER_ALERT_ENTITY,
    CONF_OUTDOOR_TEMPERATURE,
    CONF_OUTDOOR_HUMIDITY,
    CONF_OUTDOOR_PM25,
    CONF_OUTDOOR_PM10,
    CONF_ILLUMINANCE,
    CONF_PRESENCE_ENTITY,
    "temperature_sensor",
    "humidity_sensor",
    "co2_sensor",
    "voc_sensor",
    "pm25_sensor",
    "occupancy_sensor",
    "climate_entity",
    "fan_entity",
    "power_sensor",
    "contact_sensor",
    "tilt_sensor",
    "cover_entity",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: AdaptiveVentilationConfigEntry
) -> dict[str, Any]:
    """Diagnostics download for one config entry."""
    return build_diagnostics(entry.runtime_data)


def build_diagnostics(coordinator: AdaptiveVentilationCoordinator) -> dict[str, Any]:
    """The same payload the ``export_diagnostics`` service returns."""
    result = coordinator.data
    world = coordinator.world

    payload: dict[str, Any] = {
        "engine_version": ENGINE_VERSION,
        "rules": sorted(REGISTRY),
        "options": async_redact_data(dict(coordinator.config_entry.options), TO_REDACT),
        "rooms": [async_redact_data(_plain(room), TO_REDACT) for room in coordinator.config.rooms],
        "windows": [
            async_redact_data(_plain(window), TO_REDACT) for window in coordinator.config.windows
        ],
        "mode": coordinator.mode.value,
        "purge_active": {k: v.isoformat() for k, v in coordinator.purge_active.items()},
        "learned": summarise_learned(coordinator.learned),
        "memory": {
            "targets": {
                key: {
                    "action": value.action.value,
                    "since": _plain(value.since),
                    "contradictions": value.contradictions,
                }
                for key, value in coordinator.memory.targets.items()
            },
            "cooldowns": {k: _plain(v) for k, v in coordinator.memory.cooldowns.items()},
            "snoozed": {k: _plain(v) for k, v in coordinator.memory.snoozed_until.items()},
            "manual_hold": dict(coordinator.memory.manual_hold),
            "pushes_today": coordinator.memory.pushes_today,
            "budget_history": [list(item) for item in coordinator.memory.budget_history],
        },
    }

    if world is not None:
        payload["world"] = async_redact_data(
            {
                "now": _plain(world.now),
                "season": world.season.value,
                "is_night": world.is_night,
                "presence": world.presence,
                "outdoor": _plain(world.outdoor),
                "sun": _plain(world.sun),
                "building": _plain(world.building),
                "preferences": _plain(world.preferences),
                "rooms": [_plain(room) for room in world.rooms],
                "windows": [_plain(window) for window in world.windows],
                "alerts": [_plain(alert) for alert in world.weather_alerts],
                "forecast": [_plain(hour) for hour in world.forecast[:48]],
            },
            TO_REDACT,
        )

    if result is not None:
        payload["result"] = {
            "global_state": result.global_state.value,
            "recommendations": [_plain(rec) for rec in result.recommendations],
            "suppressed": [
                {"rule": rec.rule_id, "target": rec.target, "action": rec.action.value}
                for rec in result.suppressed
            ],
            "tipping_points": _plain(result.tipping_points),
            "cooling_budget": _plain(result.cooling_budget),
            "schedule": schedule_payload(result),
            "rooms": {key: _plain(value) for key, value in result.rooms.items()},
            "diagnostics": result.diagnostics,
        }

    return payload


def _plain(value: Any) -> Any:
    """Recursively convert dataclasses, enums and datetimes into JSON."""
    if is_dataclass(value) and not isinstance(value, type):
        return {key: _plain(item) for key, item in asdict(value).items()}
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (datetime, time)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_plain(item) for item in value]
    return value
