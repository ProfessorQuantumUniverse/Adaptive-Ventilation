"""Thermal model: purge durations, cool-down rates, tipping points."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from adaptive_ventilation.engine import thermal
from adaptive_ventilation.engine.state import (
    BuildingProfile,
    BuildingType,
    ForecastHour,
    LearnedRoom,
    RoomState,
    WindowState,
)

NOW = datetime(2025, 7, 15, 22, 0, tzinfo=UTC)


def _room(temperature: float = 21.0, volume: float = 40.0, **kwargs: object) -> RoomState:
    return RoomState.create("r", "Room", temperature, 50.0, volume_m3=volume, **kwargs)


def _window(area: float = 1.4, azimuth: float = 180.0, **kwargs: object) -> WindowState:
    return WindowState(id="w", name="Window", room_id="r", area_m2=area, azimuth=azimuth, **kwargs)


# --------------------------------------------------------------------------
# Purge duration - the table from SPEC.md section 6
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("outdoor", "low", "high"),
    [
        (-12.0, 3, 4),
        (-5.0, 4, 6),
        (5.0, 8, 12),
        (14.0, 12, 18),
        (22.0, 20, 30),
    ],
)
def test_purge_duration_matches_reference_table(outdoor: float, low: int, high: int) -> None:
    """A reference room must reproduce the published duration table."""
    minutes = thermal.purge_duration(_room(), outdoor, windows=[_window()])
    assert low <= minutes <= high, f"{outdoor} C -> {minutes} min, expected {low}..{high}"


def test_tilt_makes_it_three_to_four_times_longer() -> None:
    wide = thermal.purge_duration(_room(), 5.0, windows=[_window()])
    tilted = thermal.purge_duration(_room(), 5.0, windows=[_window()], tilted=True)
    assert 3.0 <= tilted / wide <= 4.5


def test_cross_ventilation_shortens_it_dramatically() -> None:
    """Bathroom *and* bedroom at once: four minutes instead of twelve."""
    single = thermal.purge_duration(_room(), 5.0, windows=[_window()])
    both = thermal.purge_duration(
        _room(),
        5.0,
        windows=[_window(), WindowState(id="w2", name="W2", room_id="r", area_m2=1.4, azimuth=0.0)],
        cross=True,
    )
    assert both < single / 2.5


def test_bigger_window_needs_less_time() -> None:
    small = thermal.purge_duration(_room(), 5.0, windows=[_window(area=0.6)])
    large = thermal.purge_duration(_room(), 5.0, windows=[_window(area=3.0)])
    assert large < small


# --------------------------------------------------------------------------
# Cool-down rates - the table from SPEC.md section 7
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("building_type", "low", "high"),
    [
        (BuildingType.OLD_MASSIVE, 0.9, 1.6),
        (BuildingType.OLD_RENOVATED, 0.7, 1.3),
        (BuildingType.NEW_INSULATED, 0.6, 1.1),
        (BuildingType.LIGHTWEIGHT, 1.5, 2.6),
        (BuildingType.HALF_TIMBERED, 0.9, 1.6),
    ],
)
def test_night_cooling_rate_matches_building_table(
    building_type: BuildingType, low: float, high: float
) -> None:
    """Open window, ΔT = 10 K: the rate has to land in the documented band."""
    room = _room(temperature=28.0, volume=60.0)
    rate = thermal.cooling_rate_k_per_h(
        room,
        [_window(area=2.0)],
        18.0,
        BuildingProfile(building_type=building_type),
        LearnedRoom(),
    )
    assert low <= rate <= high, f"{building_type}: {rate:.2f} K/h"


def test_learned_rate_overrides_the_model() -> None:
    room = _room(temperature=28.0, volume=60.0)
    learned = LearnedRoom(night_cooling_k_per_h=2.0, samples=14)
    rate = thermal.cooling_rate_k_per_h(room, [_window(area=2.0)], 18.0, BuildingProfile(), learned)
    assert rate == pytest.approx(2.0, abs=0.01)


def test_learned_value_ignored_below_minimum_samples() -> None:
    room = _room(temperature=28.0, volume=60.0)
    learned = LearnedRoom(night_cooling_k_per_h=2.0, samples=1)
    rate = thermal.cooling_rate_k_per_h(room, [_window(area=2.0)], 18.0, BuildingProfile(), learned)
    assert rate != pytest.approx(2.0, abs=0.01)


def test_massive_building_retains_more_cooling_than_lightweight() -> None:
    room = _room()
    massive = thermal.storable_cooling_k(
        room, BuildingProfile(building_type=BuildingType.OLD_MASSIVE), LearnedRoom()
    )
    light = thermal.storable_cooling_k(
        room, BuildingProfile(building_type=BuildingType.LIGHTWEIGHT), LearnedRoom()
    )
    assert massive > light


# --------------------------------------------------------------------------
# Air exchange and cross ventilation detection
# --------------------------------------------------------------------------


def test_cross_pairs_need_opposite_facades_and_a_connection() -> None:
    bedroom = RoomState.create("bedroom", "Bedroom", 22.0, 50.0, connected_rooms=("bath",))
    bath = RoomState.create("bath", "Bath", 24.0, 70.0, connected_rooms=("bedroom",))
    kitchen = RoomState.create("kitchen", "Kitchen", 23.0, 55.0)

    east = WindowState(id="e", name="East", room_id="bedroom", azimuth=90.0)
    west = WindowState(id="w", name="West", room_id="bath", azimuth=270.0)
    almost = WindowState(id="s", name="South", room_id="bath", azimuth=160.0)
    unconnected = WindowState(id="k", name="Kitchen", room_id="kitchen", azimuth=280.0)

    pairs = thermal.cross_ventilation_pairs(
        [east, west, almost, unconnected], [bedroom, bath, kitchen]
    )
    ids = {tuple(sorted((a.id, b.id))) for a, b in pairs}
    assert ("e", "w") in ids
    assert ("e", "s") not in ids  # only 70 degrees apart
    assert ("e", "k") not in ids  # rooms are not connected


def test_tilted_window_exchanges_far_less_air() -> None:
    room = _room()
    wide = thermal.window_air_changes(_window(is_open=True), room, 10.0)
    tilted = thermal.window_air_changes(_window(is_open=True, is_tilted=True), room, 10.0)
    assert tilted < wide / 4.0


def test_windward_window_gets_more_air_than_leeward() -> None:
    room = _room()
    window = _window(azimuth=270.0, is_open=True)
    windward = thermal.window_air_changes(window, room, 2.0, wind_speed=6.0, wind_bearing=270.0)
    leeward = thermal.window_air_changes(window, room, 2.0, wind_speed=6.0, wind_bearing=90.0)
    assert windward > leeward


# --------------------------------------------------------------------------
# Forecast analysis
# --------------------------------------------------------------------------


def _forecast(temperatures: list[float], start: datetime = NOW) -> list[ForecastHour]:
    return [
        ForecastHour(time=start + timedelta(hours=i), temperature=t, humidity=60.0)
        for i, t in enumerate(temperatures)
    ]


def test_tipping_points_find_both_crossings() -> None:
    # 22:00 start, cools down, warms past 25 C in the morning, cools again.
    temperatures = [20, 19, 18, 17, 16, 16, 17, 20, 23, 26, 29, 31, 32, 31, 28, 25, 22, 20]
    morning, evening = thermal.find_tipping_points(25.0, _forecast(temperatures), NOW)
    assert morning is not None and evening is not None
    assert morning < evening
    # 23 C at 06:00 and 26 C at 07:00 -> the 25 C crossing is interpolated to 06:40.
    assert (morning.hour, morning.minute) == (6, 40)


def test_no_tipping_point_when_it_stays_cooler() -> None:
    morning, _evening = thermal.find_tipping_points(25.0, _forecast([15] * 24), NOW)
    assert morning is None


def test_tropical_night_detection() -> None:
    hot = _forecast([24, 23, 22.5, 22, 21.8, 22, 23, 26])
    cool = _forecast([22, 20, 18, 16, 15, 16, 18, 22])
    assert thermal.is_tropical_night(hot, NOW, 20.0)
    assert not thermal.is_tropical_night(cool, NOW, 20.0)


def test_heatwave_lookahead_ignores_the_next_few_hours() -> None:
    temperatures = [35.0] * 6 + [20.0] * 60
    found, _peak, _time = thermal.heatwave_ahead(_forecast(temperatures), NOW, 32.0)
    assert not found

    temperatures = [20.0] * 20 + [36.0] * 10 + [20.0] * 30
    found, peak, moment = thermal.heatwave_ahead(_forecast(temperatures), NOW, 32.0)
    assert found and peak == 36.0 and moment is not None


def test_temperature_drop_detects_a_front() -> None:
    drop = thermal.find_temperature_drop(_forecast([33, 32, 24, 21, 20]), NOW, min_drop_k=5.0)
    assert drop is not None
    start, end, magnitude = drop
    assert magnitude >= 5.0 and end > start

    assert thermal.find_temperature_drop(_forecast([25, 24, 23, 22]), NOW, min_drop_k=5.0) is None


# --------------------------------------------------------------------------
# Simulation
# --------------------------------------------------------------------------


def test_simulation_with_open_window_ends_cooler() -> None:
    room = _room(temperature=28.0, volume=60.0)
    windows = [_window(area=2.0)]
    forecast = _forecast([18.0] * 26)
    building = BuildingProfile()

    closed = thermal.simulate(
        room,
        windows,
        forecast,
        building,
        LearnedRoom(),
        thermal.VentilationPlan(label="closed"),
        start=NOW,
        hours=8.0,
    )
    opened = thermal.simulate(
        room,
        windows,
        forecast,
        building,
        LearnedRoom(),
        thermal.VentilationPlan(
            label="open", open_from=NOW, open_until=NOW + timedelta(hours=8), window_ids=("w",)
        ),
        start=NOW,
        hours=8.0,
    )
    assert opened.end_temperature < closed.end_temperature - 1.0
    # And it must not overshoot below the outdoor temperature.
    assert opened.min_temperature >= 17.5


def test_simulation_is_deterministic() -> None:
    room = _room(temperature=26.0)
    args = (
        [_window()],
        _forecast([20.0] * 26),
        BuildingProfile(),
        LearnedRoom(),
        thermal.VentilationPlan(label="x"),
    )
    first = thermal.simulate(room, *args, start=NOW, hours=6.0)
    second = thermal.simulate(room, *args, start=NOW, hours=6.0)
    assert first.points == second.points
