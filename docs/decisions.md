# Design decisions

Section 20 of the specification asks seven questions before implementation starts. Nobody was
available to answer them, so they were answered with defaults that are safe when wrong and are
listed here so they can be argued with.

Everything below is a decision, not a fact. Each one says what would change if the answer were
different.

---

## The seven open questions

**1. Is the domain `adaptive_ventilation` free?**
Assumed yes, and used. It is not registered in `home-assistant/brands` yet — that is the one
outstanding item in `quality_scale.yaml`. Changing the domain later means a painful migration,
so if it turns out to be taken, do it before the first release, not after.

**2. What does the actual flat look like?**
Unknown, so nothing is hard-coded. The building type is asked for and used *only* as a starting
value for the thermal model; after roughly a week of history the self-calibration replaces it.
Rooms and windows are subentries, so any layout works. The defaults assume a renovated old
building in Central Europe, which is the least wrong guess for the stated use case.

**3. How many covers, external or internal?**
Per window, and it matters a great deal: an external shutter stops about 80 % of the solar gain,
an internal blind about 30 %. The flag is in the window subentry and feeds directly into the
urgency of every shading recommendation. Default: external, because that is the common case in
German housing.

**4. Companion app in use?**
Assumed yes. Actionable notifications ("Done" / "Snooze 1 h" / "Ignore today") are on by default
and can be switched off. Without the Companion app the notifications still arrive, just without
buttons — nothing breaks.

**5. How much recorder history?**
Unknown. The calibration therefore looks at seven days, requires at least three usable episodes
before a learned value replaces the seed, and degrades silently to the building defaults when
the recorder is disabled entirely. It never fails setup over missing history.

**6. Should the integration switch covers itself?**
**No, by default.** Two independent switches have to be on: the global
`switch.adaptive_ventilation_cover_automation`, and "may be moved automatically" on that specific
window. Everything else is a recommendation. This is the conservative reading of "kann optional
dort direkt eingreifen, wo der Nutzer es explizit erlaubt". Windows are never motorised.

**7. German or English for the recommendation texts?**
English is the primary language of every repository artefact — code, docs, entity names, as the
specification asks. The user-visible texts follow `hass.config.language`, with a complete German
translation of the UI (`translations/de.json`) and of the recommendation sentences
(`messages.py`). Anything else falls back to English.

---

## Deviations from the specification

These are places where the implementation deliberately differs. Each is a judgement call, not an
oversight.

### One file per rule *group*, not per rule

The specification asks for "je Regel eine Datei". There are 36 rules; thirty-six four-line files
make the catalogue harder to read, not easier. Rules are grouped by domain — `safety.py`,
`air_quality.py`, `moisture.py`, `summer.py`, `winter.py`, `covers.py`, `general.py` — and each
one is still an individually addressable, individually testable registry entry with its own id,
priority and season filter. The registry, not the filesystem, is the unit of granularity.

### The panel is hand written JavaScript, not Lit + TypeScript

The specification suggests Lit and a build step into `frontend/`. The panel is instead a single
ES module with no bundler, no `node_modules` and no build job in CI. Reasons: the panel is about
900 lines, the browser can load it as-is, and a custom integration that requires `npm run build`
before it can be installed from source is a worse experience for everyone. The 24 h timeline is
plain SVG, exactly as the specification recommends.

### The tuning preview is a *now* preview, not a 24 h replay

The specification wants "with these values you would have had 4 instead of 9 notifications
today". That needs a replay over recorder history. The panel instead re-runs the engine on the
*current* world state with the candidate settings and shows the difference immediately — which is
fast, honest about what it measures, and enough to understand what a slider does. The real replay
over history exists as `scripts/replay.py`, and the panel says so.

### Cross ventilation shortens the duration through the air-exchange model

Rather than dividing the table value by a fixed factor, cross ventilation raises the modelled air
exchange, and the duration follows from that. Same result for the documented case (four minutes
instead of twelve), but it also handles a large window, a small room, or wind on the windward
side, which a fixed factor cannot.

### Thermal constants are calibrated against the tables, not guessed

The first working version predicted a 10 K night flush and a 56 K "best window". The RC model was
re-derived so that a reference room reproduces both published tables from the specification: the
purge duration table (section 6) and the per-building-type cool-down rates (section 7).
`tests/test_engine_thermal.py` asserts both. The specification's own tables are the ground truth;
the model is fitted to them, not the other way round.

### The schedule carries a unit

In summer a schedule slot measures Kelvin of cooling. In winter it measures grams of water per
cubic metre that a purge would remove. Summing the winter numbers and printing them as Kelvin
produced windows like "16:00-06:00, 29 K". `VentilationSchedule.metric` now says which one it is,
and the winter window is capped at two hours because a winter purge is minutes long.

---

## Things that were tempting and were not done

- **Machine learning for the thermal model.** Explicitly out of scope, and rightly so: an
  exponential fit on twelve nights beats a black box you cannot explain to the user whose window
  it is telling to close.
- **Vetoing ventilation on humidity in summer.** Physically defensible, practically wrong. On a
  30 °C day, cooling is worth more than dryness to most people. Humidity lowers the urgency,
  weighted by the user's own slider, and the reason text says what the trade is.
- **Pushing the night flush at 23:00.** Quiet hours start at 22:00 and only SAFETY breaks them.
  The recommendation is visible in the panel and on the entity all night; it just does not
  vibrate. A system that wakes you to tell you to sleep cooler is a system you uninstall.
- **Estimating rooms silently.** Every estimate carries a confidence and a method, low-confidence
  rooms never push, and the fallback chain ends in `unknown` rather than in a plausible number.
