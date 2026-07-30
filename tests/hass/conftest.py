"""Fixtures for the tests that need a running Home Assistant instance.

These require ``pytest-homeassistant-custom-component``, which does not run on
Windows (it needs ``fcntl`` and unix sockets) and fails while pytest is still
loading plugins, so nothing here can skip itself. On Windows the plugin has to
be kept from loading and this directory has to be excluded by hand; CI runs it
on Linux. See ``docs/development.md``.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Let Home Assistant load ``custom_components/adaptive_ventilation``.

    ``enable_custom_integrations`` comes from
    ``pytest-homeassistant-custom-component``, which registers itself through
    its ``pytest11`` entry point. Every test in this package is skipped when
    that plugin is not active.
    """
    yield
