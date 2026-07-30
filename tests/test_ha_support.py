"""Storage round-trips, presentation, messages, calibration maths and config-flow helpers."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import voluptuous as vol

from adaptive_ventilation import config_flow, messages, presentation, storage
from adaptive_ventilation.calibration import (
    Sample,
    fit_air_changes,
    fit_night_cooling,
    fit_tau,
    linear_regression,
    summarise,
)
from adaptive_ventilation.const import (
    CONF_AZIMUTH,
    CONF_CONTACT_SENSOR,
    CONF_COVER_AUTO,
    CONF_ROOM,
    CONF_ROOM_NAME,
    CONF_TARGET_MAX,
    CONF_TARGET_MIN,
    CONF_WINDOW_NAME,
)
from adaptive_ventilation.engine import evaluate
from adaptive_ventilation.engine.state import (
    Action,
    EngineMemory,
    LearnedParameters,
    LearnedRoom,
    OutdoorState,
    RoomState,
    SunState,
    WindowState,
    WorldState,
)

from .conftest import make_forecast

NOW = datetime(2025, 7, 15, 20, 0, tzinfo=UTC)


def _world() -> WorldState:
    return WorldState(
        now=NOW,
        outdoor=OutdoorState.create(17.0, 65.0, pressure=1013.0),
        rooms=(RoomState.create("living", "Living room", 26.5, 50.0, volume_m3=60.0, priority=1),),
        windows=(WindowState(id="w1", name="South", room_id="living", azimuth=190.0, area_m2=2.2),),
        forecast=make_forecast(NOW, [17.0] * 30, 65.0),
        sun=SunState(azimuth=300.0, elevation=-10.0),
    )


# --------------------------------------------------------------------------
# Storage
# --------------------------------------------------------------------------


def test_memory_round_trip() -> None:
    memory = EngineMemory()
    memory.target("w1").action = Action.OPEN_WIDE
    memory.target("w1").since = NOW
    memory.target("w1").contradictions = 1
    memory.cooldowns["night_flush:w1"] = NOW + timedelta(hours=1)
    memory.snooze("co2_high:w1", NOW + timedelta(minutes=30))
    memory.ignore_today("mold_risk:bath", NOW)
    memory.hold("w2", NOW)
    memory.note_purge("living", NOW)
    memory.note_push(NOW)
    memory.record_budget(NOW, -1.4)

    restored = storage.deserialize_memory(storage.serialize_memory(memory))

    assert restored.target("w1").action is Action.OPEN_WIDE
    assert restored.target("w1").since == NOW
    assert restored.cooldowns["night_flush:w1"] == NOW + timedelta(hours=1)
    assert restored.is_snoozed("co2_high:w1", NOW)
    assert restored.is_ignored_today("mold_risk:bath", NOW)
    assert restored.is_held("w2", NOW)
    assert restored.hours_since_purge("living", NOW + timedelta(hours=2)) == pytest.approx(2.0)
    assert restored.pushes_today == 1
    assert restored.balance(3) == pytest.approx(-1.4)


def test_memory_from_a_newer_schema_is_discarded() -> None:
    restored = storage.deserialize_memory({"schema": 99, "pushes_today": 5})
    assert restored.pushes_today == 0


def test_memory_survives_corrupt_values() -> None:
    restored = storage.deserialize_memory(
        {
            "schema": 1,
            "targets": {"w1": {"action": "nonsense", "since": "not a date"}},
            "cooldowns": {"x": "also not a date"},
        }
    )
    assert restored.target("w1").action is Action.NO_ACTION
    assert restored.cooldowns == {}


def test_learned_round_trip_and_override_protection() -> None:
    learned = LearnedParameters(
        rooms={
            "living": LearnedRoom(
                tau_hours=72.0,
                night_cooling_k_per_h=1.1,
                samples=8,
                confidence=0.7,
                updated=NOW,
                overridden=frozenset({"tau_hours"}),
            )
        }
    )
    restored = storage.deserialize_learned(storage.serialize_learned(learned))
    assert restored.room("living").tau_hours == 72.0
    assert restored.room("living").overridden == frozenset({"tau_hours"})

    # A new calibration result must not overwrite a manual override.
    merged = storage.merge_learned_room(
        restored.room("living"), {"tau_hours": 40.0, "night_cooling_k_per_h": 1.6}, samples=1
    )
    assert merged.tau_hours == 72.0
    assert merged.night_cooling_k_per_h == 1.6
    assert merged.samples == 9


def test_summarise_learned_marks_when_a_value_is_actually_used() -> None:
    learned = LearnedParameters(
        rooms={
            "a": LearnedRoom(tau_hours=50.0, samples=1),
            "b": LearnedRoom(tau_hours=50.0, samples=5),
        }
    )
    summary = summarise(learned)
    assert summary["a"]["in_use"] is False
    assert summary["b"]["in_use"] is True


# --------------------------------------------------------------------------
# Presentation
# --------------------------------------------------------------------------


def test_state_strings_never_exceed_the_home_assistant_limit() -> None:
    result = evaluate(_world(), EngineMemory())
    text = presentation.primary_text(result)
    assert len(text) <= 255


def test_display_lines_are_short_and_populated() -> None:
    world = _world()
    result = evaluate(world, EngineMemory())
    line1, line2, line3 = presentation.display_lines(result, world)
    for line in (line1, line2, line3):
        assert 0 < len(line) <= presentation.MAX_LINE_LENGTH
    # Line 3 carries the numbers an external display needs.
    assert "C" in line3


def test_compact_payload_is_valid_json_and_small() -> None:
    import json

    world = _world()
    result = evaluate(world, EngineMemory())
    payload = presentation.compact_payload(result, world)
    parsed = json.loads(payload)
    assert parsed["s"] == result.global_state.value
    assert len(payload) < 255


def test_recommendation_attributes_carry_the_explanation() -> None:
    result = evaluate(_world(), EngineMemory())
    rec = result.primary
    assert rec is not None
    attributes = presentation.recommendation_attributes(rec)
    assert attributes["reason"]
    assert attributes["recommendation_id"] == rec.id
    assert isinstance(attributes["numbers"], dict)


def test_recommendation_attributes_handle_none() -> None:
    assert presentation.recommendation_attributes(None)["reason"] is None


def test_schedule_payload_is_json_friendly() -> None:
    import json

    result = evaluate(_world(), EngineMemory())
    json.dumps(presentation.schedule_payload(result))


# --------------------------------------------------------------------------
# Messages
# --------------------------------------------------------------------------


def test_every_reason_key_used_by_the_rules_has_a_template() -> None:
    """A recommendation without a sentence is a recommendation nobody trusts."""
    from pathlib import Path
    import re

    rules_dir = Path(__file__).parents[1] / "custom_components" / "adaptive_ventilation" / "engine"
    used: set[str] = set()
    for path in rules_dir.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        used.update(re.findall(r'reason_key="([a-z0-9_]+)"', source))
        used.update(re.findall(r'\n\s+"([a-z0-9_]+)",\s*\n\s+urgency=', source))

    missing = sorted(k for k in used if k not in messages.TEMPLATES)
    assert not missing, f"missing message templates: {missing}"


def test_all_templates_exist_in_both_languages() -> None:
    for key, entry in messages.TEMPLATES.items():
        assert "en" in entry, key
        assert "de" in entry, key


def test_render_tolerates_missing_placeholders() -> None:
    text = messages.render("co2_high", {"room": "Bedroom"})
    assert "Bedroom" in text
    assert "?" in text  # co2 and minutes were not supplied


def test_render_falls_back_for_an_unknown_key() -> None:
    assert messages.render("does_not_exist", {}) == "does not exist"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("de", "de"), ("de-DE", "de"), ("en-GB", "en"), ("fr", "en"), (None, "en")],
)
def test_language_resolution(raw: str | None, expected: str) -> None:
    assert messages.resolve_language(raw) == expected


# --------------------------------------------------------------------------
# Calibration maths
# --------------------------------------------------------------------------


def _series(start: datetime, values: list[float], step_minutes: int = 15) -> list[Sample]:
    return [
        Sample(time=start + timedelta(minutes=i * step_minutes), value=value)
        for i, value in enumerate(values)
    ]


def test_linear_regression_recovers_a_known_line() -> None:
    xs = [0.0, 1.0, 2.0, 3.0, 4.0]
    ys = [2.0 + 3.0 * x for x in xs]
    slope, intercept = linear_regression(xs, ys)
    assert slope == pytest.approx(3.0)
    assert intercept == pytest.approx(2.0)


def test_linear_regression_rejects_degenerate_input() -> None:
    assert linear_regression([1.0], [1.0]) == (None, None)
    assert linear_regression([2.0, 2.0, 2.0], [1.0, 2.0, 3.0]) == (None, None)


def test_fit_tau_recovers_a_synthetic_time_constant() -> None:
    """Feed the fitter an exact exponential decay with tau = 40 h."""
    import math

    tau = 40.0
    outdoor_value = 10.0
    indoor = [outdoor_value + 12.0 * math.exp(-(i * 0.25) / tau) for i in range(40)]
    inside = _series(NOW, indoor)
    outside = _series(NOW, [outdoor_value] * 40)

    fitted, samples = fit_tau(inside, outside, [])
    assert samples == 1
    assert fitted == pytest.approx(tau, rel=0.05)


def test_fit_tau_ignores_periods_with_an_open_window() -> None:
    inside = _series(NOW, [24.0 - 0.1 * i for i in range(40)])
    outside = _series(NOW, [10.0] * 40)
    whole_range = [(NOW, NOW + timedelta(hours=20))]
    assert fit_tau(inside, outside, whole_range) == (None, 0)


def test_fit_night_cooling_normalises_to_ten_kelvin() -> None:
    # 2 K drop over 2 h at a mean ΔT of 10 K -> 1.0 K/h normalised.
    inside = _series(NOW, [26.0, 25.5, 25.0, 24.5, 24.0], step_minutes=30)
    outside = _series(NOW, [15.0] * 5, step_minutes=30)
    period = [(NOW, NOW + timedelta(hours=2))]
    rate, samples = fit_night_cooling(inside, outside, period)
    assert samples == 1
    assert rate == pytest.approx(1.0, abs=0.15)


def test_fit_air_changes_recovers_the_rate_from_a_co2_decay() -> None:
    from adaptive_ventilation.engine import psychrometrics as psy

    values = [psy.co2_after_ventilation(1400.0, 420.0, 4.0, i * 0.05) for i in range(7)]
    co2 = _series(NOW, values, step_minutes=3)
    period = [(NOW, NOW + timedelta(minutes=20))]
    rate, samples = fit_air_changes(co2, period, 40.0)
    assert samples == 1
    assert rate == pytest.approx(4.0, rel=0.05)


def test_fit_air_changes_rejects_a_rising_curve() -> None:
    co2 = _series(NOW, [500.0, 600.0, 700.0], step_minutes=5)
    assert fit_air_changes(co2, [(NOW, NOW + timedelta(minutes=15))], 40.0) == (None, 0)


def test_outlier_is_dropped_from_the_robust_mean() -> None:
    from adaptive_ventilation.calibration import _robust_mean

    assert _robust_mean([1.0, 1.1, 0.9, 1.05, 40.0]) == pytest.approx(1.01, abs=0.05)


# --------------------------------------------------------------------------
# Config flow helpers
# --------------------------------------------------------------------------


def test_flatten_merges_the_advanced_section() -> None:
    flat = config_flow._flatten(
        {"name": "Bedroom", "advanced": {"is_bedroom": True, "target_min": None}}
    )
    assert flat == {"name": "Bedroom", "is_bedroom": True}


def test_compass_preset_beats_the_azimuth_field() -> None:
    assert config_flow._resolve_azimuth({"orientation": "s", CONF_AZIMUTH: 12.0}) == 180.0
    assert config_flow._resolve_azimuth({"orientation": "custom", CONF_AZIMUTH: 12.0}) == 12.0
    assert config_flow._resolve_azimuth({CONF_AZIMUTH: 95.0}) == 95.0


@pytest.mark.parametrize(
    ("azimuth", "preset"), [(0, "n"), (90, "e"), (180, "s"), (270, "w"), (137, "custom")]
)
def test_azimuth_maps_back_to_a_preset(azimuth: float, preset: str) -> None:
    assert config_flow._azimuth_to_preset(azimuth) == preset


def test_room_validation() -> None:
    assert config_flow._validate_room({CONF_ROOM_NAME: "Bedroom"}) == {}
    assert "base" in config_flow._validate_room(
        {CONF_ROOM_NAME: "Bedroom", CONF_TARGET_MIN: 25, CONF_TARGET_MAX: 22}
    )
    assert CONF_ROOM_NAME in config_flow._validate_room({})


def test_window_validation() -> None:
    valid = {
        CONF_WINDOW_NAME: "South",
        CONF_ROOM: "abc",
        CONF_CONTACT_SENSOR: "binary_sensor.x",
    }
    assert config_flow._validate_window(valid) == {}
    assert "base" in config_flow._validate_window({**valid, CONF_COVER_AUTO: True})
    assert CONF_CONTACT_SENSOR in config_flow._validate_window(
        {CONF_WINDOW_NAME: "South", CONF_ROOM: "abc"}
    )


# --------------------------------------------------------------------------
# Notification budget
# --------------------------------------------------------------------------


def test_push_budget_reserves_headroom_for_health() -> None:
    """A shading tip in the morning must not eat the whole day's budget."""
    from adaptive_ventilation.engine.state import Priority

    memory = EngineMemory()
    limit = 6

    # Comfort spends until only the reserve is left.
    allowed = 0
    while memory.may_push(Priority.COMFORT, NOW, limit):
        memory.register_push(f"comfort:{allowed}", f"w{allowed}", NOW, 60)
        allowed += 1
    assert allowed == 4

    # Health can still get through, safety always.
    assert memory.may_push(Priority.HEALTH, NOW, limit)
    assert memory.may_push(Priority.SAFETY, NOW, limit)

    memory.register_push("health:1", "w1", NOW, 60)
    memory.register_push("health:2", "w2", NOW, 60)
    assert not memory.may_push(Priority.HEALTH, NOW, limit)
    assert memory.may_push(Priority.SAFETY, NOW, limit)


