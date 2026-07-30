#!/usr/bin/env python3
r"""Replay recorder history through the engine.

This is the tool for tuning thresholds: it takes a CSV export of your Home
Assistant history and prints what Adaptive Ventilation *would have*
recommended, how often it would have buzzed your phone, and which rules fired.
Change a threshold, run it again, compare.

It never imports Home Assistant - the engine is a plain Python package, which
is the whole point of keeping it separate.

Usage
-----
Run every declarative scenario (quick sanity check while tuning)::

    python scripts/replay.py --scenarios

Replay a CSV export against your own flat::

    python scripts/replay.py --csv history.csv --map docs/replay_example.yaml

Getting the CSV out of Home Assistant: Developer tools -> Statistics/History
-> download, or::

    sqlite3 -header -csv home-assistant_v2.db \\
      "SELECT sm.entity_id, s.state,
              datetime(s.last_updated_ts,'unixepoch') AS last_changed
       FROM states s JOIN states_meta sm ON s.metadata_id = sm.metadata_id
       WHERE s.state NOT IN ('unknown','unavailable')
       ORDER BY s.last_updated_ts;" > history.csv
"""

from __future__ import annotations

import argparse
import bisect
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
import csv
from datetime import UTC, datetime, timedelta
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "custom_components"))

# Windows consoles still default to cp1252, and this script prints box drawing
# characters and degree signs. Reconfigure rather than dumb the output down.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from adaptive_ventilation.engine import evaluate  # noqa: E402
from adaptive_ventilation.engine.solar import sun_position  # noqa: E402
from adaptive_ventilation.engine.state import (  # noqa: E402
    Action,
    BuildingProfile,
    BuildingType,
    EngineMemory,
    EvaluationResult,
    ForecastHour,
    OutdoorState,
    Preferences,
    RoomState,
    SunState,
    WindowState,
    WorldState,
)

# --------------------------------------------------------------------------
# History
# --------------------------------------------------------------------------


class Series:
    """A sorted time series with last-known-value lookup."""

    def __init__(self) -> None:
        self.times: list[datetime] = []
        self.values: list[Any] = []

    def add(self, moment: datetime, value: Any) -> None:
        self.times.append(moment)
        self.values.append(value)

    def finalise(self) -> None:
        paired = sorted(zip(self.times, self.values, strict=True), key=lambda item: item[0])
        self.times = [item[0] for item in paired]
        self.values = [item[1] for item in paired]

    def at(self, moment: datetime) -> Any:
        if not self.times:
            return None
        index = bisect.bisect_right(self.times, moment) - 1
        return self.values[index] if index >= 0 else None

    @property
    def span(self) -> tuple[datetime, datetime] | None:
        if not self.times:
            return None
        return self.times[0], self.times[-1]


def load_history(path: Path) -> dict[str, Series]:
    """Read a CSV with ``entity_id``, ``state`` and a timestamp column."""
    series: dict[str, Series] = defaultdict(Series)
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise SystemExit(f"{path} has no header row")
        time_column = next(
            (
                name
                for name in reader.fieldnames
                if name.lower() in ("last_changed", "last_updated", "time", "timestamp")
            ),
            None,
        )
        if time_column is None or "entity_id" not in reader.fieldnames:
            raise SystemExit(
                f"{path} needs at least entity_id, state and a timestamp column "
                f"(found {reader.fieldnames})"
            )

        for row in reader:
            moment = _parse_time(row[time_column])
            if moment is None:
                continue
            series[row["entity_id"]].add(moment, _parse_value(row.get("state")))

    for entry in series.values():
        entry.finalise()
    return dict(series)


def _parse_time(raw: str | None) -> datetime | None:
    if not raw:
        return None
    text = raw.strip().replace("Z", "+00:00")
    for candidate in (text, text.replace(" ", "T")):
        try:
            parsed = datetime.fromisoformat(candidate)
        except ValueError:
            continue
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return None


def _parse_value(raw: str | None) -> Any:
    if raw is None:
        return None
    text = raw.strip()
    if text in ("on", "open", "true", "True"):
        return True
    if text in ("off", "closed", "false", "False"):
        return False
    try:
        return float(text)
    except ValueError:
        return text


