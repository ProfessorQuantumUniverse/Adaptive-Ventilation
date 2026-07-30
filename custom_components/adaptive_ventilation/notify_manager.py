"""Notification delivery: dedupe, cooldown, rate limit, actionable buttons.

The engine has already decided *whether* a recommendation may be pushed
(quiet hours, confidence, snooze). This module decides *how often* and *where*,
and it withdraws notifications that have become obsolete instead of leaving
stale advice on the lock screen.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import logging
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.util import dt as dt_util

from . import messages
from .const import (
    CONF_ACTIONABLE_NOTIFICATIONS,
    CONF_NOTIFY_TARGETS,
    DOMAIN,
    SERVICE_ACKNOWLEDGE,
    SERVICE_SNOOZE,
)
from .engine.state import Action, EvaluationResult, Priority, Recommendation

if TYPE_CHECKING:
    from .coordinator import AdaptiveVentilationCoordinator

_LOGGER = logging.getLogger(__name__)

ACTION_ACKNOWLEDGE = "AV_DONE"
ACTION_SNOOZE = "AV_SNOOZE"
ACTION_IGNORE = "AV_IGNORE"

#: Actions that get an extra "do it now" button when cover automation is off.
COVER_ACTIONS = {Action.COVER_DOWN, Action.COVER_UP, Action.COVER_SLAT}


@dataclass
class SentNotification:
    """One notification currently on the user's phone."""

    recommendation_id: str
    tag: str
    sent_at: datetime
    reason_key: str
    action: Action


class NotificationManager:
    """Owns everything that leaves the house as a push message."""

    def __init__(self, hass: HomeAssistant, coordinator: AdaptiveVentilationCoordinator) -> None:
        self.hass = hass
        self.coordinator = coordinator
        self._sent: dict[str, SentNotification] = {}
        self._listener_registered = False
        self._register_action_listener()

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    async def async_process(self, result: EvaluationResult) -> None:
        """Send what is new, update what changed, withdraw what is obsolete."""
        options = self.coordinator.config.options
        targets = options.get(CONF_NOTIFY_TARGETS) or []
        preferences = self.coordinator.world.preferences if self.coordinator.world else None
        if preferences is None or not preferences.notifications_enabled:
            await self._withdraw_all()
            return

        now = result.now
        pushable = [rec for rec in result.recommendations if rec.notify]
        active_ids = {rec.id for rec in pushable}

        # Withdraw anything that no longer applies - a notification telling you
        # to close a window you already closed is worse than none at all.
        for rec_id in list(self._sent):
            if rec_id not in active_ids:
                await self.async_clear(rec_id)

        if not targets:
            return

        limit = preferences.max_pushes_per_day
        for rec in sorted(pushable, key=lambda r: r.sort_key, reverse=True):
            previous = self._sent.get(rec.id)
            if previous is not None and previous.reason_key == rec.reason_key:
                continue  # already on screen and unchanged

            if not self.coordinator.memory.may_push(rec.priority, now, limit):
                _LOGGER.debug("daily push budget exhausted, holding back %s", rec.id)
                continue

            await self._send(rec, targets, now)

    async def async_clear(self, recommendation_id: str) -> None:
        """Withdraw a notification from every target."""
        sent = self._sent.pop(recommendation_id, None)
        if sent is None:
            return
        for target in self.coordinator.config.options.get(CONF_NOTIFY_TARGETS) or []:
            await self._call_notify(
                target, {"message": "clear_notification", "data": {"tag": sent.tag}}
            )

    async def _withdraw_all(self) -> None:
        for rec_id in list(self._sent):
            await self.async_clear(rec_id)

    # ------------------------------------------------------------------
    # Sending
    # ------------------------------------------------------------------

    async def _send(self, rec: Recommendation, targets: list[str], now: datetime) -> None:
        language = messages.resolve_language(self.hass.config.language)
        body = messages.render(rec.reason_key, rec.reason_data, language)
        if rec.expected_benefit:
            body = f"{body} ({rec.expected_benefit})"
        heading = messages.title(rec.priority.name, language)
        tag = f"{DOMAIN}_{rec.id}".replace(":", "_")

        payload: dict[str, Any] = {
            "title": heading,
            "message": body,
            "data": {
                "tag": tag,
                "group": DOMAIN,
                "channel": "Adaptive Ventilation",
                "importance": "high" if rec.priority is Priority.SAFETY else "default",
                "notification_icon": _icon(rec.action),
                # Compact payload so users can build their own automations.
                "adaptive_ventilation": {
                    "id": rec.id,
                    "rule": rec.rule_id,
                    "action": rec.action.value,
                    "priority": rec.priority.name,
                    "target": rec.target,
                    "room_id": rec.room_id,
                    "duration_minutes": rec.duration_minutes,
                },
            },
        }
        if rec.priority is Priority.SAFETY:
            payload["data"]["ttl"] = 0
            payload["data"]["priority"] = "high"

        if self.coordinator.config.options.get(CONF_ACTIONABLE_NOTIFICATIONS, True):
            payload["data"]["actions"] = _actions(rec, language)

        for target in targets:
            await self._call_notify(target, payload)

        self._sent[rec.id] = SentNotification(
            recommendation_id=rec.id,
            tag=tag,
            sent_at=now,
            reason_key=rec.reason_key,
            action=rec.action,
        )
        self.coordinator.memory.register_push(
            rec.id,
            rec.target,
            now,
            int(self.coordinator.config.options.get("cooldown_minutes", 60)),
        )

    async def _call_notify(self, target: str, payload: dict[str, Any]) -> None:
        service = target.removeprefix("notify.")
        try:
            await self.hass.services.async_call("notify", service, payload, blocking=False)
        except Exception as err:
            _LOGGER.warning("could not notify %s: %s", target, err)

    # ------------------------------------------------------------------
    # Actionable notification callbacks
    # ------------------------------------------------------------------

    @callback
    def _register_action_listener(self) -> None:
        if self._listener_registered:
            return
        self.hass.bus.async_listen("mobile_app_notification_action", self._handle_action)
        self._listener_registered = True

    async def _handle_action(self, event: Any) -> None:
        """React to the buttons on the notification."""
        raw_action = event.data.get("action", "")
        if not raw_action.startswith(("AV_",)):
            return
        try:
            name, recommendation_id = raw_action.split("|", 1)
        except ValueError:
            return

        if name == ACTION_ACKNOWLEDGE:
            await self.coordinator.async_acknowledge(recommendation_id)
        elif name == ACTION_SNOOZE:
            await self.coordinator.async_snooze(recommendation_id, "1h")
            await self.async_clear(recommendation_id)
        elif name == ACTION_IGNORE:
            await self.coordinator.async_snooze(recommendation_id, "today")
            await self.async_clear(recommendation_id)


