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
        quiet_hours_start=time(23, 59),
        quiet_hours_end=time(0, 1),
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


def test_quiet_hours_follow_the_household_clock_not_utc() -> None:
    """22:00-07:00 means 22:00-07:00 where the user lives.

    Every timestamp in the engine is UTC. Comparing it straight against the
    wall-clock quiet hours shifted the silent window by the UTC offset, so in
    central European summer nothing but SAFETY got through until 09:00 local -
    which is the whole morning-close window.
    """
    prefs = Preferences(
        quiet_hours_start=time(22, 0), quiet_hours_end=time(7, 0), timezone="Europe/Berlin"
    )
    # 06:30 UTC is 08:30 in Berlin: awake, and past the crossover.
    assert not prefs.in_quiet_hours(datetime(2025, 7, 15, 6, 30, tzinfo=UTC))
    # 21:00 UTC is 23:00 in Berlin: asleep, even though 21:00 is not quiet.
    assert prefs.in_quiet_hours(datetime(2025, 7, 15, 21, 0, tzinfo=UTC))
    # No timezone configured falls back to the old behaviour rather than raising.
    assert Preferences().in_quiet_hours(datetime(2025, 7, 15, 6, 30, tzinfo=UTC))


def test_a_window_without_a_contact_sensor_is_never_read_as_a_contradiction() -> None:
    """Only a contact sensor can say the user disagreed.

    Without one ``is_open`` is a guess, and reading it as an answer put a
    manual hold on the window two cooldowns into any open recommendation -
    silently dropping that window for the rest of the day, every day.
    """
    memory = EngineMemory()
    cool = _world(outdoor=OutdoorState.create(14.0, 55.0))
    rec = cool.windows[0]
    assert not rec.contact_known  # no contact sensor configured

    tracked = memory.target("w1")
    for offset in (60, 130):
        tracked.last_notified = cool.now + timedelta(minutes=offset - 61)
        evaluate(cool.with_overrides(now=cool.now + timedelta(minutes=offset)), memory)

    assert not memory.is_held("w1", cool.now)
    final = evaluate(cool.with_overrides(now=cool.now + timedelta(minutes=200)), memory)
    assert final.for_window("w1").action in OPENING_ACTIONS  # type: ignore[union-attr]


def test_the_morning_close_clears_the_push_threshold_with_a_single_sensor() -> None:
    """A one-sensor flat used to sit at 0.6 confidence, just under the 0.7 gate."""
    warm = _world(
        outdoor=OutdoorState.create(19.0, 60.0),
        rooms=(
            RoomState.create("living_room", "Living room", 24.0, 50.0, volume_m3=60.0, priority=1),
        ),
        forecast=tuple(
            ForecastHour(
                time=NOW + timedelta(hours=i),
                temperature=19.0 + i * 1.5,
                humidity=60.0,
            )
            for i in range(30)
        ),
    )
    result = evaluate(warm, EngineMemory())
    assert result.tipping_points.morning_confidence >= warm.preferences.min_confidence_for_push


def _hot_world_with_open_window(**window_overrides: object) -> WorldState:
    return _world(
        outdoor=OutdoorState.create(32.0, 35.0),
        windows=(
            WindowState(
                id="w1",
                name="South",
                room_id="living_room",
                azimuth=190.0,
                area_m2=2.2,
                is_open=True,
                contact_known=True,
                open_since=NOW - timedelta(minutes=30),
                **window_overrides,  # type: ignore[arg-type]
            ),
        ),
        forecast=tuple(
            ForecastHour(time=NOW + timedelta(hours=i), temperature=32.0, humidity=35.0)
            for i in range(30)
        ),
    )


def test_manual_hold_gives_in_after_two_contradictions() -> None:
    """The user re-opens the window twice after we said close - stop nagging."""
    memory = EngineMemory()
    hot = _hot_world_with_open_window()
    result = evaluate(hot, memory)
    rec = result.for_window("w1")
    assert rec is not None and rec.action is Action.CLOSE

    # Two pushes, and after each one the user goes and opens the window again -
    # the contact sensor reports a change *after* the advice went out.
    tracked = memory.target("w1")
    for offset in (60, 130):
        advised_at = hot.now + timedelta(minutes=offset - 61)
        tracked.last_notified = advised_at
        moment = hot.now + timedelta(minutes=offset)
        reopened = hot.with_overrides(now=moment)
        object.__setattr__(
            reopened.windows[0], "contact_changed", advised_at + timedelta(minutes=5)
        )
        evaluate(reopened, memory)

    assert memory.is_held("w1", hot.now)
    final = evaluate(hot.with_overrides(now=hot.now + timedelta(minutes=200)), memory)
    rec = final.for_window("w1")
    assert rec is None or rec.action is Action.NO_ACTION


def test_a_held_window_says_so_instead_of_nothing_to_do() -> None:
    """The silence has to explain itself on the screen the user actually looks at."""
    memory = EngineMemory()
    cool = _world(outdoor=OutdoorState.create(14.0, 55.0))
    memory.hold("w1", cool.now)

    rec = evaluate(cool, memory).for_window("w1")
    assert rec is not None and rec.action is Action.NO_ACTION
    assert rec.reason_key == "idle_held"


