/**
 * Adaptive Ventilation sidebar panel.
 *
 * Deliberately a single hand written ES module: no bundler, no node_modules,
 * no build step in CI. Home Assistant loads this file directly and hands the
 * element a `hass` object; everything else goes over the normal websocket
 * connection, so authentication and live updates come for free.
 *
 * The 24 h timeline is plain SVG - about fifty lines, and it looks exactly the
 * way it is supposed to instead of fighting a charting library.
 */

const DOMAIN = "adaptive_ventilation";
const REFRESH_MS = 20000;
const MIN_FETCH_GAP_MS = 3000;

const QUALITY_COLOUR = {
  good: "var(--av-good)",
  fair: "var(--av-fair)",
  bad: "var(--av-bad)",
  blocked: "var(--av-blocked)",
};

const ACTION_ICON = {
  open_wide: "🪟",
  open_tilt: "↗",
  purge: "💨",
  cross_ventilate: "↔",
  close: "🚪",
  keep_closed: "🔒",
  keep_open: "🪟",
  cover_down: "⬇",
  cover_up: "⬆",
  cover_slat: "▤",
  fan_on: "🌀",
  no_action: "·",
};

const STATUS_TONE = {
  ventilate_now: "good",
  night_flush: "good",
  air_quality: "warn",
  purge_running: "good",
  keep_closed: "neutral",
  heat_protection: "warn",
  storm: "bad",
  unavailable_data: "bad",
  away: "neutral",
  off: "neutral",
  idle: "neutral",
};

const TEXT = {
  en: {
    now: "Now",
    rooms: "Rooms",
    tuning: "Tuning",
    balance: "Balance",
    loading: "Loading…",
    notConfigured:
      "No loaded Adaptive Ventilation configuration found. Add the integration first.",
    deltaT: "ΔT inside / outside",
    absHumidity: "Absolute humidity in / out",
    nextTipping: "Next tipping point",
    windows: "Windows",
    done: "Done",
    snooze: "Snooze 1 h",
    ignore: "Ignore today",
    purge: "Purge",
    mode: "Mode",
    noRecommendations: "Nothing to do right now.",
    bestWindow: "Best window today",
    expected: "expected",
    room: "Room",
    temperature: "Temp",
    humidity: "RH",
    co2: "CO₂",
    pm25: "PM2.5",
    trend: "Trend",
    score: "Score",
    estimated: "estimated",
    why: "Why this recommendation",
    weights: "Priorities",
    thresholds: "Thresholds",
    calmness: "Calmness",
    preview: "Live preview",
    previewHint:
      "What the current situation would look like with these values. For a real 24 h replay use scripts/replay.py.",
    notifications: "notifications",
    instead: "instead of",
    coolingBudget: "Cooling budget",
    achievable: "obtainable tonight",
    required: "needed",
    storage: "stored by the structure",
    balance3d: "3 day balance",
    weakSpots: "Weak spots",
    weakSpotsHint: "Which window costs you the most Kelvin per day.",
    sensorSuggestions: "Where a sensor would help most",
    learned: "Learned values",
    samples: "samples",
    reset: "Reset",
    recalibrate: "Recalibrate now",
    noCover: "no shading",
    external: "external",
    internal: "internal",
    confidence: "confidence",
    apply: "Apply",
    outdoorStale: "Outdoor data is stale",
    avoidable: "avoidable",
    profile: "Profile",
    profileHint:
      "Sets how chatty and how twitchy the system is. Individual values below still win.",
    profileQuiet: "Quiet",
    profileBalanced: "Balanced",
    profileEager: "Maximum optimisation",
    resetAll: "Reset all tuning",
    resetHint: "Sensors, weather and notification targets are not touched.",
    changed: "values differ from the default",
    calibNever: "The self-calibration has not run yet.",
    calibNoRecorder: "No recorder history available, so nothing can be learned.",
    calibNoHistory: "No usable history yet.",
    calibNotEnough:
      "Not enough usable episodes yet. Each value needs at least {min} before it replaces the building default.",
    calibLastRun: "Last run",
    calibRooms: "{learned} of {total} rooms have data, {inUse} in use",
    recommendations: "recommendations",
    working: "Working…",
  },
  de: {
    now: "Jetzt",
    rooms: "Räume",
    tuning: "Tuning",
    balance: "Bilanz",
    loading: "Lädt…",
    notConfigured:
      "Keine geladene Adaptive-Ventilation-Konfiguration gefunden. Bitte zuerst die Integration einrichten.",
    deltaT: "ΔT innen / außen",
    absHumidity: "Absolute Feuchte innen / außen",
    nextTipping: "Nächster Kipppunkt",
    windows: "Fenster",
    done: "Erledigt",
    snooze: "1 h Ruhe",
    ignore: "Heute ignorieren",
    purge: "Stoßlüften",
    mode: "Modus",
    noRecommendations: "Gerade nichts zu tun.",
    bestWindow: "Bestes Zeitfenster heute",
    expected: "erwartet",
    room: "Raum",
    temperature: "Temp",
    humidity: "rF",
    co2: "CO₂",
    pm25: "PM2.5",
    trend: "Trend",
    score: "Score",
    estimated: "geschätzt",
    why: "Warum diese Empfehlung",
    weights: "Prioritäten",
    thresholds: "Schwellwerte",
    calmness: "Ruhe",
    preview: "Live-Vorschau",
    previewHint:
      "So sähe die aktuelle Lage mit diesen Werten aus. Für einen echten 24-h-Replay: scripts/replay.py.",
    notifications: "Benachrichtigungen",
    instead: "statt",
    coolingBudget: "Kühlbudget",
    achievable: "heute Nacht holbar",
    required: "nötig",
    storage: "vom Bau gespeichert",
    balance3d: "Bilanz 3 Tage",
    weakSpots: "Schwachstellen",
    weakSpotsHint: "Welches Fenster dich die meisten Kelvin pro Tag kostet.",
    sensorSuggestions: "Wo ein Sensor am meisten bringt",
    learned: "Gelernte Werte",
    samples: "Messungen",
    reset: "Zurücksetzen",
    recalibrate: "Jetzt kalibrieren",
    noCover: "keine Verschattung",
    external: "außen",
    internal: "innen",
    confidence: "Confidence",
    apply: "Übernehmen",
    outdoorStale: "Außendaten sind veraltet",
    avoidable: "vermeidbar",
    profile: "Profil",
    profileHint:
      "Legt fest, wie gesprächig und wie nervös das System ist. Einzelne Werte unten haben trotzdem Vorrang.",
    profileQuiet: "Ruhig",
    profileBalanced: "Ausgewogen",
    profileEager: "Maximal optimiert",
    resetAll: "Alle Tuning-Werte zurücksetzen",
    resetHint: "Sensoren, Wetter und Benachrichtigungsziele bleiben unangetastet.",
    changed: "Werte weichen vom Standard ab",
    calibNever: "Die Selbstkalibrierung ist noch nicht gelaufen.",
    calibNoRecorder: "Keine Recorder-Historie verfügbar, es kann nichts gelernt werden.",
    calibNoHistory: "Noch keine brauchbare Historie.",
    calibNotEnough:
      "Noch zu wenige brauchbare Episoden. Jeder Wert braucht mindestens {min}, bevor er den Gebäude-Startwert ersetzt.",
    calibLastRun: "Letzter Lauf",
    calibRooms: "{learned} von {total} Räumen haben Daten, {inUse} im Einsatz",
    recommendations: "Empfehlungen",
    working: "Arbeitet…",
  },
};

