"""Switch entities."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import CONF_ACTIONABLE_NOTIFICATIONS, CONF_COVER_AUTOMATION
from .coordinator import AdaptiveVentilationConfigEntry, AdaptiveVentilationCoordinator
from .entity import AdaptiveVentilationEntity
from .number import _update_option


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AdaptiveVentilationConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the entities for one config entry."""
    coordinator = entry.runtime_data
    async_add_entities(
        [
            OptionSwitch(coordinator, "notifications", "notifications_enabled", True),
            OptionSwitch(coordinator, "cover_automation", CONF_COVER_AUTOMATION, False),
            OptionSwitch(
                coordinator,
                "actionable_notifications",
                CONF_ACTIONABLE_NOTIFICATIONS,
                True,
            ),
        ]
    )


class OptionSwitch(AdaptiveVentilationEntity, SwitchEntity):
    """A boolean option, switchable without opening the options flow."""

    def __init__(
        self,
        coordinator: AdaptiveVentilationCoordinator,
        key: str,
        option: str,
        default: bool,
    ) -> None:
        super().__init__(coordinator, key)
        self._option = option
        self._default = default
        self._attr_icon = {
            "notifications": "mdi:bell",
            "cover_automation": "mdi:window-shutter-auto",
            "actionable_notifications": "mdi:gesture-tap-button",
        }.get(key, "mdi:toggle-switch")

    @property
    def available(self) -> bool:
        return True

    @property
    def is_on(self) -> bool:
        return bool(self.coordinator.config.options.get(self._option, self._default))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        if self._option != CONF_COVER_AUTOMATION:
            return {}
        allowed = [
            window.name
            for window in self.coordinator.config.windows
            if window.cover_auto_allowed and window.cover_entity
        ]
        return {"windows_allowed": allowed, "count": len(allowed)}

    async def async_turn_on(self, **kwargs: Any) -> None:
        await _update_option(self.coordinator, self._option, True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await _update_option(self.coordinator, self._option, False)
