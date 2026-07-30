# Adaptive Ventilation for Home Assistant
---

## 1. Rolle & Auftrag

Du bist ein erfahrener Home-Assistant-Integrationsentwickler (Python 3.13, async, HA 2025.x)
mit gutem Gespür für Bauphysik und für UX von Empfehlungssystemen.

Baue eine benutzerdefinierte Home-Assistant-Integration namens **Adaptive Ventilation**
(Domain: `adaptive_ventilation`), die dem Nutzer sagt, **wann er welches Fenster öffnen oder schließen
soll** — und wann er welchen Rollladen bewegen soll —, um die Innenluft ganzjährig
optimal zu halten: Temperatur, Feuchte, CO₂ und Feinstaub.

Kernanspruch: Der Nutzer soll **nicht mehr selbst darüber nachdenken müssen**, wann
gelüftet wird. Die Integration ist primär ein **Empfehlungssystem** (Notifications,
Entitäten, Dashboard), kann aber optional dort direkt eingreifen, wo der Nutzer es
explizit erlaubt (Rollladen).

Konkreter Anlass: extreme Sommerhitze in Mitteleuropa, Wohnung **ohne Klimaanlage**.
Der Sommer-Pfad ist deshalb der wichtigste — der Winter-Pfad wird aber in gleicher
Tiefe mitgebaut (Stoßlüften, Schimmelprävention, passiver Solargewinn).

---

## 2. Designphilosophie (Leitprinzipien — bei Zweifeln hierauf zurückfallen)

1. **Physik statt Faustregeln.** Entscheidungen basieren auf absoluter Feuchte,
   Taupunkt, Enthalpie, Sonnenstand und thermischer Zeitkonstante — nicht auf
   relativer Feuchte oder starren Uhrzeiten.
2. **Vorausschauend, nicht reaktiv.** „Draußen ist es gerade wärmer → zu" kann jeder.
   Wert entsteht durch Forecast-Simulation: handeln **bevor** es weh tut.
3. **Erklärbar.** Jede Empfehlung trägt einen menschenlesbaren Grund und die Zahlen,
   die zu ihr geführt haben. Ohne Erklärbarkeit vertraut niemand dem System und
   schaltet es nach drei Tagen ab.
4. **Ruhig.** Hysterese, Mindest-Verweildauer, Cooldown, Dedupe, Ruhezeiten.
   Ein flatterndes System ist ein deinstalliertes System.
5. **Gestuft konfigurierbar.** Basis-Setup in 2 Minuten mit sinnvollen Defaults,
   alles andere optional. Bei diesem Feature-Umfang ist eine abgebrochene
   Konfiguration die realistischste Fehlerquelle.
6. **Ehrlich bei Unsicherheit.** Geschätzte Werte tragen einen Confidence-Wert.
   Bei niedriger Confidence: anzeigen, aber nicht pushen.

---

## 3. Nicht-Ziele (bewusst außen vor)

- Keine Klimaanlagen-/Wärmepumpensteuerung (Heizung nur lesend + Frostschutz-Veto).
- Kein Ersatz für eine KWL-Steuerung (Anbindung optional später).
- Keine Cloud, kein externer Dienst außer den vom Nutzer bereits eingerichteten
  HA-Wetter-/Luftqualitäts-Integrationen.
- Kein ML-Framework. Selbstkalibrierung = lineare Regression / exponentieller Fit,
  nichts Schwereres.
- Keine automatische Fenstersteuerung (Fensterantriebe) in v1.

---

## 4. Technische Rahmenbedingungen

- Python 3.13, vollständig async, **kein blockierendes I/O im Event Loop**.
- Ziel: HA 2025.x, `DataUpdateCoordinator`, Config Flow **mit Subentries**
  (`async_get_supported_subentry_types`), Options Flow für globale Einstellungen.
- Struktur nach HA-Integrationsstandard, Ziel: Quality Scale `silver` erreichbar
  (`quality_scale.yaml` mitführen), HACS-kompatibel (`hacs.json`).
- Vollständige Typannotationen, `ruff` + `mypy --strict` für `engine/` sauber.
- `strings.json` + `translations/en.json` + `translations/de.json`. Primärsprache
  der Repo-Artefakte: Englisch (Code, Doku, Entity-Namen). Deutsch als vollständige
  Übersetzung.
- Alle Zustände/Empfehlungen mit `translation_key` arbeiten, nie mit hartkodierten
  Anzeigetexten.
- Persistenz gelernter Werte über `Store` (Helpers) mit Versionierung + Migration.
- Diagnostics-Support (`diagnostics.py`) mit Redaction — für Bug-Reports Gold wert.
- Repair Issues (`homeassistant.helpers.issue_registry`) für Konfigurationsfehler,
  z. B. „Außensensor steht vermutlich in der Sonne".

### Repo-Struktur

```
custom_components/adaptive_ventilation/
  __init__.py            # Setup, Coordinator-Wiring
  manifest.json
  const.py
  config_flow.py         # ConfigFlow + SubentryFlows (Raum, Fenster)
  coordinator.py         # Sammelt WorldState, ruft Engine, hält Ergebnis
  models.py              # Mapping HA-Config <-> Engine-Dataclasses
  sensor.py binary_sensor.py select.py number.py switch.py button.py text.py
  notify_manager.py      # Dedupe, Ruhezeiten, Actionable Notifications
  calibration.py         # Recorder-Auswertung, gelernte Parameter
  panel.py               # Sidebar-Panel-Registrierung
  diagnostics.py
  services.yaml
  strings.json
  translations/
  frontend/              # gebautes Panel-JS (dist)
  engine/                # 
    __init__.py
    state.py             # WorldState, RoomState, WindowState, Forecast...
    psychrometrics.py    # Magnus, abs. Feuchte, Taupunkt, Enthalpie
    solar.py             # Einfallswinkel, Solarlast pro Fenster
    thermal.py           # RC-Modell, τ, Simulation, Kipppunkte
    rules/               # je Regel eine Datei, Registry-Pattern
    arbiter.py           # Konfliktauflösung, Priorisierung
    schedule.py          # Lüftungsfahrplan über 24 h
frontend_src/            # Panel-Quellcode (Lit/TS), Build nach frontend/
tests/
  fixtures/scenarios/    # YAML-Szenarien (siehe Abschnitt 18)
  test_engine_*.py
docs/
```

---

## 5. Architektur: Engine strikt von Home Assistant trennen