const STYLES = `
  :host { display: block; }
  * { box-sizing: border-box; }
  .wrap {
    --av-good: #3ea76a;
    --av-fair: #e0a33a;
    --av-bad: #cf4a3c;
    --av-blocked: #6d7683;
    padding: 12px 16px 48px;
    max-width: 1100px;
    margin: 0 auto;
    color: var(--primary-text-color);
    font-family: var(--paper-font-body1_-_font-family, Roboto, sans-serif);
  }
  .tabs { display: flex; gap: 4px; margin-bottom: 14px; flex-wrap: wrap; }
  .tab {
    padding: 8px 16px; border-radius: 999px; cursor: pointer; border: none;
    background: var(--secondary-background-color); color: var(--primary-text-color);
    font-size: 14px;
  }
  .tab[aria-selected="true"] { background: var(--primary-color); color: var(--text-primary-color); }
  .card {
    background: var(--card-background-color); border-radius: 12px; padding: 16px;
    margin-bottom: 14px; box-shadow: var(--ha-card-box-shadow, 0 1px 3px rgba(0,0,0,.12));
  }
  .headline { display: flex; align-items: flex-start; gap: 14px; }
  .headline .dot { width: 14px; height: 14px; border-radius: 50%; margin-top: 7px; flex: 0 0 auto; }
  .tone-good { background: var(--av-good); }
  .tone-warn { background: var(--av-fair); }
  .tone-bad { background: var(--av-bad); }
  .tone-neutral { background: var(--av-blocked); }
  .headline h1 { margin: 0 0 4px; font-size: 22px; font-weight: 500; }
  .headline p { margin: 0; color: var(--secondary-text-color); font-size: 15px; line-height: 1.4; }
  .countdown { font-variant-numeric: tabular-nums; font-size: 14px; margin-top: 6px; }
  .metrics { display: flex; flex-wrap: wrap; gap: 20px; margin-top: 16px; }
  .metric { min-width: 150px; }
  .metric .label { font-size: 12px; color: var(--secondary-text-color); text-transform: uppercase; letter-spacing: .04em; }
  .metric .value { font-size: 20px; font-variant-numeric: tabular-nums; }
  table { width: 100%; border-collapse: collapse; font-size: 14px; }
  th, td { text-align: left; padding: 7px 8px; border-bottom: 1px solid var(--divider-color); }
  th { font-weight: 500; color: var(--secondary-text-color); font-size: 12px; text-transform: uppercase; letter-spacing: .04em; }
  td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
  .pill { display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 12px; background: var(--secondary-background-color); }
  .pill.warn { background: var(--av-fair); color: #1b1b1b; }
  .pill.bad { background: var(--av-bad); color: #fff; }
  .pill.good { background: var(--av-good); color: #fff; }
  button.action {
    border: 1px solid var(--divider-color); background: transparent; color: var(--primary-text-color);
    border-radius: 8px; padding: 5px 10px; cursor: pointer; font-size: 13px; margin-right: 6px;
  }
  button.action:hover { background: var(--secondary-background-color); }
  h2 { font-size: 16px; font-weight: 500; margin: 0 0 12px; }
  .sub { color: var(--secondary-text-color); font-size: 13px; margin: -6px 0 12px; }
  .slider-row { display: grid; grid-template-columns: 1fr 160px 70px; align-items: center; gap: 12px; margin-bottom: 10px; font-size: 14px; }
  input[type="range"] { width: 100%; }
  .bar { height: 10px; border-radius: 4px; background: var(--secondary-background-color); overflow: hidden; }
  .bar > span { display: block; height: 100%; }
  .muted { color: var(--secondary-text-color); }
  .reason { font-size: 13px; color: var(--secondary-text-color); }
  .grid2 { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 14px; }
  .row-click { cursor: pointer; }
  .detail { background: var(--secondary-background-color); }
  .detail td { font-size: 13px; }
  svg { display: block; width: 100%; height: auto; overflow: visible; }
  .legend { display: flex; gap: 14px; font-size: 12px; color: var(--secondary-text-color); margin-top: 8px; flex-wrap: wrap; }
  .legend i { display: inline-block; width: 10px; height: 10px; border-radius: 2px; margin-right: 5px; }
  .empty { color: var(--secondary-text-color); font-style: italic; }
`;

class AdaptiveVentilationPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._tab = "now";
    this._data = null;
    this._error = null;
    this._expandedRoom = null;
    this._pendingPreferences = {};
    this._preview = null;
    this._previewError = null;
    this._previewPending = false;
    this._lastFetch = 0;
    this._timer = null;
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (first) {
      this._fetch();
      this._timer = window.setInterval(() => this._fetch(), REFRESH_MS);
    }
  }

  get hass() {
    return this._hass;
  }

  disconnectedCallback() {
    if (this._timer) window.clearInterval(this._timer);
  }

  get t() {
    const language = (this._data && this._data.language) || "en";
    return TEXT[language] || TEXT.en;
  }

  async _fetch() {
    if (!this._hass) return;
    const now = Date.now();
    if (now - this._lastFetch < MIN_FETCH_GAP_MS) return;
    this._lastFetch = now;
    try {
      this._data = await this._hass.callWS({ type: `${DOMAIN}/panel_data` });
      this._error = this._data && this._data.ready ? null : "not_ready";
    } catch (err) {
      this._error = String(err && err.message ? err.message : err);
    }
    this._render();
  }

  async _act(payload) {
    if (!this._hass) return;
    try {
      await this._hass.callWS({ type: `${DOMAIN}/action`, ...payload });
    } catch (err) {
      this._error = String(err && err.message ? err.message : err);
    }
    this._lastFetch = 0;
    await this._fetch();
  }

  async _requestPreview() {
    if (!this._hass) return;
    try {
      this._preview = await this._hass.callWS({
        type: `${DOMAIN}/preview`,
        preferences: this._pendingPreferences,
      });
      this._previewError = null;
    } catch (err) {
      this._preview = null;
      this._previewError = String(err && err.message ? err.message : err);
    }
    this._previewPending = false;
    this._render();
  }

  // ------------------------------------------------------------------
  // Rendering
  // ------------------------------------------------------------------

  _render() {
    if (!this.shadowRoot) return;
    const body = this._data && this._data.ready ? this._renderTab() : this._renderPlaceholder();
    this.shadowRoot.innerHTML = `
      <style>${STYLES}</style>
      <div class="wrap">
        ${this._renderTabs()}
        ${body}
      </div>`;
    this._bind();
  }

  _renderPlaceholder() {
    if (this._error === "not_ready" || (this._data && !this._data.ready)) {
      return `<div class="card"><p class="empty">${this.t.notConfigured}</p></div>`;
    }
    if (this._error) {
      return `<div class="card"><p class="empty">${escapeHtml(this._error)}</p></div>`;
    }
    return `<div class="card"><p class="empty">${this.t.loading}</p></div>`;
  }

  _renderTabs() {
    const tabs = [
      ["now", this.t.now],
      ["rooms", this.t.rooms],
      ["tuning", this.t.tuning],
      ["balance", this.t.balance],
    ];
    return `<div class="tabs" role="tablist">${tabs
      .map(
        ([id, label]) =>
          `<button class="tab" role="tab" data-tab="${id}" aria-selected="${
            this._tab === id
          }">${label}</button>`
      )
      .join("")}</div>`;
  }

  _renderTab() {
    // The preview box used to stay empty until a slider moved, which made
    // the whole tab look broken. Fetch it once with the current values.
    if (this._tab === "tuning" && !this._preview && !this._previewPending) {
      this._previewPending = true;
      this._requestPreview();
    }
    switch (this._tab) {
      case "rooms":
        return this._renderRooms();
      case "tuning":
        return this._renderTuning();
      case "balance":
        return this._renderBalance();
      default:
        return this._renderNow();
    }
  }

  // -- Tab 1: Now ----------------------------------------------------

  _renderNow() {
    const d = this._data;
    const t = this.t;
    const tone = STATUS_TONE[d.status] || "neutral";
    const countdown =
      d.countdown === null || d.countdown === undefined
        ? ""
        : `<div class="countdown">⏱ ${formatCountdown(d.countdown, d.language)}</div>`;

    const indoor = average(d.rooms.map((r) => r.temperature));
    const indoorAh = average(d.rooms.map((r) => r.absolute_humidity));
    const deltaT =
      indoor !== null && d.outdoor.temperature !== null
        ? (indoor - d.outdoor.temperature).toFixed(1)
        : "–";

    const modes = ["auto", "summer", "winter", "away", "off"];

    return `
      <div class="card">
        <div class="headline">
          <span class="dot tone-${tone}"></span>
          <div>
            <h1>${escapeHtml(statusLabel(d.status, d.language))}</h1>
            <p>${escapeHtml(d.headline || t.noRecommendations)}</p>
            ${countdown}
            ${d.outdoor.stale ? `<p class="pill bad" style="margin-top:8px">${t.outdoorStale}</p>` : ""}
          </div>
        </div>
        <div class="metrics">
          <div class="metric">
            <div class="label">${t.deltaT}</div>
            <div class="value">${fmt(indoor)} / ${fmt(d.outdoor.temperature)} °C
              <span class="muted">(Δ ${deltaT} K)</span></div>
          </div>
          <div class="metric">
            <div class="label">${t.absHumidity}</div>
            <div class="value">${fmt(indoorAh)} / ${fmt(d.outdoor.absolute_humidity)} g/m³</div>
          </div>
          <div class="metric">
            <div class="label">${t.nextTipping}</div>
            <div class="value">${formatTime(nextTipping(d), d.language)}</div>
          </div>
          <div class="metric">
            <div class="label">${t.mode}</div>
            <div class="value">
              <select id="mode-select">
                ${modes
                  .map(
                    (m) =>
                      `<option value="${m}" ${m === d.mode ? "selected" : ""}>${m}</option>`
                  )
                  .join("")}
              </select>
            </div>
          </div>
        </div>
      </div>

      <div class="card">
        <h2>${t.bestWindow}</h2>
        <p class="sub">${bestWindowText(d, t)}</p>
        ${this._renderTimeline()}
      </div>

      <div class="card">
        <h2>${t.windows}</h2>
        ${this._renderWindowList()}
      </div>`;
  }

  /**
   * 24 h timeline: coloured quality bars plus the predicted indoor curve.
   * Pure SVG on a 0..1000 viewBox so it scales to any panel width.
   */
  _renderTimeline() {
    const slots = (this._data.schedule && this._data.schedule.slots) || [];
    if (!slots.length) return `<p class="empty">–</p>`;

    const W = 1000;
    const H = 150;
    const barTop = 18;
    const barHeight = 26;
    const curveTop = 58;
    const curveHeight = 62;
    const step = W / slots.length;

    const temps = [];
    slots.forEach((s) => {
      if (s.indoor !== null && s.indoor !== undefined) temps.push(s.indoor);
      if (s.outdoor !== null && s.outdoor !== undefined) temps.push(s.outdoor);
    });
    const min = Math.min(...temps) - 1;
    const max = Math.max(...temps) + 1;
    const scale = (v) =>
      curveTop + curveHeight - ((v - min) / Math.max(max - min, 0.1)) * curveHeight;

    const bars = slots
      .map((slot, i) => {
        const colour = QUALITY_COLOUR[slot.quality] || QUALITY_COLOUR.blocked;
        const title = `${formatTime(slot.start, this._data.language)} · ${slot.quality} · ${
          slot.outdoor
        } °C${slot.delta_k ? ` · −${slot.delta_k} K` : ""}`;
        return `<rect x="${i * step}" y="${barTop}" width="${step - 1}" height="${barHeight}"
                  rx="3" fill="${colour}"><title>${escapeHtml(title)}</title></rect>`;
      })
      .join("");

    const line = (key, colour, dash) => {
      const points = slots
        .map((slot, i) => {
          const value = slot[key];
          if (value === null || value === undefined) return null;
          return `${(i + 0.5) * step},${scale(value)}`;
        })
        .filter(Boolean)
        .join(" ");
      return points
        ? `<polyline points="${points}" fill="none" stroke="${colour}" stroke-width="2.5"
             stroke-dasharray="${dash}" stroke-linejoin="round" />`
        : "";
    };

    const hours = slots
      .map((slot, i) => {
        const date = new Date(slot.start);
        if (date.getHours() % 3 !== 0) return "";
        return `<text x="${i * step}" y="${H - 4}" font-size="11"
                  fill="var(--secondary-text-color)">${String(date.getHours()).padStart(2, "0")}</text>`;
      })
      .join("");

    // "now" marker: the first slot that contains the current time.
    const nowIndex = slots.findIndex(
      (s) => new Date(s.start) <= new Date(this._data.now) && new Date(s.end) > new Date(this._data.now)
    );
    const marker =
      nowIndex >= 0
        ? `<line x1="${(nowIndex + 0.5) * step}" y1="8" x2="${(nowIndex + 0.5) * step}" y2="${
            curveTop + curveHeight + 6
          }" stroke="var(--primary-color)" stroke-width="2" />
           <text x="${(nowIndex + 0.5) * step + 5}" y="14" font-size="11"
             fill="var(--primary-color)">now</text>`
        : "";

    const t = this.t;
    return `
      <svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img">
        ${bars}
        ${line("indoor", "var(--primary-color)", "0")}
        ${line("outdoor", "var(--secondary-text-color)", "5 4")}
        ${marker}
        ${hours}
      </svg>
      <div class="legend">
        <span><i style="background:var(--av-good)"></i>good</span>
        <span><i style="background:var(--av-fair)"></i>fair</span>
        <span><i style="background:var(--av-bad)"></i>closed</span>
        <span><i style="background:var(--av-blocked)"></i>blocked</span>
        <span><i style="background:var(--primary-color)"></i>indoor (predicted)</span>
        <span><i style="background:var(--secondary-text-color)"></i>outdoor</span>
      </div>`;
  }

  _renderWindowList() {
    const t = this.t;
    const windows = this._data.windows || [];
    if (!windows.length) return `<p class="empty">–</p>`;

    return `<table>
      <thead><tr>
        <th>${t.windows}</th><th>Ist</th><th>Soll</th><th>${t.why}</th><th></th>
      </tr></thead>
      <tbody>
      ${windows
        .map((w) => {
          const rec = w.recommendation || {};
          // No "Done"/"Snooze" on a row whose advice is "nothing to do" -
          // there is nothing to acknowledge and it just adds noise.
          const buttons = rec.recommendation_id && w.action !== "no_action"
            ? `<button class="action" data-ack="${escapeAttr(rec.recommendation_id)}">${t.done}</button>
               <button class="action" data-snooze="${escapeAttr(rec.recommendation_id)}">${t.snooze}</button>
               <button class="action" data-ignore="${escapeAttr(rec.recommendation_id)}">${t.ignore}</button>`
            : "";
          const cover = w.cover_action
            ? `<div class="reason">▤ ${escapeHtml(
                (w.cover_recommendation && w.cover_recommendation.reason) || w.cover_action
              )}</div>`
            : "";
          return `<tr>
            <td>${escapeHtml(w.name)}<div class="reason">${Math.round(w.azimuth)}° · ${
            w.solar_load ? `${Math.round(w.solar_load)} W` : "–"
          }</div></td>
            <td>${w.is_open ? (w.is_tilted ? "◲" : "🪟") : "🔒"}</td>
            <td>${ACTION_ICON[w.action] || ""} ${escapeHtml(actionLabel(w.action, this._data.language))}</td>
            <td class="reason">${escapeHtml(rec.reason || "–")}${cover}</td>
            <td>${buttons}</td>
          </tr>`;
        })
        .join("")}
      </tbody></table>`;
  }

  // -- Tab 2: Rooms --------------------------------------------------

  _renderRooms() {
    const t = this.t;
    const rooms = this._data.rooms || [];
    if (!rooms.length) return `<div class="card"><p class="empty">–</p></div>`;

    const rows = rooms
      .map((room) => {
        const badge =
          room.confidence !== null && room.confidence < 1
            ? `<span class="pill warn" title="${escapeAttr(room.estimation_method || "")}">${t.estimated} ${Math.round(
                room.confidence * 100
              )}%</span>`
            : "";
        const mold =
          room.mold_risk && room.mold_risk !== "none"
            ? `<span class="pill ${room.mold_risk === "low" ? "" : "bad"}">${room.mold_risk}</span>`
            : "";
        const detail =
          this._expandedRoom === room.id
            ? `<tr class="detail"><td colspan="8">${this._renderRoomDetail(room)}</td></tr>`
            : "";
        return `<tr class="row-click" data-room="${escapeAttr(room.id)}">
            <td>${escapeHtml(room.name)} ${badge} ${mold}</td>
            <td class="num">${fmt(room.temperature)} °C</td>
            <td class="num">${fmt(room.humidity, 0)} %</td>
            <td class="num">${fmt(room.absolute_humidity)} g/m³</td>
            <td class="num">${room.co2 === null || room.co2 === undefined ? "–" : room.co2}</td>
            <td class="num">${fmt(room.pm25)}</td>
            <td class="num">${signed(room.heating_rate)} K/h</td>
            <td class="num">${this._scoreBar(room.air_quality_score)}</td>
          </tr>${detail}`;
      })
      .join("");

    return `<div class="card">
      <table>
        <thead><tr>
          <th>${t.room}</th><th class="num">${t.temperature}</th><th class="num">${t.humidity}</th>
          <th class="num">g/m³</th><th class="num">${t.co2}</th><th class="num">${t.pm25}</th>
          <th class="num">${t.trend}</th><th class="num">${t.score}</th>
        </tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>`;
  }

  _renderRoomDetail(room) {
    const t = this.t;
    const recommendations = (this._data.recommendations || []).filter(
      (r) => r.room_id === room.id
    );
    const breakdown = room.score_breakdown || {};
    return `
      <div class="grid2">
        <div>
          <h2>${t.why}</h2>
          ${
            recommendations.length
              ? `<ul>${recommendations
                  .map(
                    (r) =>
                      `<li>${escapeHtml(actionLabel(r.action, this._data.language))} — ${escapeHtml(
                        r.reason
                      )} <span class="muted">(${r.priority}, ${r.urgency})</span></li>`
                  )
                  .join("")}</ul>`
              : `<p class="empty">${t.noRecommendations}</p>`
          }
        </div>
        <div>
          <h2>${t.score}</h2>
          ${Object.entries(breakdown)
            .map(
              ([axis, value]) =>
                `<div class="slider-row"><span>${axis}</span>
                   <div class="bar"><span style="width:${value}%;background:${scoreColour(
                  value
                )}"></span></div><span class="num">${value}</span></div>`
            )
            .join("")}
          <p class="reason">
            ${t.achievable}: ${fmt(room.night_potential_k)} K ·
            ${room.surface_temperature !== null ? `Wand ${fmt(room.surface_temperature)} °C ·` : ""}
            ${room.projected !== null ? `→ ${fmt(room.projected)} °C` : ""}
          </p>
          <button class="action" data-purge="${escapeAttr(room.id)}">${t.purge}</button>
        </div>
      </div>`;
  }

  _scoreBar(score) {
    if (score === null || score === undefined) return "–";
    return `<div class="bar" title="${score}"><span style="width:${score}%;background:${scoreColour(
      score
    )}"></span></div>`;
  }

  // -- Tab 3: Tuning -------------------------------------------------

  _renderTuning() {
    const t = this.t;
    const prefs = { ...(this._data.preferences || {}), ...this._pendingPreferences };

    const slider = (key, label, min, max, step, unit) => `
      <div class="slider-row">
        <span>${label}</span>
        <input type="range" data-pref="${key}" min="${min}" max="${max}" step="${step}"
               value="${prefs[key]}" />
        <span class="num">${prefs[key]}${unit || ""}</span>
      </div>`;

    const dirty = Object.keys(this._pendingPreferences).length > 0;
    let preview;
    if (this._preview) {
      const now = this._preview.current;
      const next = this._preview.proposed;
      const shown = dirty ? next : now;
      const delta = dirty
        ? `<span class="muted">${t.instead} ${now.notifications}</span>`
        : "";
      preview = `
        <p><strong>${shown.notifications} ${t.notifications}</strong> ${delta}</p>
        <p>${shown.recommendations} ${t.recommendations}</p>
        <p class="reason">${escapeHtml(shown.rules.join(", ") || "-")}</p>
        <p class="reason">${t.previewHint}</p>`;
    } else if (this._previewError) {
      preview = `<p class="empty">${escapeHtml(this._previewError)}</p>`;
    } else {
      preview = `<p class="empty">${t.working}</p>`;
    }

    return `<div class="grid2">
      <div>
        <div class="card">
          <h2>${t.profile}</h2>
          <p class="sub">${t.profileHint}</p>
          <select id="profile-select">
            ${[
              ["quiet", t.profileQuiet],
              ["balanced", t.profileBalanced],
              ["eager", t.profileEager],
            ]
              .map(
                ([id, label]) =>
                  `<option value="${id}" ${
                    (this._data.preferences || {}).profile === id ? "selected" : ""
                  }>${label}</option>`
              )
              .join("")}
          </select>
          <p class="reason" style="margin-top:10px">
            ${((this._data.preferences || {}).changed_from_default || []).length} ${t.changed}
          </p>
          <button class="action" id="reset-all">${t.resetAll}</button>
          <p class="reason">${t.resetHint}</p>
        </div>
        <div class="card">
          <h2>${t.weights}</h2>
          ${slider("weight_temperature", "Temperatur", 0, 100, 5, "")}
          ${slider("weight_humidity", "Feuchte", 0, 100, 5, "")}
          ${slider("weight_co2", "CO₂", 0, 100, 5, "")}
          ${slider("weight_particulate", "Feinstaub", 0, 100, 5, "")}
          ${slider("notification_restraint", "Zurückhaltung", 0, 100, 5, "")}
        </div>
        <div class="card">
          <h2>${t.thresholds}</h2>
          ${slider("summer_target_min", "Sommer min", 15, 28, 0.5, " °C")}
          ${slider("summer_target_max", "Sommer max", 18, 32, 0.5, " °C")}
          ${slider("winter_target_min", "Winter min", 14, 24, 0.5, " °C")}
          ${slider("winter_target_max", "Winter max", 16, 28, 0.5, " °C")}
          ${slider("co2_threshold", "CO₂", 600, 2000, 50, " ppm")}
          ${slider("pm25_indoor_threshold", "PM2.5 innen", 5, 100, 1, " µg/m³")}
        </div>
        <div class="card">
          <h2>${t.calmness}</h2>
          ${slider("min_state_duration_minutes", "Mindestdauer", 5, 120, 5, " min")}
          ${slider("cooldown_minutes", "Cooldown", 10, 240, 10, " min")}
          ${slider("max_pushes_per_day", "Max. Pushes", 0, 30, 1, "")}
        </div>
      </div>
      <div>
        <div class="card">
          <h2>${t.preview}</h2>
          ${preview}
          <button class="action" id="apply-prefs">${t.apply}</button>
        </div>
        <div class="card">
          <h2>${t.learned}</h2>
          ${this._renderLearned()}
          <button class="action" id="recalibrate">${t.recalibrate}</button>
        </div>
      </div>
    </div>`;
  }

  _renderLearned() {
    const t = this.t;
    const learned = this._data.learned || {};
    const meta = this._data.calibration || {};
    const rooms = this._data.rooms || [];
    const entries = Object.entries(learned);

    const footer = `<p class="reason">${
      meta.last_run ? `${t.calibLastRun}: ${formatTime(meta.last_run, this._data.language)}` : ""
    }${
      meta.rooms_total
        ? ` · ${t.calibRooms
            .replace("{learned}", meta.rooms_learned ?? 0)
            .replace("{total}", meta.rooms_total)
            .replace("{inUse}", meta.rooms_in_use ?? 0)}`
        : ""
    }</p>`;

    if (!entries.length) {
      const why =
        {
          never_run: t.calibNever,
          no_recorder: t.calibNoRecorder,
          no_history: t.calibNoHistory,
          not_enough_data: t.calibNotEnough.replace("{min}", meta.min_samples ?? 3),
          ok: t.calibNotEnough.replace("{min}", meta.min_samples ?? 3),
        }[meta.status || "never_run"] || t.calibNever;
      return `<p class="empty">${escapeHtml(why)}</p>${footer}`;
    }

    return `<table><thead><tr>
        <th>${t.room}</th><th class="num">τ</th><th class="num">K/h</th>
        <th class="num">${t.samples}</th><th></th>
      </tr></thead><tbody>
      ${entries
        .map(([roomId, values]) => {
          const room = rooms.find((r) => r.id === roomId);
          return `<tr>
            <td>${escapeHtml(room ? room.name : roomId)}
              ${values.in_use ? "" : `<span class="pill">${t.confidence} ${Math.round(values.confidence * 100)}%</span>`}
            </td>
            <td class="num">${fmt(values.tau_hours)}</td>
            <td class="num">${fmt(values.night_cooling_k_per_h, 2)}</td>
            <td class="num">${values.samples}</td>
            <td><button class="action" data-reset-room="${escapeAttr(roomId)}">${t.reset}</button></td>
          </tr>`;
        })
        .join("")}
      </tbody></table>${footer}`;
  }

  // -- Tab 4: Balance ------------------------------------------------

  _renderBalance() {
    const t = this.t;
    const budget = this._data.cooling_budget || {};
    const history = budget.history || [];

    return `
      <div class="card">
        <h2>${t.coolingBudget}</h2>
        <div class="metrics">
          <div class="metric"><div class="label">${t.achievable}</div>
            <div class="value">${fmt(budget.achievable_tonight_k)} K</div></div>
          <div class="metric"><div class="label">${t.required}</div>
            <div class="value">${fmt(budget.required_tonight_k)} K</div></div>
          <div class="metric"><div class="label">${t.storage}</div>
            <div class="value">${fmt(budget.storage_k)} K</div></div>
          <div class="metric"><div class="label">${t.balance3d}</div>
            <div class="value">${signed(budget.balance_3d)} K</div></div>
        </div>
        <p class="sub" style="margin-top:14px">${escapeHtml(verdictText(budget, this._data.language))}</p>
        ${
          history.length
            ? `<table><thead><tr><th>Tag</th><th class="num">Netto</th></tr></thead><tbody>
                 ${history
                   .map(
                     (h) =>
                       `<tr><td>${escapeHtml(h.day)}</td><td class="num">${signed(h.net_k)} K</td></tr>`
                   )
                   .join("")}
               </tbody></table>`
            : ""
        }
      </div>

      <div class="card">
        <h2>${t.weakSpots}</h2>
        <p class="sub">${t.weakSpotsHint}</p>
        <table><thead><tr>
          <th>${t.windows}</th><th>${t.room}</th><th class="num">kWh/d</th>
          <th class="num">K/d</th><th class="num">${t.avoidable}</th><th></th>
        </tr></thead><tbody>
        ${(this._data.weak_spots || [])
          .map(
            (spot) => `<tr>
              <td>${escapeHtml(spot.window)}</td>
              <td>${escapeHtml(spot.room)}</td>
              <td class="num">${fmt(spot.daily_kwh, 2)}</td>
              <td class="num">${fmt(spot.daily_k, 2)}</td>
              <td class="num">${spot.has_cover ? `−${fmt(spot.avoidable_k, 2)}` : "–"}</td>
              <td>${
                spot.has_cover
                  ? `<span class="pill">${spot.cover_external ? t.external : t.internal}</span>`
                  : `<span class="pill bad">${t.noCover}</span>`
              }</td>
            </tr>`
          )
          .join("")}
        </tbody></table>
      </div>

      <div class="card">
        <h2>${t.sensorSuggestions}</h2>
        ${
          (this._data.sensor_suggestions || []).length
            ? `<ul>${this._data.sensor_suggestions
                .map(
                  (s) =>
                    `<li>${escapeHtml(s.room)}: ${escapeHtml(s.missing.join(", "))}
                       <span class="muted">(${s.impact})</span></li>`
                )
                .join("")}</ul>`
            : `<p class="empty">–</p>`
        }
      </div>`;
  }

  // ------------------------------------------------------------------
  // Event wiring
  // ------------------------------------------------------------------

  _bind() {
    const root = this.shadowRoot;

    root.querySelectorAll(".tab").forEach((button) => {
      button.addEventListener("click", () => {
        this._tab = button.dataset.tab;
        this._render();
      });
    });

    const modeSelect = root.getElementById("mode-select");
    if (modeSelect) {
      modeSelect.addEventListener("change", (event) =>
        this._act({ action: "set_mode", mode: event.target.value })
      );
    }

    root.querySelectorAll("[data-ack]").forEach((b) =>
      b.addEventListener("click", () =>
        this._act({ action: "acknowledge", recommendation_id: b.dataset.ack })
      )
    );
    root.querySelectorAll("[data-snooze]").forEach((b) =>
      b.addEventListener("click", () =>
        this._act({ action: "snooze", recommendation_id: b.dataset.snooze })
      )
    );
    root.querySelectorAll("[data-ignore]").forEach((b) =>
      b.addEventListener("click", () =>
        this._act({ action: "ignore_today", recommendation_id: b.dataset.ignore })
      )
    );
    root.querySelectorAll("[data-purge]").forEach((b) =>
      b.addEventListener("click", (event) => {
        event.stopPropagation();
        this._act({ action: "purge", room_id: b.dataset.purge });
      })
    );
    root.querySelectorAll("[data-reset-room]").forEach((b) =>
      b.addEventListener("click", () =>
        this._act({
          action: "override",
          room_id: b.dataset.resetRoom,
          parameter: "tau_hours",
          reset: true,
        })
      )
    );

    root.querySelectorAll("[data-room]").forEach((row) =>
      row.addEventListener("click", () => {
        this._expandedRoom = this._expandedRoom === row.dataset.room ? null : row.dataset.room;
        this._render();
      })
    );

    root.querySelectorAll("[data-pref]").forEach((input) => {
      input.addEventListener("input", (event) => {
        const value = Number(event.target.value);
        this._pendingPreferences[input.dataset.pref] = value;
        const label = input.nextElementSibling;
        if (label) label.textContent = String(value);
      });
      input.addEventListener("change", () => this._requestPreview());
    });

    const apply = root.getElementById("apply-prefs");
    if (apply) {
      apply.addEventListener("click", async () => {
        const pending = { ...this._pendingPreferences };
        this._pendingPreferences = {};
        this._preview = null;
        for (const [option, value] of Object.entries(pending)) {
          await this._act({ action: "set_option", option, value });
        }
        this._previewPending = true;
        await this._requestPreview();
      });
    }

    const profileSelect = root.getElementById("profile-select");
    if (profileSelect) {
      profileSelect.addEventListener("change", (event) =>
        this._act({ action: "set_profile", value: event.target.value })
      );
    }

    const resetAll = root.getElementById("reset-all");
    if (resetAll) {
      resetAll.addEventListener("click", async () => {
        this._pendingPreferences = {};
        this._preview = null;
        this._previewPending = true;
        await this._act({ action: "reset_tuning" });
      });
    }

    const recalibrate = root.getElementById("recalibrate");
    if (recalibrate) {
      recalibrate.addEventListener("click", async () => {
        recalibrate.textContent = this.t.working;
        recalibrate.disabled = true;
        await this._act({ action: "recalibrate" });
      });
    }
  }
}

