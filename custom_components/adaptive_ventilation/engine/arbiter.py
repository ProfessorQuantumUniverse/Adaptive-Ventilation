"""Conflict resolution.

The rules are deliberately naive and may contradict each other — that is the
point of a registry of small advisors. The arbiter turns the pile of candidates
into at most one main recommendation per target, applies the vetoes, and keeps
the system quiet (hysteresis, minimum dwell time, cooldown, snooze, quiet
hours, manual hold).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any

from .context import EvaluationContext
from .state import (
    CLOSING_ACTIONS,
    COVER_ACTIONS,
    OPENING_ACTIONS,
    Action,
    GlobalState,
    Mode,
    Priority,
    Recommendation,
    Season,
    WindowState,
)

#: Rule ids whose recommendations are driven by air quality rather than heat.
AIR_QUALITY_RULES = frozenset(
    {
        "co2_high",
        "voc_high",
        "indoor_pm_high",
        "pm_both_high",
        "humidity_spike",
        "mold_risk",
        "winter_purge_schedule",
    }
)


def arbitrate(
    ctx: EvaluationContext, candidates: Sequence[Recommendation]
) -> tuple[list[Recommendation], list[Recommendation]]:
    """Return ``(active, suppressed)`` recommendations."""
    state = ctx.state
    memory = ctx.memory

    if state.mode is Mode.OFF:
        return [], list(candidates)

    suppressed: list[Recommendation] = []

    # 1. Vetoes forbid actions on their target ("global" hits everything).
    blocked: dict[str, set[Action]] = {}
    for candidate in candidates:
        if candidate.veto and candidate.blocks:
            blocked.setdefault(candidate.target, set()).update(candidate.blocks)
    global_block = blocked.get("global", set())

    allowed: list[Recommendation] = []
    for candidate in candidates:
        if candidate.veto:
            allowed.append(candidate)
            continue
        target_block = blocked.get(candidate.target, set()) | global_block
        # A room-level veto also blocks that room's windows.
        if candidate.room_id:
            target_block |= blocked.get(candidate.room_id, set())
        if candidate.action in target_block:
            suppressed.append(candidate)
            continue
        if not candidate.is_valid(state.now):
            suppressed.append(candidate)
            continue
        allowed.append(candidate)

    # 2. Group by target and pick a single winner each.
    grouped: dict[str, list[Recommendation]] = {}
    for candidate in allowed:
        grouped.setdefault(candidate.target, []).append(candidate)

    active: list[Recommendation] = []
    for target, group in grouped.items():
        winner = _pick_winner(ctx, group)
        losers = [c for c in group if c is not winner]
        suppressed.extend(losers)

        if memory.is_held(target, state.now) and winner.priority is not Priority.SAFETY:
            suppressed.append(winner)
            continue

        winner = _apply_minimum_dwell(ctx, target, winner)
        winner = _apply_notification_policy(ctx, winner)
        _track(ctx, target, winner)
        active.append(winner)

    # Every window ends up with exactly one recommendation, even if no rule had
    # an opinion. The filler goes through the same dwell logic, otherwise the
    # advice would flicker between "keep closed" and a blank entity whenever a
    # threshold is grazed.
    covered = {rec.target for rec in active}
    for window in state.windows:
        if window.id in covered:
            continue
        reason_key, reason_data = _idle_reason(ctx, window)
        idle = Recommendation(
            id=f"idle:{window.id}",
            target=window.id,
            action=Action.NO_ACTION,
            priority=Priority.OPTIMIZATION,
            urgency=0,
            reason_key=reason_key,
            reason_data=reason_data,
            room_id=window.room_id,
            notify=False,
            valid_from=state.now,
        )
        idle = _apply_minimum_dwell(ctx, window.id, idle)
        _track(ctx, window.id, idle)
        active.append(idle)

    active.sort(key=lambda r: r.sort_key, reverse=True)
    return active, suppressed


def _pick_winner(ctx: EvaluationContext, group: Sequence[Recommendation]) -> Recommendation:
    """Highest priority, then adjusted urgency; ties go to the safer action."""
    best = group[0]
    best_score = _score(ctx, best)
    for candidate in group[1:]:
        score = _score(ctx, candidate)
        if score > best_score:
            best, best_score = candidate, score
        elif score == best_score and _is_more_conservative(candidate, best):
            best = candidate
    return best


def _score(ctx: EvaluationContext, rec: Recommendation) -> tuple[int, int, float]:
    """Priority, room-weighted urgency, confidence."""
    urgency = rec.urgency
    if rec.room_id:
        room = ctx.state.room(rec.room_id)
        if room is not None:
            # Priority 1 is the most important room; give it up to +12 urgency.
            urgency += max(0, 6 - room.priority) * 3
            if room.is_bedroom and ctx.state.is_night:
                urgency += 5
    return int(rec.priority), min(100, urgency), rec.confidence


def _is_more_conservative(candidate: Recommendation, other: Recommendation) -> bool:
    """Closing beats opening on a tie; doing nothing beats acting."""
    order = {
        Action.NO_ACTION: 0,
        Action.KEEP_CLOSED: 1,
        Action.CLOSE: 2,
        Action.COVER_DOWN: 3,
        Action.COVER_SLAT: 3,
        Action.COVER_UP: 4,
        Action.FAN_ON: 4,
        Action.KEEP_OPEN: 5,
        Action.OPEN_TILT: 6,
        Action.PURGE: 7,
        Action.CROSS_VENTILATE: 7,
        Action.OPEN_WIDE: 8,
    }
    return order.get(candidate.action, 5) < order.get(other.action, 5)


def _apply_minimum_dwell(
    ctx: EvaluationContext, target: str, winner: Recommendation
) -> Recommendation:
    """Do not let a target change state faster than the minimum dwell time.

    This is what keeps the system from flapping when ΔT hovers around zero.
    SAFETY always passes.
    """
    memory = ctx.memory
    prefs = ctx.preferences
    tracked = memory.target(target)

    if winner.priority is Priority.SAFETY:
        return winner
    if tracked.action in (Action.NO_ACTION, winner.action) or tracked.since is None:
        return winner
    elapsed = (ctx.now - tracked.since).total_seconds() / 60.0
    if elapsed >= prefs.min_state_duration_minutes:
        return winner

    return replace(
        winner,
        action=tracked.action,
        reason_key="hold_min_duration",
        reason_data={
            "held_action": tracked.action.value,
            "wanted_action": winner.action.value,
            "minutes_left": int(prefs.min_state_duration_minutes - elapsed),
            "original_reason": winner.reason_key,
        },
        urgency=min(winner.urgency, 40),
        notify=False,
        duration_minutes=None,
    )


def _apply_notification_policy(ctx: EvaluationContext, rec: Recommendation) -> Recommendation:
    """Decide whether this recommendation may be pushed."""
    memory = ctx.memory
    prefs = ctx.preferences
    notify = rec.notify

    if not prefs.notifications_enabled:
        notify = False
    if memory.is_snoozed(rec.id, ctx.now) or memory.is_ignored_today(rec.id, ctx.now):
        notify = False
    cooldown_until = memory.cooldowns.get(rec.id)
    if cooldown_until is not None and cooldown_until > ctx.now:
        notify = False
    if rec.confidence < prefs.min_confidence_for_push:
        notify = False
    if rec.action is Action.NO_ACTION:
        notify = False
    if prefs.in_quiet_hours(ctx.now) and rec.priority is not Priority.SAFETY:
        notify = False
    # Restraint slider: at 100 only HEALTH and SAFETY still push.
    if prefs.notification_restraint >= 75 and rec.priority < Priority.HEALTH:
        notify = False

    return rec if notify == rec.notify else replace(rec, notify=notify)


def _track(ctx: EvaluationContext, target: str, winner: Recommendation) -> None:
    """Update the per-target memory and detect a user who keeps disagreeing."""
    memory = ctx.memory
    tracked = memory.target(target)
    if tracked.action is not winner.action:
        tracked.action = winner.action
        tracked.since = ctx.now
        return

    window = ctx.state.window(target)
    if window is None or tracked.last_notified is None:
        return
    if not _contradicted(window, winner.action, tracked.last_notified):
        return

    cooldown = timedelta(minutes=ctx.preferences.cooldown_minutes)
    if ctx.now - tracked.last_notified < cooldown:
        return

    today = memory.day_of(ctx.now)
    if tracked.contradiction_day != today:
        tracked.contradiction_day = today
        tracked.contradictions = 0
    tracked.contradictions += 1
    tracked.last_notified = ctx.now
    if tracked.contradictions >= 2:
        # The user has told us twice. Give in for today and learn.
        memory.hold(target, ctx.now)


def _contradicted(window: WindowState, action: Action, advised_at: datetime) -> bool:
    """Whether the user *overruled* us, rather than simply not having reacted.

    This has to be an action, not the absence of one. The old version asked
    only "is the window in the state we asked for", which every sleeping,
    absent or busy user fails: two cooldowns after the first push ever sent for
    a window, :func:`_track` put a manual hold on it and the arbiter dropped
    every recommendation below SAFETY for the rest of the day. ``last_notified``
    is never cleared, so the check kept re-arming and the window stayed muted
    day after day - while covers, whose actions are in neither action set, went
    on notifying. That is the "only two shutter notifications a day" report.

    So we require evidence: a contact sensor (a guess cannot overrule anything)
    that has changed *since* the advice went out, and that now reads the
    opposite of what we asked for.
    """
    if not window.contact_known:
        return False
    if window.contact_changed is None or window.contact_changed <= advised_at:
        return False
    if action in CLOSING_ACTIONS:
        return window.is_open
    if action in OPENING_ACTIONS:
        return not window.is_open
    return False


def _idle_reason(ctx: EvaluationContext, window: WindowState) -> tuple[str, dict[str, Any]]:
    """Say *why* there is nothing to do.

    A bare "nothing to do" while it is 30 degrees outside reads like a broken
    integration. Almost always there is a concrete reason - no indoor
    temperature for that room, stale outdoor data, or genuinely nothing to gain
    because inside and outside are the same - and naming it is the difference
    between a bug report and an "ah, right".
    """
    state = ctx.state
    data: dict[str, Any] = {"window": window.name}

    if state.mode is Mode.OFF:
        return "idle_off", data
    if state.outdoor.is_stale:
        return "idle_stale", data
    if state.is_away:
        return "idle_away", data
    # A held target produces advice that is then thrown away, so without this
    # the panel showed a flat "nothing to do" next to a four-Kelvin gradient -
    # the one screen that should have explained the silence was the one that
    # hid it, and diagnosing it took a dump of the engine's internals.
    if ctx.memory.is_held(window.id, state.now):
        return "idle_held", data

    room = ctx.state.room(window.room_id)
    data["room"] = room.name if room else window.room_id
    if room is None or room.temperature is None:
        return "idle_no_room_data", data

    delta_t = state.delta_t(room)
    data["indoor"] = round(room.temperature, 1)
    data["outdoor"] = round(state.outdoor.temperature, 1)
    if delta_t is not None:
        data["delta_t"] = round(abs(delta_t), 1)
        if abs(delta_t) <= ctx.preferences.delta_t_hysteresis * 3:
            return "idle_balanced", data
    return "nothing_to_do", data


def determine_global_state(ctx: EvaluationContext, active: Iterable[Recommendation]) -> GlobalState:
    """Collapse everything into the single state machine value."""
    state = ctx.state
    recommendations = list(active)

    if state.mode is Mode.OFF:
        return GlobalState.OFF
    if state.outdoor.is_stale:
        return GlobalState.UNAVAILABLE_DATA
    if any(r.rule_id.startswith("storm_warning") for r in recommendations):
        return GlobalState.STORM
    if state.purge_active:
        return GlobalState.PURGE_RUNNING
    if state.is_away:
        return GlobalState.AWAY

    if any(r.rule_id in AIR_QUALITY_RULES and r.action in OPENING_ACTIONS for r in recommendations):
        return GlobalState.AIR_QUALITY
    if any(r.rule_id == "night_flush" and r.action in OPENING_ACTIONS for r in recommendations):
        return GlobalState.NIGHT_FLUSH if state.is_night else GlobalState.VENTILATE_NOW
    if any(r.action in OPENING_ACTIONS for r in recommendations):
        return GlobalState.VENTILATE_NOW
    if ctx.season is Season.SUMMER and any(
        r.action in COVER_ACTIONS and r.action is Action.COVER_DOWN for r in recommendations
    ):
        return GlobalState.HEAT_PROTECTION
    if any(r.action in CLOSING_ACTIONS for r in recommendations):
        return GlobalState.KEEP_CLOSED
    return GlobalState.IDLE
