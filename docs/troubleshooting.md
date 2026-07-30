# Troubleshooting and limitations

## Common symptoms

| Symptom | Likely cause |
|---|---|
| Status stuck on **No usable data** | The outdoor sensors have not updated for 45 minutes. Check them; the weather entity is the fallback. |
| A repair issue about the outdoor sensor | It reads several Kelvin above the weather service in sunshine, so it is in the sun. Move it into the shade, or use the weather entity as the temperature source. |
| **No notifications at all** | Quiet hours (22:00–07:00 by default) let only SAFETY through. Estimated rooms never push. Check `switch.adaptive_ventilation_notifications` and that at least one `notify.*` target is configured. |
| Notifications stop mid-afternoon | The daily push budget (6 by default) is spent. Comfort advice cannot use the last third, so health and safety still get through. |
| **No tipping points, no 24 h plan** | The weather entity has no hourly forecast. A repair issue says so. Pick a weather integration that supports hourly forecasts. |
| Advice feels twitchy | Raise the ΔT hysteresis or the minimum dwell time under Tuning. The dead band is three times the hysteresis. |
| A room shows a confidence badge | It has no sensor and is being estimated. That is by design, and those rooms never push. |
| The sidebar panel is blank | The integration has not completed its first evaluation yet, or no config entry is loaded. Reload the page after a few seconds. |
| Shutters do not move | Two switches have to be on: the global `switch.adaptive_ventilation_cover_automation` **and** "may be moved automatically" on that specific window. |
| Learned values stay empty | The calibration needs at least three usable episodes per parameter and a working recorder. On a fresh install everything runs on the building-type defaults. |

## Reporting a bug

Attach the output of `adaptive_ventilation.export_diagnostics`, or the diagnostics download from
the integration page. It contains the whole world state, the engine output and every intermediate
value, with entity ids and coordinates redacted.

```yaml
action: adaptive_ventilation.export_diagnostics
```

## Known limitations

- **Only as good as the forecast.** Without an hourly forecast there are no tipping points, no
  24 h plan and no pre-cooling.
- **Rooms without sensors are estimated**, and those estimates never trigger a push notification,
  only a panel entry. Deliberate: an estimate that wakes you up is worse than no estimate.
- **Self-calibration needs history.** Three usable episodes per parameter minimum, seven days of
  recorder data examined.
- **No window motors, no MVHR control, no heat pump control.** Heating is read-only plus a frost
  veto.
- **The weak-spot report models solar gain, not measured heat flow.** Treat the Kelvin figures as
  ranking information, not as invoices.
- **The tuning preview is a "now" preview**, not a replay over yesterday. For that use
  `scripts/replay.py`; see [decisions.md](decisions.md).
