"""Fixtures for the tests that need a running Home Assistant instance.

These require ``pytest-homeassistant-custom-component``, which does not run on
Windows (it needs ``fcntl`` and unix sockets). They are skipped there and run
in CI on Linux; see ``docs/development.md``.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):  # noqa: ANN001, ANN201
    """Let Home Assistant load ``custom_components/adaptive_ventilation``.

    ``enable_custom_integrations`` comes from
    ``pytest-homeassistant-custom-component``, which registers itself through
    its ``pytest11`` entry point. Every test in this package is skipped when
    that plugin is not active.
    """
    yield
