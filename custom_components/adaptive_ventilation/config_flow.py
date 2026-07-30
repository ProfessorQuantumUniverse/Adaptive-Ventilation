"""Config flow, subentry flows for rooms and windows, and the options flow.

UX contract from the specification: a working basic setup in two minutes with
sensible defaults, everything else behind an "advanced" section. An abandoned
configuration is the most realistic failure mode at this feature size.
"""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    OptionsFlow,
    SubentryFlowResult,
)
from homeassistant.core import callback
from homeassistant.data_entry_flow import section
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.selector import (
    BooleanSelector,
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TimeSelector,
)
import voluptuous as vol

from .const import (
    COMPASS_PRESETS,
    CONF_ACTIONABLE_NOTIFICATIONS,
    CONF_AZIMUTH,
    CONF_BUILDING_TYPE,
    CONF_CEILING_HEIGHT,
    CONF_CLIMATE_ENTITY,
    CONF_CLOSE_LEAD_TIME,
    CONF_CO2_SENSOR,
    CONF_CO2_THRESHOLD,
    CONF_CONNECTED_ROOMS,
    CONF_CONSTRUCTION_YEAR,
    CONF_CONTACT_SENSOR,
    CONF_COOLDOWN,
    CONF_COVER_AUTO,
    CONF_COVER_AUTOMATION,
    CONF_COVER_ENTITY,
    CONF_COVER_EXTERNAL,
    CONF_DELTA_T_HYSTERESIS,
    CONF_ESTIMATION,
    CONF_F_RT,
    CONF_FAN_ENTITY,
    CONF_FLOOR,
    CONF_G_VALUE,
    CONF_GROUND_FLOOR,
    CONF_HUMIDITY_MAX,
    CONF_HUMIDITY_MIN,
    CONF_HUMIDITY_SENSOR,
    CONF_ILLUMINANCE,
    CONF_IS_BASEMENT,
    CONF_IS_BEDROOM,
    CONF_IS_MOISTURE_SOURCE,
    CONF_IS_TOP_FLOOR,
    CONF_IS_UNHEATED,
    CONF_MAX_PUSHES,
    CONF_MIN_CONFIDENCE,
    CONF_MIN_STATE_DURATION,
    CONF_NOTIFICATION_RESTRAINT,
    CONF_NOTIFY_TARGETS,
    CONF_OCCUPANCY_SENSOR,
    CONF_OK_WHEN_AWAY,
    CONF_OUTDOOR_HUMIDITY,
    CONF_OUTDOOR_PM10,
    CONF_OUTDOOR_PM25,
    CONF_OUTDOOR_TEMPERATURE,
    CONF_PM25_INDOOR,
    CONF_PM25_OUTDOOR,
    CONF_PM25_SENSOR,
    CONF_POWER_SENSOR,
    CONF_PRESENCE_ENTITY,
    CONF_PRIORITY,
    CONF_QUIET_END,
    CONF_QUIET_START,
    CONF_RAIN_SAFE,
    CONF_REFERENCE_OFFSET,
    CONF_REFERENCE_ROOM,
    CONF_ROOM,
    CONF_ROOM_AREA,
    CONF_ROOM_NAME,
    CONF_STORM_WIND,
    CONF_SUMMER_MAX,
    CONF_SUMMER_MIN,
    CONF_SUN_ENTITY,
    CONF_TARGET_MAX,
    CONF_TARGET_MIN,
    CONF_TEMPERATURE_SENSOR,
    CONF_TILT_CAPABLE,
    CONF_TILT_SENSOR,
    CONF_TOP_FLOOR,
    CONF_TROPICAL_NIGHT,
    CONF_VOC_SENSOR,
    CONF_VOLUME,
    CONF_WEATHER_ALERT_ENTITY,
    CONF_WEATHER_ENTITY,
    CONF_WEIGHT_CO2,
    CONF_WEIGHT_HUMIDITY,
    CONF_WEIGHT_PARTICULATE,
    CONF_WEIGHT_TEMPERATURE,
    CONF_WINDOW_AREA,
    CONF_WINDOW_NAME,
    CONF_WINTER_MAX,
    CONF_WINTER_MIN,
    DEFAULT_CEILING_HEIGHT,
    DEFAULT_G_VALUE,
    DEFAULT_PRIORITY,
    DEFAULT_SUN_ENTITY,
    DEFAULT_WINDOW_AREA,
    DOMAIN,
    ESTIMATION_NONE,
    ESTIMATION_OPTIONS,
    NAME,
    SUBENTRY_ROOM,
    SUBENTRY_WINDOW,
)
from .engine.state import BuildingType, Preferences