Das ist die wichtigste Strukturentscheidung. `engine/` ist ein reines
Python-Paket **ohne einen einzigen HA-Import**:

```
WorldState  →  engine.evaluate(state, config)  →  EvaluationResult
```

Vorteile, die daraus folgen (alle drei sind Pflichtziele):

- Die komplette Logik ist mit `pytest` gegen Szenario-Fixtures testbar, ohne HA.
- **Replay-Modus**: ein CLI-Skript (`scripts/replay.py`) nudelt historische
  Recorder-Daten durch die Engine und zeigt, was adaptive_ventilation **empfohlen hätte**.
  Unverzichtbar zum Tunen der Schwellwerte.
- Die Regeln sind lesbar und einzeln verstehbar, statt in einem if-Baum zu ertrinken.

Der HA-Teil ist danach nur Plumbing: Coordinator, Entities, Config Flow, Notifications, Panel.

### Datenklassen (Kern — vollständig ausformulieren)

```python
@dataclass(frozen=True)
class OutdoorState:
    temperature: float                # °C
    humidity: float | None            # % rF
    absolute_humidity: float | None   # g/m³, abgeleitet
    dew_point: float | None
    pm25: float | None                # µg/m³
    pm10: float | None
    wind_speed: float | None          # m/s
    wind_bearing: float | None        # °
    precipitation: float | None
    precipitation_probability: float | None
    cloud_coverage: float | None      # %
    illuminance: float | None         # lx
    source: Literal["sensor", "weather", "mixed"]
    is_stale: bool

@dataclass(frozen=True)
class RoomState:
    id: str
    name: str
    temperature: float | None
    humidity: float | None
    absolute_humidity: float | None
    dew_point: float | None
    co2: int | None
    voc: float | None
    pm25: float | None
    volume_m3: float | None
    occupied: bool | None
    heating_active: bool | None
    priority: int                     # 1 = wichtigster Raum
    confidence: float                 # 1.0 = gemessen, <1.0 = geschätzt
    estimation_method: Literal["measured", "reference_offset",
                               "learned_offset", "model"] | None
    tau_hours: float | None           # gelernt oder aus Gebäudetyp
    heating_rate_k_per_h: float | None
    is_basement: bool

@dataclass(frozen=True)
class WindowState:
    id: str
    name: str
    room_id: str
    is_open: bool
    open_since: datetime | None
    azimuth: float                    # 0=N, 90=O, 180=S, 270=W
    area_m2: float | None
    tilt_capable: bool
    is_ground_floor: bool
    rain_safe: bool                   # darf bei Regen offen bleiben?
    ok_when_away: bool
    cover_entity: str | None
    cover_position: int | None
    cover_external: bool              # außenliegend (≈80%) vs. innen (≈30%)
    cover_auto_allowed: bool
    horizon_profile: list[float] | None   # Verschattung durch Nachbargebäude/Bäume
    solar_load_w: float | None            # aktuell berechnet

@dataclass(frozen=True)
class WorldState:
    now: datetime
    outdoor: OutdoorState
    forecast: list[ForecastHour]       # ≥ 24 h, besser 48 h
    sun: SunState                      # azimuth, elevation, next_rising, next_setting
    rooms: list[RoomState]
    windows: list[WindowState]
    mode: Mode                         # AUTO/SUMMER/WINTER/AWAY/OFF/MANUAL_HOLD
    presence: bool
    weather_alerts: list[WeatherAlert] # DWD/Meteoalarm, Typ + Severity + Zeitraum
    building: BuildingProfile
    preferences: Preferences           # Gewichtungen, Schwellen, Ruhezeiten
    learned: LearnedParameters

@dataclass(frozen=True)
class Recommendation:
    id: str                           # stabil für Dedupe
    target: str                       # window_id | cover_id | room_id | "global"
    action: Action                     # s. u.
    priority: Priority                 # SAFETY / HEALTH / COMFORT / OPTIMIZATION
    urgency: int                       # 0..100, innerhalb der Klasse
    reason_key: str                    # translation_key
    reason_data: dict[str, Any]        # Zahlen für den Text
    expected_benefit: str | None       # z. B. "-2.4 K bis 06:00"
    valid_from: datetime
    valid_until: datetime | None
    duration_minutes: int | None       # für Stoßlüften
    confidence: float
    notify: bool                       # darf gepusht werden?

class Action(StrEnum):
    OPEN_WIDE = "open_wide"
    OPEN_TILT = "open_tilt"
    PURGE = "purge"                   # Stoßlüften mit Dauer
    CROSS_VENTILATE = "cross_ventilate"
    CLOSE = "close"
    KEEP_CLOSED = "keep_closed"
    KEEP_OPEN = "keep_open"
    COVER_DOWN = "cover_down"
    COVER_UP = "cover_up"
    COVER_SLAT = "cover_slat"         # Lamellen/Teilstellung
    FAN_ON = "fan_on"
    NO_ACTION = "no_action"

@dataclass(frozen=True)
class EvaluationResult:
    recommendations: list[Recommendation]
    global_state: GlobalState          # s. Abschnitt 14
    schedule: VentilationSchedule      # 24-h-Fahrplan
    tipping_points: TippingPoints      # morgens / abends
    cooling_budget: CoolingBudget
    diagnostics: dict[str, Any]        # Zwischenwerte für Panel/Debug
```

---

## 6. Physik-Kern (`engine/psychrometrics.py`, `solar.py`)

### Absolute Feuchte statt relativer — nicht verhandelbar

Ob Lüften **trocknet oder befeuchtet**, entscheidet nie die relative Feuchte.
Alles über absolute Feuchte / Taupunkt vergleichen.

```
Sättigungsdampfdruck (Magnus, Wasser über 0 °C):
  e_s(T) = 6.112 · exp( 17.62 · T / (243.12 + T) )        [hPa]
  (unter 0 °C über Eis: 6.112 · exp( 22.46 · T / (272.62 + T) ))

Dampfdruck:            e = rF/100 · e_s(T)
Absolute Feuchte:      AH = 216.7 · e / (T + 273.15)      [g/m³]
Taupunkt:              Td = 243.12 · ln(e/6.112) / (17.62 − ln(e/6.112))
Spezifische Enthalpie: h = 1.006·T + x·(2501 + 1.86·T)    [kJ/kg]
  mit x = 0.622 · e / (p − e)                              [kg/kg]
```