// ----------------------------------------------------------------------
// Small helpers
// ----------------------------------------------------------------------

function fmt(value, digits = 1) {
  if (value === null || value === undefined || Number.isNaN(value)) return "–";
  return Number(value).toFixed(digits);
}

function signed(value, digits = 1) {
  if (value === null || value === undefined) return "–";
  const number = Number(value);
  return `${number > 0 ? "+" : ""}${number.toFixed(digits)}`;
}

function average(values) {
  const usable = values.filter((v) => v !== null && v !== undefined);
  if (!usable.length) return null;
  return usable.reduce((a, b) => a + b, 0) / usable.length;
}

function scoreColour(score) {
  if (score >= 75) return "var(--av-good)";
  if (score >= 45) return "var(--av-fair)";
  return "var(--av-bad)";
}

function formatTime(iso, language) {
  if (!iso) return "–";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "–";
  return date.toLocaleTimeString(language === "de" ? "de-DE" : "en-GB", {
    hour: "2-digit",
    minute: "2-digit",
  });
}

function formatCountdown(minutes, language) {
  const hours = Math.floor(minutes / 60);
  const rest = Math.round(minutes % 60);
  const unit = language === "de" ? "Min" : "min";
  return hours ? `${hours} h ${rest} ${unit}` : `${rest} ${unit}`;
}

