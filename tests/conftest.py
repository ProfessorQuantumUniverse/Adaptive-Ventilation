"""Pytest configuration.

The engine tests import ``adaptive_ventilation.engine`` and must work with no
Home Assistant installed at all - that is the architectural promise the whole
``engine/`` split exists for, and CI has a job that enforces it.

Importing ``adaptive_ventilation.engine`` normally executes the package
``__init__.py`` first, and that one *does* import Home Assistant. So the parent
package is registered here as a bare namespace module: submodules resolve
through its ``__path__``, but the integration's own setup code never runs.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
import sys
import types

import pytest

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "adaptive_ventilation"

if str(ROOT / "custom_components") not in sys.path:
    sys.path.insert(0, str(ROOT / "custom_components"))

if "adaptive_ventilation" not in sys.modules:
    _package = types.ModuleType("adaptive_ventilation")
    _package.__path__ = [str(COMPONENT)]  # type: ignore[attr-defined]
    _package.__doc__ = "Namespace stub - see the module docstring in tests/conftest.py."
    sys.modules["adaptive_ventilation"] = _package

from adaptive_ventilation.engine import (  # noqa: E402
    ForecastHour,
    OutdoorState,
    RoomState,
    SunState,
    WindowState,
    WorldState,
)


@pytest.fixture
def now() -> datetime:
    return datetime(2025, 7, 15, 22, 0, tzinfo=UTC)


def make_forecast(
    start: datetime, temperatures: list[float], humidity: float | None = 60.0
) -> tuple[ForecastHour, ...]:
    return tuple(
        ForecastHour(time=start + timedelta(hours=i), temperature=t, humidity=humidity)
        for i, t in enumerate(temperatures)
    )


def make_world(
    now: datetime,
    *,
    outdoor_temperature: float = 18.0,
    outdoor_humidity: float = 65.0,
    indoor_temperature: float = 26.0,
    indoor_humidity: float = 50.0,
    forecast: tuple[ForecastHour, ...] | None = None,
    sun_elevation: float = -10.0,
    **kwargs: object,
) -> WorldState:
    """Minimal, sane world used by the unit tests."""
    room = RoomState.create(
        "living_room",
        "Living room",
        indoor_temperature,
        indoor_humidity,
        volume_m3=60.0,
        priority=1,
    )
    window = WindowState(
        id="living_south",
        name="Living room south",
        room_id="living_room",
        azimuth=190.0,
        area_m2=2.2,
    )
    return WorldState(
        now=now,
        outdoor=OutdoorState.create(outdoor_temperature, outdoor_humidity, pressure=1013.0),
        rooms=(room,),
        windows=(window,),
        forecast=forecast
        if forecast is not None
        else make_forecast(now, [outdoor_temperature] * 30, outdoor_humidity),
        sun=SunState(azimuth=300.0, elevation=sun_elevation),
        **kwargs,  # type: ignore[arg-type]
    )
