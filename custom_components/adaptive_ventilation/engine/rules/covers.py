"""Shading rules.

The whole point is to act **before** the sun arrives. Once the glass has let
the energy in, an internal blind only stops about 30 % of it — an external one
would have stopped 80 %.
"""

from __future__ import annotations

from collections.abc import Iterable

from ..context import EvaluationContext
from ..state import Action, Priority, Recommendation, Season, clamp
from . import make, rule

SUMMERISH = {Season.SUMMER, Season.TRANSITIONAL}

#: Solar load above which shading is worth a recommendation.
SHADING_THRESHOLD_W = 250.0
#: How far ahead we lower a cover before the sun reaches the window.
SHADING_LEAD_MINUTES = 45.0


@rule(
    "solar_shading",
    Priority.COMFORT,
    seasons=SUMMERISH,
    description="Lower the cover before the sun hits the window",
)
def solar_shading(ctx: EvaluationContext) -> Iterable[Recommendation]:
    """Predictive shading: the recommendation arrives before the heat does."""
    state = ctx.state
    prefs = state.preferences
    for window in state.windows:
        if not window.has_cover:
            continue
        room = ctx.room_of(window)
        if room is None:
            continue
        position = window.cover_position
        if position is not None and position <= 20:
            continue

        current = ctx.solar_loads.get(window.id, 0.0)
        saving = ctx.cover_savings.get(window.id, 0.0)
        upcoming = ctx.sun_ahead(window)
        minutes_ahead = ctx.minutes_until(upcoming)

        imminent = minutes_ahead is not None and 0 <= minutes_ahead <= SHADING_LEAD_MINUTES
        active = current >= SHADING_THRESHOLD_W
        if not (active or imminent):
            continue

        target_max = room.target_max if room.target_max is not None else prefs.summer_target_max
        warm_enough = room.temperature is None or room.temperature >= target_max - 2.0
        hot_day = (state.forecast_max(12) or state.outdoor.temperature) >= target_max
        if not (warm_enough or hot_day):
            continue

        # An external cover is worth much more than an internal one, so it
        # deserves the higher urgency and the earlier push.
        urgency = int(clamp(35 + saving / 25.0, 35, 90))
        if not window.cover_external:
            urgency = int(urgency * 0.75)
        if imminent and not active:
            urgency = int(clamp(urgency * 0.8, 25, 75))

        yield make(
            ctx,
            "solar_shading",
            f"{window.id}:cover",
            Action.COVER_DOWN if window.cover_external or not window.is_open else Action.COVER_SLAT,
            Priority.COMFORT,
            "solar_shading_ahead" if imminent and not active else "solar_shading",
            urgency=urgency,
            room_id=room.id,
            reason_data={
                "window": window.name,
                "room": room.name,
                "load_w": int(current),
                "saving_w": int(saving),
                "minutes": int(minutes_ahead) if minutes_ahead is not None else None,
                "external": window.cover_external,
                "peak": round(state.forecast_max(12) or 0.0, 1),
            },
            confidence=0.9 if window.cover_external else 0.8,
            expected_benefit=f"-{saving / 1000.0:.1f} kW",
            notify=window.cover_auto_allowed is False and saving > 300.0,
        )


@rule(
    "shading_gap",
    Priority.OPTIMIZATION,
    seasons=SUMMERISH,
    description="High solar load on a window without any shading",
)
def shading_gap(ctx: EvaluationContext) -> Iterable[Recommendation]:
    """Not "close the window" — an honest "this is the biggest hole in the setup"."""
    state = ctx.state
    candidates = [
        (w, ctx.solar_loads.get(w.id, 0.0))
        for w in state.windows
        if not w.has_cover and ctx.solar_loads.get(w.id, 0.0) >= SHADING_THRESHOLD_W
    ]
    if not candidates:
        return
    window, load = max(candidates, key=lambda item: item[1])
    room = ctx.room_of(window)
    if room is None:
        return
    daily_k = load * 6.0 / max((room.volume_m3 or 40.0) * 18.0, 1.0)
    yield make(
        ctx,
        "shading_gap",
        window.id,
        Action.NO_ACTION,
        Priority.OPTIMIZATION,
        "shading_gap",
        urgency=25,
        room_id=room.id,
        reason_data={
            "window": window.name,
            "room": room.name,
            "load_w": int(load),
            "daily_k": round(daily_k, 1),
        },
        notify=False,
        confidence=0.8,
    )


@rule(
    "shading_release",
    Priority.OPTIMIZATION,
    seasons=SUMMERISH,
    description="The sun has moved on - the blind can go back up",
)
def shading_release(ctx: EvaluationContext) -> Iterable[Recommendation]:
    """Tell people when they can have their daylight back.

    Only advising "close it" and never "you can open it again" trains people to
    sit in the dark all evening, or to ignore the integration. Matters most for
    a blind operated by hand, where nothing else will ever raise it.
    """
    state = ctx.state
    for window in state.windows:
        if not window.has_cover:
            continue
        # Only if we were the ones who asked for it to be closed.
        tracked = ctx.memory.target(f"{window.id}:cover")
        if tracked.action is not Action.COVER_DOWN:
            continue
        if window.cover_position is not None and window.cover_position > 60:
            continue  # already open again

        load = ctx.solar_loads.get(window.id, 0.0)
        upcoming = ctx.minutes_until(ctx.sun_ahead(window))
        if load >= SHADING_THRESHOLD_W * 0.4:
            continue
        if upcoming is not None and 0 <= upcoming <= 60:
            continue  # it is about to come back round

        room = ctx.room_of(window)
        yield make(
            ctx,
            "shading_release",
            f"{window.id}:cover",
            Action.COVER_UP,
            Priority.OPTIMIZATION,
            "shading_release",
            urgency=20,
            room_id=room.id if room else None,
            reason_data={
                "window": window.name,
                "room": room.name if room else None,
                "load_w": int(load),
            },
            notify=window.cover_is_manual,
            confidence=0.9,
        )
