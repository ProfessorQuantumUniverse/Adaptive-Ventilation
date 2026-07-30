"""Binary sensor entities."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import messages
from .coordinator import AdaptiveVentilationConfigEntry, AdaptiveVentilationCoordinator
from .engine.state import OPENING_ACTIONS, Action, GlobalState, Priority
from .entity import AdaptiveVentilationEntity, WindowEntity
from .models import WindowConfig
from .presentation import countdown_minutes, recommendation_attributes


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AdaptiveVentilationConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        [
            ActionRequiredBinarySensor(coordinator),
            StormRiskBinarySensor(coordinator),
            PurgeRunningBinarySensor(coordinator),
        ]
    )
    for window in coordinator.config.windows:
        async_add_entities(
            [ShouldBeOpenBinarySensor(coordinator, window)],
            config_subentry_id=window.id,
        )


class ActionRequiredBinarySensor(AdaptiveVentilationEntity, BinarySensorEntity):
    """On whenever the user actually has to do something."""

    _attr_icon = "mdi:hand-pointing-up"

    def __init__(self, coordinator: AdaptiveVentilationCoordinator) -> None:
        super().__init__(coordinator, "action_required")

    @property
    def is_on(self) -> bool | None:
        return None if self.result is None else self.result.action_required

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        if self.result is None:
            return {}
        language = messages.resolve_language(self.hass.config.language)
        payload = recommendation_attributes(self.result.primary, language)
        payload["countdown"] = countdown_minutes(self.result)
        return payload


class StormRiskBinarySensor(AdaptiveVentilationEntity, BinarySensorEntity):
    """On while a storm veto is active."""

    _attr_device_class = BinarySensorDeviceClass.SAFETY

    def __init__(self, coordinator: AdaptiveVentilationCoordinator) -> None:
        super().__init__(coordinator, "storm_risk")

    @property
    def is_on(self) -> bool | None:
        if self.result is None:
            return None
        return self.result.global_state is GlobalState.STORM

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        world = self.coordinator.world
        if world is None:
            return {}
        return {
            "alerts": [
                {
                    "event": alert.event,
                    "severity": alert.severity,
                    "kind": alert.kind,
                    "end": alert.end.isoformat() if alert.end else None,
                }
                for alert in world.active_alerts
            ],
            "wind_kmh": round((world.outdoor.wind_speed or 0.0) * 3.6, 1),
        }


class PurgeRunningBinarySensor(AdaptiveVentilationEntity, BinarySensorEntity):
    """On while a purge timer is running."""

    _attr_icon = "mdi:timer-sand"

    def __init__(self, coordinator: AdaptiveVentilationCoordinator) -> None:
        super().__init__(coordinator, "purge_running")

    @property
    def is_on(self) -> bool:
        return bool(self.coordinator.purge_active)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        world = self.coordinator.world
        now = world.now if world else None
        return {
            "rooms": [
                {
                    "room_id": room_id,
                    "ends_at": end.isoformat(),
                    "minutes_left": max(
                        0, int((end - now).total_seconds() / 60)
                    )
                    if now
                    else None,
                }
                for room_id, end in self.coordinator.purge_active.items()
            ]
        }


class ShouldBeOpenBinarySensor(WindowEntity, BinarySensorEntity):
    """Whether this window should be open right now."""

    _attr_device_class = BinarySensorDeviceClass.WINDOW

    def __init__(
        self, coordinator: AdaptiveVentilationCoordinator, window: WindowConfig
    ) -> None:
        super().__init__(coordinator, window, "should_be_open")

    @property
    def is_on(self) -> bool | None:
        rec = self.recommendation
        if rec is None:
            return None
        if rec.action in OPENING_ACTIONS:
            return True
        if rec.action is Action.NO_ACTION:
            window = self.window_state
            return None if window is None else window.is_open
        return False

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        rec = self.recommendation
        window = self.window_state
        payload: dict[str, Any] = {
            "matches_reality": None,
            "priority": rec.priority.name if rec else Priority.OPTIMIZATION.name,
        }
        if rec is not None and window is not None and rec.action is not Action.NO_ACTION:
            wanted_open = rec.action in OPENING_ACTIONS
            payload["matches_reality"] = window.is_open == wanted_open
        return payload