# --------------------------------------------------------------------------
# Reusable selectors
# --------------------------------------------------------------------------


def _sensor(device_class: str | list[str] | None = None) -> EntitySelector:
    config: dict[str, Any] = {"domain": "sensor"}
    if device_class:
        config["device_class"] = device_class
    return EntitySelector(EntitySelectorConfig(**config))


def _binary(device_class: str | list[str] | None = None) -> EntitySelector:
    config: dict[str, Any] = {"domain": ["binary_sensor", "input_boolean"]}
    if device_class:
        config["device_class"] = device_class
    return EntitySelector(EntitySelectorConfig(**config))


def _number(
    minimum: float, maximum: float, step: float = 1.0, unit: str | None = None
) -> NumberSelector:
    return NumberSelector(
        NumberSelectorConfig(
            min=minimum,
            max=maximum,
            step=step,
            unit_of_measurement=unit,
            mode=NumberSelectorMode.BOX,
        )
    )


def _slider(minimum: float, maximum: float, step: float = 1.0) -> NumberSelector:
    return NumberSelector(
        NumberSelectorConfig(min=minimum, max=maximum, step=step, mode=NumberSelectorMode.SLIDER)
    )


def _select(options: list[str], key: str, *, multiple: bool = False) -> SelectSelector:
    return SelectSelector(
        SelectSelectorConfig(
            options=options,
            translation_key=key,
            mode=SelectSelectorMode.DROPDOWN,
            multiple=multiple,
        )
    )


def _options_select(options: list[SelectOptionDict], *, multiple: bool = False) -> SelectSelector:
    return SelectSelector(
        SelectSelectorConfig(options=options, mode=SelectSelectorMode.DROPDOWN, multiple=multiple)
    )


BUILDING_TYPES = [t.value for t in BuildingType]


# --------------------------------------------------------------------------
# Main config flow
# --------------------------------------------------------------------------


class AdaptiveVentilationConfigFlow(ConfigFlow, domain=DOMAIN):
    """Two minutes: name it, point it at the weather, done."""

    VERSION = 1
    MINOR_VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            options = {
                key: value
                for key, value in user_input.items()
                if key != "title" and value not in (None, "", [])
            }
            options.setdefault(CONF_SUN_ENTITY, DEFAULT_SUN_ENTITY)
            return self.async_create_entry(
                title=user_input.get("title") or NAME, data={}, options=options
            )

        self._async_abort_entries_match()
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required("title", default=NAME): TextSelector(),
                    vol.Optional(CONF_WEATHER_ENTITY): EntitySelector(
                        EntitySelectorConfig(domain="weather")
                    ),
                    vol.Optional(CONF_OUTDOOR_TEMPERATURE): _sensor("temperature"),
                    vol.Optional(CONF_OUTDOOR_HUMIDITY): _sensor("humidity"),
                    vol.Required(
                        CONF_BUILDING_TYPE, default=BuildingType.OLD_RENOVATED.value
                    ): _select(BUILDING_TYPES, "building_type"),
                }
            ),
            description_placeholders={
                "docs": "https://github.com/ProfessorQuantumUniverse/Adaptive-Ventilation"
            },
        )

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        return {SUBENTRY_ROOM: RoomSubentryFlow, SUBENTRY_WINDOW: WindowSubentryFlow}

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return AdaptiveVentilationOptionsFlow()


# --------------------------------------------------------------------------
# Room subentry
# --------------------------------------------------------------------------


