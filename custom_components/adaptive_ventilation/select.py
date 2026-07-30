"""Mode selector."""

from __future__ import annotations

from typing import ClassVar

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import AdaptiveVentilationConfigEntry, AdaptiveVentilationCoordinator
from .engine.state import Mode
from .entity import AdaptiveVentilationEntity

SELECTABLE_MODES = [Mode.AUTO, Mode.SUMMER, Mode.WINTER, Mode.AWAY, Mode.OFF]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AdaptiveVentilationConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the entities for one config entry."""
    async_add_entities([ModeSelect(entry.runtime_data)])


class ModeSelect(AdaptiveVentilationEntity, SelectEntity):
    """AUTO / SUMMER / WINTER / AWAY / OFF."""

    _attr_icon = "mdi:tune-variant"
    _attr_options: ClassVar[list[str]] = [mode.value for mode in SELECTABLE_MODES]

    def __init__(self, coordinator: AdaptiveVentilationCoordinator) -> None:
        super().__init__(coordinator, "mode")

    @property
    def available(self) -> bool:
        # The mode must stay changeable even before the first evaluation.
        return True

    @property
    def current_option(self) -> str:
        return self.coordinator.mode.value

    @property
    def extra_state_attributes(self) -> dict[str, str | None]:
        result = self.result
        return {
            "resolved_season": None if result is None else result.diagnostics.get("season"),
        }

    async def async_select_option(self, option: str) -> None:
        await self.coordinator.async_set_mode(Mode(option))