def test_register_push_sets_a_durable_cooldown() -> None:
    """The cooldown lives in the persisted memory, so a restart does not re-notify."""
    memory = EngineMemory()
    memory.register_push("night_flush:w1", "w1", NOW, 60)

    assert memory.cooldowns["night_flush:w1"] == NOW + timedelta(minutes=60)
    assert memory.target("w1").last_notified == NOW
    assert memory.pushes_today == 1

    restored = storage.deserialize_memory(storage.serialize_memory(memory))
    assert restored.cooldowns["night_flush:w1"] == NOW + timedelta(minutes=60)


def test_budget_resets_on_a_new_day() -> None:
    from adaptive_ventilation.engine.state import Priority

    memory = EngineMemory()
    for i in range(6):
        memory.register_push(f"x{i}", f"w{i}", NOW, 60)
    assert not memory.may_push(Priority.HEALTH, NOW, 6)
    assert memory.may_push(Priority.HEALTH, NOW + timedelta(days=1), 6)


def test_preferences_accept_a_string_clock() -> None:
    """YAML scenario files and the replay map hand over plain strings."""
    from adaptive_ventilation.engine.state import Preferences

    prefs = Preferences(quiet_hours_start="23:30", quiet_hours_end="06:00")  # type: ignore[arg-type]
    assert prefs.quiet_hours_start.hour == 23
    assert prefs.quiet_hours_end.hour == 6
    # And the comparison it exists for actually works.
    assert prefs.in_quiet_hours(NOW.replace(hour=1))
    assert not prefs.in_quiet_hours(NOW.replace(hour=12))


