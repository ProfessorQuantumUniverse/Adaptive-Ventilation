"""The 24 h ventilation plan.

Simulates the reference room forward through the forecast and rates every hour
as good / fair / bad / blocked. The result is both the "best window today"
statement and the data behind the timeline bar in the panel.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta

from . import thermal
from .context import EvaluationContext
from .state import (
    ForecastHour,
    RoomState,
    ScheduleSlot,
    Season,
    SlotQuality,
    VentilationSchedule,
    WindowState,
)

#: Precipitation above which a slot counts as blocked (mm/h).
RAIN_BLOCK_MM = 0.3
#: Wind speed above which a slot counts as blocked (m/s ~ 54 km/h).
WIND_BLOCK_MS = 15.0


def build_schedule(ctx: EvaluationContext, *, hours: int = 24) -> VentilationSchedule:
    """Rate the next ``hours`` hours and pick the best contiguous window."""
    state = ctx.state
    room = _reference_room(state.rooms)
    if room is None or room.temperature is None or not state.forecast:
        return VentilationSchedule(summary_key="no_forecast")

    windows = state.windows_of(room.id) or list(state.windows)
    entries = _hourly(state.forecast, state.now, hours)
    if not entries:
        return VentilationSchedule(summary_key="no_forecast")

    slots: list[ScheduleSlot] = []
    baseline = _simulate_baseline(ctx, room, windows)

    # ``indoor`` follows the plan: it drops in slots we would actually use and
    # otherwise tracks the do-nothing simulation. Without this the per-slot
    # gains would all be computed against the same hot starting temperature and
    # the total would add up to physically impossible numbers.
    indoor = room.temperature
    for entry in entries:
        end = entry.time + timedelta(hours=1)
        drift = _baseline_drift(baseline, entry.time)

        blocked_by = _blocking_reason(ctx, entry)
        if blocked_by is not None:
            indoor += drift
            slots.append(
                ScheduleSlot(
                    start=entry.time,
                    end=end,
                    quality=SlotQuality.BLOCKED,
                    reason_key=f"slot_blocked_{blocked_by}",
                    outdoor_temperature=round(entry.temperature, 1),
                    predicted_indoor_temperature=_round(indoor),
                    blocked_by=blocked_by,
                )
            )
            continue

        if ctx.season is Season.WINTER:
            quality, reason, delta = _rate_winter(ctx, room, entry, indoor)
        else:
            quality, reason, delta = _rate_summer(ctx, room, windows, entry, indoor)

        if quality in (SlotQuality.GOOD, SlotQuality.FAIR) and ctx.season is not Season.WINTER:
            indoor -= delta
        else:
            indoor += drift

        slots.append(
            ScheduleSlot(
                start=entry.time,
                end=end,
                quality=quality,
                reason_key=reason,
                outdoor_temperature=round(entry.temperature, 1),
                predicted_indoor_temperature=_round(indoor),
                delta_k=round(delta, 2),
            )
        )

    winter = ctx.season is Season.WINTER
    best_start, best_end, best_delta = _best_window(slots, winter=winter)
    summary_key, summary_data = _summarise(ctx, room, slots, best_start, best_end, best_delta)

    return VentilationSchedule(
        slots=tuple(slots),
        best_start=best_start,
        best_end=best_end,
        best_delta_k=round(best_delta, 2),
        metric="grams" if winter else "kelvin",
        summary_key=summary_key,
        summary_data=summary_data,
    )


# --------------------------------------------------------------------------
# Rating
# --------------------------------------------------------------------------


def _rate_summer(
    ctx: EvaluationContext,
    room: RoomState,
    windows: Sequence[WindowState],
    entry: ForecastHour,
    indoor: float,
) -> tuple[SlotQuality, str, float]:
    hysteresis = ctx.preferences.delta_t_hysteresis
    delta_t = indoor - entry.temperature
    if delta_t <= hysteresis:
        return SlotQuality.BAD, "slot_warmer_outside", 0.0

    probe = RoomState(
        id=room.id,
        name=room.name,
        temperature=indoor,
        volume_m3=room.volume_m3,
        tau_hours=room.tau_hours,
        is_top_floor=room.is_top_floor,
    )
    rate = thermal.cooling_rate_k_per_h(
        probe,
        list(windows),
        entry.temperature,
        ctx.state.building,
        ctx.learned(room.id),
        cross=len(windows) >= 2,
    )
    gain = min(rate, max(0.0, delta_t))

    humid = False
    if entry.absolute_humidity is not None and room.absolute_humidity is not None:
        humid = entry.absolute_humidity > room.absolute_humidity + 0.8

    if gain >= 0.6 and not humid:
        return SlotQuality.GOOD, "slot_cool_air", gain
    if gain >= 0.25:
        return (
            SlotQuality.FAIR,
            "slot_humid_air" if humid else "slot_marginal",
            gain * (0.6 if humid else 1.0),
        )
    return SlotQuality.BAD, "slot_no_benefit", 0.0


def _rate_winter(
    ctx: EvaluationContext, room: RoomState, entry: ForecastHour, indoor: float
) -> tuple[SlotQuality, str, float]:
    """In winter a slot is good when the air outside is dry and not brutal."""
    if room.absolute_humidity is None or entry.absolute_humidity is None:
        drying = entry.temperature < indoor - 5.0
        margin = 0.0
    else:
        margin = room.absolute_humidity - entry.absolute_humidity
        drying = margin > 0.5

    if not drying:
        return SlotQuality.BAD, "slot_no_drying", 0.0
    if entry.temperature < -5.0:
        return SlotQuality.FAIR, "slot_very_cold", margin
    if (entry.precipitation_probability or 0.0) >= 70.0:
        return SlotQuality.FAIR, "slot_wet", margin
    return SlotQuality.GOOD, "slot_dry_air", margin


def _blocking_reason(ctx: EvaluationContext, entry: ForecastHour) -> str | None:
    if (entry.precipitation or 0.0) >= RAIN_BLOCK_MM:
        return "rain"
    if (entry.wind_speed or 0.0) >= WIND_BLOCK_MS:
        return "wind"
    for alert in ctx.state.weather_alerts:
        if alert.severity in ("severe", "extreme") and alert.is_active(entry.time):
            return "alert"
    return None


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _reference_room(rooms: Sequence[RoomState]) -> RoomState | None:
    candidates = [r for r in rooms if r.temperature is not None and not r.is_basement]
    if not candidates:
        return None
    return min(candidates, key=lambda r: (r.priority, -(r.temperature or 0.0)))


def _hourly(forecast: Sequence[ForecastHour], now: datetime, hours: int) -> list[ForecastHour]:
    horizon = now + timedelta(hours=hours)
    entries = [f for f in forecast if f.time >= now - timedelta(minutes=30) and f.time <= horizon]
    return entries[:hours]


def _simulate_baseline(
    ctx: EvaluationContext, room: RoomState, windows: Sequence[WindowState]
) -> thermal.SimulationResult | None:
    plan = thermal.VentilationPlan(label="do_nothing")
    result = thermal.simulate(
        room,
        list(windows),
        ctx.state.forecast,
        ctx.state.building,
        ctx.learned(room.id),
        plan,
        start=ctx.now,
        hours=24.0,
        step_minutes=30,
    )
    return result if result.points else None


def _baseline_drift(baseline: thermal.SimulationResult | None, moment: datetime) -> float:
    """Temperature change over the next hour if nothing is done."""
    if baseline is None:
        return 0.0
    here = baseline.temperature_at(moment)
    later = baseline.temperature_at(moment + timedelta(hours=1))
    if here is None or later is None:
        return 0.0
    return later - here


#: A winter purge is minutes long, so the recommended winter window is the best
#: couple of hours to do it in, not "any time the air outside is dry".
WINTER_WINDOW_HOURS = 2


def _best_window(
    slots: Sequence[ScheduleSlot], *, winter: bool = False
) -> tuple[datetime | None, datetime | None, float]:
    """The single best ventilation window.

    Summer: the contiguous run with the largest total cooling, because the
    window really is open the whole time and the Kelvin add up.
    Winter: the best short slice by *average* drying margin - summing grams
    over fourteen hours would describe a purge nobody is going to do.
    """
    usable = [
        slot
        for slot in slots
        if slot.quality in (SlotQuality.GOOD, SlotQuality.FAIR) and slot.delta_k > 0.0
    ]
    if not usable:
        return None, None, 0.0

    if winter:
        best_slot = max(usable, key=lambda s: s.delta_k)
        index = slots.index(best_slot)
        run = [
            slot
            for slot in slots[index : index + WINTER_WINDOW_HOURS]
            if slot.quality in (SlotQuality.GOOD, SlotQuality.FAIR)
        ]
        mean = sum(slot.delta_k for slot in run) / len(run)
        return run[0].start, run[-1].end, mean

    best: tuple[datetime | None, datetime | None, float] = (None, None, 0.0)
    current_start: datetime | None = None
    current_end: datetime | None = None
    current_sum = 0.0

    for slot in slots:
        if slot.quality in (SlotQuality.GOOD, SlotQuality.FAIR) and slot.delta_k > 0.0:
            if current_start is None:
                current_start = slot.start
            current_end = slot.end
            current_sum += slot.delta_k
        else:
            if current_start is not None and current_sum > best[2]:
                best = (current_start, current_end, current_sum)
            current_start, current_end, current_sum = None, None, 0.0

    if current_start is not None and current_sum > best[2]:
        best = (current_start, current_end, current_sum)
    return best


def _summarise(
    ctx: EvaluationContext,
    room: RoomState,
    slots: Sequence[ScheduleSlot],
    best_start: datetime | None,
    best_end: datetime | None,
    best_delta: float,
) -> tuple[str, dict[str, object]]:
    data: dict[str, object] = {
        "room": room.name,
        "best_start": best_start.isoformat() if best_start else None,
        "best_end": best_end.isoformat() if best_end else None,
        "expected_k": round(best_delta, 1),
        "good_hours": sum(1 for s in slots if s.quality is SlotQuality.GOOD),
        "blocked_hours": sum(1 for s in slots if s.quality is SlotQuality.BLOCKED),
    }
    if best_start is None:
        if all(s.quality is SlotQuality.BLOCKED for s in slots):
            return "schedule_all_blocked", data
        return "schedule_no_window", data
    if ctx.season is Season.WINTER:
        return "schedule_winter_window", data
    if best_delta < 1.0:
        return "schedule_weak_window", data
    return "schedule_best_window", data


def _round(value: float | None) -> float | None:
    return None if value is None else round(value, 1)
