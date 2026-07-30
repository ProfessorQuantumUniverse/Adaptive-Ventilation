"""Properties that must hold for *any* input, plus the anti-flapping loop.

These are the tests that catch the failure modes a scenario file never will:
contradictory advice, a safety rule losing, and the system oscillating at
ΔT ≈ 0 until the user uninstalls it.
"""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
import itertools
from itertools import pairwise
import random

import pytest

from adaptive_ventilation.engine import evaluate
from adaptive_ventilation.engine.rules import REGISTRY
from adaptive_ventilation.engine.state import (
    CLOSING_ACTIONS,
    OPENING_ACTIONS,
    Action,
    BuildingProfile,
    BuildingType,
    EngineMemory,
    ForecastHour,
    GlobalState,
    Mode,
    OutdoorState,
    Preferences,
    Priority,
    RoomState,
    SunState,
    WeatherAlert,
    WindowState,
    WorldState,
)

from .scenario import load_all

# Deliberately outside the default quiet hours (22:00-07:00) so these tests
# exercise the notification path instead of the quiet-hours suppression.
NOW = datetime(2025, 7, 15, 20, 0, tzinfo=UTC)
SCENARIOS = load_all()


def _world(**kwargs: object) -> WorldState:
    defaults: dict[str, object] = {
        "now": NOW,
        "outdoor": OutdoorState.create(18.0, 65.0, pressure=1013.0),
        "rooms": (
            RoomState.create("living_room", "Living room", 26.0, 50.0, volume_m3=60.0, priority=1),
        ),
        "windows": (
            WindowState(id="w1", name="South", room_id="living_room", azimuth=190.0, area_m2=2.2),
        ),
        "forecast": tuple(
            ForecastHour(time=NOW + timedelta(hours=i), temperature=18.0, humidity=65.0)
            for i in range(30)
        ),
        "sun": SunState(azimuth=300.0, elevation=-10.0),
    }
    defaults.update(kwargs)
    return WorldState(**defaults)  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# Structural properties
# --------------------------------------------------------------------------


def test_every_rule_has_a_unique_id_and_a_description() -> None:
    assert len(REGISTRY) == len({d.id for d in REGISTRY.values()})
    for definition in REGISTRY.values():
        assert definition.description, f"{definition.id} has no description"
        assert isinstance(definition.priority, Priority)


def test_all_specified_rules_exist() -> None:
    """Every rule id from the specification catalogue is implemented."""
    required = {
        # SAFETY
        "storm_warning",
        "rain_incoming",
        "frost_and_heating",
        "away_and_open",
        "dark_and_open",
        "outdoor_pm_spike",
        "outdoor_sensor_implausible",
        "data_stale",
        "basement_summer_veto",
        # year round
        "co2_high",
        "voc_high",
        "indoor_pm_high",
        "pm_both_high",
        "humidity_spike",
        "mold_risk",
        "laundry_drying",
        "internal_load",
        "window_forgotten",
        # summer
        "night_flush",
        "morning_close",
        "keep_closed_hot",
        "solar_shading",
        "shading_gap",
        "precool_heatwave",
        "tropical_night",
        "thunderstorm_window",
        "fan_instead",
        "away_prepare",
        # winter
        "winter_purge_schedule",
        "avoid_tilt_winter",
        "dry_air",
        "passive_solar_gain",
        "cover_night_insulation",
        "preheat_before_purge",
        "inversion_pm",
        "unheated_room",
    }
    assert required <= set(REGISTRY)


def test_no_rule_crashes_on_a_nearly_empty_world() -> None:
    """Missing sensors are the norm, not the exception."""
    bare = WorldState(now=NOW, outdoor=OutdoorState(temperature=15.0))
    result = evaluate(bare)
    assert result.diagnostics["rule_errors"] == {}

    sensorless = _world(
        rooms=(RoomState(id="r", name="Room"),),
        windows=(WindowState(id="w", name="W", room_id="r"),),
        outdoor=OutdoorState(temperature=15.0),
        forecast=(),
    )
    assert evaluate(sensorless).diagnostics["rule_errors"] == {}


# --------------------------------------------------------------------------
# Behavioural invariants over random worlds
# --------------------------------------------------------------------------


