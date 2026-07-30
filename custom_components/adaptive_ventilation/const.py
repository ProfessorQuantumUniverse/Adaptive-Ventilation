"""Constants for the Adaptive Ventilation integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "adaptive_ventilation"
NAME: Final = "Adaptive Ventilation"
MANUFACTURER: Final = "Adaptive Ventilation"

PLATFORMS: Final = [
    "binary_sensor",
    "button",
    "number",
    "select",
    "sensor",
    "switch",
    "text",
]

UPDATE_INTERVAL: Final = timedelta(minutes=2)
CALIBRATION_INTERVAL: Final = timedelta(hours=24)
#: Sensor readings older than this make the outdoor state stale.
STALE_AFTER: Final = timedelta(minutes=45)

STORAGE_VERSION: Final = 1
STORAGE_KEY_LEARNED: Final = f"{DOMAIN}.learned"
STORAGE_KEY_MEMORY: Final = f"{DOMAIN}.memory"

PANEL_URL: Final = "/adaptive-ventilation-panel"
PANEL_FILENAME: Final = "adaptive-ventilation-panel.js"
PANEL_TITLE: Final = "Ventilation"
PANEL_ICON: Final = "mdi:weather-windy"

# --------------------------------------------------------------------------
# Subentry types
# --------------------------------------------------------------------------

SUBENTRY_ROOM: Final = "room"
SUBENTRY_WINDOW: Final = "window"

# --------------------------------------------------------------------------
# Global options
# --------------------------------------------------------------------------

CONF_OUTDOOR_TEMPERATURE: Final = "outdoor_temperature_sensor"
CONF_OUTDOOR_HUMIDITY: Final = "outdoor_humidity_sensor"
CONF_OUTDOOR_PM25: Final = "outdoor_pm25_sensor"
CONF_OUTDOOR_PM10: Final = "outdoor_pm10_sensor"
CONF_WEATHER_ENTITY: Final = "weather_entity"
CONF_WEATHER_ALERT_ENTITY: Final = "weather_alert_entity"
CONF_SUN_ENTITY: Final = "sun_entity"
CONF_ILLUMINANCE: Final = "illuminance_sensor"
CONF_PRESENCE_ENTITY: Final = "presence_entity"
CONF_CALENDAR_ENTITY: Final = "calendar_entity"
CONF_NOTIFY_TARGETS: Final = "notify_targets"

CONF_BUILDING_TYPE: Final = "building_type"
CONF_CONSTRUCTION_YEAR: Final = "construction_year"
CONF_FLOOR: Final = "floor"
CONF_TOP_FLOOR: Final = "top_floor"
CONF_F_RT: Final = "f_rt"

CONF_PROFILE: Final = "profile"

#: Presets for the "how chatty and how twitchy" knobs. The normal way to tune
#: this should be picking one word, not balancing six sliders and hoping.
#: Individual settings still override the profile, so nothing is taken away -
#: but there is always a one-click way back to somewhere sane.
PROFILE_QUIET: Final = "quiet"
PROFILE_BALANCED: Final = "balanced"
PROFILE_EAGER: Final = "eager"
PROFILE_OPTIONS: Final = [PROFILE_QUIET, PROFILE_BALANCED, PROFILE_EAGER]

PROFILES: Final[dict[str, dict[str, float]]] = {
    PROFILE_QUIET: {
        "notification_restraint": 80,
        "max_pushes_per_day": 3,
        "min_state_duration_minutes": 30,
        "cooldown_minutes": 120,
        "min_confidence_for_push": 0.8,
        "delta_t_hysteresis": 0.8,
    },
    PROFILE_BALANCED: {},
    PROFILE_EAGER: {
        "notification_restraint": 20,
        "max_pushes_per_day": 12,
        "min_state_duration_minutes": 10,
        "cooldown_minutes": 30,
        "min_confidence_for_push": 0.6,
        "delta_t_hysteresis": 0.4,
    },
}

#: The keys a profile governs. Choosing a profile clears these, so the choice
#: takes effect instead of losing silently to a forgotten override.
PROFILE_KEYS: Final = (
    "notification_restraint",
    "max_pushes_per_day",
    "min_state_duration_minutes",
    "cooldown_minutes",
    "min_confidence_for_push",
    "delta_t_hysteresis",
)

#: Everything "reset to defaults" clears. Deliberately excludes data sources,
#: the building profile and the notification targets: resetting the tuning must
#: never disconnect the integration from your sensors.
TUNABLE_KEYS: Final = (
    *PROFILE_KEYS,
    "weight_temperature",
    "weight_humidity",
    "weight_co2",
    "weight_particulate",
    "summer_target_min",
    "summer_target_max",
    "winter_target_min",
    "winter_target_max",
    "humidity_target_min",
    "humidity_target_max",
    "co2_threshold",
    "co2_urgent",
    "voc_threshold",
    "pm25_indoor_threshold",
    "pm25_outdoor_threshold",
    "storm_wind_kmh",
    "tropical_night_threshold",
    "heatwave_threshold",
    "close_lead_time_minutes",
    "quiet_hours_start",
    "quiet_hours_end",
)

CONF_MODE: Final = "mode"
CONF_ACTIONABLE_NOTIFICATIONS: Final = "actionable_notifications"
CONF_COVER_AUTOMATION: Final = "cover_automation"
CONF_DISABLED_RULES: Final = "disabled_rules"

# Preference keys - these map 1:1 onto engine.state.Preferences fields.
CONF_WEIGHT_TEMPERATURE: Final = "weight_temperature"
CONF_WEIGHT_HUMIDITY: Final = "weight_humidity"
CONF_WEIGHT_CO2: Final = "weight_co2"
CONF_WEIGHT_PARTICULATE: Final = "weight_particulate"
CONF_WEIGHT_ENERGY: Final = "weight_energy"
CONF_NOTIFICATION_RESTRAINT: Final = "notification_restraint"

CONF_SUMMER_MIN: Final = "summer_target_min"
CONF_SUMMER_MAX: Final = "summer_target_max"
CONF_WINTER_MIN: Final = "winter_target_min"
CONF_WINTER_MAX: Final = "winter_target_max"
CONF_HUMIDITY_MIN: Final = "humidity_target_min"
CONF_HUMIDITY_MAX: Final = "humidity_target_max"

CONF_CO2_THRESHOLD: Final = "co2_threshold"
CONF_CO2_URGENT: Final = "co2_urgent"
CONF_VOC_THRESHOLD: Final = "voc_threshold"
CONF_PM25_INDOOR: Final = "pm25_indoor_threshold"
CONF_PM25_OUTDOOR: Final = "pm25_outdoor_threshold"
CONF_DELTA_T_HYSTERESIS: Final = "delta_t_hysteresis"
CONF_STORM_WIND: Final = "storm_wind_kmh"
CONF_TROPICAL_NIGHT: Final = "tropical_night_threshold"
CONF_HEATWAVE: Final = "heatwave_threshold"

CONF_MIN_STATE_DURATION: Final = "min_state_duration_minutes"
CONF_COOLDOWN: Final = "cooldown_minutes"
CONF_QUIET_START: Final = "quiet_hours_start"
CONF_QUIET_END: Final = "quiet_hours_end"
CONF_MAX_PUSHES: Final = "max_pushes_per_day"
CONF_CLOSE_LEAD_TIME: Final = "close_lead_time_minutes"
CONF_MIN_CONFIDENCE: Final = "min_confidence_for_push"

# --------------------------------------------------------------------------
# Room subentry
# --------------------------------------------------------------------------

CONF_ROOM_NAME: Final = "name"
CONF_AREA_ID: Final = "area_id"
CONF_TEMPERATURE_SENSOR: Final = "temperature_sensor"
CONF_HUMIDITY_SENSOR: Final = "humidity_sensor"
CONF_CO2_SENSOR: Final = "co2_sensor"
CONF_VOC_SENSOR: Final = "voc_sensor"
CONF_PM25_SENSOR: Final = "pm25_sensor"
CONF_OCCUPANCY_SENSOR: Final = "occupancy_sensor"
CONF_CLIMATE_ENTITY: Final = "climate_entity"
CONF_FAN_ENTITY: Final = "fan_entity"
CONF_POWER_SENSOR: Final = "power_sensor"
CONF_VOLUME: Final = "volume_m3"
CONF_ROOM_AREA: Final = "floor_area_m2"
CONF_CEILING_HEIGHT: Final = "ceiling_height_m"
CONF_PRIORITY: Final = "priority"
CONF_TARGET_MIN: Final = "target_min"
CONF_TARGET_MAX: Final = "target_max"
CONF_IS_BASEMENT: Final = "is_basement"
CONF_IS_MOISTURE_SOURCE: Final = "is_moisture_source"
CONF_IS_UNHEATED: Final = "is_unheated"
CONF_IS_TOP_FLOOR: Final = "is_top_floor"
CONF_IS_BEDROOM: Final = "is_bedroom"
CONF_CONNECTED_ROOMS: Final = "connected_rooms"
CONF_REFERENCE_ROOM: Final = "reference_room"
CONF_REFERENCE_OFFSET: Final = "reference_offset"
CONF_ESTIMATION: Final = "estimation"

# --------------------------------------------------------------------------
# Window subentry
# --------------------------------------------------------------------------

CONF_WINDOW_NAME: Final = "name"
CONF_ROOM: Final = "room"
CONF_CONTACT_SENSOR: Final = "contact_sensor"
CONF_TILT_SENSOR: Final = "tilt_sensor"
CONF_AZIMUTH: Final = "azimuth"
CONF_WINDOW_AREA: Final = "area_m2"
CONF_TILT_CAPABLE: Final = "tilt_capable"
CONF_G_VALUE: Final = "g_value"
CONF_COVER_ENTITY: Final = "cover_entity"
CONF_COVER_EXTERNAL: Final = "cover_external"
CONF_COVER_AUTO: Final = "cover_auto_allowed"
CONF_GROUND_FLOOR: Final = "is_ground_floor"
CONF_OK_WHEN_AWAY: Final = "ok_when_away"
CONF_RAIN_SAFE: Final = "rain_safe"
CONF_HORIZON: Final = "horizon_profile"
CONF_MANUAL_COVER: Final = "manual_cover"
CONF_SUN_FROM: Final = "sun_from"
CONF_SUN_UNTIL: Final = "sun_until"

# --------------------------------------------------------------------------
# Defaults
# --------------------------------------------------------------------------

DEFAULT_VOLUME: Final = 40.0
DEFAULT_CEILING_HEIGHT: Final = 2.5
DEFAULT_WINDOW_AREA: Final = 1.5
DEFAULT_G_VALUE: Final = 0.6
DEFAULT_PRIORITY: Final = 5
DEFAULT_SUN_ENTITY: Final = "sun.sun"

ESTIMATION_NONE: Final = "measured"
ESTIMATION_REFERENCE: Final = "reference_offset"
ESTIMATION_MODEL: Final = "model"
ESTIMATION_OPTIONS: Final = [ESTIMATION_NONE, ESTIMATION_REFERENCE, ESTIMATION_MODEL]
#: Confidence attached to each estimation level (specification section 11).
ESTIMATION_CONFIDENCE: Final = {
    "measured": 1.0,
    "reference_offset": 0.6,
    "learned_offset": 0.75,
    "model": 0.5,
    "average": 0.4,
}

COMPASS_PRESETS: Final = {
    "n": 0.0,
    "ne": 45.0,
    "e": 90.0,
    "se": 135.0,
    "s": 180.0,
    "sw": 225.0,
    "w": 270.0,
    "nw": 315.0,
}

# --------------------------------------------------------------------------
# Services
# --------------------------------------------------------------------------

SERVICE_START_PURGE: Final = "start_purge"
SERVICE_SNOOZE: Final = "snooze"
SERVICE_ACKNOWLEDGE: Final = "acknowledge"
SERVICE_SET_MODE: Final = "set_mode"
SERVICE_RECALIBRATE: Final = "recalibrate"
SERVICE_OVERRIDE_PARAMETER: Final = "override_parameter"
SERVICE_EXPORT_DIAGNOSTICS: Final = "export_diagnostics"
SERVICE_SIMULATE: Final = "simulate"

ATTR_ROOM: Final = "room"
ATTR_WINDOW: Final = "window"
ATTR_DURATION: Final = "duration"
ATTR_RECOMMENDATION_ID: Final = "recommendation_id"
ATTR_UNTIL: Final = "until"
ATTR_SCOPE: Final = "scope"
ATTR_PARAMETER: Final = "parameter"
ATTR_VALUE: Final = "value"
ATTR_RESET: Final = "reset"
ATTR_START: Final = "start"
ATTR_END: Final = "end"
ATTR_SHADING_FROM: Final = "shading_from"

SNOOZE_SCOPES: Final = ["1h", "until_evening", "today", "all_1h"]

# --------------------------------------------------------------------------
# Events
# --------------------------------------------------------------------------

EVENT_RECOMMENDATION_ADDED: Final = f"{DOMAIN}_recommendation_added"
EVENT_RECOMMENDATION_CLEARED: Final = f"{DOMAIN}_recommendation_cleared"
EVENT_PURGE_FINISHED: Final = f"{DOMAIN}_purge_finished"
EVENT_CALIBRATION_UPDATED: Final = f"{DOMAIN}_calibration_updated"

# --------------------------------------------------------------------------
# Repair issues
# --------------------------------------------------------------------------

ISSUE_OUTDOOR_SENSOR_IN_SUN: Final = "outdoor_sensor_in_sun"
ISSUE_NO_FORECAST: Final = "no_hourly_forecast"
ISSUE_NO_ROOMS: Final = "no_rooms_configured"
ISSUE_NO_WINDOWS: Final = "no_windows_configured"
ISSUE_MISSING_ENTITY: Final = "missing_entity"
