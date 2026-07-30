"""Serialisation of the engine memory and the learned parameters.

Kept separate from the coordinator so the persisted format has one obvious
home, and so migrations stay readable when the schema changes.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from datetime import datetime
from typing import Any, Mapping

from homeassistant.util import dt as dt_util

from .engine.state import (
    Action,
    EngineMemory,
    LearnedParameters,
    LearnedRoom,
    TargetMemory,
)

_LOGGER = logging.getLogger(__name__)

MEMORY_SCHEMA_VERSION = 1
LEARNED_SCHEMA_VERSION = 1


# --------------------------------------------------------------------------
# Engine memory
# --------------------------------------------------------------------------


def serialize_memory(memory: EngineMemory) -> dict[str, Any]:
    return {
        "schema": MEMORY_SCHEMA_VERSION,
        "targets": {
            key: {
                "action": value.action.value,
                "since": _iso(value.since),
                "last_notified": _iso(value.last_notified),
                "contradictions": value.contradictions,
                "contradiction_day": value.contradiction_day,
            }
            for key, value in memory.targets.items()
        },
        "cooldowns": {k: _iso(v) for k, v in memory.cooldowns.items()},
        "snoozed_until": {k: _iso(v) for k, v in memory.snoozed_until.items()},
        "ignored_today": dict(memory.ignored_today),
        "manual_hold": dict(memory.manual_hold),
        "last_purge": {k: _iso(v) for k, v in memory.last_purge.items()},
        "pushes_today": memory.pushes_today,
        "push_day": memory.push_day,
        "budget_history": [list(item) for item in memory.budget_history],
    }


def deserialize_memory(raw: Mapping[str, Any]) -> EngineMemory:
    memory = EngineMemory()
    version = raw.get("schema", 1)
    if version > MEMORY_SCHEMA_VERSION:
        _LOGGER.warning(
            "Stored memory is newer (v%s) than this version understands - starting fresh",
            version,
        )
        return memory

    for key, value in (raw.get("targets") or {}).items():
        try:
            action = Action(value.get("action", Action.NO_ACTION.value))
        except ValueError:
            action = Action.NO_ACTION
        memory.targets[key] = TargetMemory(
            action=action,
            since=_dt(value.get("since")),
            last_notified=_dt(value.get("last_notified")),
            contradictions=int(value.get("contradictions", 0)),
            contradiction_day=value.get("contradiction_day"),
        )

    memory.cooldowns = _dt_map(raw.get("cooldowns"))
    memory.snoozed_until = _dt_map(raw.get("snoozed_until"))
    memory.ignored_today = dict(raw.get("ignored_today") or {})
    memory.manual_hold = dict(raw.get("manual_hold") or {})
    memory.last_purge = _dt_map(raw.get("last_purge"))
    memory.pushes_today = int(raw.get("pushes_today", 0))
    memory.push_day = raw.get("push_day")
    memory.budget_history = [
        (str(day), float(value)) for day, value in (raw.get("budget_history") or [])
    ]
    return memory


# --------------------------------------------------------------------------
# Learned parameters
# --------------------------------------------------------------------------


def serialize_learned(learned: LearnedParameters) -> dict[str, Any]:
    return {
        "schema": LEARNED_SCHEMA_VERSION,
        "version": learned.version,
        "rooms": {
            room_id: {
                "tau_hours": room.tau_hours,
                "night_cooling_k_per_h": room.night_cooling_k_per_h,
                "solar_gain_coefficient": room.solar_gain_coefficient,
                "internal_gain_w": room.internal_gain_w,
                "air_changes_per_hour": room.air_changes_per_hour,
                "co2_decay_per_hour": room.co2_decay_per_hour,
                "samples": room.samples,
                "confidence": room.confidence,
                "updated": _iso(room.updated),
                "overridden": sorted(room.overridden),
            }
            for room_id, room in learned.rooms.items()
        },
        "window_azimuth": dict(learned.window_azimuth),
    }


def deserialize_learned(raw: Mapping[str, Any]) -> LearnedParameters:
    version = raw.get("schema", 1)
    if version > LEARNED_SCHEMA_VERSION:
        _LOGGER.warning("Stored calibration is newer than this version - ignoring it")
        return LearnedParameters()

    rooms = {
        room_id: LearnedRoom(
            tau_hours=_float(values.get("tau_hours")),
            night_cooling_k_per_h=_float(values.get("night_cooling_k_per_h")),
            solar_gain_coefficient=_float(values.get("solar_gain_coefficient")),
            internal_gain_w=_float(values.get("internal_gain_w")),
            air_changes_per_hour=_float(values.get("air_changes_per_hour")),
            co2_decay_per_hour=_float(values.get("co2_decay_per_hour")),
            samples=int(values.get("samples", 0)),
            confidence=float(values.get("confidence", 0.0)),
            updated=_dt(values.get("updated")),
            overridden=frozenset(values.get("overridden") or ()),
        )
        for room_id, values in (raw.get("rooms") or {}).items()
    }
    return LearnedParameters(
        version=int(raw.get("version", 1)),
        rooms=rooms,
        window_azimuth={
            k: float(v) for k, v in (raw.get("window_azimuth") or {}).items()
        },
    )


def merge_learned_room(
    existing: LearnedRoom, updates: Mapping[str, Any], *, samples: int
) -> LearnedRoom:
    """Apply new calibration results, never touching manual overrides."""
    payload = {
        key: value
        for key, value in updates.items()
        if value is not None and key not in existing.overridden
    }
    if not payload:
        return existing
    return replace(
        existing,
        **payload,
        samples=existing.samples + samples,
        confidence=min(1.0, 0.3 + 0.05 * (existing.samples + samples)),
        updated=dt_util.utcnow(),
    )


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _iso(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def _dt(value: Any) -> datetime | None:
    if not value:
        return None
    parsed = dt_util.parse_datetime(str(value))
    return None if parsed is None else dt_util.as_utc(parsed)


def _dt_map(raw: Any) -> dict[str, datetime]:
    result: dict[str, datetime] = {}
    for key, value in (raw or {}).items():
        parsed = _dt(value)
        if parsed is not None:
            result[key] = parsed
    return result


def _float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