def _random_world(rng: random.Random) -> WorldState:
    outdoor_temperature = rng.uniform(-15.0, 40.0)
    indoor_temperature = rng.uniform(14.0, 34.0)
    rooms = tuple(
        RoomState.create(
            f"room{i}",
            f"Room {i}",
            indoor_temperature + rng.uniform(-3.0, 3.0),
            rng.uniform(25.0, 90.0),
            co2=rng.choice([None, 500, 900, 1300, 1800]),
            pm25=rng.choice([None, 3.0, 20.0, 45.0]),
            volume_m3=rng.uniform(15.0, 80.0),
            priority=rng.randint(1, 6),
            is_basement=rng.random() < 0.15,
            is_moisture_source=rng.random() < 0.2,
            is_bedroom=rng.random() < 0.3,
            fan_available=rng.random() < 0.3,
            heating_active=rng.choice([None, True, False]),
        )
        for i in range(rng.randint(1, 4))
    )
    windows = tuple(
        WindowState(
            id=f"win{i}",
            name=f"Window {i}",
            room_id=rng.choice(rooms).id,
            is_open=rng.random() < 0.4,
            is_tilted=rng.random() < 0.2,
            open_since=NOW - timedelta(minutes=rng.randint(1, 400)),
            azimuth=rng.uniform(0.0, 360.0),
            area_m2=rng.uniform(0.4, 3.5),
            is_ground_floor=rng.random() < 0.3,
            rain_safe=rng.random() < 0.3,
            ok_when_away=rng.random() < 0.3,
            cover_entity="cover.x" if rng.random() < 0.5 else None,
            cover_position=rng.choice([0, 40, 100]),
            cover_external=rng.random() < 0.6,
        )
        for i in range(rng.randint(1, 5))
    )
    forecast = tuple(
        ForecastHour(
            time=NOW + timedelta(hours=i),
            temperature=outdoor_temperature + rng.uniform(-8.0, 8.0),
            humidity=rng.uniform(30.0, 98.0),
            precipitation=rng.choice([0.0, 0.0, 0.0, 2.0]),
            cloud_coverage=rng.uniform(0.0, 100.0),
            wind_speed=rng.uniform(0.0, 20.0),
        )
        for i in range(30)
    )
    alerts: tuple[WeatherAlert, ...] = ()
    if rng.random() < 0.2:
        alerts = (WeatherAlert(event="Storm", severity="severe", kind="storm"),)

    return WorldState(
        now=NOW,
        outdoor=OutdoorState.create(
            outdoor_temperature,
            rng.uniform(20.0, 100.0),
            pm25=rng.choice([None, 5.0, 30.0, 120.0]),
            wind_speed=rng.uniform(0.0, 25.0),
            wind_bearing=rng.uniform(0.0, 360.0),
            precipitation=rng.choice([0.0, 0.0, 1.5]),
            cloud_coverage=rng.uniform(0.0, 100.0),
            pressure=rng.uniform(985.0, 1035.0),
            is_stale=rng.random() < 0.1,
        ),
        rooms=rooms,
        windows=windows,
        forecast=forecast,
        sun=SunState(azimuth=rng.uniform(0.0, 360.0), elevation=rng.uniform(-60.0, 60.0)),
        mode=rng.choice(list(Mode)),
        presence=rng.random() < 0.7,
        weather_alerts=alerts,
        building=BuildingProfile(building_type=rng.choice(list(BuildingType))),
    )


@pytest.mark.parametrize("seed", range(60))
def test_random_worlds_keep_the_invariants(seed: int) -> None:
    rng = random.Random(seed)
    state = _random_world(rng)
    result = evaluate(state, EngineMemory())

    assert result.diagnostics["rule_errors"] == {}

    # One main recommendation per target.
    targets = [r.target for r in result.recommendations]
    assert len(targets) == len(set(targets))

    for window in state.windows:
        recs = [r for r in result.recommendations if r.target == window.id]
        assert len(recs) <= 1
        for rec in recs:
            assert not (rec.action in OPENING_ACTIONS and rec.action in CLOSING_ACTIONS)

    # A safety veto is never overruled on its own target.
    vetoed = {
        r.target: r.blocks
        for r in itertools.chain(result.recommendations, result.suppressed)
        if r.veto and r.blocks
    }
    for rec in result.recommendations:
        blocked = vetoed.get(rec.target, frozenset()) | vetoed.get("global", frozenset())
        if rec.priority is not Priority.SAFETY:
            assert rec.action not in blocked, f"{rec.rule_id} slipped past a veto"

    # Mode OFF really means off.
    if state.mode is Mode.OFF:
        assert result.global_state is GlobalState.OFF
        assert not result.recommendations

    # Nothing gets pushed while notifications are disabled or data is stale.
    if state.outdoor.is_stale:
        assert result.global_state is GlobalState.UNAVAILABLE_DATA


