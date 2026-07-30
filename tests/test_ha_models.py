"""Mapping between Home Assistant state and the engine dataclasses.

These run without ``pytest-homeassistant-custom-component``: they only need the
``homeassistant`` package to be importable, not a running instance.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest
from homeassistant.core import State

from adaptive_ventilation import models
from adaptive_ventilation.const import (
    CONF_BUILDING_TYPE,
    CONF_F_RT,
    CONF_OUTDOOR_HUMIDITY,
    CONF_OUTDOOR_TEMPERATURE,
    CONF_QUIET_END,
    CONF_QUIET_START,
)
from adaptive_ventilation.engine.state import BuildingType

UTC = timezone.utc


class FakeStates:
    """The two methods of ``hass.states`` that the mapping layer uses."""

    def __init__(self, states: dict[str, State]) -> None:
        self._states = states

    def get(self, entity_id: str) -> State | None:
        return self._states.get(entity_id)


def fake_hass(states: dict[str, State] | None = None, **config: Any) -> Any:
    return SimpleNamespace(
        states=FakeStates(states or {}),
        config=SimpleNamespace(latitude=50.11, longitude=8.68, language="en", **config),
    )


def state(entity_id: str, value: Any, **attributes: Any) -> State:
    return State(entity_id, str(value), attributes)


# --------------------------------------------------------------------------
# Preferences and building profile
# --------------------------------------------------------------------------


def test_preferences_defaults_survive_an_empty_options_dict() -> None:
    prefs = models.build_preferences({})
    assert prefs.co2_threshold == 1000
    assert prefs.quiet_hours_start == time(22, 0)


def test_preferences_pick_up_options_and_parse_times() -> None:
    prefs = models.build_preferences(
        {
            "co2_threshold": 850,
            "weight_humidity": 80,
            CONF_QUIET_START: "23:15:00",
            CONF_QUIET_END: "06:30:00",
        }
    )
    assert prefs.co2_threshold == 850
    assert prefs.weight_humidity == 80
    assert prefs.quiet_hours_start == time(23, 15)
    assert prefs.quiet_hours_end == time(6, 30)


def test_preferences_ignore_unknown_keys() -> None:
    """Stale options from an older version must not break the setup."""
    prefs = models.build_preferences({"co2_threshold": 900, "obsolete_setting": 42})
    assert prefs.co2_threshold == 900


def test_building_profile_maps_type_and_falls_back() -> None:
    profile = models.build_building({CONF_BUILDING_TYPE: "old_massive"}, 50.0, 8.0)
    assert profile.building_type is BuildingType.OLD_MASSIVE
    assert profile.f_rt == pytest.approx(0.35)

    unknown = models.build_building({CONF_BUILDING_TYPE: "does_not_exist"}, 50.0, 8.0)
    assert unknown.building_type is BuildingType.UNKNOWN


def test_f_rt_override_wins() -> None:
    profile = models.build_building(
        {CONF_BUILDING_TYPE: "old_massive", CONF_F_RT: 0.15}, 50.0, 8.0
    )
    assert profile.f_rt == pytest.approx(0.15)


# --------------------------------------------------------------------------
# Reading entity state
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [("21.5", 21.5), ("unavailable", None), ("unknown", None), ("not a number", None)],
)
def test_read_float_is_defensive(value: str, expected: float | None) -> None:
    hass = fake_hass({"sensor.x": state("sensor.x", value)})
    assert models.read_float(hass, "sensor.x") == expected


def test_read_float_handles_a_missing_entity() -> None:
    assert models.read_float(fake_hass(), "sensor.gone") is None
    assert models.read_float(fake_hass(), None) is None


def test_outdoor_prefers_the_sensor_over_the_weather_entity() -> None:
    hass = fake_hass(
        {
            "sensor.outdoor": state("sensor.outdoor", 18.4),
            "sensor.outdoor_humidity": state("sensor.outdoor_humidity", 62),
        }
    )
    weather = state(
        "weather.home",
        "sunny",
        temperature=21.0,
        humidity=40,
        wind_speed=18.0,
        wind_speed_unit="km/h",
        wind_bearing=270,
        cloud_coverage=25,
        pressure=1014,
    )
    outdoor = models.build_outdoor(
        hass,
        {
            CONF_OUTDOOR_TEMPERATURE: "sensor.outdoor",
            CONF_OUTDOOR_HUMIDITY: "sensor.outdoor_humidity",
        },
        weather,
    )
    assert outdoor.temperature == 18.4
    assert outdoor.humidity == 62
    assert outdoor.source == "mixed"
    # Wind is converted from km/h to m/s.
    assert outdoor.wind_speed == pytest.approx(5.0)
    assert outdoor.absolute_humidity is not None


def test_outdoor_falls_back_to_the_weather_entity() -> None:
    weather = state("weather.home", "cloudy", temperature=12.0, humidity=80)
    outdoor = models.build_outdoor(fake_hass(), {}, weather)
    assert outdoor.temperature == 12.0
    assert outdoor.source == "weather"
    assert not outdoor.is_stale


def test_outdoor_reports_stale_when_there_is_nothing_at_all() -> None:
    outdoor = models.build_outdoor(fake_hass(), {}, None)
    assert outdoor.is_stale


# --------------------------------------------------------------------------
# Rooms and the estimation fallback chain
# --------------------------------------------------------------------------


def _room_config(**kwargs: Any) -> models.RoomConfig:
    defaults: dict[str, Any] = {"id": "r1", "name": "Room"}
    defaults.update(kwargs)
    return models.RoomConfig(**defaults)


def test_measured_room_has_full_confidence() -> None:
    hass = fake_hass(
        {
            "sensor.t": state("sensor.t", 21.5),
            "sensor.h": state("sensor.h", 48),
        }
    )
    rooms = models.build_rooms(
        hass, [_room_config(temperature_sensor="sensor.t", humidity_sensor="sensor.h")]
    )
    assert rooms[0].temperature == 21.5
    assert rooms[0].confidence == 1.0
    assert rooms[0].estimation_method == "measured"
    assert rooms[0].absolute_humidity is not None


def test_reference_offset_estimation_keeps_absolute_humidity() -> None:
    """The offset room inherits g/m3, not the reference room's percentage."""
    from adaptive_ventilation.engine import psychrometrics as psy

    hass = fake_hass(
        {"sensor.t": state("sensor.t", 21.0), "sensor.h": state("sensor.h", 50)}
    )
    configs = [
        _room_config(id="living", name="Living", temperature_sensor="sensor.t",
                     humidity_sensor="sensor.h"),
        _room_config(
            id="attic",
            name="Attic",
            estimation="reference_offset",
            reference_room="living",
            reference_offset=3.0,
        ),
    ]
    rooms = models.build_rooms(hass, configs)
    attic = next(r for r in rooms if r.id == "attic")
    assert attic.temperature == pytest.approx(24.0)
    assert attic.confidence == pytest.approx(0.6)
    assert attic.estimation_method == "reference_offset"
    # Same absolute humidity as the living room, lower relative humidity.
    assert attic.absolute_humidity == pytest.approx(psy.absolute_humidity(21.0, 50.0), abs=0.05)
    assert attic.humidity is not None and attic.humidity < 50.0


