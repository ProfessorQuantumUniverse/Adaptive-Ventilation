"""Service handlers.

Every service targets one config entry; when the user has only one flat (the
normal case) the entry can be omitted and we pick the single loaded one.
"""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.util import dt as dt_util

from .const import (
    ATTR_DURATION,
    ATTR_END,
    ATTR_PARAMETER,
    ATTR_RECOMMENDATION_ID,
    ATTR_RESET,
    ATTR_ROOM,
    ATTR_SCOPE,
    ATTR_SHADING_FROM,
    ATTR_START,
    ATTR_VALUE,
    ATTR_WINDOW,
    DOMAIN,
    SERVICE_ACKNOWLEDGE,
    SERVICE_EXPORT_DIAGNOSTICS,
    SERVICE_OVERRIDE_PARAMETER,
    SERVICE_RECALIBRATE,
    SERVICE_SET_MODE,
    SERVICE_SIMULATE,
    SERVICE_SNOOZE,
    SERVICE_START_PURGE,
    SNOOZE_SCOPES,
)
from .coordinator import AdaptiveVentilationCoordinator
from .engine.state import Mode

_LOGGER = logging.getLogger(__name__)

CONF_ENTRY_ID = "entry_id"

OVERRIDABLE_PARAMETERS = (
    "tau_hours",
    "night_cooling_k_per_h",
    "solar_gain_coefficient",
    "internal_gain_w",
    "air_changes_per_hour",
)

_BASE = {vol.Optional(CONF_ENTRY_ID): cv.string}