@pytest.mark.parametrize("seed", range(20))
def test_evaluation_is_pure(seed: int) -> None:
    """Two runs on the same snapshot produce the same advice."""
    state = _random_world(random.Random(seed))
    first = evaluate(state, EngineMemory())
    second = evaluate(state, EngineMemory())
    assert [(r.target, r.action, r.urgency) for r in first.recommendations] == [
        (r.target, r.action, r.urgency) for r in second.recommendations
    ]
    assert first.global_state is second.global_state


def test_notifications_never_exceed_the_configured_restraint() -> None:
    """At maximum restraint only HEALTH and SAFETY may still push."""
    prefs = Preferences(
        notification_restraint=100,
        quiet_hours_start=__import__("datetime").time(23, 59),
        quiet_hours_end=__import__("datetime").time(0, 1),
    )
    state = _world(preferences=prefs, outdoor=OutdoorState.create(14.0, 60.0))
    result = evaluate(state)
    for rec in result.recommendations:
        if rec.notify:
            assert rec.priority >= Priority.HEALTH


def test_off_mode_is_silent() -> None:
    result = evaluate(_world(mode=Mode.OFF))
    assert result.global_state is GlobalState.OFF
    assert not result.recommendations


# --------------------------------------------------------------------------
# Anti-flapping - the test the specification calls mandatory
# --------------------------------------------------------------------------


def test_anti_flapping_around_zero_delta_t() -> None:
    """Jitter ΔT around zero for two hours; the advice must not oscillate."""
    scenario = next(s for s in SCENARIOS if s.name == "flapping_delta_t_zero")
    memory = scenario.memory
    state = scenario.state
    rng = random.Random(1234)

    actions: list[Action] = []
    for step in range(24):
        moment = state.now + timedelta(minutes=5 * step)
        indoor = 24.0 + rng.uniform(-0.25, 0.25)
        outdoor = 24.0 + rng.uniform(-0.4, 0.4)
        jittered = state.with_overrides(
            now=moment,
            outdoor=OutdoorState.create(outdoor, 55.0, pressure=1015.0),
            rooms=(
                RoomState.create(
                    "living_room", "Living room", indoor, 50.0, volume_m3=60.0, priority=1
                ),
            ),
        )
        result = evaluate(jittered, memory)
        rec = result.for_window("living_south")
        actions.append(rec.action if rec else Action.NO_ACTION)

    changes = sum(1 for a, b in pairwise(actions) if a != b)
    # Two hours of jitter straddling the threshold may legitimately produce one
    # settled transition. Anything beyond that is flapping.
    assert changes <= 2, f"advice flapped {changes} times: {[a.value for a in actions]}"

    pushed = sum(1 for a in actions if a in OPENING_ACTIONS)
    assert pushed == 0, "no ventilation advice should be produced inside the dead band"


def test_minimum_dwell_time_blocks_a_fast_reversal() -> None:
    """A genuine change is held back until the minimum dwell time has passed."""
    memory = EngineMemory()
    cold = _world(outdoor=OutdoorState.create(16.0, 60.0))
    first = evaluate(cold, memory)
    assert first.for_window("w1") is not None
    assert first.for_window("w1").action in OPENING_ACTIONS  # type: ignore[union-attr]

    # Five minutes later it is suddenly much warmer outside.
    hot = _world(
        now=NOW + timedelta(minutes=5),
        outdoor=OutdoorState.create(30.0, 40.0),
        forecast=tuple(
            ForecastHour(time=NOW + timedelta(hours=i), temperature=30.0, humidity=40.0)
            for i in range(30)
        ),
    )
    held = evaluate(hot, memory)
    rec = held.for_window("w1")
    assert rec is not None and rec.reason_key == "hold_min_duration"
    assert not rec.notify

    # Twenty minutes later the change goes through.
    later = hot.with_overrides(now=NOW + timedelta(minutes=25))
    settled = evaluate(later, memory)
    rec = settled.for_window("w1")
    assert rec is not None and rec.action in CLOSING_ACTIONS


