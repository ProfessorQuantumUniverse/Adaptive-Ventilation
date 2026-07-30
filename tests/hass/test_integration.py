"""Config flow, subentries, setup and services against a real Home Assistant."""

from __future__ import annotations

import importlib.util
import sys

import pytest

HAS_PLUGIN = (
    sys.platform != "win32"
    and importlib.util.find_spec("pytest_homeassistant_custom_component") is not None
)
pytestmark = pytest.mark.skipif(
    not HAS_PLUGIN,
    reason="pytest-homeassistant-custom-component is unavailable on this platform",
)

if HAS_PLUGIN:  # pragma: no cover - import guard for Windows
    from custom_components.adaptive_ventilation.const import (
        CONF_AZIMUTH,
        CONF_BUILDING_TYPE,
        CONF_CONTACT_SENSOR,
        CONF_HUMIDITY_SENSOR,
        CONF_OUTDOOR_HUMIDITY,
        CONF_OUTDOOR_TEMPERATURE,
        CONF_PRIORITY,
        CONF_ROOM,
        CONF_ROOM_NAME,
        CONF_TEMPERATURE_SENSOR,
        CONF_WINDOW_NAME,
        DOMAIN,
        SERVICE_SET_MODE,
        SERVICE_SNOOZE,
        SERVICE_START_PURGE,
        SUBENTRY_ROOM,
        SUBENTRY_WINDOW,
    )
    from homeassistant.config_entries import SOURCE_USER, ConfigEntryState
    from homeassistant.core import HomeAssistant
    from homeassistant.data_entry_flow import FlowResultType
    from pytest_homeassistant_custom_component.common import MockConfigEntry

ROOM_ID = "room-living"
WINDOW_ID = "window-south"


