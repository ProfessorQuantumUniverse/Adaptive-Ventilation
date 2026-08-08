"""SAFETY rules — vetoes that always win.

A veto does two things: it emits its own recommendation (usually ``CLOSE``) and
it *forbids* a set of actions on its target for this evaluation. The arbiter
drops every candidate that collides with an active veto.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import timedelta

from ..context import EvaluationContext
from ..state import (
    OPENING_ACTIONS,
    Action,
    Priority,
    Recommendation,
    Season,
    bearing_difference,
)
from . import make, rule

#: Wind speeds are handled in m/s internally; the user threshold is in km/h.
KMH_PER_MS = 3.6
#: A window counts as "on the weather side" within this angle of the wind.
WEATHER_SIDE_DEGREES = 80.0


@rule("storm_warning", Priority.SAFETY, description="Storm or severe weather warning active")
def storm_warning(ctx: EvaluationContext) -> Iterable[Recommendation]:
    """Close everything, and lower covers when hail is in the warning."""
    state = ctx.state
    gust_kmh = (state.outdoor.wind_speed or 0.0) * KMH_PER_MS
    alerts = [
        a
        for a in state.active_alerts
        if a.kind in ("storm", "hail", "thunderstorm") and a.severity in ("severe", "extreme")
    ]
    over_threshold = gust_kmh >= state.preferences.storm_wind_kmh
    if not alerts and not over_threshold:
        return

    event = alerts[0].event if alerts else "wind"
    severity = alerts[0].severity if alerts else "moderate"
    hail = any(a.kind == "hail" for a in alerts)
    data = {
        "event": event,
        "severity": severity,
        "wind_kmh": round(gust_kmh, 1),
        "threshold_kmh": state.preferences.storm_wind_kmh,
    }

    for window in state.windows:
        yield make(
            ctx,
            "storm_warning",
            window.id,
            Action.CLOSE if window.is_open else Action.KEEP_CLOSED,
            Priority.SAFETY,
            "storm_warning",
            urgency=100 if window.is_open else 60,
            room_id=window.room_id,
            reason_data=data,
            veto=True,
            blocks=OPENING_ACTIONS,
            # Storms are rare enough that "I cannot see this window, go check
            # it" is worth a push. Rain, below, is not - that would be daily.
            notify=window.may_be_open,
            valid_until=alerts[0].end if alerts else None,
        )
        if hail and window.has_cover and (window.cover_position or 0) > 10:
            yield make(
                ctx,
                "storm_warning_cover",
                f"{window.id}:cover",
                Action.COVER_DOWN,
                Priority.SAFETY,
                "storm_warning_cover",
                urgency=95,
                room_id=window.room_id,
                reason_data=data,
            )


@rule("rain_incoming", Priority.SAFETY, description="Rain expected on the weather side")
def rain_incoming(ctx: EvaluationContext) -> Iterable[Recommendation]:
    """Close non rain-safe windows before the rain reaches them."""
    state = ctx.state
    soon = [
        f
        for f in state.forecast
        if state.now <= f.time <= state.now + timedelta(minutes=45)
        and ((f.precipitation or 0.0) > 0.2 or (f.precipitation_probability or 0.0) >= 60.0)
    ]
    raining_now = state.outdoor.is_raining
    if not soon and not raining_now:
        return

    first = soon[0] if soon else None
    if raining_now:
        minutes = 0
    elif first is not None:
        minutes = int((first.time - state.now).total_seconds() / 60)
    else:
        minutes = 30
    wind_bearing = state.outdoor.wind_bearing
    data = {
        "minutes": minutes,
        "probability": round(first.precipitation_probability or 0.0) if first else 100,
        "amount": round((first.precipitation if first else state.outdoor.precipitation) or 0.0, 1),
    }

    for window in state.windows:
        if window.rain_safe:
            continue
        driven_away = (
            wind_bearing is not None
            and bearing_difference(wind_bearing, window.azimuth) > WEATHER_SIDE_DEGREES
        )
        # Rain is driven away from this facade; only warn when wide open.
        if driven_away and (not window.is_open or window.is_tilted):
            continue
        yield make(
            ctx,
            "rain_incoming",
            window.id,
            Action.CLOSE if window.is_open else Action.KEEP_CLOSED,
            Priority.SAFETY,
            "rain_incoming",
            urgency=90 if window.is_open else 40,
            room_id=window.room_id,
            reason_data=dict(data, window=window.name),
            veto=True,
            blocks=OPENING_ACTIONS,
            notify=window.is_open,
            valid_until=state.now + timedelta(hours=2),
        )


@rule("frost_and_heating", Priority.SAFETY, description="Frost outside while the heating runs")
def frost_and_heating(ctx: EvaluationContext) -> Iterable[Recommendation]:
    """Do not heat the street: close long-open windows during frost."""
    state = ctx.state
    if state.outdoor.temperature >= state.preferences.frost_temperature:
        return
    for window in state.windows:
        room = ctx.room_of(window)
        if room is None or not window.is_open:
            continue
        open_minutes = window.open_minutes(state.now) or 0.0
        heating = bool(room.heating_active)
        limit = 6.0 if heating else 12.0
        if open_minutes < limit:
            continue
        yield make(
            ctx,
            "frost_and_heating",
            window.id,
            Action.CLOSE,
            Priority.SAFETY,
            "frost_and_heating",
            urgency=85 if heating else 65,
            room_id=room.id,
            reason_data={
                "outdoor": round(state.outdoor.temperature, 1),
                "minutes": int(open_minutes),
                "heating": heating,
                "room": room.name,
            },
            veto=True,
            blocks=OPENING_ACTIONS,
        )


@rule("away_and_open", Priority.SAFETY, description="Nobody home and a window is open")
def away_and_open(ctx: EvaluationContext) -> Iterable[Recommendation]:
    """Security veto: open windows while away, ground floor especially."""
    state = ctx.state
    if not state.is_away:
        return
    for window in state.windows:
        if not window.is_open:
            yield make(
                ctx,
                "away_and_open",
                window.id,
                Action.KEEP_CLOSED,
                Priority.SAFETY,
                "away_keep_closed",
                urgency=30,
                room_id=window.room_id,
                notify=False,
                veto=True,
                blocks=OPENING_ACTIONS if not window.ok_when_away else frozenset(),
            )
            continue
        if window.ok_when_away and not window.is_ground_floor:
            continue
        yield make(
            ctx,
            "away_and_open",
            window.id,
            Action.CLOSE,
            Priority.SAFETY,
            "away_and_open",
            urgency=95 if window.is_ground_floor else 75,
            room_id=window.room_id,
            reason_data={"window": window.name, "ground_floor": window.is_ground_floor},
            veto=True,
            blocks=OPENING_ACTIONS,
        )


@rule("dark_and_open", Priority.SAFETY, description="Open ground floor window after dark")
def dark_and_open(ctx: EvaluationContext) -> Iterable[Recommendation]:
    """Reminder, not a hard veto: a ground floor window standing open at night."""
    state = ctx.state
    if not state.is_dark:
        return
    for window in state.windows:
        if not window.is_open or not window.is_ground_floor:
            continue
        if state.presence and window.ok_when_away:
            continue
        yield make(
            ctx,
            "dark_and_open",
            window.id,
            Action.CLOSE,
            Priority.SAFETY,
            "dark_and_open",
            urgency=55,
            room_id=window.room_id,
            reason_data={"window": window.name},
            notify=not state.presence,
        )


@rule("outdoor_pm_spike", Priority.SAFETY, description="Outdoor particulate spike")
def outdoor_pm_spike(ctx: EvaluationContext) -> Iterable[Recommendation]:
    """Veto ventilation while the air outside is markedly worse than inside."""
    state = ctx.state
    outdoor_pm = state.outdoor.pm25
    if outdoor_pm is None or outdoor_pm <= state.preferences.pm25_outdoor_threshold:
        return

    indoor_values = [r.pm25 for r in state.rooms if r.pm25 is not None]
    indoor = min(indoor_values) if indoor_values else None
    ratio = outdoor_pm / indoor if indoor and indoor > 1.0 else None
    hard_veto = outdoor_pm > 50.0 or (ratio is not None and ratio >= 2.0)
    if not hard_veto:
        return

    data = {
        "outdoor_pm25": round(outdoor_pm, 1),
        "indoor_pm25": round(indoor, 1) if indoor is not None else None,
        "ratio": round(ratio, 1) if ratio else None,
    }
    yield make(
        ctx,
        "outdoor_pm_spike",
        "global",
        Action.KEEP_CLOSED,
        Priority.SAFETY,
        "outdoor_pm_spike",
        urgency=70,
        reason_data=data,
        veto=True,
        blocks=OPENING_ACTIONS,
    )
    for window in state.windows:
        # Closed windows get an explicit KEEP_CLOSED too, otherwise the
        # per-window entity would sit there blank while a global veto is the
        # actual reason nothing is happening.
        yield make(
            ctx,
            "outdoor_pm_spike",
            window.id,
            Action.CLOSE if window.is_open else Action.KEEP_CLOSED,
            Priority.SAFETY,
            "outdoor_pm_spike_close" if window.is_open else "outdoor_pm_spike",
            urgency=80 if window.is_open else 35,
            room_id=window.room_id,
            reason_data=data,
            veto=True,
            blocks=OPENING_ACTIONS,
            notify=window.is_open,
        )


@rule(
    "outdoor_sensor_implausible",
    Priority.SAFETY,
    description="Outdoor sensor reads far above the weather service",
)
def outdoor_sensor_implausible(ctx: EvaluationContext) -> Iterable[Recommendation]:
    """Most likely the outdoor sensor sits in the sun — degrade the source."""
    state = ctx.state
    if state.outdoor.source == "weather":
        return
    reference = state.forecast_at(state.now)
    if reference is None or not state.sun.is_up:
        return
    delta = state.outdoor.temperature - reference.temperature
    if delta <= 5.0:
        return
    ctx.notes["outdoor_sensor_implausible"] = round(delta, 1)
    yield make(
        ctx,
        "outdoor_sensor_implausible",
        "global",
        Action.NO_ACTION,
        Priority.SAFETY,
        "outdoor_sensor_implausible",
        urgency=20,
        reason_data={
            "sensor": round(state.outdoor.temperature, 1),
            "forecast": round(reference.temperature, 1),
            "delta": round(delta, 1),
        },
        notify=False,
        confidence=0.8,
    )


@rule("data_stale", Priority.SAFETY, description="Core sensor data is stale")
def data_stale(ctx: EvaluationContext) -> Iterable[Recommendation]:
    """Suppress everything but pure safety advice when the data is old."""
    state = ctx.state
    if not state.outdoor.is_stale:
        return
    yield make(
        ctx,
        "data_stale",
        "global",
        Action.NO_ACTION,
        Priority.SAFETY,
        "data_stale",
        urgency=10,
        notify=False,
        veto=True,
        blocks=frozenset(
            {
                Action.OPEN_WIDE,
                Action.OPEN_TILT,
                Action.PURGE,
                Action.CROSS_VENTILATE,
                Action.KEEP_OPEN,
                Action.COVER_DOWN,
                Action.COVER_UP,
                Action.COVER_SLAT,
                Action.FAN_ON,
            }
        ),
        confidence=1.0,
    )


@rule(
    "basement_summer_veto",
    Priority.SAFETY,
    seasons={Season.SUMMER, Season.TRANSITIONAL},
    description="Basement ventilation would condense water on cold walls",
)
def basement_summer_veto(ctx: EvaluationContext) -> Iterable[Recommendation]:
    """Warm humid air on cold cellar walls is how you make a mould farm."""
    state = ctx.state
    outdoor_ah = state.outdoor.absolute_humidity
    if outdoor_ah is None:
        return
    for room in state.rooms:
        if not room.is_basement or room.absolute_humidity is None:
            continue
        if outdoor_ah <= room.absolute_humidity + 0.2:
            continue
        data = {
            "room": room.name,
            "outdoor_ah": round(outdoor_ah, 1),
            "indoor_ah": round(room.absolute_humidity, 1),
            "dew_point": round(state.outdoor.dew_point, 1) if state.outdoor.dew_point else None,
            "indoor_temperature": round(room.temperature, 1) if room.temperature else None,
        }
        yield make(
            ctx,
            "basement_summer_veto",
            room.id,
            Action.KEEP_CLOSED,
            Priority.SAFETY,
            "basement_summer_veto",
            urgency=60,
            room_id=room.id,
            reason_data=data,
            veto=True,
            blocks=OPENING_ACTIONS,
            notify=False,
        )
        for window in ctx.windows_of(room):
            yield make(
                ctx,
                "basement_summer_veto",
                window.id,
                Action.CLOSE if window.is_open else Action.KEEP_CLOSED,
                Priority.SAFETY,
                "basement_summer_veto",
                urgency=75 if window.is_open else 40,
                room_id=room.id,
                reason_data=data,
                veto=True,
                blocks=OPENING_ACTIONS,
                notify=window.is_open,
            )