START_PURGE_SCHEMA = vol.Schema(
    {
        **_BASE,
        vol.Optional(ATTR_ROOM): cv.string,
        vol.Optional(ATTR_DURATION): vol.All(vol.Coerce(int), vol.Range(min=1, max=120)),
    }
)
SNOOZE_SCHEMA = vol.Schema(
    {
        **_BASE,
        vol.Optional(ATTR_RECOMMENDATION_ID): cv.string,
        vol.Optional(ATTR_SCOPE, default="1h"): vol.In(SNOOZE_SCOPES),
    }
)
ACKNOWLEDGE_SCHEMA = vol.Schema(
    {**_BASE, vol.Required(ATTR_RECOMMENDATION_ID): cv.string}
)
SET_MODE_SCHEMA = vol.Schema(
    {**_BASE, vol.Required("mode"): vol.In([m.value for m in Mode])}
)
RECALIBRATE_SCHEMA = vol.Schema(_BASE)
OVERRIDE_SCHEMA = vol.Schema(
    {
        **_BASE,
        vol.Required(ATTR_ROOM): cv.string,
        vol.Required(ATTR_PARAMETER): vol.In(OVERRIDABLE_PARAMETERS),
        vol.Optional(ATTR_VALUE): vol.Coerce(float),
        vol.Optional(ATTR_RESET, default=False): cv.boolean,
    }
)
EXPORT_SCHEMA = vol.Schema(_BASE)
SIMULATE_SCHEMA = vol.Schema(
    {
        **_BASE,
        vol.Required(ATTR_ROOM): cv.string,
        vol.Optional(ATTR_WINDOW): vol.All(cv.ensure_list, [cv.string]),
        vol.Optional(ATTR_START): cv.datetime,
        vol.Optional(ATTR_END): cv.datetime,
        vol.Optional(ATTR_SHADING_FROM): cv.datetime,
    }
)


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register every service exactly once."""
    if hass.services.has_service(DOMAIN, SERVICE_START_PURGE):
        return

    hass.services.async_register(
        DOMAIN,
        SERVICE_START_PURGE,
        _start_purge,
        schema=START_PURGE_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(DOMAIN, SERVICE_SNOOZE, _snooze, schema=SNOOZE_SCHEMA)
    hass.services.async_register(
        DOMAIN, SERVICE_ACKNOWLEDGE, _acknowledge, schema=ACKNOWLEDGE_SCHEMA
    )
    hass.services.async_register(DOMAIN, SERVICE_SET_MODE, _set_mode, schema=SET_MODE_SCHEMA)
    hass.services.async_register(
        DOMAIN,
        SERVICE_RECALIBRATE,
        _recalibrate,
        schema=RECALIBRATE_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_OVERRIDE_PARAMETER, _override, schema=OVERRIDE_SCHEMA
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_EXPORT_DIAGNOSTICS,
        _export_diagnostics,
        schema=EXPORT_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SIMULATE,
        _simulate,
        schema=SIMULATE_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )


@callback
def async_unload_services(hass: HomeAssistant) -> None:
    for service in (
        SERVICE_START_PURGE,
        SERVICE_SNOOZE,
        SERVICE_ACKNOWLEDGE,
        SERVICE_SET_MODE,
        SERVICE_RECALIBRATE,
        SERVICE_OVERRIDE_PARAMETER,
        SERVICE_EXPORT_DIAGNOSTICS,
        SERVICE_SIMULATE,
    ):
        hass.services.async_remove(DOMAIN, service)


def _coordinator(hass: HomeAssistant, call: ServiceCall) -> AdaptiveVentilationCoordinator:
    entries = [
        entry
        for entry in hass.config_entries.async_entries(DOMAIN)
        if entry.state is ConfigEntryState.LOADED
    ]
    entry_id = call.data.get(CONF_ENTRY_ID)
    if entry_id:
        entries = [entry for entry in entries if entry.entry_id == entry_id]
    if not entries:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="no_entry_found"
        )
    if len(entries) > 1:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="entry_id_required"
        )
    return entries[0].runtime_data


def _resolve_room(coordinator: AdaptiveVentilationCoordinator, value: str | None) -> str | None:
    """Accept a subentry id or a room name - users will type the name."""
    if not value:
        return None
    if coordinator.config.room(value) is not None:
        return value
    lowered = value.strip().lower()
    for room in coordinator.config.rooms:
        if room.name.strip().lower() == lowered:
            return room.id
    raise ServiceValidationError(
        translation_domain=DOMAIN,
        translation_key="unknown_room",
        translation_placeholders={"room": value},
    )


async def _start_purge(call: ServiceCall) -> ServiceResponse:
    coordinator = _coordinator(call.hass, call)
    room_id = _resolve_room(coordinator, call.data.get(ATTR_ROOM))
    return await coordinator.async_start_purge(room_id, call.data.get(ATTR_DURATION))


async def _snooze(call: ServiceCall) -> None:
    coordinator = _coordinator(call.hass, call)
    await coordinator.async_snooze(
        call.data.get(ATTR_RECOMMENDATION_ID), call.data.get(ATTR_SCOPE, "1h")
    )


async def _acknowledge(call: ServiceCall) -> None:
    coordinator = _coordinator(call.hass, call)
    await coordinator.async_acknowledge(call.data[ATTR_RECOMMENDATION_ID])


async def _set_mode(call: ServiceCall) -> None:
    coordinator = _coordinator(call.hass, call)
    await coordinator.async_set_mode(Mode(call.data["mode"]))


async def _recalibrate(call: ServiceCall) -> ServiceResponse:
    coordinator = _coordinator(call.hass, call)
    return await coordinator.async_recalibrate()


async def _override(call: ServiceCall) -> None:
    coordinator = _coordinator(call.hass, call)
    room_id = _resolve_room(coordinator, call.data[ATTR_ROOM])
    assert room_id is not None
    await coordinator.async_override_parameter(
        room_id,
        call.data[ATTR_PARAMETER],
        call.data.get(ATTR_VALUE),
        call.data.get(ATTR_RESET, False),
    )


async def _export_diagnostics(call: ServiceCall) -> ServiceResponse:
    from .diagnostics import build_diagnostics

    coordinator = _coordinator(call.hass, call)
    return build_diagnostics(coordinator)


async def _simulate(call: ServiceCall) -> ServiceResponse:
    coordinator = _coordinator(call.hass, call)
    room_id = _resolve_room(coordinator, call.data[ATTR_ROOM])
    assert room_id is not None

    window_ids: list[str] = []
    for value in call.data.get(ATTR_WINDOW, []) or []:
        window = coordinator.config.window(value) or next(
            (w for w in coordinator.config.windows if w.name.lower() == value.lower()), None
        )
        if window is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="unknown_window",
                translation_placeholders={"window": value},
            )
        window_ids.append(window.id)

    payload: dict[str, Any] = {
        "room_id": room_id,
        "window_ids": window_ids,
        "open_from": _utc(call.data.get(ATTR_START)),
        "open_until": _utc(call.data.get(ATTR_END)),
        "shading_from": _utc(call.data.get(ATTR_SHADING_FROM)),
    }
    return await coordinator.async_simulate(**payload)


def _utc(value: Any) -> Any:
    return None if value is None else dt_util.as_utc(value)
