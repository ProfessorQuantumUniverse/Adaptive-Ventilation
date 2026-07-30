"""Sensor entities."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, UnitOfPower, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import messages
from .coordinator import AdaptiveVentilationConfigEntry, AdaptiveVentilationCoordinator
from .engine.state import Action, GlobalState, MoldRisk
from .entity import AdaptiveVentilationEntity, RoomEntity, WindowEntity
from .models import RoomConfig, WindowConfig
from .presentation import (
    primary_text,
    recommendation_attributes,
    schedule_payload,
    status_attributes,
)

GRAMS_PER_CUBIC_METER = "g/m³"
KILOJOULE_PER_KILOGRAM = "kJ/kg"
KELVIN_PER_HOUR = "K/h"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AdaptiveVentilationConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data

    async_add_entities(
        [
            StatusSensor(coordinator),
            NextActionSensor(coordinator),
            TippingPointSensor(coordinator, "morning"),
            TippingPointSensor(coordinator, "evening"),
            CoolingBudgetSensor(coordinator, "today"),
            CoolingBudgetSensor(coordinator, "balance_3d"),
            OpenWindowsSensor(coordinator),
        ]
    )

    for room in coordinator.config.rooms:
        async_add_entities(
            [
                RoomAbsoluteHumiditySensor(coordinator, room),
                RoomDewPointSensor(coordinator, room),
                RoomEnthalpySensor(coordinator, room),
                RoomMoldRiskSensor(coordinator, room),
                RoomSurfaceTemperatureSensor(coordinator, room),
                RoomHeatingRateSensor(coordinator, room),
                RoomAirQualityScoreSensor(coordinator, room),
                RoomRecommendationSensor(coordinator, room),
                RoomEstimatedTemperatureSensor(coordinator, room),
            ],
            config_subentry_id=room.id,
        )

    for window in coordinator.config.windows:
        async_add_entities(
            [
                WindowRecommendationSensor(coordinator, window),
                WindowSolarLoadSensor(coordinator, window),
            ],
            config_subentry_id=window.id,
        )


# --------------------------------------------------------------------------
# Global sensors
# --------------------------------------------------------------------------


class StatusSensor(AdaptiveVentilationEntity, SensorEntity):
    """The state machine of the whole integration."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = [state.value for state in GlobalState]
    _attr_icon = "mdi:weather-windy"

    def __init__(self, coordinator: AdaptiveVentilationCoordinator) -> None:
        super().__init__(coordinator, "status")

    @property
    def native_value(self) -> str | None:
        return None if self.result is None else self.result.global_state.value

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        if self.result is None:
            return {}
        return status_attributes(self.result, self.coordinator.world, self._language)

    @property
    def _language(self) -> str:
        return messages.resolve_language(self.hass.config.language)


class NextActionSensor(AdaptiveVentilationEntity, SensorEntity):
    """Short text of the main recommendation; details live in the attributes."""

    _attr_icon = "mdi:message-alert-outline"

    def __init__(self, coordinator: AdaptiveVentilationCoordinator) -> None:
        super().__init__(coordinator, "next_action")

    @property
    def native_value(self) -> str | None:
        if self.result is None:
            return None
        return primary_text(self.result, messages.resolve_language(self.hass.config.language))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        if self.result is None:
            return {}
        language = messages.resolve_language(self.hass.config.language)
        rec = self.result.primary
        payload = recommendation_attributes(rec, language)
        payload["schedule"] = schedule_payload(self.result)
        payload["best_window"] = {
            "start": self.result.schedule.best_start.isoformat()
            if self.result.schedule.best_start
            else None,
            "end": self.result.schedule.best_end.isoformat()
            if self.result.schedule.best_end
            else None,
            "expected_k": self.result.schedule.best_delta_k,
        }
        payload["all"] = [
            {
                "id": item.id,
                "target": item.target,
                "action": item.action.value,
                "priority": item.priority.name,
                "urgency": item.urgency,
                "reason": messages.render(item.reason_key, item.reason_data, language),
            }
            for item in self.result.recommendations
            if item.action is not Action.NO_ACTION
        ]
        return payload