Konsequenzen, die die Engine ausdrücklich ziehen muss:

- Kalte Winterluft mit 90 % rF ist **absolut knochentrocken** → Lüften trocknet.
- Schwüler Sommerabend, 24 °C / 65 % rF → bringt **Feuchte herein**, obwohl die
  Prozentzahl kleiner aussieht als drinnen.
- Für „was ist kühler" im Sommer bei schwüler Luft ist **Enthalpie** der ehrlichere
  Vergleich als reine Temperatur (feuchte Luft trägt Latentwärme herein).

### Schimmelrisiko (fällt praktisch geschenkt ab)

Oberflächentemperatur der kältesten Wand schätzen:

```
T_surface ≈ T_in − f_rt · (T_in − T_out)
f_rt (Temperaturfaktor, aus Gebäudetyp, überschreibbar):
  Altbau ungedämmt, Wärmebrücke   0.30 – 0.40
  Altbau saniert                   0.20
  Neubau gedämmt                   0.10 – 0.13
rF an der Oberfläche = e_in / e_s(T_surface) · 100
  > 80 %  → Schimmelrisiko (DIN 4108-2)
  > 95 %  → Kondensation
```

Daraus: `sensor.<room>_mold_risk` (Enum: none/low/moderate/high) plus
`sensor.<room>_wall_surface_temperature`.

### Stoßlüftungsdauer berechnen statt raten

ΔT-abhängig skalieren, Startwerte (Fenster weit offen, ein Fenster, windstill):

| Außentemperatur | Dauer weit offen |
|---|---|
| < −10 °C | 3–4 min |
| −10…0 °C | 4–6 min |
| 0…10 °C | 8–12 min |
| 10…18 °C | 12–18 min |
| > 18 °C | 20–30 min (bzw. dauerhaft, wenn kühler als innen) |

Kipplüftung: Faktor ≈ 3–4 auf die Dauer, im Winter energetisch schlecht
(auskühlende Laibung → Schimmelrisiko) → Engine soll Kipp im Winter **abwerten**.