# --------------------------------------------------------------------------
# Mapping file
# --------------------------------------------------------------------------


def load_map(path: Path) -> dict[str, Any]:
    import yaml

    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def build_world(
    mapping: dict[str, Any],
    history: dict[str, Series],
    moment: datetime,
    forecast: Sequence[ForecastHour],
) -> WorldState:
    """Assemble a WorldState for one point in time from the history."""
    building_raw = mapping.get("building", {})
    building = BuildingProfile(
        building_type=BuildingType(building_raw.get("type", "old_renovated")),
        floor=int(building_raw.get("floor", 1)),
        top_floor=bool(building_raw.get("top_floor", False)),
        latitude=float(building_raw.get("latitude", 50.11)),
        longitude=float(building_raw.get("longitude", 8.68)),
    )

    outdoor_map = mapping.get("outdoor", {})
    temperature = _number(history, outdoor_map.get("temperature"), moment)
    if temperature is None:
        temperature = 15.0
    outdoor = OutdoorState.create(
        temperature=temperature,
        humidity=_number(history, outdoor_map.get("humidity"), moment),
        pm25=_number(history, outdoor_map.get("pm25"), moment),
        wind_speed=_number(history, outdoor_map.get("wind_speed"), moment),
        wind_bearing=_number(history, outdoor_map.get("wind_bearing"), moment),
        pressure=_number(history, outdoor_map.get("pressure"), moment),
        source="sensor",
    )

    rooms = tuple(
        RoomState.create(
            id=entry["id"],
            name=entry.get("name", entry["id"]),
            temperature=_number(history, entry.get("temperature"), moment),
            humidity=_number(history, entry.get("humidity"), moment),
            co2=_integer(history, entry.get("co2"), moment),
            pm25=_number(history, entry.get("pm25"), moment),
            volume_m3=float(entry.get("volume", 40.0)),
            priority=int(entry.get("priority", 5)),
            is_bedroom=bool(entry.get("bedroom", False)),
            is_basement=bool(entry.get("basement", False)),
            is_moisture_source=bool(entry.get("moisture_source", False)),
            connected_rooms=tuple(entry.get("connected", [])),
        )
        for entry in mapping.get("rooms", [])
    )

    windows = tuple(
        WindowState(
            id=entry["id"],
            name=entry.get("name", entry["id"]),
            room_id=entry["room"],
            is_open=bool(_raw(history, entry.get("contact"), moment)),
            azimuth=float(entry.get("azimuth", 180.0)),
            area_m2=float(entry.get("area", 1.5)),
            is_ground_floor=bool(entry.get("ground_floor", False)),
            rain_safe=bool(entry.get("rain_safe", False)),
            ok_when_away=bool(entry.get("ok_when_away", False)),
            cover_entity=entry.get("cover"),
            cover_external=bool(entry.get("cover_external", True)),
        )
        for entry in mapping.get("windows", [])
    )

    elevation, azimuth = sun_position(moment, building.latitude, building.longitude)

    return WorldState(
        now=moment,
        outdoor=outdoor,
        rooms=rooms,
        windows=windows,
        forecast=tuple(f for f in forecast if f.time >= moment - timedelta(hours=1)),
        sun=SunState(
            azimuth=azimuth,
            elevation=elevation,
            latitude=building.latitude,
            longitude=building.longitude,
        ),
        building=building,
        preferences=Preferences(**(mapping.get("preferences") or {})),
    )


def synth_forecast(
    mapping: dict[str, Any], history: dict[str, Series], times: Sequence[datetime]
) -> list[ForecastHour]:
    """Use the recorded outdoor series itself as a perfect forecast.

    That is deliberate: for threshold tuning you want to know whether the rules
    make the right call given correct information, not how bad the weather
    service was that week.
    """
    outdoor_map = mapping.get("outdoor", {})
    hourly: list[ForecastHour] = []
    seen: set[datetime] = set()
    for moment in times:
        hour = moment.replace(minute=0, second=0, microsecond=0)
        if hour in seen:
            continue
        seen.add(hour)
        temperature = _number(history, outdoor_map.get("temperature"), hour)
        if temperature is None:
            continue
        hourly.append(
            ForecastHour(
                time=hour,
                temperature=temperature,
                humidity=_number(history, outdoor_map.get("humidity"), hour),
                precipitation=_number(history, outdoor_map.get("precipitation"), hour),
                wind_speed=_number(history, outdoor_map.get("wind_speed"), hour),
            )
        )
    return hourly