class RoomSubentryFlow(ConfigSubentryFlow):
    """Add or edit one room."""

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        return await self._async_form(user_input, reconfigure=False)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        return await self._async_form(user_input, reconfigure=True)

    async def _async_form(
        self, user_input: dict[str, Any] | None, *, reconfigure: bool
    ) -> SubentryFlowResult:
        entry = self._get_entry()
        current: dict[str, Any] = {}
        if reconfigure:
            current = dict(self._get_reconfigure_subentry().data)

        if user_input is not None:
            data = _flatten(user_input)
            errors = _validate_room(data)
            if not errors:
                title = data[CONF_ROOM_NAME]
                if reconfigure:
                    return self.async_update_and_abort(
                        entry, self._get_reconfigure_subentry(), data=data, title=title
                    )
                return self.async_create_entry(title=title, data=data)
            return self.async_show_form(
                step_id="reconfigure" if reconfigure else "user",
                data_schema=self._schema(entry, current | _flatten(user_input)),
                errors=errors,
            )

        return self.async_show_form(
            step_id="reconfigure" if reconfigure else "user",
            data_schema=self._schema(entry, current),
        )

    def _schema(self, entry: ConfigEntry, current: dict[str, Any]) -> vol.Schema:
        other_rooms = [
            SelectOptionDict(value=subentry_id, label=subentry.title)
            for subentry_id, subentry in entry.subentries.items()
            if subentry.subentry_type == SUBENTRY_ROOM
        ]
        suggested_name = current.get(CONF_ROOM_NAME) or _suggest_room_name(self.hass, entry)

        return vol.Schema(
            {
                vol.Required(CONF_ROOM_NAME, default=suggested_name): TextSelector(),
                vol.Optional(
                    CONF_TEMPERATURE_SENSOR,
                    description={"suggested_value": current.get(CONF_TEMPERATURE_SENSOR)},
                ): _sensor("temperature"),
                vol.Optional(
                    CONF_HUMIDITY_SENSOR,
                    description={"suggested_value": current.get(CONF_HUMIDITY_SENSOR)},
                ): _sensor("humidity"),
                vol.Required(
                    CONF_PRIORITY, default=current.get(CONF_PRIORITY, DEFAULT_PRIORITY)
                ): _slider(1, 9),
                vol.Required("advanced"): section(
                    vol.Schema(
                        {
                            vol.Optional(
                                CONF_CO2_SENSOR,
                                description={"suggested_value": current.get(CONF_CO2_SENSOR)},
                            ): _sensor("carbon_dioxide"),
                            vol.Optional(
                                CONF_VOC_SENSOR,
                                description={"suggested_value": current.get(CONF_VOC_SENSOR)},
                            ): _sensor(["volatile_organic_compounds_parts", "aqi"]),
                            vol.Optional(
                                CONF_PM25_SENSOR,
                                description={"suggested_value": current.get(CONF_PM25_SENSOR)},
                            ): _sensor("pm25"),
                            vol.Optional(
                                CONF_OCCUPANCY_SENSOR,
                                description={"suggested_value": current.get(CONF_OCCUPANCY_SENSOR)},
                            ): _binary(["occupancy", "presence", "motion"]),
                            vol.Optional(
                                CONF_CLIMATE_ENTITY,
                                description={"suggested_value": current.get(CONF_CLIMATE_ENTITY)},
                            ): EntitySelector(EntitySelectorConfig(domain="climate")),
                            vol.Optional(
                                CONF_FAN_ENTITY,
                                description={"suggested_value": current.get(CONF_FAN_ENTITY)},
                            ): EntitySelector(EntitySelectorConfig(domain=["fan", "switch"])),
                            vol.Optional(
                                CONF_POWER_SENSOR,
                                description={"suggested_value": current.get(CONF_POWER_SENSOR)},
                            ): _sensor("power"),
                            vol.Optional(
                                CONF_VOLUME,
                                description={"suggested_value": current.get(CONF_VOLUME)},
                            ): _number(3, 500, 1, "m³"),
                            vol.Optional(
                                CONF_ROOM_AREA,
                                description={"suggested_value": current.get(CONF_ROOM_AREA)},
                            ): _number(2, 200, 0.5, "m²"),
                            vol.Optional(
                                CONF_CEILING_HEIGHT,
                                default=current.get(CONF_CEILING_HEIGHT, DEFAULT_CEILING_HEIGHT),
                            ): _number(1.8, 6.0, 0.05, "m"),
                            vol.Optional(
                                CONF_TARGET_MIN,
                                description={"suggested_value": current.get(CONF_TARGET_MIN)},
                            ): _number(10, 30, 0.5, "°C"),
                            vol.Optional(
                                CONF_TARGET_MAX,
                                description={"suggested_value": current.get(CONF_TARGET_MAX)},
                            ): _number(12, 35, 0.5, "°C"),
                            vol.Optional(
                                CONF_IS_BEDROOM, default=current.get(CONF_IS_BEDROOM, False)
                            ): BooleanSelector(),
                            vol.Optional(
                                CONF_IS_MOISTURE_SOURCE,
                                default=current.get(CONF_IS_MOISTURE_SOURCE, False),
                            ): BooleanSelector(),
                            vol.Optional(
                                CONF_IS_BASEMENT,
                                default=current.get(CONF_IS_BASEMENT, False),
                            ): BooleanSelector(),
                            vol.Optional(
                                CONF_IS_UNHEATED,
                                default=current.get(CONF_IS_UNHEATED, False),
                            ): BooleanSelector(),
                            vol.Optional(
                                CONF_IS_TOP_FLOOR,
                                default=current.get(CONF_IS_TOP_FLOOR, False),
                            ): BooleanSelector(),
                            vol.Optional(
                                CONF_CONNECTED_ROOMS,
                                description={
                                    "suggested_value": current.get(CONF_CONNECTED_ROOMS, [])
                                },
                            ): _options_select(other_rooms, multiple=True),
                            vol.Optional(
                                CONF_ESTIMATION,
                                default=current.get(CONF_ESTIMATION, ESTIMATION_NONE),
                            ): _select(ESTIMATION_OPTIONS, "estimation"),
                            vol.Optional(
                                CONF_REFERENCE_ROOM,
                                description={"suggested_value": current.get(CONF_REFERENCE_ROOM)},
                            ): _options_select(other_rooms),
                            vol.Optional(
                                CONF_REFERENCE_OFFSET,
                                default=current.get(CONF_REFERENCE_OFFSET, 0.0),
                            ): _number(-10, 10, 0.1, "K"),
                        }
                    ),
                    {"collapsed": True},
                ),
            }
        )


