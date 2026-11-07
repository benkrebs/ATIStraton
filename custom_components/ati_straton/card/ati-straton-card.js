/*
 * ATI Straton – Dashboard-Karte
 *
 * Tagesverlauf der Intensität samt Bedienung und Temperaturen auf einen Blick.
 * Wird von der Integration selbst ausgeliefert und angemeldet; ein Eintrag
 * unter Dashboard-Ressourcen ist nicht nötig.
 *
 *   type: custom:ati-straton-card
 *   device_id: <optional, bei mehreren Geräten>
 *   title: <optional>
 *   smooth: false      # Kurve geglättet statt linear zeichnen
 *   full_day: false    # immer 00:00–24:00 statt des beleuchteten Fensters
 *
 * Bewusst ohne Build-Schritt und ohne Fremdbibliotheken: Die Datei muss so,
 * wie sie im Repository liegt, im Browser laufen.
 */

const CARD_TAG = "ati-straton-card";
const DOMAIN = "ati_straton";
const DAY = 86400;

const I18N = {
  de: {
    connected: "Lokal verbunden",
    unreachable: "Nicht erreichbar",
    pushOk: "Push aktiv",
    pushLost: "Push unterbrochen",
    dailyCurve: "Tagesverlauf",
    dailyCurveSub: "Form bleibt erhalten, nur die Helligkeit skaliert",
    original: "Original (100 %)",
    intensity: "Intensität",
    preview: "Vorschau",
    now: "Jetzt",
    colors: "Farben im Tagesverlauf",
    from: "ab",
    lighting: "Beleuchtung",
    lightingSub: "Tagesverlauf bleibt erhalten",
    program: "Lichtprogramm",
    level: "Gewöhnungsstufe",
    levelHint: "wirkt beim nächsten Programmwechsel",
    module: "Modul",
    online: "online",
    offline: "offline",
    noData: "keine Daten",
    guard: "Temperaturwächter",
    reducesAt: "senkt ab {v} °C",
    releasesBelow: "gibt frei unter {v} °C",
    load: "Auslastung",
    mode: "Betriebsmodus",
    locked: "Gesperrt, solange der Temperaturwächter regelt",
    guardStates: {
      disabled: "Aus",
      idle: "Bereit",
      reducing: "Senkt ab",
      holding: "Hält Absenkung",
      recovering: "Gibt frei",
    },
    modes: { normal: "normal", manual_intensity: "manuell", guard: "Wächter" },
    confirmProgram:
      "„{p}“ laden?\n\nDas überschreibt den Tagesverlauf auf dem Gerät. Eine Sicherung wird vorher angelegt.",
    noDevice: "Kein ATI-Straton-Gerät gefunden.",
    noCurve: "Kein Tagesverlauf vorhanden",
    cfgDevice: "Gerät",
    cfgTitle: "Titel",
    cfgSmooth: "Kurve glätten",
    cfgFullDay: "Ganzen Tag zeigen",
  },
  en: {
    connected: "Connected locally",
    unreachable: "Unreachable",
    pushOk: "push active",
    pushLost: "push interrupted",
    dailyCurve: "Daily curve",
    dailyCurveSub: "Shape is kept, only brightness scales",
    original: "Original (100 %)",
    intensity: "Intensity",
    preview: "Preview",
    now: "Now",
    colors: "Colours in the daily curve",
    from: "from",
    lighting: "Lighting",
    lightingSub: "Daily curve is kept",
    program: "Light program",
    level: "Acclimatisation",
    levelHint: "applies on the next program change",
    module: "Module",
    online: "online",
    offline: "offline",
    noData: "no data",
    guard: "Temperature guard",
    reducesAt: "reduces at {v} °C",
    releasesBelow: "releases below {v} °C",
    load: "Load",
    mode: "Mode",
    locked: "Locked while the temperature guard is active",
    guardStates: {
      disabled: "Off",
      idle: "Ready",
      reducing: "Reducing",
      holding: "Holding",
      recovering: "Recovering",
    },
    modes: { normal: "normal", manual_intensity: "manual", guard: "guard" },
    confirmProgram:
      "Load “{p}”?\n\nThis overwrites the daily curve on the device. A backup is made first.",
    noDevice: "No ATI Straton device found.",
    noCurve: "No daily curve available",
    cfgDevice: "Device",
    cfgTitle: "Title",
    cfgSmooth: "Smooth curve",
    cfgFullDay: "Show full day",
  },
};

/** Zuordnung Kartenrolle → translation_key der Integration. */
const ROLES = {
  power: ["switch", "power"],
  intensity: ["number", "intensity"],
  program: ["select", "program"],
  level: ["select", "acclimatisation"],
  now: ["sensor", "current_intensity"],
  mode: ["sensor", "mode"],
  guard: ["switch", "temperature_guard"],
  guardState: ["sensor", "guard_state"],
  guardReduction: ["sensor", "guard_reduction"],
  guardMax: ["number", "guard_max_temp"],
  guardLow: ["number", "guard_low_temp"],
  load: ["sensor", "current_load"],
};

const UNAVAILABLE = new Set(["unavailable", "unknown"]);
/** Wie spectrum.NEUTRAL: Farbe eines Knotens mit dunklem Spektrum. */
const NEUTRAL = "#808080";

const esc = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c],
  );

const num = (value) => {
  const n = typeof value === "number" ? value : parseFloat(value);
  return Number.isFinite(n) ? n : null;
};

const hhmm = (seconds) => {
  const s = Math.round(seconds) % DAY;
  return `${String(Math.floor(s / 3600)).padStart(2, "0")}:${String(Math.floor((s % 3600) / 60)).padStart(2, "0")}`;
};

/* ------------------------------------------------------------------ Kurven */

