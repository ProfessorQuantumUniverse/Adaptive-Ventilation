# How it works

The long version. If you just want it installed, the [README](../README.md) is enough.

---

## The four axes

Adaptive Ventilation watches four things, and each of them can *demand* ventilation or *forbid*
it. You set how much each one matters; an arbiter resolves the conflict and produces at most one
recommendation per window.

| Axis | What it decides |
|---|---|
| **Temperature** | Is it actually cooler outside — in enthalpy, not just on the thermometer? |
| **Humidity** | Does opening the window *dry* the room or wet it? Absolute humidity, never RH. |
| **CO₂** | Purge duration computed from ΔT, not guessed. |
| **Particulates** | Is the air outside worse than the air inside right now? |

That balancing act is the whole product. A rule that only knows about temperature will tell you
to open the window on a muggy evening; a rule that only knows about humidity will tell you to
keep it shut during a heatwave. Both are wrong.

---

## The physics

### Absolute humidity, not relative

Whether ventilation dries a room is never decided by the percentage:

```
e_s(T) = 6.112 · exp(17.62 · T / (243.12 + T))       saturation vapour pressure [hPa]
e      = RH/100 · e_s(T)                             actual vapour pressure     [hPa]
AH     = 216.7 · e / (T + 273.15)                    absolute humidity          [g/m³]
```

- **−5 °C at 90 % RH → 2.6 g/m³.** Bone dry. Ventilating *dries* the flat.
- **21 °C at 88 % RH → 16.1 g/m³.** Cooler on the thermometer, four grams wetter per m³ than a
  21 °C room at 45 %.

### Enthalpy for "is it really cooler"

Humid air carries latent heat in with it, so on a muggy evening the honest comparison is
specific enthalpy:

```
x = 0.622 · e / (p − e)                              humidity ratio  [kg/kg]
h = 1.006 · T + x · (2501 + 1.86 · T)                enthalpy        [kJ/kg]
```

Summer does **not** veto on humidity, though. It weighs it: you decide how much cooling a gram of
water is worth, and the recommendation text says what the trade was.

### Mould, for free

The same numbers give you the surface temperature of the coldest wall and the humidity there:

```
T_surface = T_in − f_Rsi · (T_in − T_out)
RH_surface = e_in / e_s(T_surface) · 100
  > 80 %  mould risk (DIN 4108-2)
  > 95 %  condensation
```

`f_Rsi` comes from the building type: 0.30–0.40 uninsulated old building, 0.20 renovated,
0.10–0.13 modern insulated.

### The thermal model

One RC node per room:

```
dT_in/dt = ( (T_out − T_in)/R + Q_solar + Q_internal
             + n_ach · V · ρ · c_p · (T_out − T_in)/3600 ) / C
τ = R · C
```

Its constants are **fitted to published tables, not invented.** The test suite asserts that a
reference room reproduces both the purge duration table and the per-building-type cool-down
rates:

| Outdoor | Purge, wide open | | Building | τ (h) | Cool-down at ΔT 10 K |
|---|---|---|---|---|---|
| < −10 °C | 3–4 min | | Old, solid, uninsulated | 60 | ~1.0 K/h |
| −10…0 °C | 4–6 min | | Old, renovated | 80 | ~0.95 K/h |
| 0…10 °C | 8–12 min | | Modern, insulated | 110 | ~0.85 K/h |
| 10…18 °C | 12–18 min | | Lightweight / attic | 20 | ~2.4 K/h |
| > 18 °C | 20–30 min | | Half-timbered | 45 | ~1.1 K/h |

After roughly a week of history the self-calibration replaces those seeds with values measured in
*your* flat: τ from an exponential fit on cool-down curves, the real air exchange from a CO₂
decay, the solar gain coefficient from a linear regression. No ML, no black box — every learned
value is visible in the panel with its sample count, and you can override it.

### Solar load per window

A NOAA solar-position implementation gives elevation and azimuth for any moment, including hours
into the future, which is what makes shading *predictive*:

```
cos θ = cos(elev) · cos(azi_sun − azi_window)
load  ≈ area · g · (G_direct · max(0, cos θ) · cloud_factor + G_diffuse · sky_view)
```

The asymmetry that drives everything: an **external** shutter stops ~80 % of the gain, an
**internal** blind only ~30 %. By the time an internal blind is involved, the glass has already
let the energy in.

---

## What it produces

- **Tipping points.** `sensor.adaptive_ventilation_tipping_point_morning` is the time outdoor
  temperature overtakes indoor. In summer this is the number you live by, and no standard
  dashboard has it. You get warned *before* it, not after.
- **A 24 h plan.** "Best window tonight: 23:00–05:30, expected −3.2 K", plus the timeline the
  panel draws.
- **Cross-ventilation detection.** Two open windows on opposite facades in connected rooms give
  three to five times the air exchange, so four minutes replace twelve. It says so by name.
- **A cooling budget.** Kelvin obtainable tonight versus Kelvin needed. When the answer is "not
  enough", it says that instead of pretending.
- **A reason for every recommendation**, with the numbers that produced it.

Nothing is switched without permission. Covers move only where you tick "may be moved
automatically" *and* the global switch is on. Windows are never motorised.

---

## Example scenarios

| Situation | What it does |
|---|---|
| 22:00, 18 °C out, 27 °C in | "Bedroom: open — about 5 K cooler by morning." Pushes it. |
| 06:40 next morning | "Close in 30 minutes — peak 33 °C today." Before, not after. |
| 21:00, 21 °C / 88 % out, 25 °C / 50 % in | Still recommends opening, but at visibly lower urgency: you are buying cooling with humidity. |
| Third tropical night in a row | "About 4 K obtainable, 5.5 K needed. Night flushing will not carry this." |
| Storm warning, windows open | Everything closes. Pushes even inside quiet hours. Nothing else gets a say. |
| −5 °C, CO₂ climbing | "Cross ventilate with the living room — 4 minutes is enough that way." |
| 180 µg/m³ outside on New Year's night | Ventilation vetoed, and it explains why the CO₂ advice is overruled. |
| Cellar, 28 °C / 70 % outside | Vetoed. That is condensation, not airing out. |

All fifteen mandatory scenarios are executable files under
[`tests/fixtures/scenarios/`](../tests/fixtures/scenarios/). Run them with:

```bash
python scripts/replay.py --scenarios
```

---

## Staying quiet

A system that buzzes constantly gets muted, so calmness is a feature, not an afterthought:

- **Hysteresis** on every threshold. The dead band is three times the configured value: enter at
  0.5 K, leave at 1.0 K in the other direction.
- **Minimum dwell time** (15 min) — no target changes state faster than that, except SAFETY.
- **Cooldown** per recommendation, **dedupe** by id, notifications **withdrawn** when they become
  obsolete rather than left on your lock screen.
- **Quiet hours** (22:00–07:00). Only SAFETY breaks them.
- **A daily push budget** where comfort cannot eat the share health needs later.
- **Manual hold**: ignore the same advice twice in a day and it gives in for that day.

---

## Modes

| Mode | Behaviour |
|---|---|
| `auto` | Season derived from the forecast and whether the heating runs, not from the calendar. |
| `summer` / `winter` | Forces the season. |
| `away` | Security rules only. |
| `off` | No recommendations at all. |

---

## Services and events

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
`_purge_finished` / `_calibration_updated`. Examples in
[dashboard.md](dashboard.md#your-own-automations).

---

## Tuning the thresholds

Defaults are a starting point, not a truth.

- **Panel → Tuning**: move a slider, see immediately what the current situation would look like
  with the new value, then apply.
- **`scripts/replay.py`**: feed it a CSV of your recorder history and it prints what the
  integration *would have* recommended over those days, how many notifications it would have
  sent, and which rules fired. Change a threshold, run it again, compare.

```bash
python scripts/replay.py --csv history.csv --map docs/replay_example.yaml
```

Details in [development.md](development.md#tuning-against-your-own-history).
