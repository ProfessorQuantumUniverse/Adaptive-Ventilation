"""Psychrometrics — the physical core of Adaptive Ventilation.

Every decision about whether ventilation *dries* or *humidifies* a room is made
on absolute humidity, dew point and enthalpy. Relative humidity is only ever an
input, never a criterion: cold winter air at 90 % RH is bone dry in absolute
terms, a muggy summer evening at 65 % RH carries water into the flat.

Formulas follow the Magnus approximation as specified in
``SPEC.md`` section 6. Valid range roughly -45 °C .. +60 °C.
"""

from __future__ import annotations

import math
from typing import Final

#: Magnus coefficients over liquid water (T >= 0 °C).
MAGNUS_A_WATER: Final = 17.62
MAGNUS_B_WATER: Final = 243.12
#: Magnus coefficients over ice (T < 0 °C).
MAGNUS_A_ICE: Final = 22.46
MAGNUS_B_ICE: Final = 272.62
#: Saturation vapour pressure at 0 °C in hPa.
E0: Final = 6.112

STANDARD_PRESSURE: Final = 1013.25
#: Specific heat capacity of dry air in kJ/(kg·K).
CP_AIR: Final = 1.006
#: Specific heat capacity of water vapour in kJ/(kg·K).
CP_VAPOUR: Final = 1.86
#: Evaporation enthalpy of water at 0 °C in kJ/kg.
H_VAPORISATION: Final = 2501.0
#: Air density at 20 °C, used for the ventilation heat balance in kg/m³.
RHO_AIR: Final = 1.2

#: Mold risk thresholds on the wall surface (DIN 4108-2).
MOLD_LOW: Final = 70.0
MOLD_MODERATE: Final = 80.0
MOLD_HIGH: Final = 90.0
MOLD_CONDENSATION: Final = 95.0


def _magnus_coefficients(temperature: float) -> tuple[float, float]:
    """Magnus coefficients, switching to the ice formulation below 0 °C."""
    if temperature < 0.0:
        return MAGNUS_A_ICE, MAGNUS_B_ICE
    return MAGNUS_A_WATER, MAGNUS_B_WATER


def saturation_vapour_pressure(temperature: float) -> float:
    """Saturation vapour pressure ``e_s`` in hPa."""
    a, b = _magnus_coefficients(temperature)
    return E0 * math.exp(a * temperature / (b + temperature))


def vapour_pressure(temperature: float, relative_humidity: float) -> float:
    """Actual vapour pressure ``e`` in hPa."""
    return max(0.0, relative_humidity) / 100.0 * saturation_vapour_pressure(temperature)


def absolute_humidity(temperature: float, relative_humidity: float) -> float:
    """Absolute humidity in g/m³ — *the* comparison metric of this integration."""
    e = vapour_pressure(temperature, relative_humidity)
    return 216.7 * e / (temperature + 273.15)


def absolute_humidity_from_vapour_pressure(temperature: float, e: float) -> float:
    """Absolute humidity in g/m³ from a vapour pressure in hPa."""
    return 216.7 * e / (temperature + 273.15)


def relative_humidity_from_absolute(temperature: float, absolute: float) -> float:
    """Invert :func:`absolute_humidity` — RH in % for a given g/m³."""
    e = absolute * (temperature + 273.15) / 216.7
    e_s = saturation_vapour_pressure(temperature)
    if e_s <= 0.0:
        return 0.0
    return min(100.0, max(0.0, e / e_s * 100.0))


def dew_point(temperature: float, relative_humidity: float) -> float:
    """Dew point in °C."""
    if relative_humidity <= 0.0:
        return float("-inf")
    a, b = _magnus_coefficients(temperature)
    e = vapour_pressure(temperature, relative_humidity)
    ln_ratio = math.log(max(e, 1e-9) / E0)
    return b * ln_ratio / (a - ln_ratio)


def dew_point_from_absolute(temperature: float, absolute: float) -> float:
    """Dew point in °C for a given absolute humidity in g/m³."""
    return dew_point(temperature, relative_humidity_from_absolute(temperature, absolute))


def mixing_ratio(
    temperature: float, relative_humidity: float, pressure: float = STANDARD_PRESSURE
) -> float:
    """Humidity ratio ``x`` in kg water per kg dry air."""
    e = vapour_pressure(temperature, relative_humidity)
    denominator = max(pressure - e, 1e-6)
    return 0.622 * e / denominator


def enthalpy(
    temperature: float, relative_humidity: float, pressure: float = STANDARD_PRESSURE
) -> float:
    """Specific enthalpy in kJ per kg dry air.

    For "is it actually cooler out there" on a muggy summer evening this is the
    honest comparison: humid air carries latent heat into the room even when the
    thermometer says it is cooler.
    """
    x = mixing_ratio(temperature, relative_humidity, pressure)
    return CP_AIR * temperature + x * (H_VAPORISATION + CP_VAPOUR * temperature)


def wet_bulb_temperature(temperature: float, relative_humidity: float) -> float:
    """Wet bulb temperature in °C (Stull 2011 approximation, ±0.3 K)."""
    rh = max(0.5, min(100.0, relative_humidity))
    return (
        temperature * math.atan(0.151977 * math.sqrt(rh + 8.313659))
        + math.atan(temperature + rh)
        - math.atan(rh - 1.676331)
        + 0.00391838 * rh**1.5 * math.atan(0.023101 * rh)
        - 4.686035
    )


