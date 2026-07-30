"""Winter rules — purging, tilt avoidance, passive solar gain, night insulation."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import timedelta

from .. import solar as solar_mod
from ..context import EvaluationContext
from ..state import (
    Action,
    Priority,
    Recommendation,
    RoomState,
    Season,
    clamp,
)
from . import make, rule
from .helpers import purge_action, purge_windows

WINTERISH = {Season.WINTER, Season.TRANSITIONAL}

#: Hours of stale air after which a purge is due, occupied / unoccupied.
PURGE_INTERVAL_OCCUPIED = 4.0
PURGE_INTERVAL_DEFAULT = 7.0


def _purge_due(ctx: EvaluationContext, room: RoomState) -> tuple[bool, str, float | None]:
    """Whether a room is due for a purge, plus the reason and the elapsed hours."""
    prefs = ctx.preferences
    if room.co2 is not None and room.co2 >= prefs.co2_threshold:
        return False, "co2", None  # co2_high already owns this case
    if room.humidity is not None and room.humidity >= prefs.humidity_target_max + 8.0:
        return True, "humidity", None

    elapsed = ctx.memory.hours_since_purge(room.id, ctx.now)
    limit = PURGE_INTERVAL_OCCUPIED if room.occupied else PURGE_INTERVAL_DEFAULT
    if elapsed is None:
        # No history yet: fall back to CO₂ / humidity being mildly elevated.
        mild_co2 = room.co2 is not None and room.co2 >= prefs.co2_threshold - 250
        return mild_co2, "co2_trend", None
    return elapsed >= limit, "schedule", elapsed


@rule(
    "winter_purge_schedule",
    Priority.HEALTH,
    seasons=WINTERISH,
    description="Scheduled winter purge, 2-4 times a day",
)
def winter_purge_schedule(ctx: EvaluationContext) -> Iterable[Recommendation]:
    """Short, wide open, ideally cross ventilated — with a calculated duration."""
    state = ctx.state
    if state.is_away:
        return
    for room in state.rooms:
        due, why, elapsed = _purge_due(ctx, room)
        if not due:
            continue
        windows, cross = purge_windows(ctx, room)
        if not windows:
            continue
        minutes = ctx.purge_minutes(room, cross=cross)
        data = {
            "room": room.name,
            "minutes": minutes,
            "outdoor": round(state.outdoor.temperature, 1),
            "reason": why,
            "hours": round(elapsed, 1) if elapsed is not None else None,
            "cross": cross,
            "partner": windows[1].name if cross and len(windows) > 1 else None,
        }
        for window in windows:
            yield make(
                ctx,
                "winter_purge_schedule",
                window.id,
                purge_action(cross),
                Priority.HEALTH,
                "winter_purge_cross" if cross else "winter_purge",
                urgency=50,
                room_id=room.id,
                reason_data=dict(data, window=window.name),
                duration_minutes=minutes,
                confidence=room.confidence,
                valid_until=ctx.now + timedelta(hours=2),
            )


@rule(
    "avoid_tilt_winter",
    Priority.COMFORT,
    seasons={Season.WINTER},
    description="Tilted window in winter cools the reveal and grows mould",
)
def avoid_tilt_winter(ctx: EvaluationContext) -> Iterable[Recommendation]:
    """Swap a tilted window for short and wide — the reveal is the mould spot."""
    state = ctx.state
    for window in state.windows:
        if not (window.is_open and window.is_tilted):
            continue
        room = ctx.room_of(window)
        if room is None:
            continue
        open_minutes = window.open_minutes(state.now) or 0.0
        if open_minutes < 20.0:
            continue
        minutes = ctx.purge_minutes(room)
        yield make(
            ctx,
            "avoid_tilt_winter",
            window.id,
            Action.CLOSE,
            Priority.COMFORT,
            "avoid_tilt_winter",
            urgency=int(clamp(45 + open_minutes / 10.0, 45, 80)),
            room_id=room.id,
            reason_data={
                "window": window.name,
                "room": room.name,
                "minutes_open": int(open_minutes),
                "purge_minutes": minutes,
                "outdoor": round(state.outdoor.temperature, 1),
            },
            confidence=room.confidence,
        )


@rule(
    "passive_solar_gain",
    Priority.OPTIMIZATION,
    seasons=WINTERISH,
    description="Free heat: open the covers where the sun hits",
)
def passive_solar_gain(ctx: EvaluationContext) -> Iterable[Recommendation]:
    """Winter inverts the shading logic — sun on the glass is free heating."""
    state = ctx.state
    if not state.sun.is_up:
        return
    prefs = state.preferences
    for window in state.windows:
        if not window.has_cover or (window.cover_position or 0) >= 85:
            continue
        room = ctx.room_of(window)
        if room is None or room.temperature is None:
            continue
        target_min = room.target_min if room.target_min is not None else prefs.winter_target_min
        if room.temperature > target_min + 1.0:
            continue
        gain = solar_mod.solar_load(
            window,
            state.sun.elevation,
            state.sun.azimuth,
            state.outdoor.cloud_coverage,
            apply_cover=False,
        )
        if gain < 120.0:
            continue
        yield make(
            ctx,
            "passive_solar_gain",
            f"{window.id}:cover",
            Action.COVER_UP,
            Priority.OPTIMIZATION,
            "passive_solar_gain",
            urgency=int(clamp(25 + gain / 25.0, 25, 65)),
            room_id=room.id,
            reason_data={
                "window": window.name,
                "room": room.name,
                "gain_w": int(gain),
                "indoor": round(room.temperature, 1),
                "target": target_min,
            },
            confidence=room.confidence,
            notify=False,
        )


@rule(
    "cover_night_insulation",
    Priority.OPTIMIZATION,
    seasons=WINTERISH,
    description="Closed covers cut 10-20 percent of the transmission loss",
)
def cover_night_insulation(ctx: EvaluationContext) -> Iterable[Recommendation]:
    """A closed roller shutter is a genuine extra insulation layer at night."""
    state = ctx.state
    if state.sun.elevation > -2.0:
        return
    if state.outdoor.temperature > 12.0:
        return
    for window in state.windows:
        if not window.has_cover or window.is_open:
            continue
        if (window.cover_position or 100) <= 10:
            continue
        room = ctx.room_of(window)
        if room is None:
            continue
        area = window.area_m2 or 1.5
        # ~0.6 W/(m²·K) improvement for a closed external shutter.
        delta_t = (room.temperature or 20.0) - state.outdoor.temperature
        saving_w = max(0.0, 0.6 * area * delta_t)
        yield make(
            ctx,
            "cover_night_insulation",
            f"{window.id}:cover",
            Action.COVER_DOWN,
            Priority.OPTIMIZATION,
            "cover_night_insulation",
            urgency=30,
            room_id=room.id,
            reason_data={
                "window": window.name,
                "room": room.name,
                "saving_w": int(saving_w),
                "outdoor": round(state.outdoor.temperature, 1),
            },
            notify=False,
        )


@rule(
    "preheat_before_purge",
    Priority.OPTIMIZATION,
    seasons={Season.WINTER},
    description="Turn the radiator down for the purge, up again afterwards",
)
def preheat_before_purge(ctx: EvaluationContext) -> Iterable[Recommendation]:
    """A radiator heating an open window is the most expensive kind of nothing."""
    for room in ctx.rooms:
        if not room.heating_active:
            continue
        due, _why, _elapsed = _purge_due(ctx, room)
        co2_due = room.co2 is not None and room.co2 >= ctx.preferences.co2_threshold
        if not (due or co2_due):
            continue
        yield make(
            ctx,
            "preheat_before_purge",
            room.id,
            Action.NO_ACTION,
            Priority.OPTIMIZATION,
            "preheat_before_purge",
            urgency=20,
            room_id=room.id,
            reason_data={"room": room.name},
            notify=False,
        )
