# Development

## Layout

```
custom_components/adaptive_ventilation/
  engine/            ← pure Python, no Home Assistant import anywhere
    psychrometrics.py  Magnus, absolute humidity, dew point, enthalpy, mould
    solar.py           NOAA sun position, per-window solar load, shading horizon
    thermal.py         RC model, purge duration, tipping points, simulation
    state.py           every dataclass; the contract between the two worlds
    context.py         everything derived once per run and shared by the rules
    rules/             36 advisor rules behind a registry
    arbiter.py         vetoes, conflicts, hysteresis, dwell time, quiet hours
    schedule.py        the 24 h plan
  models.py          ← the only module that speaks both languages
  coordinator.py     collects the world, runs the engine, publishes the result
  ...                entity platforms, notifications, calibration, panel
scripts/replay.py    recorder history in, recommendations out
tests/               engine tests, mapping tests, Home Assistant runtime tests
```

The one structural rule: **nothing under `engine/` may import Home Assistant.** CI enforces it by
running the engine tests in a job where Home Assistant is not installed at all, and by walking
the AST of every file in the package. This is what makes the whole decision logic testable in a
second and replayable offline.

## Running the tests

```bash
pip install -r requirements-dev.txt
pytest tests
```

### On Windows

`pytest-homeassistant-custom-component` needs `fcntl` and unix sockets, so it cannot run on
Windows. It registers itself automatically through its `pytest11` entry point and breaks the
whole session, so disable it:

```bash
pytest tests -p no:homeassistant
```

Everything except the thirteen tests under `tests/hass/` runs that way — the engine, the physics,
the property tests, the mapping layer, storage, presentation and the calibration maths. The
`tests/hass/` tests skip themselves on Windows and run in CI on Linux.

### Only the engine

```bash
pytest tests/test_engine_*.py
```

These need nothing but `pytest` and `pyyaml`.

## The quality gate

```bash
ruff check custom_components scripts tests
ruff format --check custom_components scripts tests
mypy --strict --follow-imports=silent custom_components/adaptive_ventilation/engine
pytest tests
python scripts/replay.py --scenarios
```

`--follow-imports=silent` is needed because mypy reaches Home Assistant through the parent
package; without it you get several hundred errors from code that is not yours.

The engine is checked under `--strict`. The Home Assistant layer is fully annotated but not
strict — large parts of the HA API are still untyped, and chasing that produces noise rather than
correctness.

## Adding a rule

1. Pick the module it belongs to under `engine/rules/`.
2. Write a function, decorate it:

```python
@rule("my_rule", Priority.COMFORT, seasons={Season.SUMMER}, description="One line")
def my_rule(ctx: EvaluationContext) -> Iterable[Recommendation]:
    """What it does and, more importantly, why."""
    for room in ctx.rooms:
        if not some_condition(room):
            continue
        yield make(
            ctx, "my_rule", window.id, Action.OPEN_WIDE, Priority.COMFORT,
            "my_rule",                      # translation / message key
            urgency=55,
            room_id=room.id,
            reason_data={"room": room.name, "number": 42},
        )
```

3. Add the sentence to `messages.py` in both `en` and `de`. A test fails if you forget — every
   `reason_key` used anywhere in the engine must have a template.
4. Add a scenario under `tests/fixtures/scenarios/` if the rule is worth a regression test. The
   YAML is declarative; no Python needed.

The rule does not have to worry about conflicts, hysteresis, quiet hours, snoozing, cooldown or
whether the user has already ignored it twice today. That is all the arbiter's job. Rules should
be naive and readable.

## Adding a scenario

```yaml
name: my_scenario
description: One or two sentences on what makes this situation interesting.
now: "2025-07-15T22:00:00+02:00"
outdoor: {temperature: 18, humidity: 65}
forecast:
  start: "2025-07-15T22:00:00+02:00"
  temperatures: [18, 17.5, 17, ...]
rooms:
  - {id: bedroom, name: Bedroom, temperature: 27, humidity: 50, priority: 1, bedroom: true}
windows:
  - {id: bedroom_east, name: Bedroom east, room: bedroom, azimuth: 95, area: 1.5}
expect:
  global_state: night_flush
  rules_present: [night_flush]
  rules_absent: [keep_closed_hot]
  actions:
    bedroom_east: [open_wide, cross_ventilate]
```

`python scripts/replay.py --scenarios` runs all of them and prints what the engine makes of each,
which is the fastest way to see the effect of a threshold change.

## Tuning against your own history

```bash
python scripts/replay.py --csv history.csv --map my_flat.yaml -v
```

The map file describes which entity is which; `docs/replay_example.yaml` is a starting point.
The forecast is taken from the recorded outdoor series itself — deliberately, because when tuning
thresholds you want to know whether the rules make the right call given correct information, not
how wrong the weather service was that week.

## The panel

`custom_components/adaptive_ventilation/frontend/adaptive-ventilation-panel.js` is loaded by the
browser as-is. There is no build step. To iterate on it without a Home Assistant instance, serve
the file next to a JSON payload and stub `hass.callWS` — the payload shape is exactly what
`presentation.panel_payload()` returns, so you can generate a realistic one straight from the
engine.

## Releasing

1. Bump `version` in `manifest.json` and `pyproject.toml`.
2. Make sure `quality_scale.yaml` still reflects reality.
3. Tag; HACS picks up tags.
