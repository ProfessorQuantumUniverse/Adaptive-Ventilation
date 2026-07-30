"""Shared entity base classes and device wiring."""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER, NAME
from .coordinator import AdaptiveVentilationCoordinator
from .engine.state import EvaluationResult, RoomAssessment
from .models import RoomConfig, WindowConfig


class AdaptiveVentilationEntity(CoordinatorEntity[AdaptiveVentilationCoordinator]):
    """Base for every entity of this integration."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: AdaptiveVentilationCoordinator, key: str, *, unique_suffix: str = ""
    ) -> None:
        super().__init__(coordinator)
        self._key = key
        entry_id = coordinator.config_entry.entry_id
        suffix = unique_suffix or key
        self._attr_unique_id = f"{entry_id}_{suffix}"
        self._attr_translation_key = key
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry_id)},
            name=coordinator.config_entry.title or NAME,
            manufacturer=MANUFACTURER,
            model="Ventilation advisor",
            entry_type=None,
            configuration_url="homeassistant://adaptive-ventilation-panel",
        )

    @property
    def result(self) -> EvaluationResult | None:
        return self.coordinator.data

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.data is not None


class RoomEntity(AdaptiveVentilationEntity):
    """An entity that belongs to one room subentry."""

    def __init__(
        self,
        coordinator: AdaptiveVentilationCoordinator,
        room: RoomConfig,
        key: str,
    ) -> None:
        super().__init__(coordinator, key, unique_suffix=f"{room.id}_{key}")
        self.room = room
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, room.id)},
            name=room.name,
            manufacturer=MANUFACTURER,
            model="Room",
            via_device=(DOMAIN, coordinator.config_entry.entry_id),
        )

    @property
    def assessment(self) -> RoomAssessment | None:
        if self.result is None:
            return None
        return self.result.rooms.get(self.room.id)

    @property
    def room_state(self) -> Any:
        world = self.coordinator.world
        return None if world is None else world.room(self.room.id)


class WindowEntity(AdaptiveVentilationEntity):
    """An entity that belongs to one window subentry."""

    def __init__(
        self,
        coordinator: AdaptiveVentilationCoordinator,
        window: WindowConfig,
        key: str,
    ) -> None:
        super().__init__(coordinator, key, unique_suffix=f"{window.id}_{key}")
        self.window = window
        room = coordinator.config.room(window.room_id)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, window.id)},
            name=window.name,
            manufacturer=MANUFACTURER,
            model="Window",
            via_device=(DOMAIN, room.id if room else coordinator.config_entry.entry_id),
        )

    @property
    def recommendation(self) -> Any:
        if self.result is None:
            return None
        return self.result.for_window(self.window.id)

    @property
    def window_state(self) -> Any:
        world = self.coordinator.world
        return None if world is None else world.window(self.window.id)
