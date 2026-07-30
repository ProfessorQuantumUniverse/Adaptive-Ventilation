"""Device registration.

The devices are created here, in order, before any platform is set up, rather
than being created as a side effect of whichever platform happens to run first.

Windows are nested under their room via ``via_device``, and referencing a
``via_device`` that does not exist yet is deprecated in Home Assistant (and
stops working in 2025.12). Platforms are forwarded concurrently, so relying on
the sensor platform to have created the room device first is exactly the kind
of ordering assumption that breaks on a slow morning.
"""

from __future__ import annotations

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr

from .const import DOMAIN, MANUFACTURER, NAME
from .coordinator import AdaptiveVentilationConfigEntry


@callback
def async_register_devices(hass: HomeAssistant, entry: AdaptiveVentilationConfigEntry) -> None:
    """Create the hub, then the rooms, then the windows."""
    registry = dr.async_get(hass)
    coordinator = entry.runtime_data

    registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        name=entry.title or NAME,
        manufacturer=MANUFACTURER,
        model="Ventilation advisor",
        configuration_url="homeassistant://adaptive-ventilation-panel",
    )

    for room in coordinator.config.rooms:
        registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            config_subentry_id=room.id,
            identifiers={(DOMAIN, room.id)},
            name=room.name,
            manufacturer=MANUFACTURER,
            model="Room",
            via_device=(DOMAIN, entry.entry_id),
        )

    for window in coordinator.config.windows:
        room = coordinator.config.room(window.room_id)
        registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            config_subentry_id=window.id,
            identifiers={(DOMAIN, window.id)},
            name=window.name,
            manufacturer=MANUFACTURER,
            model="Window",
            via_device=(DOMAIN, room.id if room else entry.entry_id),
        )
