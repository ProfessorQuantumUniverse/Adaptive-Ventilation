"""Sidebar panel registration and its websocket API.

The panel is a single hand written ES module - no build step, no bundler, no
node_modules in the repository. It talks to Home Assistant over the normal
websocket connection, so it inherits authentication and live updates.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import voluptuous as vol
from homeassistant.components import frontend, websocket_api
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, callback

from .const import (
    DOMAIN,
    PANEL_FILENAME,
    PANEL_ICON,
    PANEL_TITLE,
    PANEL_URL,
)
from .engine.state import Mode
from .presentation import panel_payload

_LOGGER = logging.getLogger(__name__)

STATIC_URL = f"/{DOMAIN}_frontend"
_REGISTERED = f"{DOMAIN}_panel_registered"


async def async_register_panel(hass: HomeAssistant) -> None:
    """Serve the panel module and add it to the sidebar."""
    if hass.data.get(_REGISTERED):
        return

    source = Path(__file__).parent / "frontend"
    await hass.http.async_register_static_paths(
        [StaticPathConfig(STATIC_URL, str(source), cache_headers=False)]
    )

    frontend.async_register_built_in_panel(
        hass,
        component_name="custom",
        sidebar_title=PANEL_TITLE,
        sidebar_icon=PANEL_ICON,
        frontend_url_path=PANEL_URL.lstrip("/"),
        require_admin=False,
        config={
            "_panel_custom": {
                "name": "adaptive-ventilation-panel",
                "embed_iframe": False,
                "trust_external": False,
                "module_url": f"{STATIC_URL}/{PANEL_FILENAME}",
            }
        },
    )

    _register_websocket_api(hass)
    hass.data[_REGISTERED] = True


@callback
def async_unregister_panel(hass: HomeAssistant) -> None:
    if not hass.data.pop(_REGISTERED, False):
        return
    frontend.async_remove_panel(hass, PANEL_URL.lstrip("/"))


@callback
def _register_websocket_api(hass: HomeAssistant) -> None:
    websocket_api.async_register_command(hass, ws_panel_data)
    websocket_api.async_register_command(hass, ws_action)
    websocket_api.async_register_command(hass, ws_simulate)


def _entries(hass: HomeAssistant) -> list[Any]:
    return [
        entry
        for entry in hass.config_entries.async_entries(DOMAIN)
        if entry.state is ConfigEntryState.LOADED
    ]


def _coordinator(hass: HomeAssistant, entry_id: str | None) -> Any | None:
    entries = _entries(hass)
    if entry_id:
        entries = [entry for entry in entries if entry.entry_id == entry_id]
    return entries[0].runtime_data if entries else None


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/panel_data",
        vol.Optional("entry_id"): str,
    }
)
@websocket_api.async_response
async def ws_panel_data(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Everything the panel renders, in one document."""
    coordinator = _coordinator(hass, msg.get("entry_id"))
    if coordinator is None:
        connection.send_result(
            msg["id"], {"ready": False, "entries": [], "error": "not_loaded"}
        )
        return

    payload = panel_payload(coordinator)
    payload["entries"] = [
        {"entry_id": entry.entry_id, "title": entry.title} for entry in _entries(hass)
    ]
    payload["entry_id"] = coordinator.config_entry.entry_id
    connection.send_result(msg["id"], payload)


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/action",
        vol.Optional("entry_id"): str,
        vol.Required("action"): vol.In(
            ["acknowledge", "snooze", "ignore_today", "purge", "set_mode", "set_option",
             "override", "recalibrate"]
        ),
        vol.Optional("recommendation_id"): str,
        vol.Optional("room_id"): str,
        vol.Optional("duration"): int,
        vol.Optional("mode"): str,
        vol.Optional("option"): str,
        vol.Optional("value"): vol.Any(str, int, float, bool, None),
        vol.Optional("parameter"): str,
        vol.Optional("reset"): bool,
    }
)
@websocket_api.async_response
async def ws_action(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Everything the panel can trigger, from one endpoint."""
    coordinator = _coordinator(hass, msg.get("entry_id"))
    if coordinator is None:
        connection.send_error(msg["id"], "not_loaded", "no loaded config entry")
        return

    action = msg["action"]
    try:
        if action == "acknowledge":
            await coordinator.async_acknowledge(msg["recommendation_id"])
        elif action == "snooze":
            await coordinator.async_snooze(msg.get("recommendation_id"), "1h")
        elif action == "ignore_today":
            await coordinator.async_snooze(msg.get("recommendation_id"), "today")
        elif action == "purge":
            await coordinator.async_start_purge(msg.get("room_id"), msg.get("duration"))
        elif action == "set_mode":
            await coordinator.async_set_mode(Mode(msg["mode"]))
        elif action == "set_option":
            options = dict(coordinator.config_entry.options)
            options[msg["option"]] = msg.get("value")
            hass.config_entries.async_update_entry(
                coordinator.config_entry, options=options
            )
        elif action == "override":
            await coordinator.async_override_parameter(
                msg["room_id"],
                msg["parameter"],
                msg.get("value"),
                bool(msg.get("reset", False)),
            )
        elif action == "recalibrate":
            await coordinator.async_recalibrate()
    except Exception as err:  # noqa: BLE001 - report back instead of dropping the socket
        _LOGGER.exception("panel action %s failed", action)
        connection.send_error(msg["id"], "action_failed", str(err))
        return

    connection.send_result(msg["id"], {"ok": True})


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/simulate",
        vol.Optional("entry_id"): str,
        vol.Required("room_id"): str,
        vol.Optional("window_ids"): [str],
        vol.Optional("open_from"): str,
        vol.Optional("open_until"): str,
        vol.Optional("shading_from"): str,
    }
)
@websocket_api.async_response
async def ws_simulate(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """What-if simulation for the tuning tab."""
    from homeassistant.util import dt as dt_util

    coordinator = _coordinator(hass, msg.get("entry_id"))
    if coordinator is None:
        connection.send_error(msg["id"], "not_loaded", "no loaded config entry")
        return

    def _parse(key: str):  # noqa: ANN202 - tiny local helper
        raw = msg.get(key)
        return dt_util.parse_datetime(raw) if raw else None

    try:
        result = await coordinator.async_simulate(
            room_id=msg["room_id"],
            window_ids=msg.get("window_ids", []),
            open_from=_parse("open_from"),
            open_until=_parse("open_until"),
            shading_from=_parse("shading_from"),
        )
    except Exception as err:  # noqa: BLE001
        connection.send_error(msg["id"], "simulate_failed", str(err))
        return
    connection.send_result(msg["id"], result)
