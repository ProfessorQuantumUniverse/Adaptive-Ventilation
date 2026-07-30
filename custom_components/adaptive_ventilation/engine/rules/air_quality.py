"""HEALTH rules for CO₂, VOC and particulate matter."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import timedelta

from ..context import EvaluationContext
from ..state import Action, Priority, Recommendation, Season, clamp
from . import make, rule
from .helpers import purge_action, purge_windows, round_or_none


@rule("co2_high", Priority.HEALTH, description="CO2 above the comfort threshold")
def co2_high(ctx: EvaluationContext) -> Iterable[Recommendation]:
    """Purge with a duration derived from ΔT, not from a fixed number."""
    prefs = ctx.preferences
    for room in ctx.rooms:
        if room.co2 is None:
            continue
        threshold = prefs.co2_threshold
        # Hysteresis: once we have advised a purge, keep advising until the
        # level drops a good chunk below the threshold.
        memory = ctx.memory.target(room.id)
        if memory.action in (Action.PURGE, Action.CROSS_VENTILATE):
            threshold -= prefs.co2_hysteresis
        if room.co2 < threshold:
            continue

        windows, cross = purge_windows(ctx, room)
        if not windows:
            continue
        minutes = ctx.purge_minutes(room, cross=cross)
        urgent = room.co2 >= prefs.co2_urgent
        urgency = int(clamp(40 + (room.co2 - prefs.co2_threshold) / 12.0, 40, 95))
        data = {
            "room": room.name,
            "co2": room.co2,
            "threshold": prefs.co2_threshold,
            "minutes": minutes,
            "cross": cross,
            "partner": windows[1].name if cross and len(windows) > 1 else None,
        }
        for window in windows:
            yield make(
                ctx,
                "co2_high",
                window.id,
                purge_action(cross),
                Priority.HEALTH,
                "co2_high_urgent" if urgent else "co2_high",
                urgency=urgency,
                room_id=room.id,
                reason_data=dict(data, window=window.name),
                duration_minutes=minutes,
                confidence=room.confidence,
                expected_benefit=f"CO2 -> ~{max(450, int(room.co2 * 0.45))} ppm",
                valid_until=ctx.now + timedelta(hours=2),
                notify=urgent or not ctx.preferences.in_quiet_hours(ctx.now),
            )


@rule("voc_high", Priority.HEALTH, description="VOC index above the threshold")
def voc_high(ctx: EvaluationContext) -> Iterable[Recommendation]:
    """VOC is the early warning that CO₂ misses (solvents, new furniture, glue)."""
    for room in ctx.rooms:
        if room.voc is None or room.voc < ctx.preferences.voc_threshold:
            continue
        windows, cross = purge_windows(ctx, room)
        if not windows:
            continue
        minutes = ctx.purge_minutes(room, cross=cross)
        for window in windows:
            yield make(
                ctx,
                "voc_high",
                window.id,
                purge_action(cross),
                Priority.HEALTH,
                "voc_high",
                urgency=60,
                room_id=room.id,
                reason_data={
                    "room": room.name,
                    "voc": round(room.voc, 0),
                    "threshold": ctx.preferences.voc_threshold,
                    "minutes": minutes,
                },
                duration_minutes=minutes,
                confidence=room.confidence,
            )


@rule("indoor_pm_high", Priority.HEALTH, description="Indoor particulate matter high")
def indoor_pm_high(ctx: EvaluationContext) -> Iterable[Recommendation]:
    """Cooking, frying, candles, wood stove — purge even when it costs comfort."""
    outdoor = ctx.outdoor.pm25
    for room in ctx.rooms:
        if room.pm25 is None or room.pm25 <= ctx.preferences.pm25_indoor_threshold:
            continue
        if outdoor is not None and outdoor >= room.pm25:
            # Handled by pm_both_high / outdoor_pm_spike instead.
            continue
        windows, cross = purge_windows(ctx, room)
        if not windows:
            continue
        minutes = max(5, ctx.purge_minutes(room, cross=cross))
        for window in windows:
            yield make(
                ctx,
                "indoor_pm_high",
                window.id,
                purge_action(cross),
                Priority.HEALTH,
                "indoor_pm_high",
                urgency=int(clamp(55 + room.pm25, 55, 90)),
                room_id=room.id,
                reason_data={
                    "room": room.name,
                    "indoor_pm25": round(room.pm25, 1),
                    "outdoor_pm25": round_or_none(outdoor),
                    "minutes": minutes,
                },
                duration_minutes=minutes,
                confidence=room.confidence,
                valid_until=ctx.now + timedelta(hours=1),
            )


@rule("pm_both_high", Priority.HEALTH, description="Particulates high indoors and out")
def pm_both_high(ctx: EvaluationContext) -> Iterable[Recommendation]:
    """Trade-off: short and hard instead of long — or not at all."""
    outdoor = ctx.outdoor.pm25
    if outdoor is None or outdoor <= ctx.preferences.pm25_outdoor_threshold:
        return
    for room in ctx.rooms:
        if room.pm25 is None or room.pm25 <= ctx.preferences.pm25_indoor_threshold:
            continue
        if outdoor >= room.pm25 * 2.0:
            # Outside is clearly worse: outdoor_pm_spike already vetoes.
            continue
        windows, cross = purge_windows(ctx, room)
        if not windows:
            continue
        minutes = max(3, int(ctx.purge_minutes(room, cross=cross) * 0.5))
        for window in windows:
            yield make(
                ctx,
                "pm_both_high",
                window.id,
                purge_action(cross),
                Priority.HEALTH,
                "pm_both_high",
                urgency=50,
                room_id=room.id,
                reason_data={
                    "room": room.name,
                    "indoor_pm25": round(room.pm25, 1),
                    "outdoor_pm25": round(outdoor, 1),
                    "minutes": minutes,
                },
                duration_minutes=minutes,
                confidence=room.confidence * 0.9,
            )


@rule(
    "inversion_pm",
    Priority.COMFORT,
    seasons={Season.WINTER, Season.TRANSITIONAL},
    description="Winter inversion with high outdoor particulates",
)
def inversion_pm(ctx: EvaluationContext) -> Iterable[Recommendation]:
    """Calm, cold, high pressure: wood smoke sits in the street. Postpone."""
    state = ctx.state
    outdoor = state.outdoor.pm25
    if outdoor is None or outdoor < 20.0:
        return
    calm = (state.outdoor.wind_speed or 0.0) < 2.0
    high_pressure = (state.outdoor.pressure or 1013.0) > 1018.0
    cold = state.outdoor.temperature < 6.0
    if not (calm and cold and (high_pressure or state.is_night)):
        return
    yield make(
        ctx,
        "inversion_pm",
        "global",
        Action.KEEP_CLOSED,
        Priority.COMFORT,
        "inversion_pm",
        urgency=45,
        reason_data={
            "outdoor_pm25": round(outdoor, 1),
            "wind": round(state.outdoor.wind_speed or 0.0, 1),
            "pressure": round(state.outdoor.pressure or 0.0),
        },
        notify=False,
    )