def _raw(history: dict[str, Series], entity_id: str | None, moment: datetime) -> Any:
    if not entity_id or entity_id not in history:
        return None
    return history[entity_id].at(moment)


def _number(history: dict[str, Series], entity_id: str | None, moment: datetime) -> float | None:
    value = _raw(history, entity_id, moment)
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _integer(history: dict[str, Series], entity_id: str | None, moment: datetime) -> int | None:
    value = _number(history, entity_id, moment)
    return None if value is None else round(value)


# --------------------------------------------------------------------------
# Replay
# --------------------------------------------------------------------------


def replay(
    mapping: dict[str, Any],
    history: dict[str, Series],
    *,
    step_minutes: int,
    start: datetime | None,
    end: datetime | None,
    verbose: bool,
) -> int:
    spans = [entry.span for entry in history.values() if entry.span]
    if not spans:
        raise SystemExit("no usable rows in the history")

    first = start or min(span[0] for span in spans)
    last = end or max(span[1] for span in spans)
    times = _timeline(first, last, step_minutes)
    forecast = synth_forecast(mapping, history, times)

    memory = EngineMemory()
    pushes: Counter[str] = Counter()
    rules: Counter[str] = Counter()
    actions: Counter[str] = Counter()
    per_day: Counter[str] = Counter()
    transitions = 0
    previous_actions: dict[str, Action] = {}

    print(
        f"Replaying {first:%Y-%m-%d %H:%M} .. {last:%Y-%m-%d %H:%M} "
        f"({len(times)} steps of {step_minutes} min)\n"
    )

    for moment in times:
        world = build_world(mapping, history, moment, forecast)
        result = evaluate(world, memory)

        for rec in result.recommendations:
            if rec.action is Action.NO_ACTION:
                continue
            rules[rec.rule_id or "-"] += 1
            actions[rec.action.value] += 1

            # rec.notify only means "may be pushed". Whether it *would* have
            # been pushed also depends on the daily budget and the cooldown,
            # which the notification manager applies at delivery time - so the
            # replay applies exactly the same policy, or the headline number
            # here would be pure fiction.
            if rec.notify and _would_push(rec, memory, moment, world.preferences):
                memory.register_push(rec.id, rec.target, moment, world.preferences.cooldown_minutes)
                pushes[rec.rule_id or "-"] += 1
                per_day[moment.date().isoformat()] += 1
                if verbose:
                    _print_push(moment, rec)

            if previous_actions.get(rec.target) not in (None, rec.action):
                transitions += 1
            previous_actions[rec.target] = rec.action

    _print_summary(times, per_day, pushes, rules, actions, transitions)
    return 0


def _would_push(rec: Any, memory: EngineMemory, moment: datetime, preferences: Preferences) -> bool:
    """The notification manager's delivery policy, replayed."""
    cooldown = memory.cooldowns.get(rec.id)
    if cooldown is not None and cooldown > moment:
        return False
    return memory.may_push(rec.priority, moment, preferences.max_pushes_per_day)


def _timeline(first: datetime, last: datetime, step_minutes: int) -> list[datetime]:
    times: list[datetime] = []
    moment = first
    while moment <= last:
        times.append(moment)
        moment += timedelta(minutes=step_minutes)
    return times


def _print_push(moment: datetime, rec: Any) -> None:
    from adaptive_ventilation import messages

    text = messages.render(rec.reason_key, rec.reason_data)
    print(f"  {moment:%Y-%m-%d %H:%M}  {rec.priority.name:<12} {rec.action.value:<16} {text}")


