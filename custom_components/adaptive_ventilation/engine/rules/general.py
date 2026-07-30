"""Year-round housekeeping rules."""

from __future__ import annotations

from collections.abc import Iterable

from ..context import EvaluationContext
from ..state import Action, Priority, Recommendation, clamp
from . import make, rule
from .helpers import purge_windows

#: Appliance power above which a kitchen window earns its own recommendation.
INTERNAL_LOAD_W = 1000.0


@rule("window_forgotten", Priority.COMFORT, description="Window open for hours without benefit")
def window_forgotten(ctx: EvaluationContext) -> Iterable[Recommendation]:
    """Politely point out the window nobody remembers opening."""
    state = ctx.state
    limit_hours = state.preferences.window_forgotten_hours
    for window in state.windows:
        open_minutes = window.open_minutes(state.now)
        if open_minutes is None or open_minutes < limit_hours * 60.0:
            continue
        room = ctx.room_of(window)
        if room is None:
            continue

        delta_t = state.delta_t(room)
        delta_ah = state.delta_ah(room)
        useful_cooling = delta_t is not None and delta_t > state.preferences.delta_t_hysteresis
        useful_drying = delta_ah is not None and delta_ah > 0.5
        stale_air = room.co2 is not None and room.co2 > state.preferences.co2_threshold
        if useful_cooling or useful_drying or stale_air:
            continue

        yield make(
            ctx,
            "window_forgotten",
            window.id,
            Action.CLOSE,
            Priority.COMFORT,
            "window_forgotten",
            urgency=int(clamp(30 + open_minutes / 60.0 * 5.0, 30, 60)),
            room_id=room.id,
            reason_data={
                "window": window.name,
                "room": room.name,
                "hours": round(open_minutes / 60.0, 1),
                "delta_t": round(delta_t, 1) if delta_t is not None else None,
            },
            confidence=room.confidence,
        )


@rule("internal_load", Priority.COMFORT, description="Large appliance running in the room")
def internal_load(ctx: EvaluationContext) -> Iterable[Recommendation]:
    """Oven on -> open the kitchen window now, not once the flat is warm."""
    state = ctx.state
    for room in state.rooms:
        if not room.internal_load_w or room.internal_load_w < INTERNAL_LOAD_W:
            continue
        windows, _cross = purge_windows(ctx, room)
        if not windows:
            continue
        delta_t = state.delta_t(room)
        action = Action.OPEN_WIDE
        if delta_t is not None and delta_t < -1.0:
            # Hotter outside: tilt is the compromise, or the fan if there is one.
            action = Action.OPEN_TILT if windows[0].tilt_capable else Action.FAN_ON
        for window in windows:
            yield make(
                ctx,
                "internal_load",
                window.id,
                action,
                Priority.COMFORT,
                "internal_load",
                urgency=int(clamp(35 + room.internal_load_w / 100.0, 35, 70)),
                room_id=room.id,
                reason_data={
                    "room": room.name,
                    "window": window.name,
                    "load_w": int(room.internal_load_w),
                    "indoor": round(room.temperature, 1) if room.temperature else None,
                },
                confidence=room.confidence,
                notify=False,
            )