# --------------------------------------------------------------------------
# Config flow schemas
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_every_options_step_schema_validates_its_own_defaults() -> None:
    """A unitless number selector used to reject the whole form.

    ``unit_of_measurement=None`` is not a valid NumberSelectorConfig, so any
    step containing a unitless field (g-value, counts, factors) failed
    validation. Three of the five steps were affected and none were covered.
    """
    import types

    from adaptive_ventilation import config_flow as cf

    captured: dict[str, Any] = {}

    class Harness(cf.AdaptiveVentilationOptionsFlow):
        def __init__(self) -> None:
            self._entry = types.SimpleNamespace(options={}, entry_id="x")
            self.hass = types.SimpleNamespace(
                services=types.SimpleNamespace(
                    async_services=lambda: {"notify": {"mobile_app_phone": None}}
                )
            )

        @property
        def config_entry(self):  # type: ignore[override]
            return self._entry

        def async_show_form(self, *, step_id, data_schema, **kwargs):
            captured[step_id] = data_schema
            return {"step_id": step_id}

    flow = Harness()
    steps = ("sources", "building", "notifications", "weights", "thresholds")
    for step in steps:
        await getattr(flow, f"async_step_{step}")()

    assert set(captured) == set(steps)
    for schema in captured.values():
        defaults = {}
        for key in schema.schema:
            default = getattr(key, "default", None)
            if default is None or default is vol.UNDEFINED:
                continue
            defaults[str(key)] = default()
        schema(defaults)  # raises if the step is unusable


def test_unitless_number_selector_is_accepted() -> None:
    from adaptive_ventilation import config_flow as cf

    schema = vol.Schema({vol.Optional("g_value", default=0.6): cf._number(0.1, 0.9, 0.05)})
    assert schema({"g_value": 0.6}) == {"g_value": 0.6}

    with_unit = vol.Schema({vol.Optional("v", default=1.0): cf._number(0, 10, 1, "m³")})
    assert with_unit({"v": 2.0}) == {"v": 2.0}