def _entry() -> MockConfigEntry:
    """A configured flat: one room with sensors and one south facing window."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Flat",
        data={},
        options={
            CONF_OUTDOOR_TEMPERATURE: "sensor.outdoor_temperature",
            CONF_OUTDOOR_HUMIDITY: "sensor.outdoor_humidity",
            CONF_BUILDING_TYPE: "old_renovated",
        },
        subentries_data=[
            {
                "subentry_id": ROOM_ID,
                "subentry_type": SUBENTRY_ROOM,
                "title": "Living room",
                "unique_id": None,
                "data": {
                    CONF_ROOM_NAME: "Living room",
                    CONF_TEMPERATURE_SENSOR: "sensor.living_temperature",
                    CONF_HUMIDITY_SENSOR: "sensor.living_humidity",
                    CONF_PRIORITY: 1,
                },
            },
            {
                "subentry_id": WINDOW_ID,
                "subentry_type": SUBENTRY_WINDOW,
                "title": "Living south",
                "unique_id": None,
                "data": {
                    CONF_WINDOW_NAME: "Living south",
                    CONF_ROOM: ROOM_ID,
                    CONF_CONTACT_SENSOR: "binary_sensor.living_window",
                    CONF_AZIMUTH: 190.0,
                },
            },
        ],
    )


def _seed_states(hass: HomeAssistant) -> None:
    hass.states.async_set("sensor.outdoor_temperature", "17.0")
    hass.states.async_set("sensor.outdoor_humidity", "65")
    hass.states.async_set("sensor.living_temperature", "26.5")
    hass.states.async_set("sensor.living_humidity", "50")
    hass.states.async_set("binary_sensor.living_window", "off")


async def _setup(hass: HomeAssistant) -> MockConfigEntry:
    _seed_states(hass)
    entry = _entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


# --------------------------------------------------------------------------
# Config flow
# --------------------------------------------------------------------------


async def test_user_flow_creates_an_entry(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"title": "Flat", CONF_BUILDING_TYPE: "old_renovated"},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Flat"
    assert result["options"][CONF_BUILDING_TYPE] == "old_renovated"


async def test_room_subentry_flow(hass: HomeAssistant) -> None:
    entry = await _setup(hass)

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_ROOM), context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            CONF_ROOM_NAME: "Bedroom",
            CONF_TEMPERATURE_SENSOR: "sensor.living_temperature",
            CONF_PRIORITY: 1,
            "advanced": {"is_bedroom": True},
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Bedroom"
    assert result["data"]["is_bedroom"] is True


async def test_window_subentry_needs_a_room(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, title="Empty", data={}, options={})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_WINDOW), context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_rooms"


async def test_window_subentry_uses_the_compass_preset(hass: HomeAssistant) -> None:
    entry = await _setup(hass)

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_WINDOW), context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            CONF_WINDOW_NAME: "Kitchen east",
            CONF_ROOM: ROOM_ID,
            CONF_CONTACT_SENSOR: "binary_sensor.living_window",
            "orientation": "e",
            "advanced": {},
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_AZIMUTH] == 90.0


async def test_options_flow_menu_and_thresholds(hass: HomeAssistant) -> None:
    entry = await _setup(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.MENU

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "thresholds"}
    )
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"co2_threshold": 850}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["co2_threshold"] == 850


# --------------------------------------------------------------------------
# Setup and entities
# --------------------------------------------------------------------------


async def test_setup_creates_the_expected_entities(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    assert entry.state is ConfigEntryState.LOADED

    status = hass.states.get("sensor.flat_status")
    assert status is not None
    assert status.state in (
        "idle",
        "ventilate_now",
        "keep_closed",
        "night_flush",
        "heat_protection",
        "air_quality",
        "unavailable_data",
    )
    # State strings must never hit the 255 character limit.
    assert len(status.state) <= 255
    assert "line1" in status.attributes

    assert hass.states.get("select.flat_mode") is not None
    assert hass.states.get("binary_sensor.flat_action_required") is not None
    assert hass.states.get("sensor.living_room_absolute_humidity") is not None
    assert hass.states.get("sensor.living_south_recommendation") is not None
    assert hass.states.get("text.flat_display_line1") is not None


async def test_night_flush_is_recommended_when_it_is_cooler_outside(
    hass: HomeAssistant,
) -> None:
    await _setup(hass)
    recommendation = hass.states.get("sensor.living_south_recommendation")
    assert recommendation is not None
    # 26.5 C inside against 17 C outside: the window should want to be open.
    assert recommendation.state in ("open_wide", "cross_ventilate", "keep_open")
    assert recommendation.attributes["reason"]


async def test_unload_removes_the_entities(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED


# --------------------------------------------------------------------------
# Services and diagnostics
# --------------------------------------------------------------------------


async def test_services_are_registered(hass: HomeAssistant) -> None:
    await _setup(hass)
    for service in (SERVICE_START_PURGE, SERVICE_SNOOZE, SERVICE_SET_MODE):
        assert hass.services.has_service(DOMAIN, service)


async def test_set_mode_service_changes_the_select(hass: HomeAssistant) -> None:
    await _setup(hass)
    await hass.services.async_call(DOMAIN, SERVICE_SET_MODE, {"mode": "winter"}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get("select.flat_mode").state == "winter"


async def test_start_purge_service_starts_a_timer(hass: HomeAssistant) -> None:
    entry = await _setup(hass)
    response = await hass.services.async_call(
        DOMAIN,
        SERVICE_START_PURGE,
        {"room": "Living room", "duration": 8},
        blocking=True,
        return_response=True,
    )
    assert response["started"][ROOM_ID] == 8
    await hass.async_block_till_done()
    assert entry.runtime_data.purge_active


async def test_diagnostics_redacts_entity_ids(hass: HomeAssistant) -> None:
    from custom_components.adaptive_ventilation.diagnostics import (
        async_get_config_entry_diagnostics,
    )
    from homeassistant.components.diagnostics import REDACTED

    entry = await _setup(hass)
    payload = await async_get_config_entry_diagnostics(hass, entry)

    assert payload["engine_version"]
    assert payload["options"][CONF_OUTDOOR_TEMPERATURE] == REDACTED
    assert payload["rooms"][0]["temperature_sensor"] == REDACTED
    assert payload["result"]["global_state"]


async def test_panel_websocket_returns_data(hass: HomeAssistant, hass_ws_client) -> None:
    await _setup(hass)
    client = await hass_ws_client(hass)
    await client.send_json_auto_id({"type": f"{DOMAIN}/panel_data"})
    response = await client.receive_json()
    assert response["success"]
    assert response["result"]["ready"] is True
    assert response["result"]["rooms"][0]["name"] == "Living room"
    assert response["result"]["schedule"]["slots"]