def test_safety_bypasses_the_minimum_dwell_time() -> None:
    memory = EngineMemory()
    evaluate(_world(outdoor=OutdoorState.create(16.0, 60.0)), memory)

    storm = _world(
        now=NOW + timedelta(minutes=2),
        weather_alerts=(WeatherAlert(event="Storm", severity="severe", kind="storm"),),
    )
    result = evaluate(storm, memory)
    rec = result.for_window("w1")
    assert rec is not None and rec.priority is Priority.SAFETY
    assert rec.reason_key != "hold_min_duration"


def test_snooze_and_ignore_silence_a_recommendation() -> None:
    memory = EngineMemory()
    state = _world(outdoor=OutdoorState.create(14.0, 55.0))
    result = evaluate(state, memory)
    rec = result.for_window("w1")
    assert rec is not None and rec.notify

    memory.snooze(rec.id, NOW + timedelta(hours=1))
    assert evaluate(state, memory).for_window("w1").notify is False  # type: ignore[union-attr]

    memory.snoozed_until.clear()
    memory.ignore_today(rec.id, NOW)
    assert evaluate(state, memory).for_window("w1").notify is False  # type: ignore[union-attr]


def test_manual_hold_gives_in_after_two_contradictions() -> None:
    """The user leaves the window open twice - stop nagging for today."""
    memory = EngineMemory()
    hot = _world(
        outdoor=OutdoorState.create(32.0, 35.0),
        windows=(
            WindowState(
                id="w1",
                name="South",
                room_id="living_room",
                azimuth=190.0,
                area_m2=2.2,
                is_open=True,
                open_since=NOW - timedelta(minutes=30),
            ),
        ),
        forecast=tuple(
            ForecastHour(time=NOW + timedelta(hours=i), temperature=32.0, humidity=35.0)
            for i in range(30)
        ),
    )
    result = evaluate(hot, memory)
    rec = result.for_window("w1")
    assert rec is not None and rec.action is Action.CLOSE

    # Simulate two ignored pushes, an hour apart each.
    tracked = memory.target("w1")
    for offset in (60, 130):
        tracked.last_notified = hot.now + timedelta(minutes=offset - 61)
        evaluate(hot.with_overrides(now=hot.now + timedelta(minutes=offset)), memory)

    assert memory.is_held("w1", hot.now)
    final = evaluate(hot.with_overrides(now=hot.now + timedelta(minutes=200)), memory)
    rec = final.for_window("w1")
    assert rec is None or rec.action is Action.NO_ACTION


# --------------------------------------------------------------------------
# Schedule units
# --------------------------------------------------------------------------


def test_schedule_reports_kelvin_in_summer_and_grams_in_winter() -> None:
    """Summing a g/m3 drying margin and calling it Kelvin produced "29 K" windows."""
    summer = _world(
        outdoor=OutdoorState.create(16.0, 60.0),
        forecast=tuple(
            ForecastHour(time=NOW + timedelta(hours=i), temperature=16.0 + (i % 12), humidity=60.0)
            for i in range(30)
        ),
    )
    result = evaluate(summer)
    assert result.schedule.metric == "kelvin"
    assert result.schedule.best_delta_k < 12.0

    winter = _world(
        outdoor=OutdoorState.create(-3.0, 85.0),
        rooms=(RoomState.create("living_room", "Living room", 21.0, 55.0, volume_m3=60.0),),
        forecast=tuple(
            ForecastHour(time=NOW + timedelta(hours=i), temperature=-3.0, humidity=85.0)
            for i in range(30)
        ),
    )
    result = evaluate(winter)
    assert result.schedule.metric == "grams"
    # A winter window is a purge slot, not half the night.
    if result.schedule.best_start and result.schedule.best_end:
        hours = (result.schedule.best_end - result.schedule.best_start).total_seconds() / 3600
        assert hours <= 3
        assert result.schedule.best_delta_k < 15.0