class TippingPointSensor(AdaptiveVentilationEntity, SensorEntity):
    """The time indoor and outdoor temperature cross - the summer number."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, coordinator: AdaptiveVentilationCoordinator, which: str) -> None:
        super().__init__(coordinator, f"tipping_point_{which}")
        self._which = which
        self._attr_icon = (
            "mdi:weather-sunset-up" if which == "morning" else "mdi:weather-sunset-down"
        )

    @property
    def native_value(self) -> datetime | None:
        if self.result is None:
            return None
        return getattr(self.result.tipping_points, self._which)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        if self.result is None:
            return {}
        tipping = self.result.tipping_points
        return {
            "confidence": round(
                tipping.morning_confidence
                if self._which == "morning"
                else tipping.evening_confidence,
                2,
            ),
            "cooler_outside": tipping.cooler_outside,
            "minutes_to_next": tipping.minutes_to_next,
        }


class CoolingBudgetSensor(AdaptiveVentilationEntity, SensorEntity):
    """How many Kelvin we can bank tonight versus how many we need."""

    _attr_native_unit_of_measurement = UnitOfTemperature.KELVIN
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:scale-balance"

    def __init__(self, coordinator: AdaptiveVentilationCoordinator, which: str) -> None:
        key = "cooling_budget_today" if which == "today" else "cooling_balance_3d"
        super().__init__(coordinator, key)
        self._which = which

    @property
    def native_value(self) -> float | None:
        if self.result is None:
            return None
        budget = self.result.cooling_budget
        return budget.achievable_tonight_k if self._which == "today" else budget.balance_3d

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        if self.result is None:
            return {}
        budget = self.result.cooling_budget
        return {
            "required_tonight_k": budget.required_tonight_k,
            "achievable_tonight_k": budget.achievable_tonight_k,
            "storage_k": budget.storage_k,
            "gained_k": budget.gained_k,
            "lost_k": budget.lost_k,
            "net_k": round(budget.net_k, 2),
            "verdict": budget.verdict_key,
            "sufficient": budget.is_sufficient,
            "history": [
                {"day": day, "net_k": value}
                for day, value in self.coordinator.memory.budget_history
            ],
        }


class OpenWindowsSensor(AdaptiveVentilationEntity, SensorEntity):
    """How many windows are open right now."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:window-open"

    def __init__(self, coordinator: AdaptiveVentilationCoordinator) -> None:
        super().__init__(coordinator, "open_windows_count")

    @property
    def native_value(self) -> int | None:
        world = self.coordinator.world
        return None if world is None else len(world.open_windows)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        world = self.coordinator.world
        if world is None:
            return {}
        return {
            "open": [w.name for w in world.open_windows],
            "tilted": [w.name for w in world.open_windows if w.is_tilted],
            "longest_open_minutes": max(
                (int(w.open_minutes(world.now) or 0) for w in world.open_windows),
                default=0,
            ),
        }


# --------------------------------------------------------------------------
# Room sensors
# --------------------------------------------------------------------------