def test_model_estimation_uses_the_flat_average_with_flags() -> None:
    hass = fake_hass({"sensor.t": state("sensor.t", 22.0)})
    configs = [
        _room_config(id="living", name="Living", temperature_sensor="sensor.t"),
        _room_config(id="attic", name="Attic", estimation="model", is_top_floor=True),
        _room_config(id="cellar", name="Cellar", estimation="model", is_basement=True),
    ]
    rooms = {room.id: room for room in models.build_rooms(hass, configs)}
    assert rooms["attic"].temperature == pytest.approx(24.0)
    assert rooms["cellar"].temperature == pytest.approx(18.0)
    assert rooms["attic"].confidence == pytest.approx(0.5)


def test_room_without_any_reference_stays_unknown() -> None:
    """Never guess silently - no data means no temperature and no confidence."""
    rooms = models.build_rooms(fake_hass(), [_room_config(estimation="model")])
    assert rooms[0].temperature is None
    assert rooms[0].confidence == 0.0
    assert rooms[0].estimation_method is None


def test_heating_active_is_read_from_the_climate_entity() -> None:
    hass = fake_hass(
        {
            "sensor.t": state("sensor.t", 20.0),
            "climate.rad": state("climate.rad", "heat", hvac_action="heating"),
        }
    )
    rooms = models.build_rooms(
        hass, [_room_config(temperature_sensor="sensor.t", climate_entity="climate.rad")]
    )
    assert rooms[0].heating_active is True


