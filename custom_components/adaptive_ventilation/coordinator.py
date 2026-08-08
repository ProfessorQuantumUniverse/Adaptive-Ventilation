"""The coordinator: collect the world, run the engine, publish the result."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict
from datetime import datetime, timedelta
from functools import partial
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import CALLBACK_TYPE, CoreState, Event, HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (
    CALIBRATION_INTERVAL,
    CONF_CALENDAR_ENTITY,
    CONF_COVER_AUTOMATION,
    CONF_DISABLED_RULES,
    CONF_PRESENCE_ENTITY,
    CONF_SUN_ENTITY,
    CONF_WEATHER_ALERT_ENTITY,
    CONF_WEATHER_ENTITY,
    DEFAULT_SUN_ENTITY,
    DOMAIN,
    EVENT_PURGE_FINISHED,
    EVENT_RECOMMENDATION_ADDED,
    EVENT_RECOMMENDATION_CLEARED,
    ISSUE_NO_FORECAST,
    ISSUE_NO_ROOMS,
    ISSUE_OUTDOOR_SENSOR_IN_SUN,
    STORAGE_KEY_LEARNED,
    STORAGE_KEY_MEMORY,
    STORAGE_VERSION,
    SUBENTRY_ROOM,
    SUBENTRY_WINDOW,
    UPDATE_INTERVAL,
)
from .engine import EvaluationResult, evaluate, simulate_plan
from .engine.state import (
    Action,
    EngineMemory,
    LearnedParameters,
    Mode,
    Recommendation,
    WorldState,
)
from .models import (
    ParsedConfig,
    RoomConfig,
    WindowConfig,
    build_alerts,
    build_building,
    build_forecast,
    build_outdoor,
    build_preferences,
    build_rooms,
    build_sun,
    build_windows,
    read_bool,
)
from .storage import deserialize_learned, deserialize_memory, serialize_learned, serialize_memory

_LOGGER = logging.getLogger(__name__)

#: How long a fetched forecast is reused before asking the weather integration
#: again.
FORECAST_MAX_AGE = timedelta(minutes=20)
#: How long a forecast may keep being served once refreshing it starts failing.
#: Beyond this it is thrown away, which surfaces the ISSUE_NO_FORECAST repair.
FORECAST_MAX_STALENESS = timedelta(hours=3)

type AdaptiveVentilationConfigEntry = ConfigEntry["AdaptiveVentilationCoordinator"]


class AdaptiveVentilationCoordinator(DataUpdateCoordinator[EvaluationResult]):
    """Owns the world state, the engine memory and everything persisted."""

    config_entry: AdaptiveVentilationConfigEntry

    def __init__(self, hass: HomeAssistant, entry: AdaptiveVentilationConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=UPDATE_INTERVAL,
            config_entry=entry,
        )
        self.config = ParsedConfig()
        self.memory = EngineMemory()
        self.learned = LearnedParameters()
        self.mode = Mode.AUTO
        self.purge_active: dict[str, datetime] = {}
        self.world: WorldState | None = None

        self._memory_store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{STORAGE_KEY_MEMORY}.{entry.entry_id}"
        )
        self._learned_store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{STORAGE_KEY_LEARNED}.{entry.entry_id}"
        )
        self._forecast: tuple[Any, ...] = ()
        self._forecast_fetched: datetime | None = None
        self._unsubscribers: list[CALLBACK_TYPE] = []
        self._previous_ids: set[str] = set()
        self._debouncer = Debouncer(
            hass,
            _LOGGER,
            cooldown=2.0,
            immediate=False,
            function=self.async_refresh,
        )
        self._notifier: Any = None
        self._calibrator: Any = None

    # ------------------------------------------------------------------
    # Setup / teardown
    # ------------------------------------------------------------------

    async def async_setup(self) -> None:
        """Load persisted data, parse the config and start listening."""
        self.parse_config()

        stored_memory = await self._memory_store.async_load()
        if stored_memory:
            self.memory = deserialize_memory(stored_memory)
            # Unguarded this raises ValueError on any mode string this version
            # no longer knows, and an exception here fails the whole config
            # entry - the integration disappears rather than degrading.
            # deserialize_memory already treats a stale Action the same way.
            try:
                self.mode = Mode(stored_memory.get("mode", Mode.AUTO.value))
            except ValueError:
                _LOGGER.warning(
                    "Stored mode %r is not one this version knows - falling back to auto",
                    stored_memory.get("mode"),
                )
                self.mode = Mode.AUTO
            self.purge_active = {
                key: dt_util.parse_datetime(value) or dt_util.utcnow()
                for key, value in (stored_memory.get("purge_active") or {}).items()
            }
        stored_learned = await self._learned_store.async_load()
        if stored_learned:
            self.learned = deserialize_learned(stored_learned)

        from .calibration import Calibrator
        from .notify_manager import NotificationManager

        self._notifier = NotificationManager(self.hass, self)
        self._calibrator = Calibrator(self.hass, self)

        self._subscribe()

    def parse_config(self) -> None:
        """Re-read the subentries and options of the config entry."""
        entry = self.config_entry
        rooms: list[RoomConfig] = []
        windows: list[WindowConfig] = []
        for subentry_id, subentry in entry.subentries.items():
            if subentry.subentry_type == SUBENTRY_ROOM:
                rooms.append(RoomConfig.from_subentry(subentry_id, subentry.data))
            elif subentry.subentry_type == SUBENTRY_WINDOW:
                windows.append(WindowConfig.from_subentry(subentry_id, subentry.data))
        self.config = ParsedConfig(rooms=rooms, windows=windows, options=dict(entry.options))

    @callback
    def _subscribe(self) -> None:
        self._unsubscribe()
        entities = self.config.tracked_entities
        for key in (CONF_PRESENCE_ENTITY, CONF_WEATHER_ALERT_ENTITY, CONF_CALENDAR_ENTITY):
            value = self.config.options.get(key)
            if value:
                entities.append(value)
        if entities:
            self._unsubscribers.append(
                async_track_state_change_event(self.hass, entities, self._handle_state_change)
            )
        self._unsubscribers.append(
            async_track_time_interval(self.hass, self._handle_calibration, CALIBRATION_INTERVAL)
        )
        if self.hass.state is CoreState.running:
            # Set up after startup (a reload, or the entry being added by hand),
            # so the event we would be waiting for has already been and gone.
            self._debouncer.async_schedule_call()
            return

        @callback
        def _on_started(_event: Event) -> None:
            # A once-listener unregisters itself as it fires. Keeping our copy
            # of its unsubscribe meant every later reload called it a second
            # time, and Home Assistant logged "Unable to remove unknown job
            # listener" with a ValueError traceback for it.
            if unsubscribe in self._unsubscribers:
                self._unsubscribers.remove(unsubscribe)
            self._debouncer.async_schedule_call()

        unsubscribe = self.hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, _on_started)
        self._unsubscribers.append(unsubscribe)

    @callback
    def _unsubscribe(self) -> None:
        while self._unsubscribers:
            self._unsubscribers.pop()()

    async def async_shutdown(self) -> None:
        self._unsubscribe()
        if self._notifier is not None:
            self._notifier.async_shutdown()
        # Debouncer.async_shutdown is a @callback, not a coroutine - awaiting it
        # raised TypeError and made unloading the config entry fail.
        self._debouncer.async_shutdown()
        await self.async_persist()
        await super().async_shutdown()

    @callback
    def _handle_state_change(self, _event: Event) -> None:
        """A tracked entity changed - refresh, but not more than every 2 s."""
        self._debouncer.async_schedule_call()

    async def _handle_calibration(self, _now: datetime) -> None:
        if self._calibrator is not None:
            await self._calibrator.async_run()

    # ------------------------------------------------------------------
    # Update cycle
    # ------------------------------------------------------------------

    async def _async_update_data(self) -> EvaluationResult:
        try:
            world = await self.async_build_world()
        except Exception as err:  # pragma: no cover - defensive
            raise UpdateFailed(f"could not build world state: {err}") from err

        self.world = world
        # Not persisted, so it has to be re-applied on every cycle: the memory
        # decides what "today" means for the push budget, "ignore today" and
        # the manual hold, and that has to be the user's today, not UTC's.
        self.memory.timezone = self.hass.config.time_zone
        self._expire_purges(world.now)

        # partial, not positional args: disabled_rules is keyword-only.
        result = await self.hass.async_add_executor_job(
            partial(
                evaluate,
                world,
                self.memory,
                disabled_rules=self.config.options.get(CONF_DISABLED_RULES, ()),
            )
        )

        await self._fire_recommendation_events(result)
        await self._apply_cover_automation(result)
        if self._notifier is not None:
            await self._notifier.async_process(result)
        await self.async_persist()
        self._update_repairs(result)
        return result

    async def async_build_world(self) -> WorldState:
        """Read every configured entity and assemble the engine input."""
        options = self.config.options
        building = build_building(options, self.hass.config.latitude, self.hass.config.longitude)
        weather_entity = options.get(CONF_WEATHER_ENTITY)
        weather_state = self.hass.states.get(weather_entity) if weather_entity else None

        forecast = await self._async_forecast(weather_entity)
        presence = read_bool(self.hass, options.get(CONF_PRESENCE_ENTITY))
        now = dt_util.utcnow()

        return WorldState(
            now=now,
            outdoor=build_outdoor(self.hass, options, weather_state, _forecast_at(forecast, now)),
            rooms=build_rooms(self.hass, self.config.rooms),
            windows=build_windows(self.hass, self.config.windows),
            forecast=forecast,
            sun=build_sun(self.hass, options.get(CONF_SUN_ENTITY, DEFAULT_SUN_ENTITY), building),
            mode=self.mode,
            presence=True if presence is None else presence,
            weather_alerts=build_alerts(self.hass, options.get(CONF_WEATHER_ALERT_ENTITY)),
            building=building,
            preferences=build_preferences(options, self.hass.config.time_zone),
            learned=self.learned,
            purge_active=dict(self.purge_active),
        )

    async def _async_forecast(self, weather_entity: str | None) -> tuple[Any, ...]:
        """Hourly forecast, cached so we do not hammer the weather integration."""
        if not weather_entity:
            return ()
        now = dt_util.utcnow()
        if (
            self._forecast
            and self._forecast_fetched is not None
            and now - self._forecast_fetched < FORECAST_MAX_AGE
        ):
            return self._forecast

        try:
            response = await self.hass.services.async_call(
                "weather",
                "get_forecasts",
                {"entity_id": weather_entity, "type": "hourly"},
                blocking=True,
                return_response=True,
            )
        except Exception as err:
            _LOGGER.debug("hourly forecast unavailable for %s: %s", weather_entity, err)
            return self._keep_or_drop_forecast(weather_entity, now)

        raw = (response or {}).get(weather_entity, {}).get("forecast", [])
        forecast = build_forecast(raw)
        if not forecast:
            return self._keep_or_drop_forecast(weather_entity, now)

        self._forecast = forecast
        self._forecast_fetched = now
        return self._forecast

    def _keep_or_drop_forecast(self, weather_entity: str, now: datetime) -> tuple[Any, ...]:
        """Serve the cached forecast, but not forever.

        A weather integration that stops answering used to leave the last
        forecast in place indefinitely: the schedule, the tipping points and
        the whole cooling budget kept being computed from hours-old numbers,
        and the "no hourly forecast" repair never fired because the cache was
        not empty. Past FORECAST_MAX_STALENESS we would rather have no forecast
        than a confidently wrong one.
        """
        if self._forecast_fetched is None or now - self._forecast_fetched <= FORECAST_MAX_STALENESS:
            return self._forecast

        _LOGGER.warning(
            "no hourly forecast from %s since %s - dropping the cached one",
            weather_entity,
            self._forecast_fetched.isoformat(),
        )
        self._forecast = ()
        self._forecast_fetched = None
        return self._forecast

    # ------------------------------------------------------------------
    # Side effects
    # ------------------------------------------------------------------

    async def _fire_recommendation_events(self, result: EvaluationResult) -> None:
        current = {rec.id for rec in result.recommendations if rec.action is not Action.NO_ACTION}
        for rec in result.recommendations:
            if rec.id in self._previous_ids or rec.action is Action.NO_ACTION:
                continue
            self.hass.bus.async_fire(EVENT_RECOMMENDATION_ADDED, _recommendation_payload(rec))
        for stale in self._previous_ids - current:
            self.hass.bus.async_fire(EVENT_RECOMMENDATION_CLEARED, {"id": stale})
        self._previous_ids = current

    async def _apply_cover_automation(self, result: EvaluationResult) -> None:
        """Actually move the covers - but only where the user allowed it."""
        if not self.config.options.get(CONF_COVER_AUTOMATION, False):
            return
        for rec in result.recommendations:
            if rec.action not in (Action.COVER_DOWN, Action.COVER_UP, Action.COVER_SLAT):
                continue
            window_id = rec.target.removesuffix(":cover")
            window = self.config.window(window_id)
            if window is None or not window.cover_entity or not window.cover_auto_allowed:
                continue
            position = {
                Action.COVER_DOWN: 0,
                Action.COVER_UP: 100,
                Action.COVER_SLAT: 35,
            }[rec.action]
            _LOGGER.debug("moving %s to %s%% (%s)", window.cover_entity, position, rec.rule_id)
            await self.hass.services.async_call(
                "cover",
                "set_cover_position",
                {"entity_id": window.cover_entity, "position": position},
                blocking=False,
            )

    def _expire_purges(self, now: datetime) -> None:
        finished = [room_id for room_id, end in self.purge_active.items() if end <= now]
        for room_id in finished:
            del self.purge_active[room_id]
            self.memory.note_purge(room_id, now)
            self.hass.bus.async_fire(EVENT_PURGE_FINISHED, {"room_id": room_id})

    def _update_repairs(self, result: EvaluationResult) -> None:
        """Raise or clear repair issues for configuration problems."""
        entry_id = self.config_entry.entry_id

        _toggle_issue(
            self.hass,
            f"{ISSUE_NO_ROOMS}_{entry_id}",
            ISSUE_NO_ROOMS,
            not self.config.rooms,
            severity=ir.IssueSeverity.WARNING,
        )
        _toggle_issue(
            self.hass,
            f"{ISSUE_NO_FORECAST}_{entry_id}",
            ISSUE_NO_FORECAST,
            not self._forecast and bool(self.config.options.get(CONF_WEATHER_ENTITY)),
            severity=ir.IssueSeverity.WARNING,
        )
        implausible = result.diagnostics.get("notes", {}).get("outdoor_sensor_implausible")
        _toggle_issue(
            self.hass,
            f"{ISSUE_OUTDOOR_SENSOR_IN_SUN}_{entry_id}",
            ISSUE_OUTDOOR_SENSOR_IN_SUN,
            implausible is not None,
            severity=ir.IssueSeverity.WARNING,
            placeholders={"delta": str(implausible)} if implausible else None,
        )

    async def async_persist(self) -> None:
        """Write memory and learned parameters back to disk."""
        payload = serialize_memory(self.memory)
        payload["mode"] = self.mode.value
        payload["purge_active"] = {k: v.isoformat() for k, v in self.purge_active.items()}
        await self._memory_store.async_save(payload)
        await self._learned_store.async_save(serialize_learned(self.learned))

    # ------------------------------------------------------------------
    # Public API used by entities and services
    # ------------------------------------------------------------------

    async def async_set_mode(self, mode: Mode) -> None:
        self.mode = mode
        await self.async_persist()
        await self.async_request_refresh()

    async def async_start_purge(
        self, room_id: str | None = None, duration_minutes: int | None = None
    ) -> dict[str, Any]:
        """Start a purge timer for one or all rooms."""
        now = dt_util.utcnow()
        result = self.data
        targets = [room_id] if room_id else [room.id for room in self.config.rooms]
        started: dict[str, int] = {}

        for target in targets:
            if target is None:
                continue
            minutes = duration_minutes
            if minutes is None and result is not None:
                candidates = [
                    rec.duration_minutes for rec in result.for_room(target) if rec.duration_minutes
                ]
                minutes = min(candidates) if candidates else None
            if minutes is None and self.world is not None:
                room = self.world.room(target)
                if room is not None:
                    from .engine import thermal

                    minutes = thermal.purge_duration(
                        room,
                        self.world.outdoor.temperature,
                        windows=self.world.windows_of(target),
                    )
            minutes = minutes or 10
            self.purge_active[target] = now + timedelta(minutes=minutes)
            started[target] = minutes

        await self.async_persist()
        await self.async_request_refresh()
        return {"started": started}

    async def async_snooze(self, recommendation_id: str | None, scope: str = "1h") -> None:
        now = dt_util.utcnow()
        ids: list[str] = []
        if recommendation_id:
            ids = [recommendation_id]
        elif self.data is not None:
            ids = [rec.id for rec in self.data.recommendations]

        for rec_id in ids:
            if scope == "today":
                # UTC, not local: everything that reads this back - the arbiter,
                # the notification manager, EngineMemory.prune - compares
                # against world.now, which is UTC. Storing the local date made
                # "ignore today" a no-op whenever the two dates disagree (00:00
                # to 02:00 local in central European summer), because prune()
                # dropped the entry again on the very next evaluation.
                self.memory.ignore_today(rec_id, now)
            elif scope == "until_evening":
                evening = dt_util.as_local(now).replace(hour=18, minute=0, second=0, microsecond=0)
                if evening <= dt_util.as_local(now):
                    evening += timedelta(days=1)
                self.memory.snooze(rec_id, dt_util.as_utc(evening))
            else:
                self.memory.snooze(rec_id, now + timedelta(hours=1))
        await self.async_persist()
        await self.async_request_refresh()

    async def async_acknowledge(self, recommendation_id: str) -> None:
        """Mark a recommendation as done: cooldown plus a learning signal."""
        now = dt_util.utcnow()
        cooldown = self.config.options.get("cooldown_minutes", 60)
        self.memory.cooldowns[recommendation_id] = now + timedelta(minutes=int(cooldown))
        if self.data is not None:
            for rec in self.data.recommendations:
                if rec.id != recommendation_id:
                    continue
                if rec.room_id and rec.action in (Action.PURGE, Action.CROSS_VENTILATE):
                    self.memory.note_purge(rec.room_id, now)
                tracked = self.memory.target(rec.target)
                tracked.contradictions = 0
                # The advice has been dealt with, so there is nothing left to
                # disagree with. Leaving the timestamp in place kept the
                # contradiction detection armed against a notification that is
                # no longer on screen.
                tracked.last_notified = None
        if self._notifier is not None:
            await self._notifier.async_clear(recommendation_id)
        await self.async_persist()
        await self.async_request_refresh()

    async def async_recalibrate(self) -> dict[str, Any]:
        if self._calibrator is None:
            return {"updated": {}}
        return await self._calibrator.async_run(force=True)

    async def async_override_parameter(
        self, room_id: str, parameter: str, value: float | None, reset: bool = False
    ) -> None:
        """Overwrite or reset a learned value."""
        from dataclasses import replace

        room = self.learned.room(room_id)
        overrides = set(room.overridden)
        if reset:
            updated = replace(room, **{parameter: None})
            overrides.discard(parameter)
        else:
            updated = replace(room, **{parameter: value})
            overrides.add(parameter)
        rooms = dict(self.learned.rooms)
        rooms[room_id] = replace(updated, overridden=frozenset(overrides))
        self.learned = replace(self.learned, rooms=rooms)
        await self.async_persist()
        await self.async_request_refresh()

    async def async_simulate(self, **kwargs: Any) -> dict[str, Any]:
        if self.world is None:
            raise ValueError("no world state available yet")
        return await self.hass.async_add_executor_job(
            lambda: simulate_plan(self.world, **kwargs)  # type: ignore[arg-type]
        )

    # ------------------------------------------------------------------
    # Convenience accessors for the entities and the panel
    # ------------------------------------------------------------------

    def room_config(self, room_id: str) -> RoomConfig | None:
        return self.config.room(room_id)

    def window_config(self, window_id: str) -> WindowConfig | None:
        return self.config.window(window_id)

    @property
    def device_name(self) -> str:
        return self.config_entry.title


def _forecast_at(forecast: tuple[Any, ...], moment: datetime) -> Any | None:
    """Forecast entry closest to ``moment``, used to fill in cloud cover."""
    best = None
    best_delta = timedelta(minutes=90)
    for entry in forecast:
        delta = abs(entry.time - moment)
        if delta <= best_delta:
            best, best_delta = entry, delta
    return best


def _recommendation_payload(rec: Recommendation) -> dict[str, Any]:
    payload = asdict(rec)
    payload["action"] = rec.action.value
    payload["priority"] = rec.priority.name
    payload["blocks"] = sorted(a.value for a in rec.blocks)
    for key in ("valid_from", "valid_until"):
        value = payload.get(key)
        if isinstance(value, datetime):
            payload[key] = value.isoformat()
    return payload


@callback
def _toggle_issue(
    hass: HomeAssistant,
    issue_id: str,
    translation_key: str,
    active: bool,
    *,
    severity: ir.IssueSeverity,
    placeholders: Mapping[str, str] | None = None,
) -> None:
    if active:
        ir.async_create_issue(
            hass,
            DOMAIN,
            issue_id,
            is_fixable=False,
            severity=severity,
            translation_key=translation_key,
            translation_placeholders=dict(placeholders) if placeholders else None,
        )
    else:
        ir.async_delete_issue(hass, DOMAIN, issue_id)
