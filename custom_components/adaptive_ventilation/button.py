"""Button entities."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import AdaptiveVentilationConfigEntry, AdaptiveVentilationCoordinator
from .entity import AdaptiveVentilationEntity, RoomEntity
from .models import RoomConfig


@dataclass(frozen=True)
class ButtonSpec:
    """One global button and the coordinator call behind it."""

    key: str
    icon: str
    action: Callable[[AdaptiveVentilationCoordinator], Awaitable[object]]


BUTTONS: tuple[ButtonSpec, ...] = (
    ButtonSpec("purge_now", "mdi:air-filter", lambda c: c.async_start_purge()),
    ButtonSpec("snooze_1h", "mdi:bell-sleep", lambda c: c.async_snooze(None, "1h")),
    ButtonSpec("recalibrate", "mdi:reload", lambda c: c.async_recalibrate()),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AdaptiveVentilationConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the entities for one config entry."""
    coordinator = entry.runtime_data
    async_add_entities(ActionButton(coordinator, spec) for spec in BUTTONS)
    for room in coordinator.config.rooms:
        async_add_entities([RoomPurgeButton(coordinator, room)], config_subentry_id=room.id)


class ActionButton(AdaptiveVentilationEntity, ButtonEntity):
    """Global one-shot actions."""

    def __init__(self, coordinator: AdaptiveVentilationCoordinator, spec: ButtonSpec) -> None:
        super().__init__(coordinator, spec.key)
        self._spec = spec
        self._attr_icon = spec.icon

    @property
    def available(self) -> bool:
        return True

    async def async_press(self) -> None:
        await self._spec.action(self.coordinator)


class RoomPurgeButton(RoomEntity, ButtonEntity):
    """Start a purge for exactly this room, with the calculated duration."""

    _attr_icon = "mdi:air-filter"

    def __init__(self, coordinator: AdaptiveVentilationCoordinator, room: RoomConfig) -> None:
        super().__init__(coordinator, room, "purge_room")

    @property
    def available(self) -> bool:
        return True

    async def async_press(self) -> None:
        await self.coordinator.async_start_purge(self.room.id)