def _actions(rec: Recommendation, language: str) -> list[dict[str, str]]:
    done = "Erledigt" if language == "de" else "Done"
    snooze = "1 h später" if language == "de" else "Snooze 1 h"
    ignore = "Heute ignorieren" if language == "de" else "Ignore today"
    return [
        {"action": f"{ACTION_ACKNOWLEDGE}|{rec.id}", "title": done},
        {"action": f"{ACTION_SNOOZE}|{rec.id}", "title": snooze},
        {"action": f"{ACTION_IGNORE}|{rec.id}", "title": ignore},
    ]


def _icon(action: Action) -> str:
    return {
        Action.OPEN_WIDE: "mdi:window-open",
        Action.OPEN_TILT: "mdi:window-open-variant",
        Action.PURGE: "mdi:air-filter",
        Action.CROSS_VENTILATE: "mdi:swap-horizontal",
        Action.CLOSE: "mdi:window-closed",
        Action.KEEP_CLOSED: "mdi:window-closed-variant",
        Action.KEEP_OPEN: "mdi:window-open",
        Action.COVER_DOWN: "mdi:window-shutter",
        Action.COVER_UP: "mdi:window-shutter-open",
        Action.COVER_SLAT: "mdi:window-shutter-settings",
        Action.FAN_ON: "mdi:fan",
        Action.NO_ACTION: "mdi:weather-windy",
    }.get(action, "mdi:weather-windy")


def cooldown_until(now: datetime, minutes: int) -> datetime:
    """End of a cooldown window."""
    return now + timedelta(minutes=minutes)


__all__ = [
    "SERVICE_ACKNOWLEDGE",
    "SERVICE_SNOOZE",
    "NotificationManager",
    "cooldown_until",
    "dt_util",
]