# --------------------------------------------------------------------------
# Windows
# --------------------------------------------------------------------------


def test_window_state_reads_contact_tilt_and_cover() -> None:
    hass = fake_hass(
        {
            "binary_sensor.w": state("binary_sensor.w", "on"),
            "binary_sensor.w_tilt": state("binary_sensor.w_tilt", "on"),
            "cover.w": state("cover.w", "open", current_position=40),
        }
    )
    config = models.WindowConfig(
        id="w1",
        name="Window",
        room_id="r1",
        contact_sensor="binary_sensor.w",
        tilt_sensor="binary_sensor.w_tilt",
        cover_entity="cover.w",
        azimuth=200.0,
    )
    window = models.build_windows(hass, [config])[0]
    assert window.is_open and window.is_tilted
    assert window.cover_position == 40
    assert window.open_since is not None


def test_cover_without_position_falls_back_to_open_closed() -> None:
    hass = fake_hass(
        {
            "binary_sensor.w": state("binary_sensor.w", "off"),
            "cover.w": state("cover.w", "closed"),
        }
    )
    config = models.WindowConfig(
        id="w1", name="W", room_id="r1", contact_sensor="binary_sensor.w",
        cover_entity="cover.w",
    )
    window = models.build_windows(hass, [config])[0]
    assert window.cover_position == 0
    assert not window.is_open


def test_window_config_derives_volume_from_area_and_height() -> None:
    config = models.RoomConfig.from_subentry(
        "abc", {"name": "Room", "floor_area_m2": 20, "ceiling_height_m": 2.6}
    )
    assert config.volume_m3 == pytest.approx(52.0)


# --------------------------------------------------------------------------
# Forecast and alerts
# --------------------------------------------------------------------------


def test_forecast_conversion_sorts_and_drops_broken_entries() -> None:
    base = datetime(2025, 7, 15, 20, 0, tzinfo=UTC)
    raw = [
        {"datetime": (base + timedelta(hours=2)).isoformat(), "temperature": 17.0,
         "humidity": 70},
        {"datetime": base.isoformat(), "temperature": 19.0, "humidity": 65,
         "wind_speed": 10.8, "wind_speed_unit": "km/h"},
        {"datetime": None, "temperature": 5.0},
        {"datetime": (base + timedelta(hours=1)).isoformat()},
    ]
    forecast = models.build_forecast(raw)
    assert [hour.temperature for hour in forecast] == [19.0, 17.0]
    assert forecast[0].wind_speed == pytest.approx(3.0)
    assert forecast[0].absolute_humidity is not None


def test_dwd_style_warnings_are_parsed() -> None:
    hass = fake_hass(
        {
            "binary_sensor.dwd": state(
                "binary_sensor.dwd",
                "on",
                warning_count=1,
                warning_1_name="Sturmböen",
                warning_1_level=3,
                warning_1_start="2025-06-28T19:00:00+00:00",
                warning_1_end="2025-06-29T00:00:00+00:00",
            )
        }
    )
    alerts = models.build_alerts(hass, "binary_sensor.dwd")
    assert len(alerts) == 1
    assert alerts[0].kind == "storm"
    assert alerts[0].severity == "severe"
    assert alerts[0].start is not None


def test_meteoalarm_style_warnings_are_parsed() -> None:
    hass = fake_hass(
        {
            "binary_sensor.meteo": state(
                "binary_sensor.meteo",
                "on",
                warnings=[{"event": "Hail storm", "severity": "extreme"}],
            )
        }
    )
    alerts = models.build_alerts(hass, "binary_sensor.meteo")
    assert alerts[0].kind == "hail"
    assert alerts[0].severity == "extreme"


def test_unparseable_warning_entity_degrades_to_a_generic_alert() -> None:
    hass = fake_hass({"binary_sensor.x": state("binary_sensor.x", "on", friendly_name="Warnung")})
    alerts = models.build_alerts(hass, "binary_sensor.x")
    assert len(alerts) == 1
    assert alerts[0].kind == "other"


def test_no_warning_entity_means_no_alerts() -> None:
    assert models.build_alerts(fake_hass(), None) == ()
    assert models.build_alerts(fake_hass(), "binary_sensor.missing") == ()
