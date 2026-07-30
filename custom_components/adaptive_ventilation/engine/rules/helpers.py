"""Small shared helpers for the rule modules."""

from __future__ import annotations

from ..context import EvaluationContext
from ..state import Action, RoomState, WindowState


def purge_windows(ctx: EvaluationContext, room: RoomState) -> tuple[list[WindowState], bool]:
    """Best window set for a purge in ``room`` and whether it is a cross draught.

    Prefers a cross-ventilation pair (three to five times the air exchange, so
    "bathroom *and* bedroom, then four minutes instead of twelve"), otherwise
    the largest single window in the room.
    """
    own = ctx.windows_of(room)
    if not own:
        return [], False

    for window in own:
        partner = ctx.is_cross_partner(window)
        if partner is not None:
            return [window, partner], True

    largest = max(own, key=lambda w: w.area_m2 or 1.0)
    return [largest], False


def purge_action(cross: bool) -> Action:
    """Cross ventilation gets its own action so the text can say so."""
    return Action.CROSS_VENTILATE if cross else Action.PURGE


def rooms_with_windows(ctx: EvaluationContext) -> list[RoomState]:
    """Only rooms the engine can actually do something about."""
    return [room for room in ctx.rooms if ctx.windows_of(room)]


def blocked_by_outdoor_air(ctx: EvaluationContext) -> bool:
    """``True`` when outdoor particulate levels make ventilation questionable."""
    outdoor = ctx.outdoor.pm25
    return outdoor is not None and outdoor > ctx.preferences.pm25_outdoor_threshold


def confidence_of(room: RoomState) -> float:
    """Confidence of a room, for readability at the call site."""
    return room.confidence


def round_or_none(value: float | None, digits: int = 1) -> float | None:
    """Round a value that may be missing."""
    return None if value is None else round(value, digits)