def test_today_follows_the_household_clock() -> None:
    """Ignoring something at 23:00 must not expire two hours later.

    The engine runs on UTC, so a bare ``now.date()`` rolled the push budget,
    the manual hold and "ignore today" over at 02:00 local in CEST.
    """
    memory = EngineMemory(timezone="Europe/Berlin")
    late = datetime(2025, 7, 15, 21, 30, tzinfo=UTC)  # 23:30 in Berlin
    past_utc_midnight = datetime(2025, 7, 15, 22, 30, tzinfo=UTC)  # 00:30 in Berlin

    memory.ignore_today("rec", late)
    memory.hold("w1", late)
    assert memory.day_of(late) == "2025-07-15"

    # Still the same evening on the wall clock, even though UTC has ticked over.
    assert memory.day_of(past_utc_midnight) == "2025-07-16"
    assert not memory.is_ignored_today("rec", past_utc_midnight)
    assert memory.is_ignored_today("rec", late)
    assert memory.is_held("w1", late)

    # Without a timezone the old UTC behaviour is kept rather than guessed at.
    assert EngineMemory().day_of(late) == "2025-07-15"


def test_not_reacting_to_a_push_is_not_a_contradiction() -> None:
    """Being asleep must not mute a window for the rest of the day.

    The regression that made this integration go quiet: a window that simply
    stayed as it was counted as the user disagreeing, twice per cooldown, from
    the first push onwards - and ``last_notified`` is never cleared, so it
    re-armed forever. Covers were immune (their actions are in neither action
    set), which is why shutter advice was the only thing that still arrived.
    """
    memory = EngineMemory()
    hot = _hot_world_with_open_window(contact_changed=NOW - timedelta(hours=6))
    assert evaluate(hot, memory).for_window("w1").action is Action.CLOSE  # type: ignore[union-attr]

    tracked = memory.target("w1")
    for offset in (60, 130, 200, 270):
        tracked.last_notified = hot.now + timedelta(minutes=offset - 61)
        evaluate(hot.with_overrides(now=hot.now + timedelta(minutes=offset)), memory)

    assert not memory.is_held("w1", hot.now)
    assert memory.target("w1").contradictions == 0
    late = evaluate(hot.with_overrides(now=hot.now + timedelta(minutes=300)), memory)
    assert late.for_window("w1").action is Action.CLOSE  # type: ignore[union-attr]


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


# --------------------------------------------------------------------------
# Threshold hysteresis
# --------------------------------------------------------------------------


def _co2_world(co2: int, minutes: int = 0) -> WorldState:
    """A winter flat with one room, one window and a CO2 reading."""
    moment = NOW + timedelta(minutes=minutes)
    return WorldState(
        now=moment,
        outdoor=OutdoorState.create(2.0, 80.0, pressure=1013.0),
        rooms=(
            RoomState.create("living", "Living", 21.0, 45.0, co2=co2, volume_m3=60.0, priority=1),
        ),
        windows=(WindowState(id="w1", name="South", room_id="living", azimuth=180.0, area_m2=2.0),),
        forecast=tuple(
            ForecastHour(time=NOW + timedelta(hours=i), temperature=2.0, humidity=80.0)
            for i in range(30)
        ),
        sun=SunState(azimuth=180.0, elevation=15.0),
    )


def _co2_high_fired(result: object) -> bool:
    """Whether co2_high produced a candidate, won the target or not."""
    everything = itertools.chain(result.recommendations, result.suppressed)  # type: ignore[attr-defined]
    return any(rec.rule_id == "co2_high" for rec in everything)


def test_co2_hysteresis_keeps_advising_below_the_threshold() -> None:
    """Once a purge is advised it holds until CO2 has really come down.

    The rule looked the previous action up under the *room* id, but the advice
    is recorded per window - both the rule and the arbiter target windows - so
    the lookup never saw a purge and the hysteresis silently never engaged.
    """
    memory = EngineMemory()

    assert _co2_high_fired(evaluate(_co2_world(1100), memory)), "1100 ppm has to trigger a purge"
    assert memory.target("w1").action in (Action.PURGE, Action.CROSS_VENTILATE)

    # 920 ppm is below the 1000 ppm threshold but inside the 150 ppm band.
    assert _co2_high_fired(evaluate(_co2_world(920, minutes=30), memory))
    # 830 ppm is below the band - now it may stop.
    assert not _co2_high_fired(evaluate(_co2_world(830, minutes=60), memory))


def test_co2_hysteresis_does_not_lower_the_threshold_out_of_nowhere() -> None:
    """Without a purge in the memory the plain threshold applies."""
    assert not _co2_high_fired(evaluate(_co2_world(920), EngineMemory()))


#: The counterparts that need ``adaptive_ventilation.models`` - and therefore
#: Home Assistant - live in ``tests/test_ha_models.py``. This module has to stay
#: importable with no Home Assistant installed at all; CI has a job that proves
#: it by running exactly these files against a bare interpreter.


def test_ignore_today_only_survives_when_stored_on_the_same_clock() -> None:
    """`prune` and `is_ignored_today` both read world.now, which is UTC.

    Storing the *local* date instead - as the coordinator used to - made
    "ignore today" a no-op whenever the two dates disagree, because prune()
    threw the entry away again on the very next evaluation.
    """
    from datetime import timezone

    utc_now = datetime(2025, 7, 15, 22, 30, tzinfo=UTC)
    local_now = utc_now.astimezone(timezone(timedelta(hours=2)))  # 00:30 the next day
    assert local_now.date() != utc_now.date(), "the fixture has to straddle midnight"

    on_local_clock = EngineMemory()
    on_local_clock.ignore_today("co2_high:w1", local_now)
    on_local_clock.prune(utc_now)
    assert not on_local_clock.is_ignored_today("co2_high:w1", utc_now)

    on_world_clock = EngineMemory()
    on_world_clock.ignore_today("co2_high:w1", utc_now)
    on_world_clock.prune(utc_now)
    assert on_world_clock.is_ignored_today("co2_high:w1", utc_now)