/** Lineare Interpolation mit gehaltenen Randwerten – wie das Gerät rechnet. */
function interpolate(points, t) {
  if (!points.length) return null;
  if (t <= points[0][0]) return points[0][1];
  const last = points[points.length - 1];
  if (t >= last[0]) return last[1];
  for (let i = 0; i < points.length - 1; i++) {
    const [t0, v0] = points[i];
    const [t1, v1] = points[i + 1];
    if (t0 <= t && t <= t1) return t1 === t0 ? v1 : v0 + ((v1 - v0) * (t - t0)) / (t1 - t0);
  }
  return last[1];
}

/** Ausschnitt [from, to] mit interpolierten Randpunkten. */
function clip(points, from, to) {
  if (!points.length) return [];
  const inner = points.filter(([t]) => t > from && t < to);
  return [[from, interpolate(points, from)], ...inner, [to, interpolate(points, to)]];
}

/**
 * Kurve durch Stützpunkte, linear oder monoton kubisch (Fritsch–Carlson).
 * Die monotone Variante überschwingt nicht: Ein Plateau bleibt ein Plateau und
 * die Kurve sinkt nie unter null.
 */
class Curve {
  constructor(points, smooth) {
    this.p = points;
    this.smooth = smooth && points.length > 2;
    if (this.smooth) this.m = Curve.tangents(points);
  }

  static tangents(p) {
    const n = p.length;
    const slope = [];
    for (let i = 0; i < n - 1; i++) {
      const dx = p[i + 1][0] - p[i][0];
      slope.push(dx ? (p[i + 1][1] - p[i][1]) / dx : 0);
    }
    const m = new Array(n).fill(0);
    m[0] = slope[0];
    m[n - 1] = slope[n - 2];
    for (let i = 1; i < n - 1; i++) {
      const a = slope[i - 1];
      const b = slope[i];
      const h0 = p[i][0] - p[i - 1][0];
      const h1 = p[i + 1][0] - p[i][0];
      if (a * b <= 0 || !h0 || !h1) continue;
      m[i] = (3 * (h0 + h1)) / ((2 * h1 + h0) / a + (h1 + 2 * h0) / b);
    }
    return m;
  }

  value(t) {
    const p = this.p;
    if (!this.smooth) return interpolate(p, t);
    if (t <= p[0][0]) return p[0][1];
    for (let i = 0; i < p.length - 1; i++) {
      const [t0, v0] = p[i];
      const [t1, v1] = p[i + 1];
      if (t > t1) continue;
      const h = t1 - t0;
      if (!h) return v1;
      const s = (t - t0) / h;
      const s2 = s * s;
      const s3 = s2 * s;
      return (
        (2 * s3 - 3 * s2 + 1) * v0 +
        (s3 - 2 * s2 + s) * h * this.m[i] +
        (-2 * s3 + 3 * s2) * v1 +
        (s3 - s2) * h * this.m[i + 1]
      );
    }
    return p[p.length - 1][1];
  }

  path(X, Y) {
    const p = this.p;
    if (!p.length) return "";
    let d = `M${X(p[0][0]).toFixed(1)},${Y(p[0][1]).toFixed(1)}`;
    for (let i = 1; i < p.length; i++) {
      const [t1, v1] = p[i];
      if (!this.smooth) {
        d += `L${X(t1).toFixed(1)},${Y(v1).toFixed(1)}`;
        continue;
      }
      const [t0, v0] = p[i - 1];
      const h = (t1 - t0) / 3;
      d +=
        `C${X(t0 + h).toFixed(1)},${Y(v0 + this.m[i - 1] * h).toFixed(1)} ` +
        `${X(t1 - h).toFixed(1)},${Y(v1 - this.m[i] * h).toFixed(1)} ` +
        `${X(t1).toFixed(1)},${Y(v1).toFixed(1)}`;
    }
    return d;
  }
}

/* ------------------------------------------------------------------- Karte */