# --------------------------------------------------------------------------
# Window subentry
# --------------------------------------------------------------------------


class WindowSubentryFlow(ConfigSubentryFlow):
    """Add or edit one window, including its cover."""

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        return await self._async_form(user_input, reconfigure=False)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        return await self._async_form(user_input, reconfigure=True)

    async def _async_form(
        self, user_input: dict[str, Any] | None, *, reconfigure: bool
    ) -> SubentryFlowResult:
        entry = self._get_entry()
        rooms = [
            SelectOptionDict(value=subentry_id, label=subentry.title)
            for subentry_id, subentry in entry.subentries.items()
            if subentry.subentry_type == SUBENTRY_ROOM
        ]
        if not rooms:
            return self.async_abort(reason="no_rooms")

        current: dict[str, Any] = {}
        if reconfigure:
            current = dict(self._get_reconfigure_subentry().data)

        if user_input is not None:
            data = _flatten(user_input)
            data[CONF_AZIMUTH] = _resolve_azimuth(data)
            errors = _validate_window(data)
            if not errors:
                title = data[CONF_WINDOW_NAME]
                if reconfigure:
                    return self.async_update_and_abort(
                        entry, self._get_reconfigure_subentry(), data=data, title=title
                    )
                return self.async_create_entry(title=title, data=data)
            return self.async_show_form(
                step_id="reconfigure" if reconfigure else "user",
                data_schema=self._schema(rooms, current | _flatten(user_input)),
                errors=errors,
            )

        return self.async_show_form(
            step_id="reconfigure" if reconfigure else "user",
            data_schema=self._schema(rooms, current),
        )

    def _schema(self, rooms: list[SelectOptionDict], current: dict[str, Any]) -> vol.Schema:
        suggested_contact = current.get(CONF_CONTACT_SENSOR) or _suggest_window_sensor(self.hass)
        return vol.Schema(
            {
                vol.Required(
                    CONF_WINDOW_NAME, default=current.get(CONF_WINDOW_NAME, "Window")
                ): TextSelector(),
                vol.Required(
                    CONF_ROOM, description={"suggested_value": current.get(CONF_ROOM)}
                ): _options_select(rooms),
                vol.Required(
                    CONF_CONTACT_SENSOR,
                    description={"suggested_value": suggested_contact},
                ): _binary(["window", "door", "opening"]),
                vol.Required(
                    "orientation",
                    default=_azimuth_to_preset(current.get(CONF_AZIMUTH, 180.0)),
                ): _select([*COMPASS_PRESETS, "custom"], "orientation"),
                vol.Required("advanced"): section(
                    vol.Schema(
                        {
                            vol.Optional(
                                CONF_AZIMUTH, default=current.get(CONF_AZIMUTH, 180.0)
                            ): _number(0, 359, 1, "°"),
                            vol.Optional(
                                CONF_WINDOW_AREA,
                                default=current.get(CONF_WINDOW_AREA, DEFAULT_WINDOW_AREA),
                            ): _number(0.1, 12, 0.1, "m²"),
                            vol.Optional(
                                CONF_TILT_SENSOR,
                                description={"suggested_value": current.get(CONF_TILT_SENSOR)},
                            ): _binary(),
                            vol.Optional(
                                CONF_TILT_CAPABLE,
                                default=current.get(CONF_TILT_CAPABLE, True),
                            ): BooleanSelector(),
                            vol.Optional(
                                CONF_G_VALUE,
                                default=current.get(CONF_G_VALUE, DEFAULT_G_VALUE),
                            ): _number(0.1, 0.9, 0.05),
                            vol.Optional(
                                CONF_COVER_ENTITY,
                                description={"suggested_value": current.get(CONF_COVER_ENTITY)},
                            ): EntitySelector(EntitySelectorConfig(domain="cover")),
                            vol.Optional(
                                CONF_COVER_EXTERNAL,
                                default=current.get(CONF_COVER_EXTERNAL, True),
                            ): BooleanSelector(),
                            vol.Optional(
                                CONF_COVER_AUTO, default=current.get(CONF_COVER_AUTO, False)
                            ): BooleanSelector(),
                            vol.Optional(
                                CONF_GROUND_FLOOR,
                                default=current.get(CONF_GROUND_FLOOR, False),
                            ): BooleanSelector(),
                            vol.Optional(
                                CONF_OK_WHEN_AWAY,
                                default=current.get(CONF_OK_WHEN_AWAY, False),
                            ): BooleanSelector(),
                            vol.Optional(
                                CONF_RAIN_SAFE, default=current.get(CONF_RAIN_SAFE, False)
                            ): BooleanSelector(),
                        }
                    ),
                    {"collapsed": True},
                ),
            }
        )


