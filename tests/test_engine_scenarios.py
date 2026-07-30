"""Run every declarative scenario through the engine."""

from __future__ import annotations

import pytest

from adaptive_ventilation.engine import evaluate
from adaptive_ventilation.engine.state import Action

from .scenario import Scenario, load_all

SCENARIOS = load_all()


def _ids(scenarios: list[Scenario]) -> list[str]:
    return [s.name for s in scenarios]


def test_scenarios_are_present() -> None:
    """Every mandatory scenario from the specification has a fixture."""
    required = {
        "heatwave_35c_day",
        "heatwave_night_flush",
        "tropical_night_multiday",
        "humid_summer_evening",
        "thunderstorm_front",
        "winter_morning_minus5c",
        "winter_dry_air",
        "bathroom_after_shower",
        "co2_bedroom_night",
        "storm_warning_windows_open",
        "pm25_inversion",
        "basement_summer",
        "room_without_sensor",
        "flapping_delta_t_zero",
        "stale_sensor_data",
    }
    assert required <= {s.name for s in SCENARIOS}


@pytest.mark.parametrize("scenario", SCENARIOS, ids=_ids(SCENARIOS))
def test_scenario(scenario: Scenario) -> None:
    result = evaluate(scenario.state, scenario.memory)
    expect = scenario.expect
    triggered = {r.rule_id for r in result.recommendations}
    context = (
        f"\nscenario: {scenario.name}"
        f"\nstate: {result.global_state}"
        f"\nrecommendations: "
        + ", ".join(
            f"{r.rule_id}->{r.target}:{r.action}(u{r.urgency},n{int(r.notify)})"
            for r in result.recommendations
        )
    )

    if expect.global_state:
        assert result.global_state.value == expect.global_state, context
    if expect.global_state_in:
        assert result.global_state.value in expect.global_state_in, context

    for rule_id in expect.rules_present:
        assert rule_id in triggered, f"missing rule {rule_id}{context}"
    for rule_id in expect.rules_absent:
        assert rule_id not in triggered, f"unexpected rule {rule_id}{context}"

    for target, allowed in expect.actions.items():
        matching = [r for r in result.recommendations if r.target == target]
        assert matching, f"no recommendation for target {target}{context}"
        assert matching[0].action.value in allowed, (
            f"target {target} has {matching[0].action.value}, expected one of {allowed}{context}"
        )

    for rule_id in expect.notify_rules:
        pushed = [r for r in result.recommendations if r.rule_id == rule_id and r.notify]
        assert pushed, f"rule {rule_id} should push{context}"
    for rule_id in expect.silent_rules:
        pushed = [r for r in result.recommendations if r.rule_id == rule_id and r.notify]
        assert not pushed, f"rule {rule_id} must stay silent{context}"
    for target in expect.silent_targets:
        pushed = [r for r in result.recommendations if r.target == target and r.notify]
        assert not pushed, f"target {target} must stay silent{context}"
    if expect.no_notifications:
        pushed = [r for r in result.recommendations if r.notify]
        assert not pushed, f"nothing should be pushed{context}"

    for rule_id, limit in expect.max_duration.items():
        for rec in (r for r in result.recommendations if r.rule_id == rule_id):
            assert rec.duration_minutes is not None, f"{rule_id} has no duration{context}"
            assert rec.duration_minutes <= limit, context
    for rule_id, limit in expect.min_duration.items():
        for rec in (r for r in result.recommendations if r.rule_id == rule_id):
            assert rec.duration_minutes is not None and rec.duration_minutes >= limit, context

    for rule_id, limit in expect.max_urgency.items():
        for rec in (r for r in result.recommendations if r.rule_id == rule_id):
            assert rec.urgency <= limit, f"{rule_id} urgency {rec.urgency} > {limit}{context}"
    for rule_id, limit in expect.min_urgency.items():
        for rec in (r for r in result.recommendations if r.rule_id == rule_id):
            assert rec.urgency >= limit, context

    for target, limit in expect.max_confidence.items():
        for rec in (r for r in result.recommendations if r.target == target):
            assert rec.confidence <= limit + 1e-9, context

    for room_id, risk in expect.mold_risk.items():
        assert result.rooms[room_id].mold_risk.value == risk, context

    if expect.cooling_verdict:
        assert result.cooling_budget.verdict_key == expect.cooling_verdict, (
            f"{result.cooling_budget}{context}"
        )


@pytest.mark.parametrize("scenario", SCENARIOS, ids=_ids(SCENARIOS))
def test_scenario_invariants(scenario: Scenario) -> None:
    """Invariants that must hold for *every* scenario."""
    result = evaluate(scenario.state, scenario.memory)

    # At most one main recommendation per target.
    targets = [r.target for r in result.recommendations]
    assert len(targets) == len(set(targets)), f"duplicate targets: {targets}"

    # No window may be told to open and to close at the same time.
    for window in scenario.state.windows:
        actions = {r.action for r in result.recommendations if r.target == window.id}
        opening = actions & {
            Action.OPEN_WIDE,
            Action.OPEN_TILT,
            Action.PURGE,
            Action.CROSS_VENTILATE,
            Action.KEEP_OPEN,
        }
        closing = actions & {Action.CLOSE, Action.KEEP_CLOSED}
        assert not (opening and closing), f"{window.id}: {actions}"

    # Every recommendation carries an explanation and a confidence.
    for rec in result.recommendations:
        assert rec.reason_key
        assert 0.0 <= rec.confidence <= 1.0
        assert 0 <= rec.urgency <= 100

    # Everything the panel needs is populated.
    assert result.diagnostics["engine_version"]
    assert set(result.rooms) == {room.id for room in scenario.state.rooms}