def _print_summary(
    times: Sequence[datetime],
    per_day: Counter[str],
    pushes: Counter[str],
    rules: Counter[str],
    actions: Counter[str],
    transitions: int,
) -> None:
    days = max(1, len({moment.date() for moment in times}))
    total_pushes = sum(pushes.values())

    print("\n" + "=" * 66)
    print(f"{'Notifications':<28}{total_pushes:>8}  ({total_pushes / days:.1f} per day)")
    print(f"{'State changes per window':<28}{transitions:>8}")
    print("=" * 66)

    if per_day:
        print("\nNotifications per day")
        for day in sorted(per_day):
            print(f"  {day}  {'#' * min(per_day[day], 40)} {per_day[day]}")

    if rules:
        print("\nRule activity (fired / pushed)")
        for rule, count in rules.most_common():
            print(f"  {rule:<26} {count:>6} / {pushes.get(rule, 0):>4}")

    if actions:
        print("\nActions")
        for action, count in actions.most_common():
            print(f"  {action:<26} {count:>6}")


# --------------------------------------------------------------------------
# Scenario mode
# --------------------------------------------------------------------------


def run_scenarios(directory: Path, verbose: bool) -> int:
    """Run every declarative scenario and print what the engine makes of it."""
    sys.path.insert(0, str(ROOT))
    from tests.scenario import load_all

    scenarios = load_all(directory)
    if not scenarios:
        raise SystemExit(f"no scenarios found in {directory}")

    failures = 0
    for scenario in scenarios:
        result = evaluate(scenario.state, scenario.memory)
        print(f"\n── {scenario.name} " + "─" * max(0, 50 - len(scenario.name)))
        print(f"   state      {result.global_state.value}")
        print(f"   season     {result.diagnostics['season']}")
        _print_scenario_body(result, verbose)
        failures += _check_expectations(scenario, result)

    print("\n" + "=" * 66)
    print(f"{len(scenarios)} scenarios, {failures} expectation mismatches")
    return 1 if failures else 0


def _print_scenario_body(result: EvaluationResult, verbose: bool) -> None:
    from adaptive_ventilation import messages

    for rec in result.recommendations:
        if rec.action is Action.NO_ACTION and not verbose:
            continue
        flag = "PUSH" if rec.notify else "    "
        print(
            f"   {flag} {rec.target:<18} {rec.action.value:<16} "
            f"u{rec.urgency:<3} {messages.render(rec.reason_key, rec.reason_data)}"
        )
    schedule = result.schedule
    if schedule.best_start:
        print(
            f"   window     {schedule.best_start:%H:%M}-{schedule.best_end:%H:%M} "
            f"({schedule.best_delta_k} {'g/m3' if schedule.metric == 'grams' else 'K'})"
        )
    budget = result.cooling_budget
    if budget.verdict_key not in ("not_applicable", "unknown"):
        print(
            f"   budget     {budget.achievable_tonight_k} K obtainable / "
            f"{budget.required_tonight_k} K needed -> {budget.verdict_key}"
        )


def _check_expectations(scenario: Any, result: EvaluationResult) -> int:
    expect = scenario.expect
    triggered = {rec.rule_id for rec in result.recommendations}
    problems: list[str] = []

    if expect.global_state and result.global_state.value != expect.global_state:
        problems.append(f"state {result.global_state.value} != {expect.global_state}")
    for rule_id in expect.rules_present:
        if rule_id not in triggered:
            problems.append(f"missing {rule_id}")
    for rule_id in expect.rules_absent:
        if rule_id in triggered:
            problems.append(f"unexpected {rule_id}")

    for problem in problems:
        print(f"   FAIL {problem}")
    return len(problems)


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--csv", type=Path, help="recorder history export")
    parser.add_argument("--map", type=Path, help="YAML describing which entity is what")
    parser.add_argument("--step", type=int, default=15, help="minutes per step (default 15)")
    parser.add_argument("--from", dest="start", help="ISO timestamp to start at")
    parser.add_argument("--to", dest="end", help="ISO timestamp to stop at")
    parser.add_argument(
        "--scenarios",
        nargs="?",
        const=str(ROOT / "tests" / "fixtures" / "scenarios"),
        help="run the declarative scenarios instead of a CSV",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.scenarios:
        return run_scenarios(Path(args.scenarios), args.verbose)

    if not args.csv or not args.map:
        parser.error("either --scenarios or both --csv and --map are required")

    return replay(
        load_map(args.map),
        load_history(args.csv),
        step_minutes=args.step,
        start=_parse_time(args.start) if args.start else None,
        end=_parse_time(args.end) if args.end else None,
        verbose=args.verbose,
    )


if __name__ == "__main__":
    raise SystemExit(main())
