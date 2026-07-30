"""Turning engine output into things a screen can show.

Two hard constraints from the specification apply here:

1. Home Assistant truncates state strings at 255 characters, so the *state* is
   always an enum or a short text and everything long lives in attributes.
2. External displays (ESPHome, e-paper) want three short lines and a compact
   JSON blob, not a dashboard.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Mapping

from . import messages
from .calibration import summarise as summarise_learned
from .engine.state import (
    Action,
    EvaluationResult,
    Recommendation,
    WorldState,
)

MAX_STATE_LENGTH = 255
#: Keep some headroom - a display line longer than this is unreadable anyway.
MAX_LINE_LENGTH = 64


def recommendation_attributes(
    rec: Recommendation | None, language: str = "en"
) -> dict[str, Any]:
    """Attribute payload for a recommendation entity."""
    if rec is None:
        return {"reason": None, "recommendation_id": None}
    return {
        "recommendation_id": rec.id,
        "rule": rec.rule_id or None,
        "priority": rec.priority.name,
        "urgency": rec.urgency,
        "confidence": round(rec.confidence, 2),
        "reason": messages.render(rec.reason_key, rec.reason_data, language),
        "reason_key": rec.reason_key,
        "expected_benefit": rec.expected_benefit,
        "duration_minutes": rec.duration_minutes,
        "valid_until": _iso(rec.valid_until),
        "notify": rec.notify,
        "numbers": {k: v for k, v in rec.reason_data.items() if _is_scalar(v)},
    }


def primary_text(result: EvaluationResult, language: str = "en") -> str:
    """One-line summary, guaranteed to fit into a state string."""
    rec = result.primary
    if rec is None:
        return messages.render("nothing_to_do", {}, language)
    text = messages.render(rec.reason_key, rec.reason_data, language)
    return _truncate(text, MAX_STATE_LENGTH)


def countdown_minutes(result: EvaluationResult) -> int | None:
    """Minutes until the next thing the user has to do."""
    tipping = result.tipping_points
    candidates: list[float] = []
    if tipping.minutes_to_next is not None and tipping.minutes_to_next >= 0:
        candidates.append(tipping.minutes_to_next)
    for rec in result.recommendations:
        if rec.valid_until is not None:
            minutes = (rec.valid_until - result.now).total_seconds() / 60.0
            if 0 <= minutes < 24 * 60:
                candidates.append(minutes)
    return int(min(candidates)) if candidates else None


def display_lines(
    result: EvaluationResult, world: WorldState | None, language: str = "en"
) -> tuple[str, str, str]:
    """Three short lines for an OLED or e-paper display."""
    rec = result.primary
    state_text = result.global_state.value.replace("_", " ")

    line1 = _truncate(state_text.title(), MAX_LINE_LENGTH)

    if rec is None:
        line2 = messages.render("nothing_to_do", {}, language)
    else:
        line2 = messages.render(rec.reason_key, rec.reason_data, language)
    line2 = _truncate(line2, MAX_LINE_LENGTH)

    parts: list[str] = []
    if world is not None:
        indoor = world.mean_indoor_temperature
        if indoor is not None:
            parts.append(f"{indoor:.1f}/{world.outdoor.temperature:.1f} C")
        indoor_ah = world.mean_indoor_absolute_humidity
        outdoor_ah = world.outdoor.absolute_humidity
        if indoor_ah is not None and outdoor_ah is not None:
            parts.append(f"{indoor_ah:.1f}/{outdoor_ah:.1f} g/m3")
    minutes = countdown_minutes(result)
    if minutes is not None:
        parts.append(f"{minutes} min")
    line3 = _truncate(" | ".join(parts) or "-", MAX_LINE_LENGTH)

    return line1, line2, line3


def compact_payload(result: EvaluationResult, world: WorldState | None) -> str:
    """Small JSON blob for displays that cannot use the ESPHome native API."""
    rec = result.primary
    payload: dict[str, Any] = {
        "s": result.global_state.value,
        "a": rec.action.value if rec else "no_action",
        "u": rec.urgency if rec else 0,
        "c": countdown_minutes(result),
        "tp": _iso(result.tipping_points.morning or result.tipping_points.evening),
        "b": result.cooling_budget.achievable_tonight_k,
    }
    if world is not None:
        payload["ti"] = _round(world.mean_indoor_temperature)
        payload["to"] = round(world.outdoor.temperature, 1)
        payload["ai"] = _round(world.mean_indoor_absolute_humidity)
        payload["ao"] = _round(world.outdoor.absolute_humidity)
        payload["ow"] = len(world.open_windows)
    return json.dumps(payload, separators=(",", ":"))


def status_attributes(
    result: EvaluationResult, world: WorldState | None, language: str = "en"
) -> dict[str, Any]:
    """Rich attributes for ``sensor.adaptive_ventilation_status``."""
    rec = result.primary
    line1, line2, line3 = display_lines(result, world, language)
    return {
        "headline": line2,
        "line1": line1,
        "line2": line2,
        "line3": line3,
        "countdown": countdown_minutes(result),
        "severity": rec.priority.name if rec else "NONE",
        "action": rec.action.value if rec else Action.NO_ACTION.value,
        "recommendation_id": rec.id if rec else None,
        "open_windows": len(world.open_windows) if world else 0,
        "season": result.diagnostics.get("season"),
        "mode": world.mode.value if world else None,
        "compact": compact_payload(result, world),
        "recommendation_count": sum(
            1 for r in result.recommendations if r.action is not Action.NO_ACTION
        ),
    }


def schedule_payload(result: EvaluationResult) -> list[dict[str, Any]]:
    """The 24 h timeline in the shape the panel draws."""
    return [
        {
            "start": _iso(slot.start),
            "end": _iso(slot.end),
            "quality": slot.quality.value,
            "reason": slot.reason_key,
            "outdoor": slot.outdoor_temperature,
            "indoor": slot.predicted_indoor_temperature,
            "delta_k": slot.delta_k,
            "blocked_by": slot.blocked_by,
        }
        for slot in result.schedule.slots
    ]


def panel_payload(coordinator: Any) -> dict[str, Any]:
    """Everything the sidebar panel needs, in one JSON document."""
    result: EvaluationResult | None = coordinator.data
    world: WorldState | None = coordinator.world
    language = messages.resolve_language(coordinator.hass.config.language)
    if result is None:
        return {"ready": False}

    rooms = []
    for config in coordinator.config.rooms:
        assessment = result.rooms.get(config.id)
        room = world.room(config.id) if world else None
        rooms.append(
            {
                "id": config.id,
                "name": config.name,
                "temperature": _round(room.temperature) if room else None,
                "humidity": _round(room.humidity) if room else None,
                "absolute_humidity": _round(assessment.absolute_humidity)
                if assessment
                else None,
                "dew_point": _round(assessment.dew_point) if assessment else None,
                "enthalpy": _round(assessment.enthalpy) if assessment else None,
                "co2": room.co2 if room else None,
                "pm25": _round(room.pm25) if room else None,
                "heating_rate": assessment.heating_rate_k_per_h if assessment else None,
                "projected": assessment.projected_temperature if assessment else None,
                "mold_risk": assessment.mold_risk.value if assessment else None,
                "surface_temperature": assessment.wall_surface_temperature
                if assessment
                else None,
                "air_quality_score": assessment.air_quality_score if assessment else None,
                "score_breakdown": assessment.score_breakdown if assessment else {},
                "confidence": round(room.confidence, 2) if room else None,
                "estimation_method": room.estimation_method if room else None,
                "priority": config.priority,
                "night_potential_k": result.diagnostics.get("night_potential_k", {}).get(
                    config.id
                ),
            }
        )

    windows = []
    for config in coordinator.config.windows:
        rec = result.for_window(config.id)
        window = world.window(config.id) if world else None
        cover_rec = next(
            (r for r in result.recommendations if r.target == f"{config.id}:cover"), None
        )
        windows.append(
            {
                "id": config.id,
                "name": config.name,
                "room_id": config.room_id,
                "is_open": window.is_open if window else False,
                "is_tilted": window.is_tilted if window else False,
                "azimuth": config.azimuth,
                "area": config.area_m2,
                "solar_load": result.diagnostics.get("solar_loads", {}).get(config.id),
                "cover_saving": result.diagnostics.get("cover_savings", {}).get(config.id),
                "has_cover": bool(config.cover_entity),
                "cover_position": window.cover_position if window else None,
                "action": rec.action.value if rec else "no_action",
                "recommendation": recommendation_attributes(rec, language),
                "cover_action": cover_rec.action.value if cover_rec else None,
                "cover_recommendation": recommendation_attributes(cover_rec, language)
                if cover_rec
                else None,
            }
        )

    return {
        "ready": True,
        "now": _iso(result.now),
        "language": language,
        "mode": world.mode.value if world else "auto",
        "status": result.global_state.value,
        "headline": primary_text(result, language),
        "countdown": countdown_minutes(result),
        "outdoor": {
            "temperature": round(world.outdoor.temperature, 1) if world else None,
            "humidity": _round(world.outdoor.humidity) if world else None,
            "absolute_humidity": _round(world.outdoor.absolute_humidity) if world else None,
            "dew_point": _round(world.outdoor.dew_point) if world else None,
            "pm25": _round(world.outdoor.pm25) if world else None,
            "wind_speed": _round(world.outdoor.wind_speed) if world else None,
            "source": world.outdoor.source if world else None,
            "stale": world.outdoor.is_stale if world else True,
        },
        "tipping_points": {
            "morning": _iso(result.tipping_points.morning),
            "evening": _iso(result.tipping_points.evening),
            "confidence": round(result.tipping_points.morning_confidence, 2),
            "cooler_outside": result.tipping_points.cooler_outside,
            "minutes_to_next": result.tipping_points.minutes_to_next,
        },
        "schedule": {
            "slots": schedule_payload(result),
            "best_start": _iso(result.schedule.best_start),
            "best_end": _iso(result.schedule.best_end),
            "best_delta_k": result.schedule.best_delta_k,
            "summary_key": result.schedule.summary_key,
            "summary": result.schedule.summary_data,
        },
        "cooling_budget": {
            "gained_k": result.cooling_budget.gained_k,
            "lost_k": result.cooling_budget.lost_k,
            "net_k": round(result.cooling_budget.net_k, 2),
            "achievable_tonight_k": result.cooling_budget.achievable_tonight_k,
            "required_tonight_k": result.cooling_budget.required_tonight_k,
            "storage_k": result.cooling_budget.storage_k,
            "balance_3d": result.cooling_budget.balance_3d,
            "verdict": result.cooling_budget.verdict_key,
            "verdict_data": result.cooling_budget.verdict_data,
            "history": [
                {"day": day, "net_k": value}
                for day, value in coordinator.memory.budget_history
            ],
        },
        "rooms": rooms,
        "windows": windows,
        "recommendations": [
            {
                "id": rec.id,
                "target": rec.target,
                "room_id": rec.room_id,
                "action": rec.action.value,
                "priority": rec.priority.name,
                "urgency": rec.urgency,
                "notify": rec.notify,
                **recommendation_attributes(rec, language),
            }
            for rec in result.recommendations
            if rec.action is not Action.NO_ACTION
        ],
        "weak_spots": weak_spots(coordinator, result),
        "sensor_suggestions": sensor_suggestions(coordinator, result),
        "learned": summarise_learned(coordinator.learned),
        "preferences": _preferences_payload(coordinator),
        "diagnostics": {
            "season": result.diagnostics.get("season"),
            "triggered_rules": result.diagnostics.get("triggered_rules", []),
            "cross_pairs": result.diagnostics.get("cross_pairs", []),
            "rule_errors": result.diagnostics.get("rule_errors", {}),
        },
    }


def weak_spots(coordinator: Any, result: EvaluationResult) -> list[dict[str, Any]]:
    """Which window costs the most Kelvin per day - the balance tab report."""
    loads: Mapping[str, float] = result.diagnostics.get("solar_loads", {})
    spots: list[dict[str, Any]] = []
    for config in coordinator.config.windows:
        room = coordinator.config.room(config.room_id)
        if room is None:
            continue
        # Six hours of peak load spread over the room's heat capacity.
        daily_k = loads.get(config.id, 0.0) * 6.0 / max(room.volume_m3 * 18.0, 1.0)
        spots.append(
            {
                "window_id": config.id,
                "window": config.name,
                "room": room.name,
                "load_w": round(loads.get(config.id, 0.0)),
                "daily_k": round(daily_k, 2),
                "has_cover": bool(config.cover_entity),
                "cover_external": config.cover_external,
            }
        )
    spots.sort(key=lambda item: item["daily_k"], reverse=True)
    return spots[:6]


def sensor_suggestions(coordinator: Any, result: EvaluationResult) -> list[dict[str, Any]]:
    """Where an extra sensor would improve the recommendations the most."""
    suggestions: list[dict[str, Any]] = []
    loads: Mapping[str, float] = result.diagnostics.get("solar_loads", {})
    for config in coordinator.config.rooms:
        missing = [
            kind
            for kind, entity in (
                ("temperature", config.temperature_sensor),
                ("humidity", config.humidity_sensor),
                ("co2", config.co2_sensor),
            )
            if not entity
        ]
        if not missing:
            continue
        window_load = sum(
            loads.get(w.id, 0.0)
            for w in coordinator.config.windows
            if w.room_id == config.id
        )
        window_area = sum(
            w.area_m2 for w in coordinator.config.windows if w.room_id == config.id
        )
        # Big glazed rooms with a high priority benefit most.
        impact = window_area * 10.0 + window_load / 50.0 + (7 - config.priority) * 5.0
        if "temperature" in missing:
            impact *= 2.0
        suggestions.append(
            {
                "room_id": config.id,
                "room": config.name,
                "missing": missing,
                "impact": round(impact, 1),
            }
        )
    suggestions.sort(key=lambda item: item["impact"], reverse=True)
    return suggestions[:5]


def _preferences_payload(coordinator: Any) -> dict[str, Any]:
    world: WorldState | None = coordinator.world
    if world is None:
        return {}
    prefs = world.preferences
    return {
        "weight_temperature": prefs.weight_temperature,
        "weight_humidity": prefs.weight_humidity,
        "weight_co2": prefs.weight_co2,
        "weight_particulate": prefs.weight_particulate,
        "notification_restraint": prefs.notification_restraint,
        "summer_target_min": prefs.summer_target_min,
        "summer_target_max": prefs.summer_target_max,
        "winter_target_min": prefs.winter_target_min,
        "winter_target_max": prefs.winter_target_max,
        "co2_threshold": prefs.co2_threshold,
        "pm25_indoor_threshold": prefs.pm25_indoor_threshold,
        "min_state_duration_minutes": prefs.min_state_duration_minutes,
        "cooldown_minutes": prefs.cooldown_minutes,
        "quiet_hours_start": prefs.quiet_hours_start.strftime("%H:%M"),
        "quiet_hours_end": prefs.quiet_hours_end.strftime("%H:%M"),
        "max_pushes_per_day": prefs.max_pushes_per_day,
    }


def _iso(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def _round(value: float | None, digits: int = 1) -> float | None:
    return None if value is None else round(value, digits)


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _is_scalar(value: Any) -> bool:
    return isinstance(value, (int, float, str, bool)) or value is None