const STYLE = `
  :host {
    --asc-text: #eaf4f8;
    --asc-muted: rgba(234, 244, 248, 0.62);
    --asc-faint: rgba(234, 244, 248, 0.38);
    --asc-tile: rgba(255, 255, 255, 0.06);
    --asc-tile-border: rgba(255, 255, 255, 0.13);
    --asc-cyan: #7fe3e6;
    --asc-violet: #9d7cf2;
    --asc-now: #f2846e;
    --asc-ok: #7ee2a8;
    --asc-warn: #f5c26b;
    --asc-bg: linear-gradient(155deg, #33627f 0%, #22496a 48%, #183754 100%);
  }
  ha-card {
    display: block;
    background: var(--asc-bg);
    color: var(--asc-text);
    border-radius: var(--ha-card-border-radius, 24px);
    border: 1px solid var(--asc-tile-border);
    padding: 22px;
    box-sizing: border-box;
    container-type: inline-size;
    font-family: var(--ha-font-family-body, var(--paper-font-body1_-_font-family, inherit));
  }
  .header { display: flex; align-items: center; gap: 14px; margin-bottom: 18px; }
  .icon-btn {
    width: 46px; height: 46px; border-radius: 50%; flex: none;
    display: grid; place-items: center; cursor: pointer;
    background: var(--asc-tile); border: 1px solid var(--asc-tile-border);
    color: var(--asc-cyan); --mdc-icon-size: 24px;
  }
  .title { font-size: 1.45rem; font-weight: 400; line-height: 1.2; }
  .status { display: flex; align-items: center; gap: 7px; font-size: 0.85rem; color: var(--asc-muted); margin-top: 3px; }
  .dot { width: 9px; height: 9px; border-radius: 50%; background: var(--asc-ok); flex: none; }
  .dot.warn { background: var(--asc-warn); }
  .dot.bad { background: var(--asc-now); }

  .layout { display: grid; grid-template-columns: minmax(0, 1.25fr) minmax(0, 1fr); gap: 16px; }
  @container (max-width: 720px) { .layout { grid-template-columns: minmax(0, 1fr); } }

  .tile {
    background: var(--asc-tile); border: 1px solid var(--asc-tile-border);
    border-radius: 18px; padding: 16px 18px; box-sizing: border-box;
  }
  .tile h3 { margin: 0; font-size: 1rem; font-weight: 600; }
  .sub { font-size: 0.8rem; color: var(--asc-muted); margin-top: 4px; }
  .row { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
  .side { display: flex; flex-direction: column; gap: 12px; min-width: 0; }
  .clickable { cursor: pointer; }
  .disabled { opacity: 0.55; }
  .locked { font-size: 0.75rem; color: var(--asc-warn); margin-top: 8px; }

  .chart-tile { display: flex; flex-direction: column; min-height: 340px; }
  .chart { position: relative; flex: 1; min-height: 240px; margin-top: 8px; touch-action: pan-y; }
  @container (max-width: 720px) { .chart { flex: none; aspect-ratio: 16 / 10; } }
  .chart svg { position: absolute; inset: 0; width: 100%; height: 100%; overflow: visible; }
  .chart text { fill: var(--asc-muted); font-size: 11px; font-family: inherit; }
  .chart .empty { fill: var(--asc-faint); font-size: 13px; }
  .legend { display: flex; flex-wrap: wrap; gap: 6px 28px; font-size: 0.78rem; color: var(--asc-muted); margin-top: 6px; }
  .legend span { display: inline-flex; align-items: center; gap: 8px; }
  .sw-dash { width: 18px; border-top: 2px dashed var(--asc-muted); }
  .sw-line { width: 18px; height: 3px; border-radius: 2px; background: linear-gradient(90deg, var(--asc-cyan), var(--asc-violet)); }
  .badge { color: var(--asc-now); }
  .colors { margin-top: 8px; padding-top: 8px; border-top: 1px solid rgba(255, 255, 255, 0.08); }
  .colors:empty { display: none; }
  .sw-dot { width: 10px; height: 10px; border-radius: 50%; border: 1.5px solid rgba(255, 255, 255, 0.85); box-sizing: border-box; }

  .switch {
    position: relative; width: 56px; height: 30px; border-radius: 15px; flex: none;
    background: rgba(255, 255, 255, 0.2); border: none; cursor: pointer; padding: 0;
    transition: background 0.2s;
  }
  .switch::after {
    content: ""; position: absolute; top: 3px; left: 3px; width: 24px; height: 24px;
    border-radius: 50%; background: #fff; transition: transform 0.2s;
    box-shadow: 0 1px 3px rgba(0, 0, 0, 0.3);
  }
  .switch[aria-checked="true"] { background: var(--asc-cyan); }
  .switch[aria-checked="true"]::after { transform: translateX(26px); }
  .switch:disabled { cursor: not-allowed; }

  .value { color: var(--asc-cyan); font-weight: 600; }
  input[type="range"] {
    --pct: 0%;
    -webkit-appearance: none; appearance: none; width: 100%; height: 8px; margin: 18px 0 4px;
    border-radius: 4px; outline: none; cursor: pointer;
    background: linear-gradient(90deg, var(--asc-cyan), var(--asc-violet)) 0 0 / var(--pct) 100% no-repeat,
      rgba(255, 255, 255, 0.18);
  }
  input[type="range"]:disabled { cursor: not-allowed; }
  input[type="range"]::-webkit-slider-thumb {
    -webkit-appearance: none; width: 24px; height: 24px; border-radius: 50%;
    background: #fff; border: 3px solid var(--asc-violet); box-sizing: border-box;
  }
  input[type="range"]::-moz-range-thumb {
    width: 24px; height: 24px; border-radius: 50%;
    background: #fff; border: 3px solid var(--asc-violet); box-sizing: border-box;
  }
  input[type="range"]:focus-visible::-webkit-slider-thumb { box-shadow: 0 0 0 4px rgba(157, 124, 242, 0.4); }

  select {
    width: 100%; margin-top: 12px; padding: 10px 40px 10px 14px; border-radius: 10px;
    font: inherit; font-size: 0.92rem; color: var(--asc-text); color-scheme: dark;
    background: rgba(10, 30, 50, 0.45)
      url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath d='M6 9l6 6 6-6' fill='none' stroke='%23eaf4f8' stroke-width='2.2' stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E")
      no-repeat right 12px center / 20px;
    border: 1px solid var(--asc-tile-border); -webkit-appearance: none; appearance: none; cursor: pointer;
  }
  select:disabled { cursor: not-allowed; }
  select.small { margin-top: 8px; font-size: 0.82rem; padding: 7px 36px 7px 12px; }
  .level-row { display: flex; align-items: center; gap: 10px; }
  .level-row select { flex: 1; }

  .modules { display: grid; grid-template-columns: repeat(auto-fit, minmax(120px, 1fr)); gap: 12px; }
  .module .name { font-size: 0.82rem; color: var(--asc-muted); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .module .temp { font-size: 1.9rem; font-weight: 300; margin: 6px 0 4px; white-space: nowrap; }
  .module .temp.warm { color: var(--asc-warn); }
  .module .temp.hot { color: var(--asc-now); }
  .module .state { font-size: 0.75rem; color: var(--asc-ok); }
  .module .state.off { color: var(--asc-now); }
  .module .state.unknown { color: var(--asc-faint); }

  .guard { display: flex; align-items: center; gap: 16px; }
  .guard-icon {
    width: 44px; height: 44px; border-radius: 50%; flex: none; display: grid; place-items: center;
    border: 2px solid currentColor; color: var(--asc-cyan); font-size: 1.4rem; font-weight: 300;
  }
  .guard-icon.off { color: var(--asc-faint); }
  .guard-icon.active { color: var(--asc-now); }
  .guard-icon.recovering { color: var(--asc-warn); }
  .guard .line1 { font-size: 0.82rem; color: var(--asc-text); margin-top: 4px; opacity: 0.85; }
  .guard .line2 { font-size: 0.75rem; color: var(--asc-muted); margin-top: 3px; }

  .message { padding: 8px 4px; color: var(--asc-muted); }
`;

