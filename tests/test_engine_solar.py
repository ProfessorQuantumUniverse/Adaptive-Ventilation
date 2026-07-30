"""Solar position and per-window solar load."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from adaptive_ventilation.engine import solar
from adaptive_ventilation.engine.state import WindowState

UTC = timezone.utc
# Frankfurt am Main.
LAT, LON = 50.11, 8.68


def _window(azimuth: float, **kwargs: object) -> WindowState:
    return WindowState(
        id="w", name="Window", room_id="r", azimuth=azimuth, area_m2=2.0, **kwargs
    )


def test_solar_noon_in_summer() -> None:
    """Around 11:25 UTC the sun is due south and ~63 deg up at this latitude."""
    moment = datetime(2025, 6, 21, 11, 25, tzinfo=UTC)
    elevation, azimuth = solar.sun_position(moment, LAT, LON)
    assert 61.0 <= elevation <= 65.0
    assert 175.0 <= azimuth <= 185.0


def test_winter_noon_is_much_lower() -> None:
    moment = datetime(2025, 12, 21, 11, 25, tzinfo=UTC)
    elevation, _azimuth = solar.sun_position(moment, LAT, LON)
    assert 14.0 <= elevation <= 18.0


def test_sun_is_below_the_horizon_at_midnight() -> None:
    elevation, _azimuth = solar.sun_position(datetime(2025, 6, 21, 0, 0, tzinfo=UTC), LAT, LON)
    assert elevation < 0.0


def test_sunrise_in_the_east_sunset_in_the_west() -> None:
    _e, morning = solar.sun_position(datetime(2025, 6, 21, 4, 0, tzinfo=UTC), LAT, LON)
    _e, evening = solar.sun_position(datetime(2025, 6, 21, 19, 0, tzinfo=UTC), LAT, LON)
    assert 40.0 < morning < 110.0
    assert 250.0 < evening < 320.0


def test_incidence_is_maximal_when_the_sun_faces_the_window() -> None:
    head_on = solar.incidence_cosine(30.0, 180.0, 180.0)
    oblique = solar.incidence_cosine(30.0, 230.0, 180.0)
    behind = solar.incidence_cosine(30.0, 0.0, 180.0)
    assert head_on > oblique > 0.0
    assert behind == 0.0


def test_north_window_gets_only_diffuse_light() -> None:
    north = solar.solar_load(_window(0.0), 45.0, 180.0, None)
    south = solar.solar_load(_window(180.0), 45.0, 180.0, None)
    assert north < south / 3.0
    assert north > 0.0


def test_clouds_damp_the_direct_beam() -> None:
    clear = solar.solar_load(_window(180.0), 45.0, 180.0, 0.0)
    overcast = solar.solar_load(_window(180.0), 45.0, 180.0, 100.0)
    assert overcast < clear / 2.0


def test_external_cover_beats_internal_cover() -> None:
    """The asymmetry the whole shading logic rests on: 80 % versus 30 %."""
    external = _window(180.0, cover_entity="cover.x", cover_position=0, cover_external=True)
    internal = _window(180.0, cover_entity="cover.x", cover_position=0, cover_external=False)
    assert solar.solar_load(external, 45.0, 180.0) < solar.solar_load(internal, 45.0, 180.0)
    assert solar.cover_saving_w(external, 45.0, 180.0) > solar.cover_saving_w(
        internal, 45.0, 180.0
    )


def test_partially_closed_cover_scales_linearly() -> None:
    half = _window(180.0, cover_entity="cover.x", cover_position=50, cover_external=True)
    open_cover = _window(180.0, cover_entity="cover.x", cover_position=100, cover_external=True)
    closed = _window(180.0, cover_entity="cover.x", cover_position=0, cover_external=True)
    loads = [solar.solar_load(w, 45.0, 180.0) for w in (closed, half, open_cover)]
    assert loads[0] < loads[1] < loads[2]


def test_horizon_profile_blocks_the_low_sun() -> None:
    """A neighbouring building 25 deg high keeps the morning sun out."""
    profile = tuple(25.0 for _ in range(36))
    shaded = _window(90.0, horizon_profile=profile)
    clear = _window(90.0)
    assert solar.solar_load(shaded, 15.0, 90.0) < solar.solar_load(clear, 15.0, 90.0)
    # Once the sun climbs above the obstruction the difference is gone.
    assert solar.solar_load(shaded, 40.0, 90.0) == pytest.approx(
        solar.solar_load(clear, 40.0, 90.0)
    )


def test_next_sun_hit_is_in_the_future_and_ordered_by_facade() -> None:
    reference = datetime(2025, 7, 1, 3, 0, tzinfo=UTC)
    east = solar.next_sun_hit(_window(90.0), reference, LAT, LON)
    west = solar.next_sun_hit(_window(270.0), reference, LAT, LON)
    assert east is not None and west is not None
    assert reference < east < west


def test_peak_time_of_a_south_window_is_around_noon() -> None:
    reference = datetime(2025, 7, 1, 3, 0, tzinfo=UTC)
    moment, value = solar.peak_solar_time(_window(180.0), reference, LAT, LON, hours=16)
    assert moment is not None and value > 300.0
    assert 9 <= moment.hour <= 13


def test_daily_energy_south_beats_north() -> None:
    day = datetime(2025, 7, 1, 0, 0, tzinfo=UTC)
    south = solar.daily_solar_energy_kwh(_window(180.0), day, LAT, LON)
    north = solar.daily_solar_energy_kwh(_window(0.0), day, LAT, LON)
    assert south > north > 0.0


def test_air_mass_grows_towards_the_horizon() -> None:
    assert solar.air_mass(90.0) == pytest.approx(1.0, abs=0.01)
    assert solar.air_mass(30.0) > solar.air_mass(60.0)
    assert solar.air_mass(0.0) > 20.0


def test_no_load_at_night() -> None:
    assert solar.solar_load(_window(180.0), -5.0, 180.0) == 0.0
    assert solar.clear_sky_direct(-1.0) == 0.0
