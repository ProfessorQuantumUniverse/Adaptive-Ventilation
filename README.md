# Adaptive Ventilation

**Adaptive Ventilation for Home Assistant** — tells you when to open which window, and when to
close it again.

[![CI](https://github.com/ProfessorQuantumUniverse/Adaptive-Ventilation/actions/workflows/ci.yaml/badge.svg)](https://github.com/ProfessorQuantumUniverse/Adaptive-Ventilation/actions/workflows/ci.yaml)
[![hacs](https://img.shields.io/badge/HACS-custom-41BDF5.svg)](https://hacs.xyz)

---

## The problem

A flat without air conditioning in a Central European summer is a thermal battery you are
charging by accident. You can win maybe 3-5 K a night — if you open the right windows at the
right time, and, more importantly, if you **close them before you lose it again**. The morning
crossover is the one you sleep through.

Winter is the mirror image: too little ventilation grows mould in the corner behind the
wardrobe, too much dries the air to 25 % RH and heats the street.

Adaptive Ventilation watches four axes and tells you what to do about them:

| Axis | What it decides |
|---|---|
| **Temperature** | Is it actually cooler outside — in enthalpy, not just on the thermometer? |
| **Humidity** | Does opening the window *dry* the room or wet it? Absolute humidity, never RH. |
| **CO₂** | Purge duration computed from ΔT, not guessed. |
| **Particulates** | Is the air outside worse than the air inside right now? |

Each axis can *demand* ventilation or *forbid* it. You set the weights; an arbiter resolves the
conflict and produces at most one recommendation per window.

---

## What it actually gives you

- **`sensor.adaptive_ventilation_tipping_point_morning`** — the time outdoor temperature overtakes
  indoor. In summer this is the number you live by, and no standard dashboard has it.
- **A 24 h ventilation plan** — "best window tonight: 23:00-05:30, expected −3.2 K".
- **Predictive shading** — the blind goes down *before* the sun reaches the window, not once the
  room is already warm.
- **Cross-ventilation detection** — two open windows on opposite facades in connected rooms give
  three to five times the air exchange, so four minutes replace twelve. It says so.
- **A cooling budget** — Kelvin obtainable tonight versus Kelvin needed. When the answer is "not
  enough", it says that instead of pretending.
- **A reason for every recommendation**, with the numbers that produced it.

Nothing is switched without permission. Covers move only where you tick "may be moved
automatically"; windows are never motorised in v1.

---

## The physics, briefly

Whether ventilation dries a room is decided by **absolute humidity**, never by the percentage:

```
e_s(T) = 6.112 · exp(17.62 · T / (243.12 + T))       saturation vapour pressure [hPa]
AH     = 216.7 · (RH/100 · e_s(T)) / (T + 273.15)    absolute humidity [g/m³]
```

- −5 °C at 90 % RH → **2.6 g/m³**. Ventilating *dries* the flat.
- 21 °C at 88 % RH → **16.1 g/m³**. Cooler on the thermometer, four grams wetter per cubic metre.

For "is it really cooler out there" on a muggy evening the honest comparison is **enthalpy**,
because humid air carries latent heat in with it. Summer does not veto on humidity, though — it
weighs it, and you decide how much cooling a gram of water is worth.

Mould falls out of the same maths for free: the surface temperature of the coldest wall is
`T_in − f_Rsi · (T_in − T_out)`, and above 80 % RH at that surface, mould grows (DIN 4108-2).

The thermal model is one RC node per room. Its constants are **calibrated against published
tables**, not invented — the test suite checks that a reference room reproduces both the purge
duration table and the per-building-type cool-down rates. After a week of history the values are
replaced by measurements from your own flat.

---

## Installation

### HACS (recommended)

1. HACS → Integrations → ⋮ → Custom repositories
2. Add `https://github.com/ProfessorQuantumUniverse/Adaptive-Ventilation`, category *Integration*
3. Install, restart Home Assistant
4. Settings → Devices & Services → Add Integration → **Adaptive Ventilation**

### Manual

Copy `custom_components/adaptive_ventilation` into your `config/custom_components/` and restart.

### Removal

Delete the config entry (Settings → Devices & Services → ⋮ → Delete). Everything it created —
entities, devices, the sidebar panel, the stored calibration — goes with it. Then remove the
folder or uninstall through HACS.

---

## Configuration in two minutes

The initial dialog asks for four things, three of them optional:

- a **weather entity with an hourly forecast** (without it there are no tipping points and no plan)
- an outdoor temperature and humidity sensor, if you have them (your own beats the weather
  service — as long as it is not in the sun; the integration will tell you if it suspects it is)
- the **building type**, purely as a starting value for the thermal model

Then add rooms and windows as **subentries** on the integration page:

- **Room**: name, temperature sensor, humidity sensor, priority. Everything else is under
  *Advanced*. A room with no sensors still works — it gets estimated, carries a confidence value
  and is excluded from push notifications.
- **Window**: name, room, contact sensor, orientation. The orientation matters: the solar load is
  computed from the azimuth. A compass point is enough to start with.

Everything else has a sensible default. Full reference: [docs/configuration.md](docs/configuration.md).

---

## Example scenarios

| Situation | What it does |
|---|---|
| 22:00, 18 °C out, 27 °C in | "Bedroom: open — about 5 K cooler by morning." Pushes it. |
| 06:40 next morning | "Close in 30 minutes — peak 33 °C today." Before, not after. |
| 21:00, 21 °C / 88 % out, 25 °C / 50 % in | Still recommends opening, but at visibly lower urgency — you are buying cooling with humidity. |
| Third tropical night in a row | "About 4 K obtainable, 5.5 K needed. Night flushing will not carry this." |
| Storm warning, windows open | Everything closes. Pushes even inside quiet hours. Nothing else gets a say. |
| −5 °C, CO₂ climbing | "Cross ventilate with the living room — 4 minutes is enough that way." |
| 180 µg/m³ outside on New Year's night | Ventilation vetoed, and it explains why the CO₂ advice is being overruled. |
| Cellar, 28 °C / 70 % outside | Vetoed. That is condensation, not airing out. |

All fifteen are executable: [`tests/fixtures/scenarios/`](tests/fixtures/scenarios/).

---

## Dashboard

The integration registers its own **sidebar panel** with four tabs: Now (24 h SVG timeline),
Rooms, Tuning (sliders with a live preview) and Balance (cooling budget, weak-spot report,
learned values).

If you want it on your normal dashboard from day one, there is a copy-and-paste Markdown card and
a Mushroom variant in [docs/dashboard.md](docs/dashboard.md).

## External displays

`text.adaptive_ventilation_display_line1/2/3` are made for a small OLED or e-paper panel. Use
ESPHome's native API rather than REST polling — a complete, working configuration is in
[docs/esphome_example.yaml](docs/esphome_example.yaml). For anything that cannot speak the native
API, line 1 carries a compact JSON blob in its `compact` attribute.

---

## Services

| Service | Purpose |
|---|---|
| `adaptive_ventilation.start_purge` | Start a purge timer with the calculated duration |
| `adaptive_ventilation.snooze` | Mute one recommendation, or all, for a while |
| `adaptive_ventilation.acknowledge` | "Done" — cooldown plus a learning signal |
| `adaptive_ventilation.set_mode` | AUTO / SUMMER / WINTER / AWAY / OFF |
| `adaptive_ventilation.recalibrate` | Re-run the self-calibration now |
| `adaptive_ventilation.override_parameter` | Overwrite or reset a learned value |
| `adaptive_ventilation.export_diagnostics` | Full internal state as a service response |
| `adaptive_ventilation.simulate` | What-if: window X open from t1 to t2 |

Events for your own automations: `adaptive_ventilation_recommendation_added` / `_cleared` /
`_purge_finished` / `_calibration_updated`.

---

## Tuning the thresholds

Defaults are a starting point, not a truth. Two ways to improve them:

- **Panel → Tuning**: move a slider, see immediately what the current situation would look like
  with the new value, then apply.
- **`scripts/replay.py`**: feed it a CSV of your recorder history and it prints what the
  integration *would have* recommended over those days, how many notifications it would have
  sent and which rules fired. Change a threshold, run it again, compare.

```bash
python scripts/replay.py --csv history.csv --map docs/replay_example.yaml
```

---

## Known limitations

- Only as good as the hourly forecast it is given. Without one there are no tipping points, no
  24 h plan and no pre-cooling — the integration says so via a repair issue.
- Rooms without sensors are estimated. Those estimates never trigger a push notification, only a
  panel entry. That is deliberate.
- Self-calibration needs history: at least three usable episodes per parameter before a learned
  value replaces the seed. On a fresh install everything runs on the building-type defaults.
- No window motors, no MVHR control, no heat pump control. Heating is read-only plus a frost veto.
- The weak-spot report models solar gain, not measured heat flow. Treat the Kelvin figures as
  ranking information, not as invoices.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| Status stuck on *No usable data* | Outdoor sensors have not updated for 45 minutes. Check them; the weather entity is the fallback. |
| A repair issue about the outdoor sensor | It reads several Kelvin above the weather service in sunshine — it is in the sun. Move it or use the weather entity. |
| No notifications at all | Quiet hours (22:00-07:00 by default) let only SAFETY through. Estimated rooms never push. Check `switch.adaptive_ventilation_notifications`. |
| No tipping points | The weather entity has no hourly forecast. |
| Advice feels twitchy | Raise the ΔT hysteresis or the minimum dwell time under Tuning. The dead band is three times the hysteresis. |

Attach the output of `adaptive_ventilation.export_diagnostics` (or the diagnostics download) to
any bug report — it contains the whole world state with entity ids and coordinates redacted.

---

## Development

```bash
pip install -r requirements-dev.txt
pytest tests                # engine + mapping layer + Home Assistant runtime tests
python scripts/replay.py --scenarios
```

The `engine/` package contains **no Home Assistant import at all** — CI enforces that by running
its tests in a job where Home Assistant is not even installed. See
[docs/development.md](docs/development.md) and [docs/decisions.md](docs/decisions.md).

## Licence

GPL-3.0 — see [LICENSE](LICENSE).