function nextTipping(data) {
  const tp = data.tipping_points || {};
  const candidates = [tp.morning, tp.evening].filter(Boolean).sort();
  return candidates.length ? candidates[0] : null;
}

function bestWindowText(data, t) {
  const schedule = data.schedule || {};
  if (!schedule.best_start) return "–";
  // Summer counts Kelvin of cooling, winter counts grams of water removed.
  const value =
    schedule.metric === "grams"
      ? `${fmt(schedule.best_delta_k)} g/m³`
      : `−${fmt(schedule.best_delta_k)} K`;
  return `${formatTime(schedule.best_start, data.language)} – ${formatTime(
    schedule.best_end,
    data.language
  )}, ${t.expected} ${value}`;
}

function statusLabel(status, language) {
  const labels = {
    en: {
      idle: "Nothing to do",
      ventilate_now: "Ventilate now",
      keep_closed: "Keep closed",
      heat_protection: "Heat protection",
      night_flush: "Night flush",
      purge_running: "Purge running",
      storm: "Storm",
      air_quality: "Air quality",
      away: "Away",
      off: "Off",
      unavailable_data: "No usable data",
    },
    de: {
      idle: "Nichts zu tun",
      ventilate_now: "Jetzt lüften",
      keep_closed: "Geschlossen halten",
      heat_protection: "Hitzeschutz",
      night_flush: "Nachtlüftung",
      purge_running: "Stoßlüften läuft",
      storm: "Sturm",
      air_quality: "Luftqualität",
      away: "Abwesend",
      off: "Aus",
      unavailable_data: "Keine brauchbaren Daten",
    },
  };
  const set = labels[language] || labels.en;
  return set[status] || status;
}

