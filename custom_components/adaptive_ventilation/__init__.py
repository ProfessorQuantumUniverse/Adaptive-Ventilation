"""The Adaptive Ventilation integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN, PLATFORMS
from .coordinator import AdaptiveVentilationConfigEntry, AdaptiveVentilationCoordinator
from .devices import async_register_devices
from .panel import async_register_panel, async_unregister_panel
from .services import async_setup_services, async_unload_services

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: AdaptiveVentilationConfigEntry) -> bool:
    """Set up Adaptive Ventilation from a config entry."""
    coordinator = AdaptiveVentilationCoordinator(hass, entry)
    await coordinator.async_setup()
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator
    # Before the platforms, so a window can nest under its room no matter which
    # platform gets scheduled first.
    async_register_devices(hass, entry)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    await async_register_panel(hass)
    async_setup_services(hass)

    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: AdaptiveVentilationConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_shutdown()

    remaining = [
        candidate
        for candidate in hass.config_entries.async_entries(DOMAIN)
        if candidate.entry_id != entry.entry_id and candidate.state.recoverable
    ]
    if not remaining:
        async_unload_services(hass)
        async_unregister_panel(hass)
    return unloaded


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload when the options or the subentries changed."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: ConfigEntry, device_entry
) -> bool:
    """Allow removing a room/window device once its subentry is gone."""
    identifiers = {identifier[1] for identifier in device_entry.identifiers}
    known = {entry.entry_id} | set(entry.subentries)
    return not (identifiers & known)


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate old config entries. Version 1 is the first public schema."""
    return entry.version <= 1