def apparent_temperature(
    temperature: float, relative_humidity: float, air_speed: float = 0.0
) -> float:
    """Perceived temperature in °C.

    Humidity pushes it up, moving air pulls it down — which is exactly why the
    ``fan_instead`` rule exists: 0.8 m/s of air movement is worth 2-3 K.
    """
    e = vapour_pressure(temperature, relative_humidity) / 10.0  # kPa
    # Steadman's apparent temperature.
    result = temperature + 0.33 * e - 0.70 * min(air_speed, 3.0) - 4.00
    if air_speed > 0.1:
        # Additional cooling from air movement, saturating around 3 K.
        result -= 2.9 * (1.0 - math.exp(-1.2 * air_speed))
    return result


def wall_surface_temperature(
    indoor_temperature: float, outdoor_temperature: float, f_rt: float
) -> float:
    """Temperature of the coldest wall surface in °C.

    ``f_rt`` is the temperature factor of the thermal bridge: 0.30-0.40 for an
    uninsulated old building, 0.10-0.13 for a modern insulated one.
    """
    return indoor_temperature - f_rt * (indoor_temperature - outdoor_temperature)


def surface_relative_humidity(
    indoor_temperature: float,
    indoor_relative_humidity: float,
    surface_temperature: float,
) -> float:
    """Relative humidity directly at the wall surface in %."""
    e_in = vapour_pressure(indoor_temperature, indoor_relative_humidity)
    e_s_surface = saturation_vapour_pressure(surface_temperature)
    if e_s_surface <= 0.0:
        return 100.0
    return min(100.0, e_in / e_s_surface * 100.0)


def mold_risk_level(surface_humidity: float) -> str:
    """Classify a surface RH into ``none``/``low``/``moderate``/``high``/``condensation``."""
    if surface_humidity >= MOLD_CONDENSATION:
        return "condensation"
    if surface_humidity >= MOLD_HIGH:
        return "high"
    if surface_humidity >= MOLD_MODERATE:
        return "moderate"
    if surface_humidity >= MOLD_LOW:
        return "low"
    return "none"


def ventilation_dries(
    indoor_temperature: float,
    indoor_humidity: float,
    outdoor_temperature: float,
    outdoor_humidity: float,
) -> bool:
    """``True`` when opening the window lowers the indoor absolute humidity."""
    return absolute_humidity(outdoor_temperature, outdoor_humidity) < absolute_humidity(
        indoor_temperature, indoor_humidity
    )


def enthalpy_delta(
    indoor_temperature: float,
    indoor_humidity: float,
    outdoor_temperature: float,
    outdoor_humidity: float,
    pressure: float = STANDARD_PRESSURE,
) -> float:
    """Indoor minus outdoor enthalpy in kJ/kg (positive = outdoor air is cooler)."""
    return enthalpy(indoor_temperature, indoor_humidity, pressure) - enthalpy(
        outdoor_temperature, outdoor_humidity, pressure
    )


def humidity_after_mixing(
    indoor_temperature: float,
    indoor_humidity: float,
    outdoor_temperature: float,
    outdoor_humidity: float,
    exchanged_fraction: float,
) -> tuple[float, float]:
    """Temperature and RH after exchanging ``exchanged_fraction`` of the air.

    Mixing is done on absolute humidity (which is conserved per volume of air),
    never on RH — mixing percentages is a classic and expensive mistake.
    """
    fraction = max(0.0, min(1.0, exchanged_fraction))
    ah_in = absolute_humidity(indoor_temperature, indoor_humidity)
    ah_out = absolute_humidity(outdoor_temperature, outdoor_humidity)
    new_temperature = indoor_temperature + fraction * (outdoor_temperature - indoor_temperature)
    new_ah = ah_in + fraction * (ah_out - ah_in)
    return new_temperature, relative_humidity_from_absolute(new_temperature, new_ah)


def co2_after_ventilation(
    indoor_ppm: float, outdoor_ppm: float, air_changes: float, hours: float
) -> float:
    """CO₂ concentration after ``hours`` at ``air_changes`` per hour."""
    return outdoor_ppm + (indoor_ppm - outdoor_ppm) * math.exp(-air_changes * hours)


def air_changes_from_co2_decay(
    start_ppm: float, end_ppm: float, outdoor_ppm: float, hours: float
) -> float | None:
    """Derive the real air exchange rate from a measured CO₂ decay.

    ``n = ln((C0 - C_out) / (C1 - C_out)) / Δt`` — this is how the rule of thumb
    becomes *your* flat (see specification section 9.2).
    """
    if hours <= 0.0:
        return None
    numerator = start_ppm - outdoor_ppm
    denominator = end_ppm - outdoor_ppm
    if numerator <= 0.0 or denominator <= 0.0 or numerator <= denominator:
        return None
    return math.log(numerator / denominator) / hours


def moisture_load_g_per_h(
    volume_m3: float, delta_absolute_humidity: float, hours: float
) -> float | None:
    """Water added to a room in g/h from a rise in absolute humidity."""
    if hours <= 0.0:
        return None
    return volume_m3 * delta_absolute_humidity / hours


def ventilation_heat_flow_w(
    volume_m3: float, air_changes: float, delta_temperature: float
) -> float:
    """Heat flow through ventilation in W (positive = heat entering the room)."""
    # 0.34 Wh/(m³·K) is the standard volumetric heat capacity of air.
    return 0.34 * volume_m3 * air_changes * delta_temperature