# --------------------------------------------------------------------------
# Options flow
# --------------------------------------------------------------------------


class AdaptiveVentilationOptionsFlow(OptionsFlow):
    """Global settings, grouped so nobody has to scroll through 40 fields."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_show_menu(
            step_id="init",
            menu_options=["sources", "building", "notifications", "weights", "thresholds"],
        )

    async def async_step_sources(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self._save(user_input)
        options = self.config_entry.options
        return self.async_show_form(
            step_id="sources",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_WEATHER_ENTITY,
                        description={"suggested_value": options.get(CONF_WEATHER_ENTITY)},
                    ): EntitySelector(EntitySelectorConfig(domain="weather")),
                    vol.Optional(
                        CONF_OUTDOOR_TEMPERATURE,
                        description={"suggested_value": options.get(CONF_OUTDOOR_TEMPERATURE)},
                    ): _sensor("temperature"),
                    vol.Optional(
                        CONF_OUTDOOR_HUMIDITY,
                        description={"suggested_value": options.get(CONF_OUTDOOR_HUMIDITY)},
                    ): _sensor("humidity"),
                    vol.Optional(
                        CONF_OUTDOOR_PM25,
                        description={"suggested_value": options.get(CONF_OUTDOOR_PM25)},
                    ): _sensor("pm25"),
                    vol.Optional(
                        CONF_OUTDOOR_PM10,
                        description={"suggested_value": options.get(CONF_OUTDOOR_PM10)},
                    ): _sensor("pm10"),
                    vol.Optional(
                        CONF_WEATHER_ALERT_ENTITY,
                        description={"suggested_value": options.get(CONF_WEATHER_ALERT_ENTITY)},
                    ): _binary(),
                    vol.Optional(
                        CONF_ILLUMINANCE,
                        description={"suggested_value": options.get(CONF_ILLUMINANCE)},
                    ): _sensor("illuminance"),
                    vol.Optional(
                        CONF_SUN_ENTITY,
                        default=options.get(CONF_SUN_ENTITY, DEFAULT_SUN_ENTITY),
                    ): EntitySelector(EntitySelectorConfig(domain="sun")),
                    vol.Optional(
                        CONF_PRESENCE_ENTITY,
                        description={"suggested_value": options.get(CONF_PRESENCE_ENTITY)},
                    ): EntitySelector(
                        EntitySelectorConfig(
                            domain=["person", "group", "device_tracker", "binary_sensor"]
                        )
                    ),
                }
            ),
        )

    async def async_step_building(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self._save(user_input)
        options = self.config_entry.options
        return self.async_show_form(
            step_id="building",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_BUILDING_TYPE,
                        default=options.get(CONF_BUILDING_TYPE, BuildingType.OLD_RENOVATED.value),
                    ): _select(BUILDING_TYPES, "building_type"),
                    vol.Optional(
                        CONF_CONSTRUCTION_YEAR,
                        description={"suggested_value": options.get(CONF_CONSTRUCTION_YEAR)},
                    ): _number(1800, 2100, 1),
                    vol.Optional(CONF_FLOOR, default=options.get(CONF_FLOOR, 1)): _number(
                        -2, 30, 1
                    ),
                    vol.Optional(
                        CONF_TOP_FLOOR, default=options.get(CONF_TOP_FLOOR, False)
                    ): BooleanSelector(),
                    vol.Optional(
                        CONF_F_RT, description={"suggested_value": options.get(CONF_F_RT)}
                    ): _number(0.05, 0.6, 0.01),
                }
            ),
        )

    async def async_step_notifications(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self._save(user_input)
        options = self.config_entry.options
        defaults = Preferences()
        notify_services = sorted(self.hass.services.async_services().get("notify", {}))
        targets = [
            SelectOptionDict(value=f"notify.{name}", label=f"notify.{name}")
            for name in notify_services
            if name not in ("persistent_notification", "send_message")
        ]
        return self.async_show_form(
            step_id="notifications",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_NOTIFY_TARGETS,
                        description={"suggested_value": options.get(CONF_NOTIFY_TARGETS, [])},
                    ): _options_select(targets, multiple=True),
                    vol.Optional(
                        CONF_ACTIONABLE_NOTIFICATIONS,
                        default=options.get(CONF_ACTIONABLE_NOTIFICATIONS, True),
                    ): BooleanSelector(),
                    vol.Optional(
                        CONF_QUIET_START,
                        default=options.get(
                            CONF_QUIET_START, defaults.quiet_hours_start.strftime("%H:%M:%S")
                        ),
                    ): TimeSelector(),
                    vol.Optional(
                        CONF_QUIET_END,
                        default=options.get(
                            CONF_QUIET_END, defaults.quiet_hours_end.strftime("%H:%M:%S")
                        ),
                    ): TimeSelector(),
                    vol.Optional(
                        CONF_MAX_PUSHES,
                        default=options.get(CONF_MAX_PUSHES, defaults.max_pushes_per_day),
                    ): _number(0, 30, 1),
                    vol.Optional(
                        CONF_NOTIFICATION_RESTRAINT,
                        default=options.get(
                            CONF_NOTIFICATION_RESTRAINT, defaults.notification_restraint
                        ),
                    ): _slider(0, 100, 5),
                    vol.Optional(
                        CONF_MIN_CONFIDENCE,
                        default=options.get(CONF_MIN_CONFIDENCE, defaults.min_confidence_for_push),
                    ): _number(0.0, 1.0, 0.05),
                    vol.Optional(
                        CONF_COVER_AUTOMATION,
                        default=options.get(CONF_COVER_AUTOMATION, False),
                    ): BooleanSelector(),
                }
            ),
        )

    async def async_step_weights(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self._save(user_input)
        options = self.config_entry.options
        defaults = Preferences()
        return self.async_show_form(
            step_id="weights",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_WEIGHT_TEMPERATURE,
                        default=options.get(CONF_WEIGHT_TEMPERATURE, defaults.weight_temperature),
                    ): _slider(0, 100, 5),
                    vol.Optional(
                        CONF_WEIGHT_HUMIDITY,
                        default=options.get(CONF_WEIGHT_HUMIDITY, defaults.weight_humidity),
                    ): _slider(0, 100, 5),
                    vol.Optional(
                        CONF_WEIGHT_CO2,
                        default=options.get(CONF_WEIGHT_CO2, defaults.weight_co2),
                    ): _slider(0, 100, 5),
                    vol.Optional(
                        CONF_WEIGHT_PARTICULATE,
                        default=options.get(CONF_WEIGHT_PARTICULATE, defaults.weight_particulate),
                    ): _slider(0, 100, 5),
                }
            ),
        )

    async def async_step_thresholds(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self._save(user_input)
        options = self.config_entry.options
        defaults = Preferences()
        return self.async_show_form(
            step_id="thresholds",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_SUMMER_MIN,
                        default=options.get(CONF_SUMMER_MIN, defaults.summer_target_min),
                    ): _number(15, 28, 0.5, "°C"),
                    vol.Optional(
                        CONF_SUMMER_MAX,
                        default=options.get(CONF_SUMMER_MAX, defaults.summer_target_max),
                    ): _number(18, 32, 0.5, "°C"),
                    vol.Optional(
                        CONF_WINTER_MIN,
                        default=options.get(CONF_WINTER_MIN, defaults.winter_target_min),
                    ): _number(14, 24, 0.5, "°C"),
                    vol.Optional(
                        CONF_WINTER_MAX,
                        default=options.get(CONF_WINTER_MAX, defaults.winter_target_max),
                    ): _number(16, 28, 0.5, "°C"),
                    vol.Optional(
                        CONF_HUMIDITY_MIN,
                        default=options.get(CONF_HUMIDITY_MIN, defaults.humidity_target_min),
                    ): _number(20, 60, 1, "%"),
                    vol.Optional(
                        CONF_HUMIDITY_MAX,
                        default=options.get(CONF_HUMIDITY_MAX, defaults.humidity_target_max),
                    ): _number(40, 80, 1, "%"),
                    vol.Optional(
                        CONF_CO2_THRESHOLD,
                        default=options.get(CONF_CO2_THRESHOLD, defaults.co2_threshold),
                    ): _number(600, 2000, 50, "ppm"),
                    vol.Optional(
                        CONF_PM25_INDOOR,
                        default=options.get(CONF_PM25_INDOOR, defaults.pm25_indoor_threshold),
                    ): _number(5, 100, 1, "µg/m³"),
                    vol.Optional(
                        CONF_PM25_OUTDOOR,
                        default=options.get(CONF_PM25_OUTDOOR, defaults.pm25_outdoor_threshold),
                    ): _number(5, 150, 1, "µg/m³"),
                    vol.Optional(
                        CONF_STORM_WIND,
                        default=options.get(CONF_STORM_WIND, defaults.storm_wind_kmh),
                    ): _number(30, 120, 5, "km/h"),
                    vol.Optional(
                        CONF_TROPICAL_NIGHT,
                        default=options.get(CONF_TROPICAL_NIGHT, defaults.tropical_night_threshold),
                    ): _number(15, 26, 0.5, "°C"),
                    vol.Optional(
                        CONF_DELTA_T_HYSTERESIS,
                        default=options.get(CONF_DELTA_T_HYSTERESIS, defaults.delta_t_hysteresis),
                    ): _number(0.1, 3.0, 0.1, "K"),
                    vol.Optional(
                        CONF_MIN_STATE_DURATION,
                        default=options.get(
                            CONF_MIN_STATE_DURATION, defaults.min_state_duration_minutes
                        ),
                    ): _number(5, 120, 5, "min"),
                    vol.Optional(
                        CONF_COOLDOWN,
                        default=options.get(CONF_COOLDOWN, defaults.cooldown_minutes),
                    ): _number(10, 240, 10, "min"),
                    vol.Optional(
                        CONF_CLOSE_LEAD_TIME,
                        default=options.get(CONF_CLOSE_LEAD_TIME, defaults.close_lead_time_minutes),
                    ): _number(5, 120, 5, "min"),
                }
            ),
        )

    def _save(self, user_input: dict[str, Any]) -> ConfigFlowResult:
        options = dict(self.config_entry.options)
        for key, value in user_input.items():
            if value in (None, ""):
                options.pop(key, None)
            else:
                options[key] = value
        return self.async_create_entry(title="", data=options)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _flatten(user_input: dict[str, Any]) -> dict[str, Any]:
    """Merge the ``advanced`` section back into a flat dict."""
    data = {k: v for k, v in user_input.items() if k != "advanced"}
    data.update(user_input.get("advanced") or {})
    return {k: v for k, v in data.items() if v not in (None, "")}


def _resolve_azimuth(data: dict[str, Any]) -> float:
    """Compass preset wins unless the user explicitly picked ``custom``."""
    orientation = data.pop("orientation", None)
    if orientation and orientation != "custom":
        return COMPASS_PRESETS[orientation]
    return float(data.get(CONF_AZIMUTH, 180.0))


def _azimuth_to_preset(azimuth: float) -> str:
    for name, value in COMPASS_PRESETS.items():
        if abs(value - float(azimuth)) < 1.0:
            return name
    return "custom"


def _validate_room(data: dict[str, Any]) -> dict[str, str]:
    errors: dict[str, str] = {}
    if not data.get(CONF_ROOM_NAME):
        errors[CONF_ROOM_NAME] = "required"
    if (
        data.get(CONF_ESTIMATION) not in (None, ESTIMATION_NONE)
        and not data.get(CONF_REFERENCE_ROOM)
        and data.get(CONF_ESTIMATION) == "reference_offset"
    ):
        errors["base"] = "reference_room_required"
    minimum = data.get(CONF_TARGET_MIN)
    maximum = data.get(CONF_TARGET_MAX)
    if minimum is not None and maximum is not None and float(minimum) >= float(maximum):
        errors["base"] = "target_band_invalid"
    return errors


def _validate_window(data: dict[str, Any]) -> dict[str, str]:
    errors: dict[str, str] = {}
    if not data.get(CONF_WINDOW_NAME):
        errors[CONF_WINDOW_NAME] = "required"
    if not data.get(CONF_ROOM):
        errors[CONF_ROOM] = "required"
    if not data.get(CONF_CONTACT_SENSOR):
        errors[CONF_CONTACT_SENSOR] = "required"
    if data.get(CONF_COVER_AUTO) and not data.get(CONF_COVER_ENTITY):
        errors["base"] = "cover_required"
    return errors


def _suggest_room_name(hass, entry: ConfigEntry) -> str:
    """Offer the next area that has no room subentry yet."""
    used = {
        subentry.title
        for subentry in entry.subentries.values()
        if subentry.subentry_type == SUBENTRY_ROOM
    }
    registry = ar.async_get(hass)
    for area in registry.async_list_areas():
        if area.name not in used:
            return area.name
    return "Room"


def _suggest_window_sensor(hass) -> str | None:
    """First unused window contact sensor in the entity registry."""
    registry = er.async_get(hass)
    for entity in registry.entities.values():
        if entity.domain != "binary_sensor":
            continue
        device_class = entity.device_class or entity.original_device_class
        if device_class == "window":
            return entity.entity_id
    return None