class _RoomValueSensor(RoomEntity, SensorEntity):
    """Base for the derived per-room numbers."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attribute = ""

    @property
    def native_value(self) -> float | None:
        assessment = self.assessment
        if assessment is None:
            return None
        value = getattr(assessment, self._attribute)
        return None if value is None else round(value, 2)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        room = self.room_state
        if room is None:
            return {}
        return {"confidence": round(room.confidence, 2), "source": room.estimation_method}


class RoomAbsoluteHumiditySensor(_RoomValueSensor):
    _attribute = "absolute_humidity"
    _attr_native_unit_of_measurement = GRAMS_PER_CUBIC_METER
    _attr_icon = "mdi:water-opacity"

    def __init__(self, coordinator: AdaptiveVentilationCoordinator, room: RoomConfig) -> None:
        super().__init__(coordinator, room, "absolute_humidity")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        payload = super().extra_state_attributes
        world = self.coordinator.world
        if world is not None and world.outdoor.absolute_humidity is not None:
            payload["outdoor"] = round(world.outdoor.absolute_humidity, 2)
            room = self.room_state
            if room is not None and room.absolute_humidity is not None:
                delta = room.absolute_humidity - world.outdoor.absolute_humidity
                payload["delta"] = round(delta, 2)
                payload["ventilation_dries"] = delta > 0.3
        return payload


class RoomDewPointSensor(_RoomValueSensor):
    _attribute = "dew_point"
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS

    def __init__(self, coordinator: AdaptiveVentilationCoordinator, room: RoomConfig) -> None:
        super().__init__(coordinator, room, "dew_point")


class RoomEnthalpySensor(_RoomValueSensor):
    _attribute = "enthalpy"
    _attr_native_unit_of_measurement = KILOJOULE_PER_KILOGRAM
    _attr_icon = "mdi:chart-bell-curve"

    def __init__(self, coordinator: AdaptiveVentilationCoordinator, room: RoomConfig) -> None:
        super().__init__(coordinator, room, "enthalpy")


class RoomSurfaceTemperatureSensor(_RoomValueSensor):
    _attribute = "wall_surface_temperature"
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_icon = "mdi:home-thermometer-outline"

    def __init__(self, coordinator: AdaptiveVentilationCoordinator, room: RoomConfig) -> None:
        super().__init__(coordinator, room, "wall_surface_temperature")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        payload = super().extra_state_attributes
        assessment = self.assessment
        if assessment is not None:
            payload["surface_humidity"] = assessment.surface_humidity
            payload["mold_risk"] = assessment.mold_risk.value
        return payload


class RoomMoldRiskSensor(RoomEntity, SensorEntity):
    """Mould risk from the surface humidity on the coldest wall."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = [risk.value for risk in MoldRisk]
    _attr_icon = "mdi:blur"

    def __init__(self, coordinator: AdaptiveVentilationCoordinator, room: RoomConfig) -> None:
        super().__init__(coordinator, room, "mold_risk")

    @property
    def native_value(self) -> str | None:
        assessment = self.assessment
        return None if assessment is None else assessment.mold_risk.value

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        assessment = self.assessment
        if assessment is None:
            return {}
        return {
            "surface_humidity": assessment.surface_humidity,
            "surface_temperature": assessment.wall_surface_temperature,
            "threshold": 80.0,
        }


class RoomHeatingRateSensor(RoomEntity, SensorEntity):
    """How fast the room is heating up or cooling down right now."""

    _attr_native_unit_of_measurement = KELVIN_PER_HOUR
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:trending-up"

    def __init__(self, coordinator: AdaptiveVentilationCoordinator, room: RoomConfig) -> None:
        super().__init__(coordinator, room, "heating_rate")

    @property
    def native_value(self) -> float | None:
        assessment = self.assessment
        return None if assessment is None else assessment.heating_rate_k_per_h

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        assessment = self.assessment
        if assessment is None:
            return {}
        return {
            "projected_temperature": assessment.projected_temperature,
            "projected_time": assessment.projected_time.isoformat()
            if assessment.projected_time
            else None,
            "ventilation_effect_k_per_h": assessment.ventilation_effect_k_per_h,
        }


class RoomAirQualityScoreSensor(RoomEntity, SensorEntity):
    """0-100 across the four axes: temperature, humidity, CO2, particulates."""

    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:gauge"

    def __init__(self, coordinator: AdaptiveVentilationCoordinator, room: RoomConfig) -> None:
        super().__init__(coordinator, room, "air_quality_score")

    @property
    def native_value(self) -> int | None:
        assessment = self.assessment
        return None if assessment is None else assessment.air_quality_score

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        assessment = self.assessment
        return {} if assessment is None else dict(assessment.score_breakdown)


