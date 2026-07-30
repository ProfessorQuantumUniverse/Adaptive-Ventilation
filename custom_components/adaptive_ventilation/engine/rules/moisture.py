"""Humidity, mould and drying rules.

All of these compare **absolute** humidity. The relative value only decides
whether it feels clammy; whether ventilation removes water is decided by g/m³.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Iterable

from ..context import EvaluationContext
from ..state import (
    Action,
    MoldRisk,
    Priority,
    Recommendation,
    Season,
    clamp,
)
from . import make, rule
from .helpers import purge_action, purge_windows

#: A room counts as "spiking" when its absolute humidity exceeds the flat
#: average by this much (g/m³) — showering adds 2-4 g/m³ within minutes.
SPIKE_MARGIN = 1.8


@rule("humidity_spike", Priority.HEALTH, description="Sudden moisture load in a room")
def humidity_spike(ctx: EvaluationContext) -> Iterable[Recommendation]:
    """Shower, cooking, laundry: get it out before it spreads through the flat."""
    state = ctx.state
    mean_ah = state.mean_indoor_absolute_humidity
    outdoor_ah = state.outdoor.absolute_humidity
    for room in state.rooms:
        if room.absolute_humidity is None or room.humidity is None:
            continue
        above_average = mean_ah is not None and room.absolute_humidity > mean_ah + SPIKE_MARGIN
        very_humid = room.humidity >= 70.0
        if not (room.is_moisture_source and (above_average or very_humid)):
            continue
        if outdoor_ah is not None and outdoor_ah >= room.absolute_humidity - 0.2:
            # Ventilating would not remove water — leave it to the fan / heating.
            yield make(
                ctx,
                "humidity_spike_blocked",
                room.id,
                Action.FAN_ON if room.fan_available else Action.NO_ACTION,
                Priority.HEALTH,
                "humidity_spike_blocked",
                urgency=40,
                room_id=room.id,
                reason_data={
                    "room": room.name,
                    "indoor_ah": round(room.absolute_humidity, 1),
                    "outdoor_ah": round(outdoor_ah, 1),
                },
                notify=False,
            )
            continue

        windows, cross = purge_windows(ctx, room)
        if not windows:
            continue
        minutes = ctx.purge_minutes(room, cross=cross)
        for window in windows:
            yield make(
                ctx,
                "humidity_spike",
                window.id,
                purge_action(cross),
                Priority.HEALTH,
                "humidity_spike",
                urgency=int(clamp(60 + (room.humidity - 65.0), 55, 90)),
                room_id=room.id,
                reason_data={
                    "room": room.name,
                    "humidity": round(room.humidity, 0),
                    "indoor_ah": round(room.absolute_humidity, 1),
                    "outdoor_ah": round(outdoor_ah, 1) if outdoor_ah is not None else None,
                    "minutes": minutes,
                    "window": window.name,
                },
                duration_minutes=minutes,
                confidence=room.confidence,
                expected_benefit=f"-{round((room.absolute_humidity - (outdoor_ah or 0)) * 0.6, 1)} g/m3",
                valid_until=ctx.now + timedelta(minutes=90),
            )


@rule("mold_risk", Priority.HEALTH, description="Surface humidity above the mould threshold")
def mold_risk(ctx: EvaluationContext) -> Iterable[Recommendation]:
    """Above 80 % RH on the coldest wall, mould grows (DIN 4108-2)."""
    outdoor_ah = ctx.outdoor.absolute_humidity
    for room in ctx.rooms:
        assessment = ctx.assessment(room.id)
        if assessment.mold_risk in (MoldRisk.NONE, MoldRisk.LOW):
            continue
        drying = outdoor_ah is not None and room.absolute_humidity is not None and (
            outdoor_ah < room.absolute_humidity - 0.3
        )
        windows, cross = purge_windows(ctx, room)
        data = {
            "room": room.name,
            "surface_humidity": assessment.surface_humidity,
            "surface_temperature": assessment.wall_surface_temperature,
            "risk": assessment.mold_risk.value,
        }
        if not drying or not windows:
            yield make(
                ctx,
                "mold_risk_heat",
                room.id,
                Action.NO_ACTION,
                Priority.HEALTH,
                "mold_risk_heat",
                urgency=55,
                room_id=room.id,
                reason_data=data,
                confidence=room.confidence,
            )
            continue

        minutes = ctx.purge_minutes(room, cross=cross)
        for window in windows:
            yield make(
                ctx,
                "mold_risk",
                window.id,
                purge_action(cross),
                Priority.HEALTH,
                "mold_risk",
                urgency=70 if assessment.mold_risk is MoldRisk.CONDENSATION else 60,
                room_id=room.id,
                reason_data=dict(data, minutes=minutes),
                duration_minutes=minutes,
                confidence=room.confidence,
            )


@rule("laundry_drying", Priority.COMFORT, description="Laundry drying in the room")
def laundry_drying(ctx: EvaluationContext) -> Iterable[Recommendation]:
    """Keep ventilating while outdoor air is drier — laundry adds ~2 l of water."""
    outdoor_ah = ctx.outdoor.absolute_humidity
    if outdoor_ah is None:
        return
    for room in ctx.rooms:
        if not room.laundry_drying or room.absolute_humidity is None:
            continue
        if outdoor_ah >= room.absolute_humidity - 0.5:
            continue
        windows, cross = purge_windows(ctx, room)
        if not windows:
            continue
        for window in windows:
            yield make(
                ctx,
                "laundry_drying",
                window.id,
                Action.OPEN_TILT if window.tilt_capable and ctx.season is not Season.WINTER else purge_action(cross),
                Priority.COMFORT,
                "laundry_drying",
                urgency=45,
                room_id=room.id,
                reason_data={
                    "room": room.name,
                    "indoor_ah": round(room.absolute_humidity, 1),
                    "outdoor_ah": round(outdoor_ah, 1),
                },
                duration_minutes=None,
                confidence=room.confidence,
                notify=False,
            )


@rule(
    "dry_air",
    Priority.COMFORT,
    seasons={Season.WINTER},
    description="Indoor air already very dry",
)
def dry_air(ctx: EvaluationContext) -> Iterable[Recommendation]:
    """In winter the classic mistake is over-ventilating into 25 % RH."""
    prefs = ctx.preferences
    for room in ctx.rooms:
        if room.absolute_humidity is None or room.humidity is None:
            continue
        if room.absolute_humidity >= prefs.dry_air_absolute or room.humidity >= 32.0:
            continue
        if room.co2 is not None and room.co2 > prefs.co2_urgent:
            continue  # health beats comfort
        yield make(
            ctx,
            "dry_air",
            room.id,
            Action.KEEP_CLOSED,
            Priority.COMFORT,
            "dry_air",
            urgency=40,
            room_id=room.id,
            reason_data={
                "room": room.name,
                "humidity": round(room.humidity, 0),
                "indoor_ah": round(room.absolute_humidity, 1),
            },
            confidence=room.confidence,
            notify=False,
        )
        for window in ctx.windows_of(room):
            if window.is_open:
                yield make(
                    ctx,
                    "dry_air_close",
                    window.id,
                    Action.CLOSE,
                    Priority.COMFORT,
                    "dry_air_close",
                    urgency=45,
                    room_id=room.id,
                    reason_data={"room": room.name, "humidity": round(room.humidity, 0)},
                    confidence=room.confidence,
                )


@rule(
    "unheated_room",
    Priority.HEALTH,
    seasons={Season.WINTER, Season.TRANSITIONAL},
    description="Cold unheated room next to warm rooms",
)
def unheated_room(ctx: EvaluationContext) -> Iterable[Recommendation]:
    """Warm humid air migrating into a cold room condenses on its walls."""
    warm_rooms = [
        r for r in ctx.rooms if r.temperature is not None and r.temperature >= 19.0
    ]
    if not warm_rooms:
        return
    for room in ctx.rooms:
        if not room.is_unheated or room.temperature is None:
            continue
        warmest = max(warm_rooms, key=lambda r: r.temperature or 0.0)
        if (warmest.temperature or 0.0) - room.temperature < 3.0:
            continue
        assessment = ctx.assessment(room.id)
        if assessment.mold_risk is MoldRisk.NONE and (room.humidity or 0.0) < 60.0:
            continue
        yield make(
            ctx,
            "unheated_room",
            room.id,
            Action.NO_ACTION,
            Priority.HEALTH,
            "unheated_room",
            urgency=50,
            room_id=room.id,
            reason_data={
                "room": room.name,
                "temperature": round(room.temperature, 1),
                "warm_room": warmest.name,
                "warm_temperature": round(warmest.temperature or 0.0, 1),
                "humidity": round(room.humidity, 0) if room.humidity else None,
            },
            confidence=room.confidence,
        )
