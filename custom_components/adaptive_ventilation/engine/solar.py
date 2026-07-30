"""Solar geometry and per-window solar load.

Two things happen here:

* :func:`sun_position` reimplements the NOAA solar position algorithm so the
  engine can extrapolate the sun into the forecast without Home Assistant.
* :func:`solar_load` turns sun position, cloud cover, window geometry and the
  shading horizon into watts hitting a specific window.

The asymmetry that drives the whole shading logic: an **external** blind stops
roughly 80 % of the gain, an **internal** one only about 30 % — by then the
glass has already let the energy in.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import math
from typing import Final

from .state import SunState, WindowState, bearing_difference

#: Solar constant in W/m².
SOLAR_CONSTANT: Final = 1361.0
#: Atmospheric transmittance for a clear sky at air mass 1.
CLEAR_SKY_TRANSMITTANCE: Final = 0.70
#: Fraction of the direct beam a fully overcast sky removes (Kasten-Czeplak).
CLOUD_ATTENUATION: Final = 0.75
#: Sky view factor of an unobstructed vertical surface.
VERTICAL_SKY_VIEW: Final = 0.5
#: Default glazing transmission (g-value) when the user did not specify one.
DEFAULT_G_VALUE: Final = 0.6


def _julian_day(moment: datetime) -> float:
    """Julian day number for a timezone-aware datetime."""
    utc = moment.astimezone(UTC)
    year, month = utc.year, utc.month
    day = utc.day + (utc.hour + (utc.minute + (utc.second / 60.0)) / 60.0) / 24.0
    if month <= 2:
        year -= 1
        month += 12
    a = year // 100
    b = 2 - a + a // 4
    return math.floor(365.25 * (year + 4716)) + math.floor(30.6001 * (month + 1)) + day + b - 1524.5


def sun_position(moment: datetime, latitude: float, longitude: float) -> tuple[float, float]:
    """Return ``(elevation, azimuth)`` in degrees (azimuth 0 = north, 90 = east).

    NOAA algorithm, accurate to roughly 0.1° — far beyond what a ventilation
    recommendation needs, but cheap and dependency free.
    """
    jd = _julian_day(moment)
    t = (jd - 2451545.0) / 36525.0

    mean_longitude = (280.46646 + t * (36000.76983 + t * 0.0003032)) % 360.0
    mean_anomaly = 357.52911 + t * (35999.05029 - 0.0001537 * t)
    eccentricity = 0.016708634 - t * (0.000042037 + 0.0000001267 * t)

    m_rad = math.radians(mean_anomaly)
    centre = (
        math.sin(m_rad) * (1.914602 - t * (0.004817 + 0.000014 * t))
        + math.sin(2 * m_rad) * (0.019993 - 0.000101 * t)
        + math.sin(3 * m_rad) * 0.000289
    )
    true_longitude = mean_longitude + centre
    omega = 125.04 - 1934.136 * t
    apparent_longitude = true_longitude - 0.00569 - 0.00478 * math.sin(math.radians(omega))

    obliquity = 23.0 + (26.0 + (21.448 - t * (46.815 + t * (0.00059 - t * 0.001813))) / 60.0) / 60.0
    obliquity_corrected = obliquity + 0.00256 * math.cos(math.radians(omega))

    lambda_rad = math.radians(apparent_longitude)
    eps_rad = math.radians(obliquity_corrected)
    declination = math.degrees(math.asin(math.sin(eps_rad) * math.sin(lambda_rad)))

    y = math.tan(eps_rad / 2.0) ** 2
    l0_rad = math.radians(mean_longitude)
    equation_of_time = 4.0 * math.degrees(
        y * math.sin(2 * l0_rad)
        - 2.0 * eccentricity * math.sin(m_rad)
        + 4.0 * eccentricity * y * math.sin(m_rad) * math.cos(2 * l0_rad)
        - 0.5 * y * y * math.sin(4 * l0_rad)
        - 1.25 * eccentricity * eccentricity * math.sin(2 * m_rad)
    )

    utc = moment.astimezone(UTC)
    minutes = utc.hour * 60.0 + utc.minute + utc.second / 60.0
    true_solar_time = (minutes + equation_of_time + 4.0 * longitude) % 1440.0
    hour_angle = true_solar_time / 4.0 - 180.0

    lat_rad = math.radians(latitude)
    dec_rad = math.radians(declination)
    ha_rad = math.radians(hour_angle)

    cos_zenith = math.sin(lat_rad) * math.sin(dec_rad) + math.cos(lat_rad) * math.cos(
        dec_rad
    ) * math.cos(ha_rad)
    cos_zenith = max(-1.0, min(1.0, cos_zenith))
    zenith = math.degrees(math.acos(cos_zenith))
    elevation = 90.0 - zenith

    sin_zenith = math.sin(math.radians(zenith))
    if abs(sin_zenith) < 1e-6:
        azimuth = 180.0
    else:
        cos_azimuth = (math.sin(dec_rad) - math.sin(lat_rad) * cos_zenith) / (
            math.cos(lat_rad) * sin_zenith
        )
        cos_azimuth = max(-1.0, min(1.0, cos_azimuth))
        azimuth = math.degrees(math.acos(cos_azimuth))
        if hour_angle > 0.0:
            azimuth = 360.0 - azimuth

    # Atmospheric refraction near the horizon.
    if -1.0 < elevation < 15.0:
        elevation += 0.0167 * (1.0 / math.tan(math.radians(max(elevation, 0.1) + 5.0)))

    return elevation, azimuth % 360.0


def air_mass(elevation: float) -> float:
    """Relative optical air mass (Kasten & Young 1989)."""
    if elevation <= 0.0:
        return 40.0
    return float(
        1.0 / (math.sin(math.radians(elevation)) + 0.50572 * (6.07995 + elevation) ** -1.6364)
    )


def clear_sky_direct(elevation: float) -> float:
    """Direct normal irradiance for a clear sky in W/m²."""
    if elevation <= 0.0:
        return 0.0
    return float(SOLAR_CONSTANT * CLEAR_SKY_TRANSMITTANCE ** (air_mass(elevation) ** 0.678))


def clear_sky_diffuse(elevation: float) -> float:
    """Diffuse horizontal irradiance for a clear sky in W/m²."""
    if elevation <= 0.0:
        return 0.0
    return 0.10 * clear_sky_direct(elevation) * math.sin(math.radians(elevation)) + 20.0


def cloud_factor(cloud_coverage: float | None) -> float:
    """Fraction of the direct beam that survives the given cloud cover."""
    if cloud_coverage is None:
        return 1.0
    cover = max(0.0, min(100.0, cloud_coverage)) / 100.0
    return float(max(0.0, 1.0 - CLOUD_ATTENUATION * cover**3.4))


def incidence_cosine(sun_elevation: float, sun_azimuth: float, window_azimuth: float) -> float:
    """cos(θ) of the sun on a vertical window, clamped at zero."""
    if sun_elevation <= 0.0:
        return 0.0
    elev = math.radians(sun_elevation)
    delta = math.radians(bearing_difference(sun_azimuth, window_azimuth))
    return max(0.0, math.cos(elev) * math.cos(delta))


def horizon_elevation(profile: tuple[float, ...] | None, azimuth: float) -> float:
    """Obstruction elevation in degrees at ``azimuth``.

    The profile is a ring of samples covering 360°; 36 entries (10° steps) is
    the resolution the config flow offers, but any length works.
    """
    if not profile:
        return 0.0
    count = len(profile)
    step = 360.0 / count
    index = int((azimuth % 360.0) / step) % count
    return profile[index]


def is_shaded_by_horizon(window: WindowState, sun: SunState) -> bool:
    """Whether neighbouring buildings or trees still block the sun."""
    return sun.elevation <= horizon_elevation(window.horizon_profile, sun.azimuth)


def solar_load(
    window: WindowState,
    sun_elevation: float,
    sun_azimuth: float,
    cloud_coverage: float | None = None,
    *,
    apply_cover: bool = True,
) -> float:
    """Solar power entering through ``window`` in watts.

    ``apply_cover`` lets callers ask the counterfactual "what would come in if
    the blind were up" — that difference is the value of shading.
    """
    area = window.area_m2
    if area is None or area <= 0.0:
        area = 1.5
    if sun_elevation <= 0.0:
        return 0.0

    blocked = sun_elevation <= horizon_elevation(window.horizon_profile, sun_azimuth)
    cos_theta = 0.0 if blocked else incidence_cosine(sun_elevation, sun_azimuth, window.azimuth)

    direct = clear_sky_direct(sun_elevation) * cos_theta * cloud_factor(cloud_coverage)
    diffuse = clear_sky_diffuse(sun_elevation) * VERTICAL_SKY_VIEW
    g_value = window.g_value or DEFAULT_G_VALUE
    watts = area * (direct + diffuse) * g_value

    if apply_cover and window.cover_position is not None and window.has_cover:
        closed_fraction = max(0.0, min(100.0, 100 - window.cover_position)) / 100.0
        watts *= 1.0 - closed_fraction * window.cover_shading_efficiency
    return watts


def cover_saving_w(
    window: WindowState,
    sun_elevation: float,
    sun_azimuth: float,
    cloud_coverage: float | None = None,
) -> float:
    """Watts that lowering this cover completely would keep out right now."""
    if not window.has_cover:
        return 0.0
    open_load = solar_load(window, sun_elevation, sun_azimuth, cloud_coverage, apply_cover=False)
    return open_load * window.cover_shading_efficiency


def peak_solar_time(
    window: WindowState,
    reference: datetime,
    latitude: float,
    longitude: float,
    *,
    hours: int = 12,
    step_minutes: int = 15,
) -> tuple[datetime | None, float]:
    """When this window sees its highest solar load, and how high it gets."""
    best_time: datetime | None = None
    best_value = 0.0
    steps = int(hours * 60 / step_minutes)
    for i in range(steps + 1):
        moment = reference + timedelta(minutes=i * step_minutes)
        elevation, azimuth = sun_position(moment, latitude, longitude)
        value = solar_load(window, elevation, azimuth, None, apply_cover=False)
        if value > best_value:
            best_time, best_value = moment, value
    return best_time, best_value


def next_sun_hit(
    window: WindowState,
    reference: datetime,
    latitude: float,
    longitude: float,
    *,
    threshold_w: float = 150.0,
    hours: int = 14,
    step_minutes: int = 10,
) -> datetime | None:
    """First moment within ``hours`` at which the window exceeds ``threshold_w``.

    This is what makes shading *predictive*: lower the blind before the sun
    arrives, not once the room is already warm.
    """
    steps = int(hours * 60 / step_minutes)
    for i in range(steps + 1):
        moment = reference + timedelta(minutes=i * step_minutes)
        elevation, azimuth = sun_position(moment, latitude, longitude)
        if solar_load(window, elevation, azimuth, None, apply_cover=False) >= threshold_w:
            return moment
    return None


def daily_solar_energy_kwh(
    window: WindowState,
    day_start: datetime,
    latitude: float,
    longitude: float,
    cloud_coverage: float | None = None,
    *,
    step_minutes: int = 30,
) -> float:
    """Energy entering a window over 24 h in kWh — the weak-spot report metric."""
    total_wh = 0.0
    steps = int(24 * 60 / step_minutes)
    for i in range(steps):
        moment = day_start + timedelta(minutes=i * step_minutes)
        elevation, azimuth = sun_position(moment, latitude, longitude)
        total_wh += (
            solar_load(window, elevation, azimuth, cloud_coverage, apply_cover=False)
            * step_minutes
            / 60.0
        )
    return total_wh / 1000.0
