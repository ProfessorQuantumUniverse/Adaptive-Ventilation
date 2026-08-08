"""Summer rules — the main path for a flat without air conditioning."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import timedelta

from .. import thermal
from ..context import EvaluationContext
from ..state import (
    CLOSING_ACTIONS,
    OPENING_ACTIONS,
    Action,
    Priority,
    Recommendation,
    Season,
    clamp,
)
from . import make, rule
from .helpers import purge_windows

SUMMERISH = {Season.SUMMER, Season.TRANSITIONAL}

#: Leaving a state costs twice as much ΔT as entering it. The resulting dead
#: band (3x the hysteresis, 1.5 K by default) is wider than the noise of a
#: cheap outdoor sensor, which is the whole point.
EXIT_HYSTERESIS_FACTOR = 2.0


@rule(
    "night_flush",
    Priority.COMFORT,
    seasons=SUMMERISH,
    description="Cool night air is available - flush the heat out",
)
def night_flush(ctx: EvaluationContext) -> Iterable[Recommendation]:
    """Open up while the outside air is genuinely cooler.

    "Genuinely" is decided on enthalpy, not temperature: humid 19 °C air can
    carry more heat into the flat than dry 21 °C air. Humidity does not veto in
    summer though — it only lowers the urgency, weighted by the user's sliders.
    """
    state = ctx.state
    prefs = state.preferences
    hysteresis = prefs.delta_t_hysteresis
    if state.is_away:
        return

    for room in state.rooms:
        if room.temperature is None or room.is_basement:
            continue
        target_max = room.target_max if room.target_max is not None else prefs.summer_target_max
        delta_t = state.delta_t(room)
        # Proper Schmitt trigger: entering needs ΔT above +hysteresis, staying
        # only needs it above -hysteresis. Without the asymmetry the advice
        # bounces every time the outdoor sensor twitches around the threshold.
        holding = any(
            ctx.memory.target(w.id).action in OPENING_ACTIONS for w in ctx.windows_of(room)
        )
        threshold = -hysteresis * EXIT_HYSTERESIS_FACTOR if holding else hysteresis
        if delta_t is None or delta_t <= threshold:
            continue
        if room.temperature <= target_max - 0.5 and not state.is_night:
            continue

        windows, cross = purge_windows(ctx, room)
        if not windows:
            continue

        potential = ctx.night_potential.get(room.id, 0.0)
        rate = thermal.cooling_rate_k_per_h(
            room,
            list(windows),
            state.outdoor.temperature,
            state.building,
            ctx.learned(room.id),
            wind_speed=state.outdoor.wind_speed,
            cross=cross,
        )

        # Humidity trade-off, weighted by the user's sliders.
        delta_ah = state.delta_ah(room)
        enthalpy_delta = ctx.enthalpy_delta(room)
        humid_penalty = 0
        if delta_ah is not None and delta_ah < -0.5:
            ratio = prefs.weight("humidity") / max(prefs.weight("temperature"), 0.2)
            humid_penalty = int(clamp(abs(delta_ah) * 6.0 * ratio, 0, 45))
        if enthalpy_delta is not None and enthalpy_delta < 0.0:
            humid_penalty += 15

        urgency = int(clamp(35 + delta_t * 9.0 - humid_penalty, 5, 95))
        if state.is_night:
            urgency += 10
        if room.is_bedroom and state.is_night:
            urgency += 5
        urgency = int(clamp(urgency, 5, 98))

        if urgency < 25:
            continue

        data = {
            "room": room.name,
            "indoor": round(room.temperature, 1),
            "outdoor": round(state.outdoor.temperature, 1),
            "delta_t": round(delta_t, 1),
            "delta_ah": round(delta_ah, 1) if delta_ah is not None else None,
            "expected_k": round(potential, 1),
            "rate": round(rate, 2),
            "cross": cross,
        }
        for window in windows:
            already_open = window.is_open and not window.is_tilted
            yield make(
                ctx,
                "night_flush",
                window.id,
                Action.KEEP_OPEN
                if already_open
                else (Action.CROSS_VENTILATE if cross else Action.OPEN_WIDE),
                Priority.COMFORT,
                "night_flush_keep" if already_open else "night_flush",
                urgency=urgency,
                room_id=room.id,
                reason_data=dict(data, window=window.name),
                confidence=room.confidence,
                expected_benefit=f"-{potential:.1f} K",
                notify=not already_open,
                valid_until=ctx.tipping.morning,
            )


@rule(
    "morning_close",
    Priority.COMFORT,
    seasons=SUMMERISH,
    description="Outdoor temperature is about to overtake indoor",
)
def morning_close(ctx: EvaluationContext) -> Iterable[Recommendation]:
    """The one you sleep through. Warn *before* the crossover, not after."""
    state = ctx.state
    tipping = ctx.tipping
    if tipping.morning is None:
        return
    minutes = ctx.minutes_until(tipping.morning)
    if minutes is None or minutes > state.preferences.close_lead_time_minutes or minutes < -30:
        return

    data = {
        "minutes": max(0, int(minutes)),
        "tipping_point": tipping.morning.isoformat(),
        "outdoor": round(state.outdoor.temperature, 1),
        "peak": round(state.forecast_max(12) or 0.0, 1),
    }
    for window in state.windows:
        room = ctx.room_of(window)
        if room is None:
            continue
        yield make(
            ctx,
            "morning_close",
            window.id,
            Action.CLOSE if window.is_open else Action.KEEP_CLOSED,
            Priority.COMFORT,
            "morning_close" if minutes > 5 else "morning_close_now",
            urgency=int(clamp(95 - minutes, 55, 95)),
            room_id=room.id,
            reason_data=dict(data, room=room.name, window=window.name),
            confidence=min(room.confidence, tipping.morning_confidence or 0.6),
            # The one push this integration exists for. A window with no contact
            # sensor is not evidence that it is closed, and staying quiet about
            # it is the failure mode the morning crossover is supposed to fix.
            notify=window.may_be_open,
            valid_until=tipping.morning + timedelta(hours=1),
        )


@rule(
    "keep_closed_hot",
    Priority.COMFORT,
    seasons=SUMMERISH,
    description="Warmer outside than inside",
)
def keep_closed_hot(ctx: EvaluationContext) -> Iterable[Recommendation]:
    """The baseline summer state, with the numbers that justify it."""
    state = ctx.state
    hysteresis = state.preferences.delta_t_hysteresis
    for room in state.rooms:
        delta_t = state.delta_t(room)
        # Mirror image of night_flush: harder to enter than to stay in.
        holding = any(
            ctx.memory.target(w.id).action in CLOSING_ACTIONS for w in ctx.windows_of(room)
        )
        threshold = hysteresis * EXIT_HYSTERESIS_FACTOR if holding else -hysteresis
        if delta_t is None or delta_t >= threshold:
            continue
        data = {
            "room": room.name,
            "indoor": round(room.temperature or 0.0, 1),
            "outdoor": round(state.outdoor.temperature, 1),
            "delta_t": round(-delta_t, 1),
            "next_opening": ctx.tipping.evening.isoformat() if ctx.tipping.evening else None,
        }
        for window in ctx.windows_of(room):
            open_now = window.is_open
            yield make(
                ctx,
                "keep_closed_hot",
                window.id,
                Action.CLOSE if open_now else Action.KEEP_CLOSED,
                Priority.COMFORT,
                "keep_closed_hot",
                urgency=int(clamp(30 + abs(delta_t) * 8.0, 30, 80)) if open_now else 25,
                room_id=room.id,
                reason_data=dict(data, window=window.name),
                confidence=room.confidence,
                notify=open_now and abs(delta_t) > 1.5,
            )


@rule(
    "precool_heatwave",
    Priority.COMFORT,
    seasons=SUMMERISH,
    description="Heatwave in one to two days - empty the thermal store now",
)
def precool_heatwave(ctx: EvaluationContext) -> Iterable[Recommendation]:
    """Cool down before it hurts. The store has to be empty *before* the wave."""
    state = ctx.state
    found, peak, peak_time = thermal.heatwave_ahead(
        state.forecast, state.now, state.preferences.heatwave_threshold
    )
    if not found or state.is_away:
        return
    if state.outdoor.temperature >= (state.mean_indoor_temperature or 99.0) - 0.5:
        return

    data = {
        "peak": round(peak, 1),
        "peak_time": peak_time.isoformat() if peak_time else None,
        "outdoor": round(state.outdoor.temperature, 1),
        "storage": ctx.cooling_budget.storage_k,
    }
    for room in state.rooms:
        if room.temperature is None or room.is_basement:
            continue
        windows, cross = purge_windows(ctx, room)
        if not windows:
            continue
        for window in windows:
            yield make(
                ctx,
                "precool_heatwave",
                window.id,
                Action.CROSS_VENTILATE if cross else Action.OPEN_WIDE,
                Priority.OPTIMIZATION,
                "precool_heatwave",
                urgency=55,
                room_id=room.id,
                reason_data=dict(data, room=room.name, window=window.name),
                confidence=room.confidence * 0.9,
                expected_benefit=f"-{ctx.night_potential.get(room.id, 0.0):.1f} K",
                notify=False,
            )


@rule(
    "tropical_night",
    Priority.COMFORT,
    seasons=SUMMERISH,
    description="Night stays above 20 C - manage expectations",
)
def tropical_night(ctx: EvaluationContext) -> Iterable[Recommendation]:
    """Be honest: night flushing alone will not carry this one."""
    state = ctx.state
    prefs = state.preferences
    if not thermal.is_tropical_night(
        state.forecast,
        state.now,
        prefs.tropical_night_threshold,
        latitude=state.building.latitude,
        longitude=state.building.longitude,
    ):
        return
    budget = ctx.cooling_budget
    night_min = min(
        (f.temperature for f in state.forecast_slice(14)), default=state.outdoor.temperature
    )
    data = {
        "night_min": round(night_min, 1),
        "threshold": prefs.tropical_night_threshold,
        "achievable": budget.achievable_tonight_k,
        "required": budget.required_tonight_k,
        "balance_3d": budget.balance_3d,
    }
    yield make(
        ctx,
        "tropical_night",
        "global",
        Action.NO_ACTION,
        Priority.COMFORT,
        "tropical_night_insufficient" if not budget.is_sufficient else "tropical_night",
        urgency=50,
        reason_data=data,
        notify=not budget.is_sufficient,
        confidence=0.8,
    )

    for room in state.rooms:
        if not room.fan_available or room.temperature is None:
            continue
        if room.temperature < (room.target_max or prefs.summer_target_max):
            continue
        yield make(
            ctx,
            "tropical_night_fan",
            room.id,
            Action.FAN_ON,
            Priority.COMFORT,
            "tropical_night_fan",
            urgency=45,
            room_id=room.id,
            reason_data=dict(data, room=room.name, temperature=round(room.temperature, 1)),
            notify=False,
        )


@rule(
    "thunderstorm_window",
    Priority.COMFORT,
    seasons=SUMMERISH,
    description="Cold front passage - short aggressive ventilation window",
)
def thunderstorm_window(ctx: EvaluationContext) -> Iterable[Recommendation]:
    """8 K in 20 minutes. Grab it, then close before the rain arrives."""
    state = ctx.state
    drop = thermal.find_temperature_drop(state.forecast, state.now, hours=6.0, min_drop_k=5.0)
    if drop is None:
        return
    start, end, magnitude = drop
    minutes_away = (start - state.now).total_seconds() / 60.0
    if minutes_away > 90.0:
        return

    rain_after = any(
        (f.precipitation or 0.0) > 0.5
        for f in state.forecast
        if end <= f.time <= end + timedelta(hours=2)
    )
    data = {
        "drop": round(magnitude, 1),
        "start": start.isoformat(),
        "end": end.isoformat(),
        "minutes": max(0, int(minutes_away)),
        "rain_after": rain_after,
    }
    for room in state.rooms:
        if room.temperature is None:
            continue
        windows, cross = purge_windows(ctx, room)
        if not windows:
            continue
        for window in windows:
            if not window.rain_safe and rain_after and minutes_away > 30:
                continue
            yield make(
                ctx,
                "thunderstorm_window",
                window.id,
                Action.CROSS_VENTILATE if cross else Action.OPEN_WIDE,
                Priority.COMFORT,
                "thunderstorm_window",
                urgency=75,
                room_id=room.id,
                reason_data=dict(data, room=room.name, window=window.name),
                duration_minutes=max(10, int((end - start).total_seconds() / 60)),
                confidence=0.7,
                valid_from=start - timedelta(minutes=10),
                valid_until=end,
            )


@rule(
    "fan_instead",
    Priority.COMFORT,
    seasons=SUMMERISH,
    description="Too warm but ventilation would not help - move the air instead",
)
def fan_instead(ctx: EvaluationContext) -> Iterable[Recommendation]:
    """Air movement is worth 2-3 K of perceived temperature and costs 30 W."""
    state = ctx.state
    prefs = state.preferences
    for room in state.rooms:
        if room.temperature is None or not room.fan_available:
            continue
        target_max = room.target_max if room.target_max is not None else prefs.summer_target_max
        if room.temperature < target_max:
            continue
        delta_t = state.delta_t(room)
        if delta_t is None or delta_t > 0.5:
            continue  # night_flush handles it, opening actually helps
        if room.occupied is False:
            continue
        yield make(
            ctx,
            "fan_instead",
            room.id,
            Action.FAN_ON,
            Priority.COMFORT,
            "fan_instead",
            urgency=40,
            room_id=room.id,
            reason_data={
                "room": room.name,
                "indoor": round(room.temperature, 1),
                "outdoor": round(state.outdoor.temperature, 1),
                "perceived_gain": 2.5,
            },
            confidence=room.confidence,
            notify=False,
        )


@rule(
    "away_prepare",
    Priority.OPTIMIZATION,
    seasons=SUMMERISH,
    description="Nobody home - shut the flat down against the heat",
)
def away_prepare(ctx: EvaluationContext) -> Iterable[Recommendation]:
    """Lower the covers before leaving instead of regretting it in the evening."""
    state = ctx.state
    if not state.is_away or not state.sun.is_up:
        return
    for window in state.windows:
        if not window.has_cover:
            continue
        saving = ctx.cover_savings.get(window.id, 0.0)
        future = ctx.sun_ahead(window)
        if saving < 100.0 and future is None:
            continue
        if (window.cover_position or 100) <= 15:
            continue
        yield make(
            ctx,
            "away_prepare",
            f"{window.id}:cover",
            Action.COVER_DOWN,
            Priority.OPTIMIZATION,
            "away_prepare",
            urgency=45,
            room_id=window.room_id,
            reason_data={
                "window": window.name,
                "saving_w": int(saving),
            },
            notify=False,
        )
