# Configuration reference

Everything is configured through the UI. Nothing goes into `configuration.yaml`.

The initial dialog asks four things; everything else is optional and can be added later.
The principle throughout: **a missing input degrades one feature, it never breaks setup.**

---

## Global settings

Settings → Devices & Services → Adaptive Ventilation → **Configure**. Five groups.

### Data sources

| Setting | Effect when missing |
|---|---|
| Weather entity (hourly forecast) | No tipping points, no 24 h plan, no pre-cooling. A repair issue says so. |
| Outdoor temperature sensor | Falls back to the weather entity. Your own sensor is better — unless it is in the sun, which the integration detects and reports. |
| Outdoor humidity sensor | No absolute humidity comparison outdoors, so the drying rules go quiet. |
| Outdoor PM2.5 / PM10 | The particulate rules never fire. |
| Weather warnings (DWD / Meteoalarm) | Storm detection falls back to the wind speed threshold alone. |
| Illuminance sensor | Darkness is derived from the sun elevation instead. |
| Sun entity | Defaults to `sun.sun`; if that is gone the position is computed internally. |
| Presence | Assumed present. The away rules never fire. |

### Building

Only a **starting value** for the thermal model. After roughly a week of history the
self-calibration replaces τ and the cool-down rate with measured ones.

| Building type | τ (h) | Cool-down at ΔT 10 K, window open |
|---|---|---|
| Old, solid, uninsulated | 60 | ~1.0 K/h |
| Old, renovated | 80 | ~0.95 K/h |
| Modern, insulated, solid | 110 | ~0.85 K/h |
| Lightweight / attic | 20 | ~2.4 K/h |
| Half-timbered | 45 | ~1.1 K/h |

`f_Rsi` (thermal bridge factor) drives the mould calculation: 0.30-0.40 uninsulated old building,
0.20 renovated, 0.10-0.13 modern insulated. Override it if you know yours.

### Notifications

| Setting | Default | Notes |
|---|---|---|
| Notification targets | none | Multiple `notify.*` services. Without one, nothing is pushed — the entities still work. |
| Actionable notifications | on | "Done" / "Snooze 1 h" / "Ignore today". Needs the Companion app. |
| Quiet hours | 22:00-07:00 | Only SAFETY breaks them. |
| Max pushes per day | 6 | Comfort and optimisation cannot spend the last third; health can, safety ignores the budget. |
| Restraint | 50 | At 75 and above, only health and safety push at all. |
| Minimum confidence | 0.7 | Estimated rooms sit below this and are panel-only. |
| Move shutters automatically | off | Also needs the per-window flag. |

### Priorities

The four axes, 0-100, 50 is neutral. They are ratios, not absolutes: what matters is
temperature *against* humidity, not their individual values.

Turning humidity up makes the engine more reluctant to buy cooling with moisture on a muggy
evening. Turning CO₂ up makes purge recommendations outrank comfort more often.

### Thresholds and calmness

| Setting | Default | What it does |
|---|---|---|
| Summer target band | 22-26 °C | Above the upper bound, cooling rules engage. |
| Winter target band | 19-23 °C | Below the lower bound, passive solar gain engages. |
| Humidity band | 40-60 % | Feeds the air quality score. |
| CO₂ threshold | 1000 ppm | Urgent at 1400. |
| PM2.5 indoors / outdoors | 25 µg/m³ | |
| Storm threshold | 60 km/h | |
| Tropical night above | 20 °C | Switches the expectation management on. |
| ΔT hysteresis | 0.5 K | The dead band is **three times** this: enter at 0.5 K, leave at 1.0 K the other way. |
| Minimum dwell time | 15 min | No target changes state faster than this, except SAFETY. |
| Cooldown | 60 min | Per recommendation id. |
| Lead time | 30 min | How far ahead of the tipping point the "close soon" advice arrives. |

---

## Per room

| Field | Notes |
|---|---|
| Name | Also the device name. |
| Temperature / humidity sensor | Optional. Without them the room is estimated. |
| Priority | 1 is the most important. The bedroom at night should beat the rest of the flat. |
| CO₂ / VOC / PM2.5 sensor | Each enables its own rules. |
| Presence sensor | Used by `fan_instead`, which does not suggest a fan for an empty room. |
| Climate entity | Read only, plus the frost veto and the preheat hint. |
| Fan entity | Enables the `FAN_ON` recommendations. |
| Power meter | Above 1 kW the `internal_load` rule fires — "oven on, open the kitchen window now". |
| Volume, or area + height | Drives the purge duration and the thermal mass. Default 40 m³. |
| Target band | Overrides the global band for this room. |
| Bedroom | Gets an urgency bonus at night. |
| Moisture source | Bathroom, kitchen: enables the humidity spike rule. |
| Basement | Enables the summer condensation veto. |
| Unheated | Enables the condensation warning against warm neighbouring rooms. |
| Top floor | Faster thermal response, warmer estimate. |
| Connected rooms | **This is what makes cross ventilation work.** List the rooms whose doors are normally open. |
| Estimation method | For rooms without a sensor: reference room + offset (confidence 0.6) or model based (0.5). |

### Rooms without a sensor

The fallback chain, in order: own sensor (confidence 1.0) → reference room plus offset (0.6) →
learned offset (0.75) → model (0.5) → flat average (0.4) → `unknown`.

It never silently guesses: the confidence and the method are attributes on every estimated
entity, and anything below the confidence threshold is shown but never pushed. Safety rules
still apply — storm, rain, absence and darkness need no temperature at all.

---

## Per window

| Field | Notes |
|---|---|
| Name, room | |
| Contact sensor | Required. |
| Orientation | Compass point, or "custom azimuth" plus the exact degrees under Advanced. |
| Azimuth | 0 = north, 90 = east, 180 = south, 270 = west. Drives the whole solar calculation. |
| Area | Drives the air exchange and the solar load. Default 1.5 m². |
| Tilt sensor | Separate sensor if you have one; otherwise a `tilt` state on the contact sensor is understood. |
| Can be tilted | Whether tilt is offered at all. |
| g-value | Glazing transmission. 0.6 is typical double glazing, 0.5 triple. |
| Shutter / blind | Enables the shading rules. |
| **External shutter** | Stops ~80 % of the gain; an internal blind only ~30 %. This flag changes how urgent shading is. |
| May be moved automatically | Off by default. Also needs the global switch. |
| Ground floor | Security rules become stricter. |
| May stay open while away | |
| May stay open in the rain | |

---

## Modes

| Mode | Behaviour |
|---|---|
| `auto` | Season derived from the forecast and whether the heating runs, not from the calendar. |
| `summer` / `winter` | Forces the season. |
| `away` | Security rules only; nothing is recommended that needs someone at home. |
| `off` | No recommendations at all. |

`manual_hold` is not selectable — the engine enters it per target when you ignore the same advice
twice in one day, and drops it at midnight.
