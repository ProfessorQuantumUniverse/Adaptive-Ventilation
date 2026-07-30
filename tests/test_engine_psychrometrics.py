"""Psychrometrics against published reference values.

Everything else in the integration rests on these numbers being right, so they
are checked against textbook values rather than against themselves.
"""

from __future__ import annotations

import pytest

from adaptive_ventilation.engine import psychrometrics as psy


@pytest.mark.parametrize(
    ("temperature", "expected"),
    [
        (0.0, 6.11),
        (10.0, 12.28),
        (20.0, 23.39),
        (25.0, 31.69),
        (30.0, 42.47),
        (-10.0, 2.60),
    ],
)
def test_saturation_vapour_pressure(temperature: float, expected: float) -> None:
    """Magnus formula against the standard table (hPa).

    The Magnus approximation is accurate to about 0.3 % over this range, which
    is three orders of magnitude better than any humidity sensor in a flat.
    """
    assert psy.saturation_vapour_pressure(temperature) == pytest.approx(expected, rel=0.004)


@pytest.mark.parametrize(
    ("temperature", "humidity", "expected"),
    [
        (20.0, 50.0, 8.65),
        (25.0, 60.0, 13.82),
        (0.0, 90.0, 4.37),
        (30.0, 40.0, 12.13),
        (-10.0, 90.0, 1.96),
    ],
)
def test_absolute_humidity(temperature: float, humidity: float, expected: float) -> None:
    assert psy.absolute_humidity(temperature, humidity) == pytest.approx(expected, abs=0.05)


@pytest.mark.parametrize(
    ("temperature", "humidity", "expected"),
    [
        (20.0, 50.0, 9.26),
        (25.0, 60.0, 16.69),
        (30.0, 80.0, 26.16),
        (5.0, 90.0, 3.51),
    ],
)
def test_dew_point(temperature: float, humidity: float, expected: float) -> None:
    assert psy.dew_point(temperature, humidity) == pytest.approx(expected, abs=0.1)


def test_dew_point_never_exceeds_temperature() -> None:
    for temperature in range(-20, 40):
        for humidity in (1, 25, 50, 75, 99, 100):
            assert psy.dew_point(float(temperature), float(humidity)) <= temperature + 1e-6


def test_absolute_humidity_roundtrip() -> None:
    for temperature in (-15.0, 0.0, 12.5, 25.0, 35.0):
        for humidity in (10.0, 45.0, 80.0, 99.0):
            absolute = psy.absolute_humidity(temperature, humidity)
            back = psy.relative_humidity_from_absolute(temperature, absolute)
            assert back == pytest.approx(humidity, abs=0.01)


@pytest.mark.parametrize(
    ("temperature", "humidity", "expected"),
    [
        (20.0, 50.0, 38.6),
        (25.0, 50.0, 50.4),
        (30.0, 60.0, 71.4),
        (0.0, 80.0, 7.54),
    ],
)
def test_enthalpy(temperature: float, humidity: float, expected: float) -> None:
    """Specific enthalpy in kJ/kg against the Mollier chart."""
    assert psy.enthalpy(temperature, humidity) == pytest.approx(expected, abs=0.6)


def test_cold_winter_air_is_bone_dry() -> None:
    """The claim the whole integration is built on, in one assertion."""
    outdoor = psy.absolute_humidity(-5.0, 90.0)
    indoor = psy.absolute_humidity(21.0, 45.0)
    assert outdoor < indoor
    assert psy.ventilation_dries(21.0, 45.0, -5.0, 90.0)


def test_muggy_summer_evening_adds_water() -> None:
    """Cooler in degrees, wetter in g/m3 - and worse in enthalpy."""
    assert not psy.ventilation_dries(25.0, 50.0, 21.0, 88.0)
    assert psy.enthalpy_delta(25.0, 50.0, 21.0, 88.0) < 0.0


def test_mixing_conserves_absolute_humidity() -> None:
    temperature, humidity = psy.humidity_after_mixing(26.0, 50.0, 16.0, 80.0, 1.0)
    assert temperature == pytest.approx(16.0)
    assert humidity == pytest.approx(80.0, abs=0.01)

    temperature, humidity = psy.humidity_after_mixing(26.0, 50.0, 16.0, 80.0, 0.0)
    assert temperature == pytest.approx(26.0)
    assert humidity == pytest.approx(50.0, abs=0.01)

    half_temperature, half_humidity = psy.humidity_after_mixing(26.0, 50.0, 16.0, 80.0, 0.5)
    expected_ah = (psy.absolute_humidity(26.0, 50.0) + psy.absolute_humidity(16.0, 80.0)) / 2.0
    assert psy.absolute_humidity(half_temperature, half_humidity) == pytest.approx(
        expected_ah, abs=0.01
    )


def test_wall_surface_and_mold_risk() -> None:
    """Uninsulated old building, cold outside, humid inside -> mould."""
    surface = psy.wall_surface_temperature(20.0, -5.0, 0.35)
    assert surface == pytest.approx(11.25, abs=0.01)
    surface_rh = psy.surface_relative_humidity(20.0, 60.0, surface)
    assert surface_rh > 80.0
    assert psy.mold_risk_level(surface_rh) in ("moderate", "high", "condensation")

    # Same room in a modern insulated building stays below the DIN threshold.
    modern = psy.wall_surface_temperature(20.0, -5.0, 0.12)
    modern_rh = psy.surface_relative_humidity(20.0, 60.0, modern)
    assert modern_rh < psy.MOLD_MODERATE
    assert psy.mold_risk_level(modern_rh) in ("none", "low")


def test_co2_decay_recovers_air_change_rate() -> None:
    """Feed the model its own output back: 3 h^-1 in, 3 h^-1 out."""
    end = psy.co2_after_ventilation(1400.0, 420.0, 3.0, 0.25)
    recovered = psy.air_changes_from_co2_decay(1400.0, end, 420.0, 0.25)
    assert recovered == pytest.approx(3.0, abs=0.01)


def test_air_changes_from_co2_decay_rejects_nonsense() -> None:
    assert psy.air_changes_from_co2_decay(800.0, 900.0, 420.0, 0.5) is None
    assert psy.air_changes_from_co2_decay(800.0, 600.0, 420.0, 0.0) is None
    assert psy.air_changes_from_co2_decay(400.0, 380.0, 420.0, 0.5) is None


def test_apparent_temperature_drops_with_air_movement() -> None:
    still = psy.apparent_temperature(28.0, 60.0, 0.0)
    breeze = psy.apparent_temperature(28.0, 60.0, 1.0)
    assert 1.5 <= still - breeze <= 4.0
