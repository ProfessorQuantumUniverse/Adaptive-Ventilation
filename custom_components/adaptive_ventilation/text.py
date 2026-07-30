"""Three short text entities for external displays (ESPHome, e-paper, OLED).

Read only from the user's point of view: they mirror the engine output. Writing
to them is accepted but immediately overwritten on the next update, which is
why they are marked as such in the docs.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.text import TextEntity, TextMode
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import messages
from .coordinator import AdaptiveVentilationConfigEntry, AdaptiveVentilationCoordinator
from .entity import AdaptiveVentilationEntity
from .presentation import compact_payload, countdown_minutes, display_lines


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AdaptiveVentilationConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(DisplayLineText(coordinator, index) for index in (1, 2, 3))


class DisplayLineText(AdaptiveVentilationEntity, TextEntity):
    """One line of the external display."""

    _attr_mode = TextMode.TEXT
    _attr_native_max = 255
    _attr_icon = "mdi:television-guide"

    def __init__(self, coordinator: AdaptiveVentilationCoordinator, index: int) -> None:
        super().__init__(coordinator, f"display_line{index}")
        self._index = index

    @property
    def native_value(self) -> str | None:
        if self.result is None:
            return None
        language = messages.resolve_language(self.hass.config.language)
        lines = display_lines(self.result, self.coordinator.world, language)
        return lines[self._index - 1]

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        if self.result is None or self._index != 1:
            return {}
        # The compact JSON blob lives on line 1 so REST clients only need one call.
        return {
            "countdown": countdown_minutes(self.result),
            "status": self.result.global_state.value,
            "compact": compact_payload(self.result, self.coordinator.world),
        }

    async def async_set_value(self, value: str) -> None:
        """Accept a write so scripts do not error, then restore on next update."""
        self.async_write_ha_state()