function actionLabel(action, language) {
  const labels = {
    en: {
      open_wide: "Open wide",
      open_tilt: "Tilt",
      purge: "Purge",
      cross_ventilate: "Cross ventilate",
      close: "Close",
      keep_closed: "Keep closed",
      keep_open: "Keep open",
      cover_down: "Shutter down",
      cover_up: "Shutter up",
      cover_slat: "Shutter partly",
      fan_on: "Fan on",
      no_action: "–",
    },
    de: {
      open_wide: "Weit öffnen",
      open_tilt: "Kippen",
      purge: "Stoßlüften",
      cross_ventilate: "Querlüften",
      close: "Schließen",
      keep_closed: "Zu lassen",
      keep_open: "Offen lassen",
      cover_down: "Rollladen runter",
      cover_up: "Rollladen hoch",
      cover_slat: "Rollladen teilweise",
      fan_on: "Ventilator an",
      no_action: "–",
    },
  };
  const set = labels[language] || labels.en;
  return set[action] || action;
}

function verdictText(budget, language) {
  const texts = {
    en: {
      sufficient: "Tonight is enough to get back into the comfort band.",
      tight: "It will be tight tonight - ventilate from the first cool hour.",
      precool_needed: "A heat peak is coming. Pre-cool now, before it hurts.",
      insufficient:
        "Night flushing alone will not carry this. Consider a fan, a different bedroom, or writing off one room.",
      not_applicable: "Not a summer situation.",
      unknown: "Not enough data yet.",
    },
    de: {
      sufficient: "Heute Nacht reicht es, um wieder ins Wohlfühlband zu kommen.",
      tight: "Heute Nacht wird es knapp - ab der ersten kühlen Stunde lüften.",
      precool_needed: "Eine Hitzespitze kommt. Jetzt vorkühlen, bevor es weh tut.",
      insufficient:
        "Nachtlüften allein trägt das nicht mehr. Ventilator, anderes Schlafzimmer oder einen Raum aufgeben.",
      not_applicable: "Keine Sommerlage.",
      unknown: "Noch zu wenig Daten.",
    },
  };
  const set = texts[language] || texts.en;
  return set[budget.verdict] || "";
}

function escapeHtml(value) {
  return String(value === null || value === undefined ? "" : value).replace(
    /[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]
  );
}

function escapeAttr(value) {
  return escapeHtml(value).replace(/`/g, "&#96;");
}

if (!customElements.get("adaptive-ventilation-panel")) {
  customElements.define("adaptive-ventilation-panel", AdaptiveVentilationPanel);
}
