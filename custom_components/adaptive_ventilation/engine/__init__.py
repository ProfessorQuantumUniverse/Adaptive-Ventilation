"""Adaptive Ventilation decision engine.

Pure Python, no Home Assistant imports anywhere below this package. The single
entry point is::

    result = evaluate(world_state, memory)

which makes the whole thing testable with plain ``pytest`` fixtures and
replayable against recorder history (``scripts/replay.py``).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime, timedelta

from . import arbiter, psychrometrics, schedule, solar, thermal
from .context import EvaluationContext, build_context
from .rules import REGISTRY, run_rules
from .state import (
    Action,
    BuildingProfile,
    CoolingBudget,
    EngineMemory,
    EvaluationResult,
    ForecastHour,
    GlobalState,
    LearnedParameters,
    LearnedRoom,
    Mode,
    MoldRisk,
    OutdoorState,
    Preferences,
    Priority,
    Recommendation,
    RoomAssessment,
    RoomState,
    Season,
    SunState,
    TippingPoints,
    VentilationSchedule,
    WeatherAlert,
    WindowState,
    WorldState,
)

__all__ = [
    "Action",
    "BuildingProfile",
    "CoolingBudget",
    "EngineMemory",
    "EvaluationContext",
    "EvaluationResult",
    "ForecastHour",
    "GlobalState",
    "LearnedParameters",
    "LearnedRoom",
    "Mode",
    "MoldRisk",
    "OutdoorState",
    "Preferences",
    "Priority",
    "Recommendation",
    "RoomAssessment",
    "RoomState",
    "Season",
    "SunState",
    "TippingPoints",
    "VentilationSchedule",
    "WeatherAlert",
    "WindowState",
    "WorldState",
    "evaluate",
    "psychrometrics",
    "rule_ids",
    "simulate_plan",
    "solar",
    "thermal",
]

ENGINE_VERSION = "1.0.0"


def rule_ids() -> list[str]:
    """All registered rule ids, in registration order."""
    return list(REGISTRY)


def evaluate(
    state: WorldState,
    memory: EngineMemory | None = None,
    *,
    disabled_rules: Iterable[str] = (),
) -> EvaluationResult:
    """Run the full decision pipeline for one snapshot of the world."""
    memory = memory if memory is not None else EngineMemory()
    memory.prune(state.now)

    ctx = build_context(state, memory)
    candidates, errors = run_rules(ctx, disabled=disabled_rules)
    active, suppressed = arbiter.arbitrate(ctx, candidates)
    global_state = arbiter.determine_global_state(ctx, active)

    plan = schedule.build_schedule(ctx)
    budget = ctx.cooling_budget
    memory.record_budget(state.now, budget.net_k)
    budget = _with_balance(budget, memory.balance(3))

    diagnostics = {
        "engine_version": ENGINE_VERSION,
        "season": ctx.season.value,
        "rules_evaluated": len(REGISTRY),
        "candidates": len(candidates),
        "active": len(active),
        "suppressed": len(suppressed),
        "rule_errors": errors,
        "is_night": state.is_night,
        "is_dark": state.is_dark,
        "mean_indoor_temperature": _round(state.mean_indoor_temperature),
        "mean_indoor_absolute_humidity": _round(state.mean_indoor_absolute_humidity),
        "outdoor_absolute_humidity": _round(state.outdoor.absolute_humidity),
        "outdoor_enthalpy": _round(state.outdoor.enthalpy),
        "solar_loads": {k: round(v) for k, v in ctx.solar_loads.items()},
        "cover_savings": {k: round(v) for k, v in ctx.cover_savings.items()},
        "night_potential_k": {k: round(v, 2) for k, v in ctx.night_potential.items()},
        "cross_pairs": [[a.id, b.id] for a, b in ctx.cross_pairs],
        "notes": dict(ctx.notes),
        "triggered_rules": sorted({r.rule_id for r in active if r.rule_id}),
    }

    return EvaluationResult(
        now=state.now,
        recommendations=tuple(active),
        suppressed=tuple(suppressed),
        global_state=global_state,
        schedule=plan,
        tipping_points=ctx.tipping,
        cooling_budget=budget,
        rooms=dict(ctx.assessments),
        diagnostics=diagnostics,
    )


def simulate_plan(
    state: WorldState,
    *,
    room_id: str,
    window_ids: Sequence[str] = (),
    open_from: datetime | None = None,
    open_until: datetime | None = None,
    shading_from: datetime | None = None,
    hours: float = 24.0,
    step_minutes: int = 15,
) -> dict[str, object]:
    """What-if simulation behind ``adaptive_ventilation.simulate``.

    Returns both the requested variant and the do-nothing baseline so callers
    can show the difference rather than an absolute curve nobody can judge.
    """
    room = state.room(room_id)
    if room is None:
        raise ValueError(f"unknown room: {room_id}")
    windows = [w for w in state.windows_of(room_id) if not window_ids or w.id in window_ids]
    learned = state.learned.room(room_id)

    variants = {
        "baseline": thermal.VentilationPlan(label="do_nothing"),
        "planned": thermal.VentilationPlan(
            label="planned",
            open_from=open_from,
            open_until=open_until,
            window_ids=tuple(w.id for w in windows),
            shading_from=shading_from,
        ),
    }

    output: dict[str, object] = {"room_id": room_id, "room": room.name}
    for key, plan in variants.items():
        result = thermal.simulate(
            room,
            windows,
            state.forecast,
            state.building,
            learned,
            plan,
            start=state.now,
            hours=hours,
            step_minutes=step_minutes,
        )
        output[key] = {
            "peak_temperature": round(result.peak_temperature, 2),
            "peak_time": result.peak_time.isoformat() if result.peak_time else None,
            "end_temperature": round(result.end_temperature, 2),
            "min_temperature": round(result.min_temperature, 2),
            "curve": [
                {
                    "time": p.time.isoformat(),
                    "indoor": round(p.indoor_temperature, 2),
                    "outdoor": round(p.outdoor_temperature, 2),
                }
                for p in result.points
            ],
        }

    baseline = output["baseline"]
    planned = output["planned"]
    assert isinstance(baseline, dict) and isinstance(planned, dict)
    output["delta_peak_k"] = round(
        float(planned["peak_temperature"]) - float(baseline["peak_temperature"]), 2
    )
    output["delta_end_k"] = round(
        float(planned["end_temperature"]) - float(baseline["end_temperature"]), 2
    )
    return output


def _with_balance(budget: CoolingBudget, balance: float) -> CoolingBudget:
    from dataclasses import replace

    return replace(budget, balance_3d=round(balance, 2))


def _round(value: float | None, digits: int = 2) -> float | None:
    return None if value is None else round(value, digits)


def default_forecast(
    start: datetime, temperatures: Sequence[float], humidity: float = 60.0
) -> tuple[ForecastHour, ...]:
    """Small helper for tests and the replay script."""
    return tuple(
        ForecastHour(time=start + timedelta(hours=i), temperature=t, humidity=humidity)
        for i, t in enumerate(temperatures)
    )