class RoomRecommendationSensor(RoomEntity, SensorEntity):
    """The strongest recommendation currently affecting this room."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = [action.value for action in Action]
    _attr_icon = "mdi:lightbulb-on-outline"

    def __init__(self, coordinator: AdaptiveVentilationCoordinator, room: RoomConfig) -> None:
        super().__init__(coordinator, room, "recommendation")

    def _best(self):  # noqa: ANN202 - internal helper
        if self.result is None:
            return None
        candidates = [
            rec
            for rec in self.result.for_room(self.room.id)
            if rec.action is not Action.NO_ACTION
        ]
        return max(candidates, key=lambda r: r.sort_key) if candidates else None

    @property
    def native_value(self) -> str | None:
        rec = self._best()
        return Action.NO_ACTION.value if rec is None else rec.action.value

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        language = messages.resolve_language(self.hass.config.language)
        return recommendation_attributes(self._best(), language)


class RoomEstimatedTemperatureSensor(RoomEntity, SensorEntity):
    """Temperature of the room including the estimation fallback chain."""

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: AdaptiveVentilationCoordinator, room: RoomConfig) -> None:
        super().__init__(coordinator, room, "temperature_estimated")

    @property
    def native_value(self) -> float | None:
        room = self.room_state
        return None if room is None or room.temperature is None else round(room.temperature, 1)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        room = self.room_state
        if room is None:
            return {}
        return {
            "confidence": round(room.confidence, 2),
            "estimation_method": room.estimation_method,
            "measured": room.estimation_method == "measured",
        }


# --------------------------------------------------------------------------
# Window sensors
# --------------------------------------------------------------------------


class WindowRecommendationSensor(WindowEntity, SensorEntity):
    """What to do with this window. State is the enum, details in attributes."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = [action.value for action in Action]
    _attr_icon = "mdi:window-open-variant"

    def __init__(
        self, coordinator: AdaptiveVentilationCoordinator, window: WindowConfig
    ) -> None:
        super().__init__(coordinator, window, "recommendation")

    @property
    def native_value(self) -> str | None:
        rec = self.recommendation
        return Action.NO_ACTION.value if rec is None else rec.action.value

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        language = messages.resolve_language(self.hass.config.language)
        payload = recommendation_attributes(self.recommendation, language)
        window = self.window_state
        if window is not None:
            payload["is_open"] = window.is_open
            payload["is_tilted"] = window.is_tilted
            payload["azimuth"] = window.azimuth
        if self.result is not None:
            cover = next(
                (
                    rec
                    for rec in self.result.recommendations
                    if rec.target == f"{self.window.id}:cover"
                ),
                None,
            )
            if cover is not None:
                payload["cover_action"] = cover.action.value
                payload["cover_reason"] = messages.render(
                    cover.reason_key, cover.reason_data, language
                )
        return payload


class WindowSolarLoadSensor(WindowEntity, SensorEntity):
    """Solar power currently entering through this window."""

    _attr_device_class = SensorDeviceClass.POWER
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:solar-power-variant"

    def __init__(
        self, coordinator: AdaptiveVentilationCoordinator, window: WindowConfig
    ) -> None:
        super().__init__(coordinator, window, "solar_load")

    @property
    def native_value(self) -> float | None:
        if self.result is None:
            return None
        loads = self.result.diagnostics.get("solar_loads", {})
        value = loads.get(self.window.id)
        return None if value is None else round(float(value))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        if self.result is None:
            return {}
        savings = self.result.diagnostics.get("cover_savings", {})
        return {
            "cover_saving_w": savings.get(self.window.id),
            "azimuth": self.window.azimuth,
            "area_m2": self.window.area_m2,
            "cover_external": self.window.cover_external,
        }
