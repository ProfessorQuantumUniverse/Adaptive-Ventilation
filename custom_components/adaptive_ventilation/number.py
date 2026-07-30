"""Live tuning knobs for the most important thresholds and weights.

These write straight into the config entry options, which is what the options
flow and the panel's tuning tab also do - one source of truth.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import PERCENTAGE, UnitOfTemperature, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import (
    CONF_CLOSE_LEAD_TIME,
    CONF_CO2_THRESHOLD,
    CONF_COOLDOWN,
    CONF_DELTA_T_HYSTERESIS,
    CONF_MAX_PUSHES,
    CONF_MIN_STATE_DURATION,
    CONF_PM25_INDOOR,
    CONF_SUMMER_MAX,
    CONF_SUMMER_MIN,
    CONF_WEIGHT_CO2,
    CONF_WEIGHT_HUMIDITY,
    CONF_WEIGHT_PARTICULATE,
    CONF_WEIGHT_TEMPERATURE,
    CONF_WINTER_MAX,
    CONF_WINTER_MIN,
)
from .coordinator import AdaptiveVentilationConfigEntry, AdaptiveVentilationCoordinator
from .engine.state import Preferences
from .entity import AdaptiveVentilationEntity

PPM = "ppm"
MICROGRAMS = "µg/m³"


@dataclass(frozen=True)
class TuningNumber:
    """One tunable option exposed as a number entity."""

    key: str
    minimum: float
    maximum: float
    step: float
    unit: str | None = None
    icon: str = "mdi:tune"
    mode: NumberMode = NumberMode.SLIDER


TUNING: tuple[TuningNumber, ...] = (
    TuningNumber(CONF_WEIGHT_TEMPERATURE, 0, 100, 5, PERCENTAGE, "mdi:thermometer"),
    TuningNumber(CONF_WEIGHT_HUMIDITY, 0, 100, 5, PERCENTAGE, "mdi:water-percent"),
    TuningNumber(CONF_WEIGHT_CO2, 0, 100, 5, PERCENTAGE, "mdi:molecule-co2"),
    TuningNumber(CONF_WEIGHT_PARTICULATE, 0, 100, 5, PERCENTAGE, "mdi:blur"),
    TuningNumber(
        CONF_SUMMER_MIN, 15, 28, 0.5, UnitOfTemperature.CELSIUS, "mdi:sun-thermometer"
    ),
    TuningNumber(
        CONF_SUMMER_MAX, 18, 32, 0.5, UnitOfTemperature.CELSIUS, "mdi:sun-thermometer"
    ),
    TuningNumber(
        CONF_WINTER_MIN, 14, 24, 0.5, UnitOfTemperature.CELSIUS, "mdi:snowflake-thermometer"
    ),
    TuningNumber(
        CONF_WINTER_MAX, 16, 28, 0.5, UnitOfTemperature.CELSIUS, "mdi:snowflake-thermometer"
    ),
    TuningNumber(CONF_CO2_THRESHOLD, 600, 2000, 50, PPM, "mdi:molecule-co2", NumberMode.BOX),
    TuningNumber(CONF_PM25_INDOOR, 5, 100, 1, MICROGRAMS, "mdi:blur", NumberMode.BOX),
    TuningNumber(
        CONF_DELTA_T_HYSTERESIS, 0.1, 3.0, 0.1, UnitOfTemperature.KELVIN, "mdi:sine-wave"
    ),
    TuningNumber(
        CONF_MIN_STATE_DURATION, 5, 120, 5, UnitOfTime.MINUTES, "mdi:timer-lock-outline"
    ),
    TuningNumber(CONF_COOLDOWN, 10, 240, 10, UnitOfTime.MINUTES, "mdi:timer-refresh-outline"),
    TuningNumber(
        CONF_CLOSE_LEAD_TIME, 5, 120, 5, UnitOfTime.MINUTES, "mdi:clock-alert-outline"
    ),
    TuningNumber(CONF_MAX_PUSHES, 0, 30, 1, None, "mdi:bell-outline", NumberMode.BOX),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AdaptiveVentilationConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(TuningNumberEntity(coordinator, spec) for spec in TUNING)


class TuningNumberEntity(AdaptiveVentilationEntity, NumberEntity):
    """A single tunable preference."""

    _attr_entity_registry_enabled_default = False

    def __init__(
        self, coordinator: AdaptiveVentilationCoordinator, spec: TuningNumber
    ) -> None:
        super().__init__(coordinator, spec.key)
        self._spec = spec
        self._attr_native_min_value = spec.minimum
        self._attr_native_max_value = spec.maximum
        self._attr_native_step = spec.step
        self._attr_native_unit_of_measurement = spec.unit
        self._attr_icon = spec.icon
        self._attr_mode = spec.mode

    @property
    def available(self) -> bool:
        return True

    @property
    def native_value(self) -> float:
        options = self.coordinator.config.options
        if self._spec.key in options and options[self._spec.key] is not None:
            return float(options[self._spec.key])
        return float(getattr(Preferences(), self._spec.key))

    async def async_set_native_value(self, value: float) -> None:
        await _update_option(self.coordinator, self._spec.key, _coerce(self._spec, value))


def _coerce(spec: TuningNumber, value: float) -> Any:
    return int(round(value)) if spec.step >= 1 and float(spec.step).is_integer() else value


async def _update_option(
    coordinator: AdaptiveVentilationCoordinator, key: str, value: Any
) -> None:
    """Write one option back to the config entry (this triggers a reload)."""
    options = dict(coordinator.config_entry.options)
    options[key] = value
    coordinator.hass.config_entries.async_update_entry(
        coordinator.config_entry, options=options
    )