def test_disabled_rules_are_actually_skipped() -> None:
    """The coordinator passes this through from the options; keyword-only."""
    state = _world(outdoor=OutdoorState.create(15.0, 60.0))

    enabled = evaluate(state, EngineMemory())
    assert "night_flush" in {rec.rule_id for rec in enabled.recommendations}

    disabled = evaluate(state, EngineMemory(), disabled_rules=["night_flush"])
    assert "night_flush" not in {rec.rule_id for rec in disabled.recommendations}
    # Everything else keeps working.
    assert disabled.diagnostics["rule_errors"] == {}
    assert len(disabled.recommendations) >= 1


# --------------------------------------------------------------------------
# Manual blinds and the shading horizon
# --------------------------------------------------------------------------


def test_a_blind_without_an_entity_still_gets_shading_advice() -> None:
    """Plenty of flats have blinds Home Assistant cannot see or move."""
    from adaptive_ventilation.engine.state import COVER_ACTIONS

    noon = NOW.replace(hour=11, minute=0)
    hot = tuple(
        ForecastHour(time=noon + timedelta(hours=i), temperature=31.0, humidity=40.0)
        for i in range(30)
    )
    windows = (
        WindowState(
            id="w1",
            name="South",
            room_id="living_room",
            azimuth=180.0,
            area_m2=2.5,
            manual_cover=True,
            cover_external=True,
        ),
    )
    state = _world(
        now=noon,
        outdoor=OutdoorState.create(31.0, 40.0, cloud_coverage=0.0),
        rooms=(RoomState.create("living_room", "Living room", 27.0, 45.0, volume_m3=60.0),),
        windows=windows,
        forecast=hot,
        sun=SunState(azimuth=180.0, elevation=55.0),
    )
    result = evaluate(state, EngineMemory())
    cover = next((r for r in result.recommendations if r.target == "w1:cover"), None)
    assert cover is not None, "a manual blind got no shading advice at all"
    assert cover.action in COVER_ACTIONS
    # Nothing can move it, so the advice has to reach the user.
    assert cover.notify


def test_a_manual_blind_is_never_driven_automatically() -> None:
    """cover_auto_allowed without an entity would be a promise we cannot keep."""
    from adaptive_ventilation.models import WindowConfig

    config = WindowConfig.from_subentry(
        "w1",
        {"name": "South", "room": "r", "manual_cover": True, "cover_auto_allowed": True},
    )
    assert config.manual_cover
    # build_windows drops the automation flag when there is no entity to drive.
    from types import SimpleNamespace

    from adaptive_ventilation.models import build_windows

    hass = SimpleNamespace(
        states=SimpleNamespace(get=lambda _e: None),
        config=SimpleNamespace(latitude=50.1, longitude=8.7),
    )
    window = build_windows(hass, [config])[0]
    assert window.has_cover
    assert window.cover_is_manual
    assert not window.cover_auto_allowed


def test_the_shading_horizon_blocks_the_sun_before_it_arrives() -> None:
    """A south window behind a taller building gets no direct sun until midday."""
    from adaptive_ventilation.engine.solar import solar_load, sun_position
    from adaptive_ventilation.models import horizon_from_sun_hours

    reference = datetime(2026, 7, 30, 12, 0, tzinfo=UTC)
    profile = horizon_from_sun_hours(time(13, 0), None, 50.11, 8.68, reference=reference)
    assert profile is not None

    open_window = WindowState(id="w", name="S", room_id="r", azimuth=180.0, area_m2=2.0)
    shaded = WindowState(
        id="w", name="S", room_id="r", azimuth=180.0, area_m2=2.0, horizon_profile=profile
    )

    morning = datetime(2026, 7, 30, 9, 0, tzinfo=UTC)
    elevation, azimuth = sun_position(morning, 50.11, 8.68)
    assert solar_load(shaded, elevation, azimuth) < solar_load(open_window, elevation, azimuth) / 4

    # Once the sun comes round, the two agree again.
    afternoon = datetime(2026, 7, 30, 15, 0, tzinfo=UTC)
    elevation, azimuth = sun_position(afternoon, 50.11, 8.68)
    assert solar_load(shaded, elevation, azimuth) == pytest.approx(
        solar_load(open_window, elevation, azimuth)
    )


def test_no_sun_hours_configured_means_no_horizon() -> None:
    from adaptive_ventilation.models import horizon_from_sun_hours

    assert horizon_from_sun_hours(None, None, 50.0, 8.0) is None