class AtiStratonCard extends HTMLElement {
  static getConfigForm() {
    return {
      schema: [
        { name: "device_id", selector: { device: { integration: DOMAIN } } },
        { name: "title", selector: { text: {} } },
        {
          type: "grid",
          name: "",
          schema: [
            { name: "smooth", selector: { boolean: {} } },
            { name: "full_day", selector: { boolean: {} } },
          ],
        },
      ],
      computeLabel: (schema) => {
        const t = I18N[(document.documentElement.lang || "de").startsWith("de") ? "de" : "en"];
        return {
          device_id: t.cfgDevice,
          title: t.cfgTitle,
          smooth: t.cfgSmooth,
          full_day: t.cfgFullDay,
        }[schema.name];
      },
    };
  }

  static getStubConfig() {
    return {};
  }

  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._uid = Math.random().toString(36).slice(2, 8);
    this._preview = null;
    this._hoverT = null;
    this._signature = "";
  }

  setConfig(config) {
    this._config = { smooth: false, full_day: false, ...(config || {}) };
    this._signature = "";
    if (this._hass) this._update();
  }

  set hass(hass) {
    this._hass = hass;
    this._update();
  }

  getCardSize() {
    return 8;
  }

  getGridOptions() {
    return { columns: 12, min_columns: 6, rows: "auto" };
  }

  connectedCallback() {
    // Die Markierung „Jetzt“ wandert ohne Zustandsänderung weiter.
    this._timer = setInterval(() => this._renderChart(), 30000);
    if (this._chartEl && !this._resize) this._observe();
  }

  disconnectedCallback() {
    clearInterval(this._timer);
    this._resize?.disconnect();
    this._resize = null;
  }

  /* ------------------------------------------------------------ Hilfen */

  get _t() {
    const lang = this._hass?.locale?.language || this._hass?.language || "de";
    return I18N[lang.startsWith("de") ? "de" : "en"];
  }

  _fmt(value, digits = 0) {
    const lang = this._hass?.locale?.language || this._hass?.language || "de";
    return new Intl.NumberFormat(lang, {
      minimumFractionDigits: digits,
      maximumFractionDigits: digits,
    }).format(value);
  }

  _state(role) {
    const id = this._ids?.[role];
    return id ? this._hass.states[id] : undefined;
  }

  _available(role) {
    const s = this._state(role);
    return !!s && !UNAVAILABLE.has(s.state);
  }

  _value(role) {
    const s = this._state(role);
    return s && !UNAVAILABLE.has(s.state) ? num(s.state) : null;
  }

  /**
   * Reglerstellung = höchster Kurvenwert. Solange der Wächter regelt, ist der
   * Regler gesperrt und unverfügbar; der Wert steht dann weiter im Verlauf.
   */
  _peak() {
    const value = this._value("intensity");
    if (value !== null) return value;
    const schedule = this._state("program")?.attributes.schedule;
    if (!Array.isArray(schedule) || !schedule.length) return null;
    return Math.max(...schedule.map((e) => num(e.intensity) ?? 0));
  }

  _moreInfo(entityId) {
    if (!entityId) return;
    this.dispatchEvent(
      new CustomEvent("hass-more-info", { detail: { entityId }, bubbles: true, composed: true }),
    );
  }

  async _call(domain, service, data) {
    try {
      await this._hass.callService(domain, service, data);
    } catch (err) {
      this.dispatchEvent(
        new CustomEvent("hass-notification", {
          detail: { message: err?.message || String(err) },
          bubbles: true,
          composed: true,
        }),
      );
      throw err;
    }
  }

  /* ---------------------------------------------------- Entitäten finden */

  _resolve() {
    const hass = this._hass;
    const all = Object.values(hass.entities || {}).filter((e) => e.platform === DOMAIN);
    let deviceId = this._config.device_id;
    if (!deviceId) {
      deviceId = (all.find((e) => e.translation_key === "intensity") || all[0])?.device_id;
    }
    const mine = all.filter((e) => e.device_id === deviceId);
    const ids = {};
    for (const [role, [domain, key]] of Object.entries(ROLES)) {
      ids[role] =
        this._config.entities?.[role] ||
        mine.find((e) => e.translation_key === key && e.entity_id.startsWith(`${domain}.`))
          ?.entity_id;
    }

    // Module: Temperatur und Verbindung tragen dieselbe external_id.
    const online = {};
    for (const e of mine) {
      if (!e.entity_id.startsWith("binary_sensor.")) continue;
      const attrs = hass.states[e.entity_id]?.attributes || {};
      if (attrs.device_class === "connectivity" && attrs.external_id) {
        online[attrs.external_id] = e.entity_id;
      }
    }
    const spots = mine
      .filter((e) => e.entity_id.startsWith("sensor."))
      .map((e) => ({ id: e.entity_id, attrs: hass.states[e.entity_id]?.attributes || {} }))
      .filter(({ attrs }) => attrs.device_class === "temperature")
      .sort((a, b) =>
        String(a.attrs.external_id || a.id).localeCompare(
          String(b.attrs.external_id || b.id),
          undefined,
          { numeric: true },
        ),
      )
      .map(({ id, attrs }) => ({ temp: id, online: online[attrs.external_id] }));

    this._deviceId = deviceId;
    this._ids = ids;
    this._spots = spots;
  }

  /* -------------------------------------------------------------- Aufbau */

  _build() {
    const t = this._t;
    this.shadowRoot.innerHTML = `
      <style>${STYLE}</style>
      <ha-card>
        <div class="header">
          <div class="icon-btn" id="device" role="button" tabindex="0"><ha-icon icon="mdi:menu"></ha-icon></div>
          <div>
            <div class="title" id="title"></div>
            <div class="status"><span class="dot" id="dot"></span><span id="status"></span></div>
          </div>
        </div>
        <div class="message" id="message" hidden></div>
        <div class="layout" id="layout">
          <div class="tile chart-tile">
            <h3>${esc(t.dailyCurve)}</h3>
            <div class="sub">${esc(t.dailyCurveSub)}</div>
            <div class="chart" id="chart"></div>
            <div class="legend" id="legend"></div>
            <div class="legend colors" id="colors" aria-label="${esc(t.colors)}"></div>
          </div>
          <div class="side">
            <div class="tile" id="power-tile">
              <div class="row">
                <div><h3>${esc(t.lighting)}</h3><div class="sub">${esc(t.lightingSub)}</div></div>
                <button class="switch" id="power" role="switch" aria-checked="false" aria-label="${esc(t.lighting)}"></button>
              </div>
            </div>
            <div class="tile" id="intensity-tile">
              <div class="row"><h3>${esc(t.intensity)}</h3><span class="value" id="intensity-value"></span></div>
              <input type="range" id="intensity" min="0" max="100" step="1" aria-label="${esc(t.intensity)}">
              <div class="locked" id="locked" hidden>${esc(t.locked)}</div>
            </div>
            <div class="tile" id="program-tile">
              <h3>${esc(t.program)}</h3>
              <select id="program" aria-label="${esc(t.program)}"></select>
              <div class="level-row" id="level-row">
                <select class="small" id="level" aria-label="${esc(t.level)}" title="${esc(t.level)} – ${esc(t.levelHint)}"></select>
              </div>
            </div>
            <div class="modules" id="modules"></div>
            <div class="tile guard clickable" id="guard">
              <div class="guard-icon" id="guard-icon">!</div>
              <div>
                <h3>${esc(t.guard)}</h3>
                <div class="line1" id="guard-line1"></div>
                <div class="line2" id="guard-line2"></div>
              </div>
            </div>
          </div>
        </div>
      </ha-card>`;

    const $ = (id) => this.shadowRoot.getElementById(id);
    this._el = $;
    this._chartEl = $("chart");
    this._builtLang = t;

    const openDevice = () => {
      if (!this._deviceId) return;
      history.pushState(null, "", `/config/devices/device/${this._deviceId}`);
      window.dispatchEvent(new CustomEvent("location-changed"));
    };
    $("device").addEventListener("click", openDevice);
    $("device").addEventListener("keydown", (ev) => ev.key === "Enter" && openDevice());

    $("power").addEventListener("click", () => {
      const on = this._state("power")?.state === "on";
      this._call("switch", on ? "turn_off" : "turn_on", { entity_id: this._ids.power }).catch(
        () => {},
      );
    });

    const slider = $("intensity");
    slider.addEventListener("input", () => {
      this._preview = Number(slider.value);
      this._paintSlider();
      this._renderChart();
    });
    slider.addEventListener("change", async () => {
      const value = Number(slider.value);
      this._preview = value;
      try {
        await this._call("number", "set_value", { entity_id: this._ids.intensity, value });
      } catch (_err) {
        /* gemeldet */
      } finally {
        this._preview = null;
        this._signature = "";
        this._update();
      }
    });

    $("program").addEventListener("change", (ev) => {
      const select = ev.target;
      const current = this._state("program")?.state;
      const option = select.value;
      if (option === current) return;
      if (!window.confirm(this._t.confirmProgram.replace("{p}", option))) {
        select.value = current;
        return;
      }
      this._call("select", "select_option", { entity_id: this._ids.program, option }).catch(() => {
        select.value = current;
      });
    });

    $("level").addEventListener("change", (ev) => {
      this._call("select", "select_option", {
        entity_id: this._ids.level,
        option: ev.target.value,
      }).catch(() => {});
    });

    $("modules").addEventListener("click", (ev) => {
      const tile = ev.target.closest("[data-entity]");
      if (tile) this._moreInfo(tile.dataset.entity);
    });
    $("guard").addEventListener("click", () => this._moreInfo(this._ids.guard));

    const chart = this._chartEl;
    chart.addEventListener("pointermove", (ev) => {
      const r = chart.getBoundingClientRect();
      this._hoverX = ev.clientX - r.left;
      this._renderChart();
    });
    chart.addEventListener("pointerleave", () => {
      this._hoverX = null;
      this._renderChart();
    });

    this._observe();
  }

  _observe() {
    if (!this._chartEl || typeof ResizeObserver === "undefined") return;
    this._resize = new ResizeObserver(() => this._renderChart());
    this._resize.observe(this._chartEl);
  }

  /* ---------------------------------------------------- Aktualisierung */

  _update() {
    if (!this._hass || !this._config) return;
    this._resolve();

    const tracked = [
      ...Object.values(this._ids),
      ...this._spots.flatMap((s) => [s.temp, s.online]),
    ];
    const signature = tracked
      .map((id) => (id ? this._hass.states[id]?.last_updated : "-"))
      .concat(this._deviceId, this._hass.locale?.language, JSON.stringify(this._config))
      .join("|");
    if (signature === this._signature) return;
    this._signature = signature;

    if (!this.shadowRoot.firstChild || this._builtLang !== this._t) this._build();
    const $ = this._el;
    const t = this._t;

    const found = !!this._ids.intensity;
    $("message").hidden = found;
    $("layout").hidden = !found;
    $("message").textContent = found ? "" : t.noDevice;

    const device = this._hass.devices?.[this._deviceId];
    $("title").textContent =
      this._config.title || device?.name_by_user || device?.name || "ATI Straton";
    if (!found) return;

    // Verbindung: Die Entitäten des Geräts sind erreichbar; die Temperaturen
    // kommen ausschließlich über den Push-Kanal.
    const reachable = this._available("intensity") || this._available("now");
    const push = this._spots.some((s) => !UNAVAILABLE.has(this._hass.states[s.temp]?.state));
    $("dot").className = `dot ${!reachable ? "bad" : push ? "" : "warn"}`;
    $("status").textContent = reachable
      ? `${t.connected} · ${push ? t.pushOk : t.pushLost}`
      : t.unreachable;

    this._updateControls();
    this._updateModules();
    this._updateGuard();
    this._renderChart();
  }

  _updateControls() {
    const $ = this._el;
    const power = this._state("power");
    const powerOk = this._available("power");
    const lit = powerOk ? power.state === "on" : (this._peak() ?? 0) > 0;
    $("power").setAttribute("aria-checked", String(lit));
    $("power").disabled = !powerOk;
    $("power-tile").classList.toggle("disabled", !powerOk);

    const intensity = this._state("intensity");
    const intensityOk = this._available("intensity");
    const slider = $("intensity");
    slider.min = intensity?.attributes.min ?? 0;
    slider.max = intensity?.attributes.max ?? 100;
    slider.step = intensity?.attributes.step ?? 1;
    slider.disabled = !intensityOk;
    if (this._preview === null) slider.value = this._peak() ?? 0;
    $("intensity-tile").classList.toggle("disabled", !intensityOk);
    const guardActive = this._state("mode")?.state === "guard";
    $("locked").hidden = !(guardActive && !intensityOk);
    this._paintSlider();

    this._fillSelect($("program"), this._state("program"), true);
    this._fillSelect($("level"), this._state("level"), false);
    $("level-row").hidden = !this._ids.level;
    $("program-tile").classList.toggle("disabled", !this._available("program"));
  }

  _paintSlider() {
    const slider = this._el("intensity");
    const min = Number(slider.min);
    const max = Number(slider.max);
    const value = Number(slider.value);
    const pct = max > min ? ((value - min) / (max - min)) * 100 : 0;
    slider.style.setProperty("--pct", `${pct}%`);
    const shown = this._preview ?? this._peak();
    this._el("intensity-value").textContent = shown === null ? "–" : `${this._fmt(shown)} %`;
  }

  _fillSelect(select, stateObj, grouped) {
    const options = stateObj?.attributes.options || [];
    let html;
    const programs = stateObj?.attributes.programs;
    if (grouped && Array.isArray(programs) && programs.length) {
      // Gleiche Bildung wie Program.label in programs.py.
      const groups = new Map();
      for (const p of programs) {
        const label = p.group ? `${p.group} · ${p.title}` : p.title;
        if (!groups.has(p.group || "")) groups.set(p.group || "", []);
        groups.get(p.group || "").push({ label });
      }
      html = [...groups]
        .map(([group, items]) => {
          const opts = items
            .map((i) => `<option value="${esc(i.label)}">${esc(i.label)}</option>`)
            .join("");
          return group ? `<optgroup label="${esc(group)}">${opts}</optgroup>` : opts;
        })
        .join("");
    } else {
      html = options.map((o) => `<option value="${esc(o)}">${esc(o)}</option>`).join("");
    }
    const current = stateObj?.state;
    const known = options.includes(current);
    if (!known) html = `<option value="" disabled>–</option>${html}`;
    // Nicht neu aufbauen, während die Liste geöffnet ist.
    if (select.dataset.html !== html && this.shadowRoot.activeElement !== select) {
      select.innerHTML = html;
      select.dataset.html = html;
    }
    if (this.shadowRoot.activeElement !== select) select.value = known ? current : "";
    select.disabled = !stateObj || UNAVAILABLE.has(stateObj.state);
  }

  _updateModules() {
    const t = this._t;
    const max = this._value("guardMax");
    const low = this._value("guardLow");
    this._el("modules").innerHTML = this._spots
      .map((spot, index) => {
        const temp = this._hass.states[spot.temp];
        const value = temp && !UNAVAILABLE.has(temp.state) ? num(temp.state) : null;
        const link = spot.online ? this._hass.states[spot.online] : null;
        let state = t.noData;
        let cls = "unknown";
        if (link && !UNAVAILABLE.has(link.state)) {
          state = link.state === "on" ? t.online : t.offline;
          cls = link.state === "on" ? "" : "off";
        }
        let heat = "";
        if (value !== null && max !== null && value >= max) heat = "hot";
        else if (value !== null && low !== null && value >= low) heat = "warm";
        const name = temp?.attributes.friendly_name || spot.temp;
        return `
          <div class="tile module clickable" data-entity="${esc(spot.temp)}" title="${esc(name)}">
            <div class="name">${esc(t.module)} ${index + 1}</div>
            <div class="temp ${heat}">${value === null ? "–" : this._fmt(value, 1)} °C</div>
            <div class="state ${cls}">${esc(state)}</div>
          </div>`;
      })
      .join("");
  }

  _updateGuard() {
    const t = this._t;
    const $ = this._el;
    const state = this._state("guardState")?.state;
    const reduction = this._value("guardReduction");
    const max = this._value("guardMax");
    const low = this._value("guardLow");
    const load = this._value("load");
    const mode = this._state("mode")?.state;

    let text = t.guardStates[state] || "–";
    if (reduction && reduction > 0) text += ` (−${this._fmt(reduction)} %)`;
    const parts = [text];
    if (max !== null) parts.push(t.reducesAt.replace("{v}", this._fmt(max)));
    if (low !== null) parts.push(t.releasesBelow.replace("{v}", this._fmt(low)));
    $("guard-line1").textContent = parts.join(" · ");

    const extra = [];
    if (load !== null) extra.push(`${t.load} ${this._fmt(load)} %`);
    if (mode && !UNAVAILABLE.has(mode)) extra.push(`${t.mode}: ${t.modes[mode] || mode}`);
    $("guard-line2").textContent = extra.join(" · ");

    const icon = $("guard-icon");
    icon.className = "guard-icon";
    if (!state || state === "disabled" || UNAVAILABLE.has(state)) icon.classList.add("off");
    else if (state === "reducing" || state === "holding") icon.classList.add("active");
    else if (state === "recovering") icon.classList.add("recovering");
  }

  /* --------------------------------------------------------- Diagramm */

  _nowSeconds() {
    // Gerätezeit, nicht Browserzeit: Das Gerät fährt seinen Verlauf in seiner
    // eigenen Zeitzone.
    const offset = num(this._state("now")?.attributes.device_utc_offset);
    if (offset !== null) return (((Date.now() / 1000 + offset * 60) % DAY) + DAY) % DAY;
    const d = new Date();
    return d.getHours() * 3600 + d.getMinutes() * 60 + d.getSeconds();
  }

  _renderChart() {
    const el = this._chartEl;
    if (!el || !this._hass || !this._ids?.intensity) return;
    const W = el.clientWidth;
    const H = el.clientHeight;
    if (!W || !H) return;
    const t = this._t;
    const uid = this._uid;

    const schedule = this._state("program")?.attributes.schedule || [];
    const nodes = schedule
      .map((e) => ({
        t: num(e.seconds),
        v: num(e.intensity),
        o: num(e.original),
        color: e.color || null,
        hex: e.hex || null,
      }))
      .filter((n) => n.t !== null && n.v !== null)
      .sort((a, b) => a.t - b.t);

    if (!nodes.length) {
      el.innerHTML = `<svg viewBox="0 0 ${W} ${H}"><text class="empty" x="${W / 2}" y="${H / 2}" text-anchor="middle">${esc(t.noCurve)}</text></svg>`;
      this._el("legend").innerHTML = "";
      this._el("colors").innerHTML = "";
      return;
    }

    // Bezugskurve: valueOrg auf 100 % normiert. Genau darauf bezieht sich der
    // Regler (value = valueOrg × n / max valueOrg).
    const maxOrg = Math.max(0, ...nodes.map((n) => n.o ?? 0));
    const original = maxOrg > 0 ? nodes.map((n) => [n.t, ((n.o ?? 0) * 100) / maxOrg]) : [];
    const current =
      this._preview !== null && maxOrg > 0
        ? nodes.map((n) => [n.t, ((n.o ?? 0) * this._preview) / maxOrg])
        : nodes.map((n) => [n.t, n.v]);

    // Sichtbares Fenster: vom letzten dunklen Punkt vor dem Anstieg bis zum
    // ersten dunklen nach dem Abfall, auf volle Stunden gerundet.
    let t0 = 0;
    let t1 = DAY;
    const ref = original.length ? original : current;
    const lit = ref.map(([, v], i) => (v > 0.05 ? i : -1)).filter((i) => i >= 0);
    if (!this._config.full_day && lit.length) {
      const start = ref[Math.max(0, lit[0] - 1)][0];
      const end = ref[Math.min(ref.length - 1, lit[lit.length - 1] + 1)][0];
      t0 = Math.max(0, Math.floor(start / 3600) * 3600);
      t1 = Math.min(DAY, Math.ceil(end / 3600) * 3600);
      if (t1 - t0 < 4 * 3600) t1 = Math.min(DAY, t0 + 4 * 3600);
    }

    const m = { l: 34, r: 14, t: 16, b: 28 };
    const pw = W - m.l - m.r;
    const ph = H - m.t - m.b;
    const X = (s) => m.l + ((s - t0) / (t1 - t0)) * pw;
    const Y = (v) => m.t + (1 - v / 100) * ph;
    const base = Y(0);

    const smooth = !!this._config.smooth;
    const curOrig = original.length ? new Curve(clip(original, t0, t1), smooth) : null;
    const curNow = new Curve(clip(current, t0, t1), smooth);
    const linePath = curNow.path(X, Y);
    const areaPath = `${linePath}L${X(t1).toFixed(1)},${base.toFixed(1)}L${X(t0).toFixed(1)},${base.toFixed(1)}Z`;

    const span = (t1 - t0) / 3600;
    const step = [1, 2, 3, 4, 6].find((h) => span / h <= 5) || 6;
    const ticks = [];
    for (let s = t0; s <= t1 + 1; s += step * 3600) ticks.push(s);

    let svg = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(t.dailyCurve)}">
      <defs>
        <linearGradient id="fill-${uid}" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stop-color="#7b8ff0" stop-opacity="0.85"/>
          <stop offset="1" stop-color="#7b6fe0" stop-opacity="0.08"/>
        </linearGradient>
        <linearGradient id="stroke-${uid}" gradientUnits="userSpaceOnUse" x1="${m.l}" y1="0" x2="${W - m.r}" y2="0">
          <stop offset="0" stop-color="#7fe3e6"/>
          <stop offset="1" stop-color="#a383f2"/>
        </linearGradient>
      </defs>`;

    for (const v of [25, 50, 75, 100]) {
      svg += `<line x1="${m.l}" x2="${W - m.r}" y1="${Y(v)}" y2="${Y(v)}" stroke="rgba(255,255,255,0.09)"/>`;
      svg += `<text x="${m.l - 8}" y="${Y(v) + 4}" text-anchor="end">${v}</text>`;
    }
    for (const s of ticks) {
      svg += `<text x="${X(s)}" y="${H - 6}" text-anchor="middle">${hhmm(s)}</text>`;
    }

    svg += `<path d="${areaPath}" fill="url(#fill-${uid})" opacity="${this._preview !== null ? 0.6 : 1}"/>`;
    if (curOrig) {
      svg += `<path d="${curOrig.path(X, Y)}" fill="none" stroke="rgba(234,244,248,0.55)" stroke-width="1.5" stroke-dasharray="5 5"/>`;
    }
    svg += `<path d="${linePath}" fill="none" stroke="url(#stroke-${uid})" stroke-width="3" stroke-linejoin="round" stroke-linecap="round"/>`;

    // Stützpunkte in der Farbe ihres Spektrums. Gezeichnet auf der Kurve, wie
    // sie gerade dargestellt wird – auch in der Vorschau.
    nodes.forEach((n, i) => {
      if (n.t < t0 || n.t > t1) return;
      const fill = n.hex || NEUTRAL;
      const tip = `${hhmm(n.t)} · ${this._fmt(current[i][1])} %${n.color ? ` · ${n.color}` : ""}`;
      svg += `<circle class="node" cx="${X(n.t).toFixed(1)}" cy="${Y(current[i][1]).toFixed(1)}" r="5"
        fill="${esc(fill)}" stroke="rgba(255,255,255,0.9)" stroke-width="1.5"><title>${esc(tip)}</title></circle>`;
    });

    // Markierung „Jetzt“. Die Zahl stammt vom Gerätesensor; nur während der
    // Vorschau wird sie aus der Kurve gerechnet.
    const now = this._nowSeconds();
    const nowValue = this._preview !== null ? curNow.value(now) : this._value("now");
    const label = `${this._preview !== null ? t.preview : t.now} · ${
      nowValue === null ? "–" : this._fmt(nowValue)
    } %`;
    if (now >= t0 && now <= t1) {
      svg += this._marker(X(now), Y(curNow.value(now)), label, {
        color: "#f2846e",
        top: Y(100),
        base,
        W,
        dashed: true,
      });
    }

    if (this._hoverX != null && this._hoverX >= m.l && this._hoverX <= W - m.r) {
      const s = t0 + ((this._hoverX - m.l) / pw) * (t1 - t0);
      const v = interpolate(current, s);
      svg += this._marker(X(s), Y(curNow.value(s)), `${hhmm(s)} · ${this._fmt(v)} %`, {
        color: "#eaf4f8",
        top: Y(100),
        base,
        W,
        dashed: false,
      });
    }

    el.innerHTML = `${svg}</svg>`;

    const peak = this._preview ?? this._peak();
    const reduction = this._value("guardReduction");
    this._el("legend").innerHTML = `
      ${curOrig ? `<span><i class="sw-dash"></i>${esc(t.original)}</span>` : ""}
      <span><i class="sw-line"></i>${esc(t.intensity)} ${peak === null ? "–" : this._fmt(peak)} %</span>
      ${
        reduction && reduction > 0
          ? `<span class="badge">${esc(t.guard)} −${this._fmt(reduction)} %</span>`
          : ""
      }`;

    // Farblegende: jede Farbe einmal, in der Reihenfolge ihres Auftretens.
    const seen = new Map();
    for (const n of nodes) {
      const key = n.color || n.hex;
      if (key && !seen.has(key)) seen.set(key, n);
    }
    this._el("colors").innerHTML = [...seen.values()]
      .map((n) => {
        const from = nodes.find((x) => (x.color || x.hex) === (n.color || n.hex) && x.t >= t0);
        const tip = from ? `${t.from} ${hhmm(from.t)}` : "";
        return `<span title="${esc(tip)}"><i class="sw-dot" style="background:${esc(n.hex || NEUTRAL)}"></i>${esc(n.color || n.hex)}</span>`;
      })
      .join("");
  }

  _marker(x, y, label, { color, top, base, W, dashed }) {
    const boxW = Math.max(70, label.length * 6.8 + 24);
    const boxH = 30;
    let bx = x + 14;
    if (bx + boxW > W - 4) bx = x - 14 - boxW;
    let by = y - boxH - 14;
    if (by < 0) by = y + 14;
    return `
      <line x1="${x}" x2="${x}" y1="${top}" y2="${base}" stroke="${color}" stroke-opacity="0.75"
        stroke-width="1.5" ${dashed ? 'stroke-dasharray="2 4"' : ""}/>
      <circle cx="${x}" cy="${y}" r="7" fill="${color}" stroke="#fff" stroke-width="2"/>
      <rect x="${bx}" y="${by}" width="${boxW}" height="${boxH}" rx="8"
        fill="rgba(16,36,56,0.88)" stroke="rgba(255,255,255,0.18)"/>
      <text x="${bx + boxW / 2}" y="${by + 19}" text-anchor="middle"
        style="fill:${color};font-weight:600;font-size:12px">${esc(label)}</text>`;
  }
}

if (!customElements.get(CARD_TAG)) {
  customElements.define(CARD_TAG, AtiStratonCard);
  window.customCards = window.customCards || [];
  window.customCards.push({
    type: CARD_TAG,
    name: "ATI Straton",
    description: "Tagesverlauf der Intensität mit Bedienung und Modultemperaturen",
    preview: true,
    documentationURL: "https://github.com/benkrebs/ATIStraton",
  });
}