**Querlüftung erkennen** — Feature, das sonst keiner hat und das bei diesem
Datenmodell geschenkt herausfällt: Sind zwei offene Fenster mit
Azimut-Differenz > 90° in verbundenen Räumen gleichzeitig offen, steigt der
Luftwechsel um Faktor 3–5 → Dauer entsprechend kürzen und aktiv als
`CROSS_VENTILATE` empfehlen („Bad **und** Schlafzimmer gleichzeitig, dann reichen
4 statt 12 Minuten").

Optional Windunterstützung: Fenster auf der Luvseite (Fenster-Azimut nahe
`wind_bearing`) → höherer Luftwechsel.

### Solarlast pro Fenster (`solar.py`)

```
cos(θ) = cos(elev)·cos(azi_sun − azi_window)      (Vertikalfläche)
solar_load ≈ area · G_direct · max(0, cosθ) · (1 − cloud/100 · k)
             + area · G_diffuse · sky_view_factor
```

- `sun.sun` liefert Azimut/Elevation; Bewölkung aus dem Forecast dämpft.
- Optionales `horizon_profile` (Verschattung durch Nachbargebäude/Bäume) — „ab wann
  kommt Sonne wirklich an".
- Verschattungswirkung: **außenliegend hält ≈ 80 % des Eintrags ab, innenliegend
  nur ≈ 30 %** — die Scheibe hat die Energie dann schon durchgelassen. Diese
  Asymmetrie muss die Priorisierung abbilden.
- Winter dreht die Logik um: Rollladen **hoch** für passiven Wärmegewinn am Tag,
  **runter** nachts als Dämmschicht (spart real 10–20 % Transmissionsverlust).

---

## 7. Gebäudemodell & thermische Trägheit (`thermal.py`)

Ein einfaches **RC-Modell pro Raum** genügt:

```
dT_in/dt = ( (T_out − T_in)/R_env + Q_solar + Q_internal
             + n_ach·V·ρ·c_p·(T_out − T_in)/3600 ) / C
τ = R · C
```

Gebäudetyp wird abgefragt, dient aber **nur als Startwert für τ**, der danach aus
Messdaten überschrieben wird. Zwei Parameter reichen für die Praxis:

- **τ (Zeitkonstante)** — wie träge reagiert der Raum
- **Nachtauskühl-Rate** in K/h bei gegebenem ΔT, gemessen bei offenem Fenster

Startwerte:

| Gebäudetyp | τ (h) | Auskühlrate @ΔT 10 K, offen | Charakter |
|---|---|---|---|
| Altbau massiv (Wand ≥ 40 cm), ungedämmt | 40–80 | 1.0–1.5 K/h | Riesiger Kältespeicher; einmal durchgewärmt aber in einer Nacht nicht mehr runterzubekommen |
| Altbau saniert | 60–100 | 0.8–1.2 K/h | Mischform |
| Neubau gedämmt, massiv | 80–150 | 0.6–1.0 K/h | Verliert kaum Wärme nach außen → **jeder** Eintrag bleibt drin, Verschattung wichtiger als Lüften |
| Leichtbau / Dachgeschoss | 10–30 | 1.5–2.5 K/h | Heizt in 2 h auf, kühlt aber auch schnell → sogar kurzes Mittagslüften bei Wolkenlücke lohnt |
| Fachwerk | 30–60 | 1.0–1.5 K/h | Feuchtesensibel → Schimmelregeln strenger |
| Keller | 200+ | — | Sonderfall, s. Regel `basement_summer_veto` |

Daraus muss die Engine **nutzbare Aussagen** ableiten, nicht nur Zahlen:

> „Deine Wohnung speichert ~4 K Kühlung. Heute Nacht sind nur 2,5 K holbar →
> Verschattung ab 09:00 ist Pflicht, sonst reicht es morgen nicht."

---

## 8. Vorausschau: Kipppunkte, Simulation, Fahrplan

### Kipppunkte — die wichtigsten Einzelentitäten im Sommer

`sensor.adaptive_ventilation_tipping_point_morning` / `..._evening`: die Uhrzeit, zu der Außen-
und Innentemperatur sich kreuzen. Das ist im Sommer **die** Zahl, nach der man
lebt, und sie steht in keinem Standard-Dashboard.

Morgens ist sie kritisch, **weil man sie verpennt**: die wertvollste Kühlluft kommt
zwischen 04:00 und Sonnenaufgang. Deshalb:

- Wo Rollladen-/Coverautomatik erlaubt ist: automatisch handeln.
- Sonst Benachrichtigung **„in 30 Minuten zumachen"** (Vorlaufzeit konfigurierbar),
  nicht eine, die kommt, wenn es längst zu spät ist.
- Nachts respektiert das die Ruhezeiten — außer bei SAFETY-Priorität.

### 24-h-Simulation

Simuliere stündlich (besser 15-minütig) mit dem RC-Modell mehrere Varianten pro Raum:

- „nichts tun"
- „Fenster X von t₁ bis t₂ offen"
- „+ Verschattung ab t₃"
- Kombinationen der erlaubten Fenster

Ergebnis: **Lüftungsfahrplan** (`VentilationSchedule`), z. B.
„Bestes Zeitfenster heute: 23:00–05:30, erwartet −3,2 K" und morgens
„Jetzt zu; ab 10:00 draußen wärmer als drinnen, Peak 34 °C um 16:00 —
Rollladen Süd um 09:30 runter." Also **bevor** die Sonne draufknallt.

Der Fahrplan ist gleichzeitig die Datengrundlage für den Timeline-Balken im Panel.

### Mehrtägige Hitzewellen

Der Modus muss sich ändern, wenn die Nächte nicht mehr auskühlen
(**Tropennacht: T_min > 20 °C**):

- **Pre-Cooling**: „Übermorgen 38 °C — ab heute Nacht durchlüften, auch wenn es sich
  noch nicht nötig anfühlt." Der Speicher muss **vor** der Welle leer sein.
- **Kühlbudget-Bilanz** (`CoolingBudget`): geholte vs. verlorene Kelvin pro Tag.
  Mehrere Tage negativ → ehrlich werden: „Nachtlüften reicht nicht mehr, Raum X
  erreicht 30 °C — Schlafzimmer wechseln / Ventilator / Zimmer aufgeben."
- **Gewitter-Fenster**: Frontdurchgang am Abend = 8 K in 20 Minuten. Kurzes,
  aggressives Zeitfenster, das man sonst verpasst → eigene, dringende Empfehlung
  mit Ablaufdatum, gekoppelt an Regen-/Wind-Veto danach.
- **Schwüle Nächte**: Wenn die Außenluft absolut feuchter ist, holt man Feuchte
  herein, die tagsüber die gefühlte Temperatur hochtreibt. Im Sommer aber **kein
  hartes Veto**, sondern Abwägung — Kühlung darf teurer sein als Trockenheit.
  → Nutzer-Gewichtung (Abschnitt 10.4).

---

## 9. Selbstkalibrierung (`calibration.py`)

Wenn Historie da ist, benutzen. Nachts (oder auf Service-Aufruf) über den Recorder:

1. **τ pro Raum** aus Abkühl-/Aufheizkurven bei geschlossenen Fenstern
   (exponentieller Fit auf `T_in − T_out`).
2. **Realer Luftwechsel**: beim nächsten Stoßlüften ΔCO₂/Δt bzw. ΔAH/Δt loggen
   → aus der Faustregel wird **deine Wohnung**. (n_ach aus CO₂-Abfall:
   `n = ln((C₀−C_out)/(C₁−C_out)) / Δt`.)
3. **Solargewinn-Koeffizient** pro Raum aus Temperaturanstieg vs. berechneter
   Solarlast (lineare Regression, Achsenabschnitt = interne Lasten).
4. **Nachtauskühl-Rate** pro Fensterkonfiguration.
5. **Fenster-Azimut lernen** (optional) durch Korrelation von Aufheizzeitpunkt mit
   Sonnenazimut — für Nutzer ohne Lust auf Kompass-App.
6. **CO₂-Abklingzeit** pro Raum → Personenzahl-Schätzung möglich.

Alle gelernten Werte: im Panel sichtbar, einzeln **überschreibbar**, mit
Confidence und Datenbasis („aus 14 Nächten"). Ausreißerfilter, Minimum-Datenmenge
bevor ein gelernter Wert den Startwert ersetzt.

---

## 10. Regel-Engine

### 10.1 Struktur

Kein großer if-Baum. Viele kleine **Advisor-Regeln** (Registry-Pattern, je Regel
eine Datei, jede liefert 0..n Kandidaten) plus ein **Arbiter**, der auflöst.

```python
class Rule(Protocol):
    id: str
    priority: Priority
    def evaluate(self, state: WorldState) -> list[Recommendation]: ...
```

Arbiter-Logik:
1. SAFETY-Vetos sammeln → sie **verbieten** Aktionen und gewinnen immer.
2. Restliche Kandidaten pro Ziel (Fenster/Raum) nach Priorität + Urgency sortieren.
3. Widersprüche auflösen (nicht gleichzeitig OPEN und CLOSE); bei Gleichstand
   gewinnt die konservativere Aktion.
4. Raum-Prioritäten anwenden (Schlafzimmer nachts > Rest).
5. Hysterese/Cooldown anwenden (10.5).
6. Ergebnis: max. **eine** aktive Hauptempfehlung pro Fenster + globaler Zustand.

### 10.2 Regelkatalog — SAFETY (Veto, gewinnen immer)

| ID | Bedingung | Aktion |
|---|---|---|
| `storm_warning` | DWD/Meteoalarm Sturm/Unwetter aktiv oder Windböen > Schwelle (Default 60 km/h) | CLOSE alle, Rollladen runter (bei Hagel), Push auch in Ruhezeit |
| `rain_incoming` | Regen in < 30 min auf der Wetterseite des Fensters (`wind_bearing` vs. `azimuth`) und `rain_safe = false` | CLOSE |
| `frost_and_heating` | T_out < 3 °C und Heizung aktiv, Fenster > X min offen | CLOSE, Heizkörper-Hinweis |
| `away_and_open` | Abwesenheit + offenes Fenster (`ok_when_away = false` oder EG) | CLOSE, Push |
| `dark_and_open` | Dunkel (Sonnenstand/Illuminanz) + offenes EG-Fenster + Abwesenheit/Nacht | CLOSE-Hinweis |
| `outdoor_pm_spike` | PM2.5 außen > 2× innen und > 25 µg/m³ | Lüften-Veto bzw. Dämpfer |
| `outdoor_sensor_implausible` | Außensensor > 5 K über Wetter-API bei Sonne | Datenquelle degradieren + Repair Issue |
| `data_stale` | Kernsensoren älter als X min | Empfehlungen unterdrücken, Zustand `unavailable_data` |
| `basement_summer_veto` | Kellerraum, AH_out > AH_in | Lüften-Veto (Kondensation an kalten Wänden) |

### 10.3 Regelkatalog — HEALTH / COMFORT / OPTIMIZATION

**Ganzjährig**

| ID | Bedingung | Aktion |
|---|---|---|
| `co2_high` | > 1000 ppm (dringend ab 1400) | PURGE, Dauer nach ΔT |
| `voc_high` | VOC-Index über Schwelle | PURGE |
| `indoor_pm_high` | PM2.5 innen > 25 µg/m³ (Kochen, Braten, Kerzen, Kaminofen) | PURGE, auch wenn thermisch unpassend |
| `pm_both_high` | innen **und** außen hoch | Abwägung: kurz+kräftig statt lang; gar nicht, wenn außen deutlich schlimmer |
| `humidity_spike` | AH innen springt (Bad/Küche/Duschen/Wäsche) | Sofort PURGE, bevor sich Feuchte in der Wohnung verteilt; Türe zu |
| `mold_risk` | rF an Oberfläche > 80 % | PURGE + Heizungshinweis |
| `laundry_drying` | Wäsche trocknet (Nutzerangabe/Feuchtemuster) | Verstärktes Lüftungsregime, solange AH_out < AH_in |
| `internal_load` | Steckdosenmessung Ofen/Trockner/Gaming-PC > 1 kW | „Ofen an → Küchenfenster jetzt auf" |
| `window_forgotten` | Fenster > X h offen ohne Nutzen | Erinnerung |

**Sommer**

| ID | Bedingung | Aktion |
|---|---|---|
| `night_flush` | T_out < T_in − Hysterese, Nacht, Enthalpie günstig | OPEN_WIDE / CROSS_VENTILATE, mit erwarteter Kelvin-Ausbeute |
| `morning_close` | Kipppunkt morgens in < Vorlaufzeit | CLOSE (+ Vorwarnung) |
| `keep_closed_hot` | T_out > T_in | KEEP_CLOSED, mit Begründung |
| `solar_shading` | Solarlast Fenster > Schwelle, Cover verfügbar | COVER_DOWN, **vorausschauend** vor dem Peak |
| `shading_gap` | Südfenster mit hoher Solarlast **ohne** Cover | Kein „Fenster zu", sondern: „größte Lücke im System" + Hinweis auf außenliegenden Schutz |
| `precool_heatwave` | Forecast: Hitzewelle in 1–2 Tagen | Speicher vorher leerlüften |
| `tropical_night` | T_min > 20 °C | Modus wechseln, Erwartungsmanagement, Ventilator-Empfehlung |
| `thunderstorm_window` | Frontdurchgang, kurzer Temperatursturz | Kurzes aggressives Lüftungsfenster, danach Regen-Veto |
| `fan_instead` | Fenster zu sinnvoll, aber Raum zu warm | FAN_ON (Luftbewegung senkt gefühlte Temperatur um 2–3 K); Ventilator im offenen Fenster macht aus Kipp echte Querlüftung |
| `away_prepare` | Verlassen erkannt (Presence/Kalender) | Alles zu + Rollos runter, statt es abends zu bereuen |

**Winter**

| ID | Bedingung | Aktion |
|---|---|---|
| `winter_purge_schedule` | Zeit seit letztem Luftwechsel, CO₂/Feuchte | PURGE mit berechneter Dauer, 2–4×/Tag, bevorzugt Querlüftung |
| `avoid_tilt_winter` | Kippfenster im Winter offen | Umstellen auf weit+kurz, Begründung Laibungsauskühlung |
| `dry_air` | AH innen sehr niedrig (< ~4 g/m³, rF < 30 %) | Lüften **reduzieren**, Hinweis Wäsche/Pflanzen statt Dauerlüften |
| `passive_solar_gain` | Winter, Sonne auf Fenster, Raum unter Ziel | COVER_UP |
| `cover_night_insulation` | Winter, Nacht | COVER_DOWN (Dämmwirkung) |
| `preheat_before_purge` | Vor Stoßlüften | Thermostat kurz runter, danach hoch (Energie sparen) |
| `inversion_pm` | Winter-Inversionslage, PM2.5 außen hoch (Kaminrauch, Silvester, Saharastaub) | Lüften verschieben, kurz+kräftig |
| `unheated_room` | Raum kalt + feucht | Warnung: Türen zu warmen Räumen zu, sonst Kondensation |

### 10.4 Nutzer-Gewichtungen (Zielkonflikte)

Vier Achsen, jede kann Lüften **fordern oder verbieten**, der Arbiter wägt ab:
**Temperatur, Feuchte, CO₂, Partikel.** (Das ist auch die runde Story fürs README.)

Als Slider im Panel (0–100), Defaults saisonabhängig:

- Kühlung priorisieren ↔ Feuchte niedrig halten
- Luftqualität priorisieren ↔ Temperatur halten
- Energie sparen ↔ Komfort
- Wenig Benachrichtigungen ↔ maximale Optimierung

### 10.5 Ruhe im System (zwingend, sonst unbenutzbar)

- **Hysterese** auf jede Schwelle (Default ΔT-Hysterese 0.5 K, CO₂ 150 ppm).
- **Mindest-Verweildauer** pro Zustand (Default 15 min) — sonst flattert das Ding
  bei ΔT ≈ 0 im Minutentakt.
- **Cooldown pro Empfehlungs-ID** (Default 60 min) + Dedupe über `Recommendation.id`.
- **Snooze**: 1 h / bis heute Abend / heute ganz ignorieren, pro Empfehlung.
- **Ruhezeiten** (Default 22:00–07:00): nur SAFETY pusht.
- **MANUAL_HOLD**: erkennt der Nutzer widerspricht (Fenster bleibt trotz Empfehlung
  offen) → nach 2 Wiederholungen für diesen Tag nachgeben und lernen.

---

## 11. Räume ohne Sensor

Drei Stufen, mit explizitem **Confidence-Wert** im Attribut:

1. **Referenzraum + Offset** — Nutzer wählt Nachbarraum, gibt Offset an
   („Dachgeschoss +3 K"). Simpel, reicht oft. Confidence 0.6.
2. **Gelernter Offset** — falls je ein Sensor dort war, oder aus Korrelation mit
   Solarlast und Nachbarräumen geschätzt. Confidence 0.7–0.8.
3. **Modellbasiert** — aus τ, Fensterfläche, Ausrichtung und Nachbartemperaturen
   simuliert. Confidence 0.5.

Regeln dazu:

- Bei niedriger Confidence **keine Push-Benachrichtigungen**, nur Anzeige im Panel.
- **Sicherheitsregeln greifen trotzdem** (Sturm, Regen, Abwesenheit, Dunkelheit) —
  die brauchen keine Temperatur.
- Hilfreicher Hinweis statt stiller Ungenauigkeit: „Ein Sensor in Raum X würde die
  Empfehlungen am stärksten verbessern" — berechnet aus Fensterfläche und Solarlast.
- Fallback-Kette insgesamt: eigener Sensor → Referenzraum+Offset → gelernt →
  Modell → Wohnungsmittelwert → `unknown` (nie stillschweigend raten).

---

## 12. Datenmodell der Konfiguration (Config Flow + Subentries)

**Config Entry = Wohnung.** Subentry-Typen: `room`, `window`.
Ein Fenster-Subentry referenziert ein Raum-Subentry.

**Global (Options Flow):**
- Außentemperatur-Quelle: eigener Sensor bevorzugt, Wetter-Entity als Fallback
- Außenfeuchte, Außen-PM2.5/PM10 (Sensor oder Luftqualitäts-Integration)
- Wetter-Entity für Forecast (stündlich!), Wetterwarnungs-Entity (DWD/Meteoalarm)
- Sonnen-Entity (Default `sun.sun`), optional Helligkeitssensor
- Gebäudeprofil: Typ, Baujahr, Etage, Dachgeschoss ja/nein, f_rt-Override
- Benachrichtigungsziele (mehrere `notify.*`), Ruhezeiten, Actionable-Notifications an/aus
- Anwesenheit (person/group/device_tracker), optional Kalender für Abwesenheit
- Gewichtungen (10.4), Einheiten, Sprache der Empfehlungstexte
- Modus-Default, Schwellwerte global

**Pro Raum:**
- Temperatur-, Feuchtesensor (Pflicht oder Schätzstufe wählen)
- optional CO₂, VOC, PM2.5
- Raumvolumen (m³) oder Fläche + Höhe
- Zieltemperatur-Band **getrennt für Sommer/Winter** (z. B. Sommer 22–26 °C,
  Winter 19–23 °C), Ziel-rF-Band (Default 40–60 %)
- Priorität (1..n) — Schlafzimmer nachts kühl > Rest der Wohnung
- optional Präsenzsensor, Heizungs-/Climate-Entity, Ventilator-Entity
- Flags: Keller, Bad/Küche (Feuchtequelle), unbeheizt, Dachgeschoss
- Verbundene Räume (für Querlüftungs-Erkennung)

**Pro Fenster:**
- Kontaktsensor (Pflicht), optional Kipp-/Offen-Unterscheidung
- **Ausrichtung als Azimut in Grad** (nicht nur Himmelsrichtung — für Solarrechnung
  nötig); UI mit Kompass-Picker + Preset-Buttons N/NO/O/…
- Verschattungshorizont (optional, „ab wann kommt Sonne wirklich an")
- Fensterfläche m², Typ (Dreh/Kipp/beides), Glasart (optional, g-Wert)
- Rollladen-/Cover-Entity, außen- oder innenliegend, Flag „darf automatisch
  gesteuert werden"
- Sicherheits-Flags: Erdgeschoss?, bei Abwesenheit offen ok?, regensicher?
- Sonnenstunden/Tag (optional, alternativ zum Horizontprofil)

**UX-Pflicht:** Basis-Setup mit Defaults in 2 Minuten abschließbar; alle
Detailfelder hinter „Erweitert". Assistent, der Fenster aus vorhandenen
`binary_sensor.*_contact`/`device_class: window` vorschlägt und Räume aus dem
Area-Registry übernimmt.

---

## 13. Services

```yaml
adaptive_ventilation.start_purge:        # Stoßlüften starten, optional Raum/Dauer, Timer läuft
adaptive_ventilation.snooze:             # Empfehlung/alle für Dauer stummschalten
adaptive_ventilation.acknowledge:        # "erledigt" -> Cooldown + Lernsignal
adaptive_ventilation.set_mode:           # AUTO/SUMMER/WINTER/AWAY/OFF
adaptive_ventilation.recalibrate:        # Kalibrierung sofort laufen lassen
adaptive_ventilation.override_parameter: # gelernten Wert überschreiben/zurücksetzen
adaptive_ventilation.export_diagnostics:
adaptive_ventilation.simulate:           # Was-wäre-wenn: Fenster X von t1..t2 -> erwartete Kurve
```

Events: `adaptive_ventilation_recommendation_added` / `_cleared` / `_purge_finished` /
`_calibration_updated` — für eigene Automationen des Nutzers.

---

## 14. Entitäten

**Global**
- `sensor.adaptive_ventilation_status` — Zustandsautomat:
  `idle / ventilate_now / keep_closed / heat_protection / night_flush /
   purge_running / storm / air_quality / away / off`
- `sensor.adaptive_ventilation_next_action` — Kurztext, Details in Attributen
- `sensor.adaptive_ventilation_tipping_point_morning` / `..._evening` (timestamp)
- `sensor.adaptive_ventilation_cooling_budget_today` (K), `..._balance_3d`
- `sensor.adaptive_ventilation_open_windows_count`
- `binary_sensor.adaptive_ventilation_action_required`, `binary_sensor.adaptive_ventilation_storm_risk`
- `text.adaptive_ventilation_display_line1` / `line2` / `line3` — für externe Displays
- `select.adaptive_ventilation_mode`
- `switch.adaptive_ventilation_notifications`, `switch.adaptive_ventilation_cover_automation`
- `number.*` für Live-Tuning der wichtigsten Schwellen und Gewichte
- `button.adaptive_ventilation_purge_now`, `button.adaptive_ventilation_snooze_1h`, `button.adaptive_ventilation_recalibrate`

**Pro Raum**
- `sensor.<room>_absolute_humidity`, `..._dew_point`, `..._enthalpy`
- `sensor.<room>_mold_risk`, `..._wall_surface_temperature`
- `sensor.<room>_heating_rate` („aktuell +0,9 K/h → 31 °C um 18:00" in Attributen)
- `sensor.<room>_air_quality_score` (0–100 aus vier Achsen)
- `sensor.<room>_recommendation` + `sensor.<room>_temperature_estimated`
  (mit `confidence`, `estimation_method`)

**Pro Fenster**
- `sensor.<window>_recommendation` — State = Enum-Aktion, Details in Attributen
- `sensor.<window>_solar_load` (W)
- `binary_sensor.<window>_should_be_open`

**Zwei Praxisfallen, die zwingend zu beachten sind:**

1. **State-Strings sind auf 255 Zeichen begrenzt.** Also: State = Kurztext/Enum;
   Langtext, `line1`, `line2`, `severity`, `countdown`, `reason`, `numbers` in
   **Attribute**.
2. Enum-Sensoren mit `device_class: enum` + `options` + `translation_key`, damit
   Übersetzung und Statistiken funktionieren.

---

## 15. Benachrichtigungen (`notify_manager.py`)

- Mehrere Ziele, pro Ziel filterbar nach Mindestpriorität.
- **Actionable Notifications** (iOS/Android Companion): „Erledigt", „Snooze 1 h",
  „Heute ignorieren", bei Cover-Empfehlungen „Jetzt runterfahren".
- Dedupe über `Recommendation.id`, Update statt Neusenden (`tag`), automatisches
  Zurückziehen (`clear_notification`), wenn die Empfehlung obsolet ist.
- Ruhezeiten, Rate-Limit (Default max. 6 Pushes/Tag außer SAFETY).
- Vorwarnungen statt Nachrufe: „in 30 Minuten zumachen".
- Persistent Notification nur für Repair-artige Dinge, nicht für Alltagsempfehlungen.
- Textbausteine kurz, mit Zahl und Nutzen: „Schlafzimmer jetzt auf — draußen 18,4 °C,
  innen 25,1 °C, bis 06:00 ca. −3,2 K."

---

## 16. Dashboard: Sidebar-Panel

Registrierung wie bei Smart Irrigation über `async_register_built_in_panel`
mit eigenem JS-Modul (Lit + TypeScript, Build nach `custom_components/adaptive_ventilation/frontend/`).

**Wichtig: das Panel gleich in Phase 2 registrieren**, auch wenn es zunächst nur
eine hässliche Tabelle zeigt. Nachträglich draufsetzen ist mehr Arbeit als von
Anfang an mitzuziehen.

**Tab 1 — Jetzt** (der Blick, den man 20× am Tag wirft)
- Große Statuszeile: Modus + Hauptempfehlung + Countdown („Fenster zu in 25 min")
- Die drei Kennzahlen: ΔT innen/außen, absolute Feuchte innen/außen, nächster Kipppunkt
- **24-h-Timeline**: grün = gute Lüftungszeit, rot = zu, gelb = bedingt, „jetzt"-Marker;
  zweite Spur = erwartete Innentemperatur. → **echtes SVG statt ApexCharts-Verrenkung**;
  farbige Balken + Marker sind ~50 Zeilen und sehen danach genau so aus wie gewollt.
- Fensterliste mit Soll/Ist und Ein-Klick-Aktionen (erledigt / snooze / heute ignorieren)

**Tab 2 — Räume**
- Pro Raum eine Zeile: T, rF, absolute Feuchte, CO₂, PM2.5, Aufheizrate,
  Confidence-Badge bei geschätzten Räumen
- Klick → Detail mit Verlauf und der Erklärung, **warum** gerade diese Empfehlung gilt

**Tab 3 — Tuning**
- Die Slider, für die man sonst YAML anfassen müsste: Zieltemperaturbänder,
  CO₂-/PM-Schwellen, Prioritätsgewichte, Ruhezeiten, Mindestdauer/Cooldown
- Rechts **Live-Vorschau**: „mit diesen Werten wären heute 4 statt 9
  Benachrichtigungen gekommen" (Replay über die letzten 24 h) — macht Tuning zum
  Spiel statt zum Ratespiel

**Tab 4 — Bilanz**
- Kühlbudget der letzten Tage
- **Schwachstellen-Report** („Südfenster Wohnzimmer: +2,1 K/Tag") — nach der ersten
  Hitzewelle: welches Fenster hat dich die meisten Kelvin gekostet
- Sensor-Empfehlungen (wo würde ein Sensor am meisten bringen)
- Gelernte τ-Werte pro Raum, überschreibbar

**Zusätzlich:** eine gut gemachte Markdown-/Mushroom-Karte als
Copy-&-Paste-Snippet in `docs/` — damit die Integration ab Tag 1 auf dem
Haupt-Dashboard nutzbar ist, ohne auf die Custom Card zu warten.

---

## 17. ESP32 / externe Displays

**Nicht REST-Polling, sondern ESPHome mit der nativen API** — Push statt Poll,
kein WLAN-Dauergezappel:

```yaml
text_sensor:
  - platform: homeassistant
    id: adaptive_ventilation_line1
    entity_id: text.adaptive_ventilation_display_line1
  - platform: homeassistant
    id: adaptive_ventilation_action
    entity_id: sensor.adaptive_ventilation_status
    attribute: countdown
```

Liefere in `docs/esphome_example.yaml` ein **fertiges, funktionierendes Snippet**
mit Display-Lambda (kleines OLED/E-Paper: Zeile 1 Status, Zeile 2 Empfehlung,
Zeile 3 ΔT + Countdown, plus Icon/Ampelfarbe). Das verkauft die Integration.

Zusätzlich REST-freundliche Attribute (kompaktes JSON in einem Attribut) für
Nicht-ESPHome-Displays.

---

## 18. Tests

- `tests/fixtures/scenarios/*.yaml`: deklarative Szenarien mit erwarteten Aktionen.
  Pflicht-Szenarien:
  - `heatwave_35c_day` — Peak mittags, alles zu, Verschattung
  - `heatwave_night_flush` — 22:00, 18 °C außen, 27 °C innen
  - `tropical_night_multiday` — Budget negativ, Erwartungsmanagement
  - `humid_summer_evening` — kühler aber absolut feuchter → Abwägung
  - `thunderstorm_front` — kurzes Fenster, danach Regen-Veto
  - `winter_morning_-5c` — Stoßlüften 5 min, Querlüftung erkannt
  - `winter_dry_air` — Lüften reduzieren
  - `bathroom_after_shower` — Feuchtespitze
  - `co2_bedroom_night` — CO₂ 1400 ppm, Ruhezeit
  - `storm_warning_windows_open` — SAFETY overrides alles
  - `pm25_inversion` — außen schlecht, innen ok
  - `basement_summer` — Kondensationsveto
  - `room_without_sensor` — Confidence, kein Push
  - `flapping_delta_t_zero` — **muss** stabil bleiben (Anti-Flatter-Test)
  - `stale_sensor_data` — degradiert sauber
- Property-Tests: keine widersprüchlichen Empfehlungen, SAFETY gewinnt immer,
  nie mehr als eine Hauptempfehlung pro Fenster.
- `scripts/replay.py`: Recorder-CSV/Historie rein, Empfehlungen + Kennzahlen raus.
- HA-Seite: Config-Flow-Tests, Entity-Snapshot-Tests, `pytest-homeassistant-custom-component`.

---

## 19. Phasenplan

Der Scope ist groß genug, dass er erschlagen kann. Strikt in dieser Reihenfolge,
**Sommer zuerst** (der Nutzer will in den nächsten Hitzetagen schon etwas davon haben).
Nach jeder Phase: lauffähig, getestet, committed.

**Phase 0 — Gerüst**
Repo, `manifest.json`, `hacs.json`, ruff/mypy/pre-commit, CI (GitHub Actions:
lint + test + hassfest), README-Skeleton, `engine/`-Paket leer aber importierbar.

**Phase 1 — Engine-Kern ohne HA**
`psychrometrics.py` (vollständig, mit Tests gegen Referenzwerte), Dataclasses,
`thermal.py` mit RC-Modell + τ-Startwerten, Regel-Registry + Arbiter,
erste Regeln (`night_flush`, `morning_close`, `keep_closed_hot`, `co2_high`,
`humidity_spike`, SAFETY-Basis), Szenario-Fixtures + Tests.
*Akzeptanz:* `pytest` grün, alle Sommer-Szenarien liefern plausible Aktionen,
Anti-Flatter-Test besteht.

**Phase 2 — HA-Integration Basis + Panel-Stub**
Config Flow mit Subentries (Raum/Fenster), Coordinator, Entitäten für Temperatur,
absolute Feuchte, Taupunkt, Empfehlung pro Fenster, `sensor.adaptive_ventilation_status`,
`select.adaptive_ventilation_mode`. Sidebar-Panel registriert (rohe Tabelle genügt).
*Akzeptanz:* in echter HA-Instanz installierbar, konfigurierbar, zeigt sinnvolle Werte.

**Phase 3 — Benachrichtigungen**
`notify_manager.py`, Dedupe, Cooldown, Ruhezeiten, Actionable Notifications,
Snooze/Acknowledge, Services, Events.
*Akzeptanz:* **ab hier täglich nützlich** — das ist der Punkt, an dem sich zeigt,
ob die Schwellwerte taugen. Danach eine Woche Praxisbetrieb einplanen.

**Phase 4 — Sonne, Verschattung, Rollladen**
`solar.py`, Solarlast pro Fenster, `solar_shading`, `shading_gap`,
Cover-Automatik (nur wo erlaubt), `passive_solar_gain`,
`cover_night_insulation`.

**Phase 5 — Vorausschau**
Forecast-Anbindung, 24-h-Simulation, Kipppunkt-Entitäten, `VentilationSchedule`,
Stoßlüft-Timer mit berechneter Dauer, Querlüftungs-Erkennung,
`precool_heatwave`, `tropical_night`, `thunderstorm_window`, Kühlbudget.

**Phase 6 — Winter vollständig**
Restliche Winterregeln, Schimmelrisiko-Entitäten, Kipp-Abwertung,
`preheat_before_purge`, Trockenluft-Logik, unbeheizte Räume.

**Phase 7 — Räume ohne Sensor + PM2.5**
Schätzstufen mit Confidence, Sensor-Empfehlungen, PM-Regeln komplett
(innen/außen/beide, Inversionslage).

**Phase 8 — Panel ausbauen**
Vier Tabs, SVG-Timeline, Tuning mit Live-Vorschau, Bilanz + Schwachstellen-Report.
Dazu ESPHome-Beispiel + Markdown-Karten-Snippet.

**Phase 9 — Selbstkalibrierung**
`calibration.py` komplett, Store-Persistenz, gelernte Werte im Panel
überschreibbar, `adaptive_ventilation.recalibrate`.

**Phase 10 — Politur**
Diagnostics, Repair Issues, Übersetzungen vollständig, Quality-Scale-Check,
Doku mit Screenshots, HACS-Einreichung.

---

## 20. Offene Fragen — stelle sie mir, bevor du mit Phase 1 anfängst

1. Domain `adaptive_ventilation` frei? (GitHub + `home-assistant/brands` prüfen — die Domain ist
   später nur mit Migrationsschmerzen änderbar. Fallback-Namen: `adaptive_ventilation`,
   `smart_ventilation`, `airflow_advisor`, `aeris`, `boreas`, `klimalotse`.)
2. Konkrete Wohnung: Gebäudetyp, Etage, Anzahl Räume/Fenster, welche Sensoren
   real vorhanden (Modelle), welche Wetter-Integration, DWD-Warnungen vorhanden?
3. Wie viele Rollläden sind in HA integriert, außen- oder innenliegend?
4. Companion App im Einsatz (für Actionable Notifications)?
5. Recorder-Historie: wie lange (`purge_keep_days`)? Bestimmt, ob Phase 9 früher
   sinnvoll ist.
6. Soll die Integration je Cover **selbst** schalten, oder immer nur empfehlen und
   Schalten dem Nutzer per Automation überlassen?
7. Zielsprache der Empfehlungstexte primär Deutsch oder Englisch?

## 21. Branding

- Projektname: **Adaptive Ventilation** — Untertitel „Adaptive Ventilation for Home Assistant"
  (Keyword für Auffindbarkeit in HACS steckt in der Description, nicht im Namen).
- Icon/Logo: Windmotiv, für `brands`-Repo in `icon.png` (256×256) + `logo.png`.
- README-Struktur: Problem („Sommer ohne Klimaanlage"), die vier Achsen
  (Temperatur/Feuchte/CO₂/Partikel), Screenshot des Panels mit Timeline,
  Physik-Kurzerklärung (absolute Feuchte!), Installation, Konfiguration in 2 Minuten,
  Beispielszenarien, ESPHome-Snippet, FAQ.
