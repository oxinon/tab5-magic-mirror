"""
Leichter HTTP-Server (uasyncio, keine externen Abhängigkeiten) - DREI Seiten
seit dem Umbau auf ein einziges konsolidiertes Tab5-Dashboard (siehe
screens/dashboard.py/HANDOFF.md):

  1. Start ("/")      - Live-Sensorwerte, Zeitverlauf-Grafiken, IAQ-Kalibrierung
                        (unverändert gegenüber der früheren Environment-Seite)
  2. Dashboard         - Kachel-Layout-Editor für ALLE Widgets des EINEN
     ("/dashboard")     Tab5-Dashboards (Position/Breite/Ein-Aus, plus
                        Titel/Entity bei Home-Assistant-Kacheln) - ersetzt
                        die früheren getrennten Seiten /environment,
                        /room_dashboard und /magic_mirror.
  3. System            - WLAN (Status/Verbinden/Access Point), Theme,
     ("/system")        Sprache - ersetzt die frühere eigenständige /wifi-
                        Seite und den früheren Tab5-Settings-Screen.

Bewusst nach dem Vorbild von webserver.py aus dem T-Display-S3-Projekt
aufgebaut (gleiche Optik: PAGE_SHELL mit Platzhaltern, _send()-Helfer,
dunkles Theme mit Cards/metric-grid/chart-wrap).

Bereitgestellte Routen:
  GET  /                        -> Start: Sensorwerte, Grafiken, IAQ-Einstellungen
  GET  /dashboard                -> Kachel-Layout-Editor für das eine Dashboard
  GET  /switches                 -> Licht/Steckdose (Atom-Relais-Board) schalten + Verbindung einstellen
  GET  /system                   -> WLAN + Theme + Sprache
  POST /wifi/connect             -> auf eingegebenes Heimnetz umschalten (nicht-blockierend)
  POST /wifi/ap                  -> sofort in den eigenen Access-Point-Modus wechseln
  GET  /api/wifi-status          -> aktueller WLAN-Status als JSON (Polling)
  POST /system/save              -> Theme/Sprache speichern (löst Live-Reload aus, siehe main.py)
  POST /system/reboot             -> Tab5 neu starten (z.B. nach Layout-/Widget-Änderungen,
                                      damit nicht jedes Mal das USB-Kabel gezogen werden muss)
  POST /save                     -> Sensor-Modus/Grafik-Zeitraum/IAQ-Modus
  POST /recalibrate              -> IAQ-Baseline sofort neu kalibrieren
  POST /sensor-offsets/save      -> Korrekturfaktoren für lokalen BME688 speichern (wirkt sofort)
  POST /layout/dashboard/save    -> komplette Widget-Liste des Dashboards ersetzen
                                     (Position/Breite/Titel/Entity/enabled, Body = JSON-Array)
  GET  /api/status                -> aktuelle Messwerte aller Sensoren als JSON
  GET  /api/history?hours=N       -> historische Messwerte von der SD-Karte als JSON
  GET  /api/ha-states              -> aktuelle Home-Assistant-Zustände der im
                                      Dashboard konfigurierten ha_entity/ha_switch-Kacheln
  GET  /api/geocode?q=...          -> Standortsuche für Wetter/Luftqualität (Open-Meteo)
  GET  /api/ags-search?q=...       -> Regionalschlüssel-Suche für Warnungen (openplzapi.org)
  GET  /api/sync?relay=N&state=0|1 -> Push-Update vom gepairten Atom-Relais-Board (siehe atom_client.py)
  GET  /api/atom-status            -> aktueller Zustand beider Atom-Relais (für /switches)
  GET  /api/climate-ext-status     -> aktueller Zustand des externen Raumklima-Geräts (für die Start-Seite)
  POST /api/atom-toggle/<1|2>      -> ein Atom-Relais umschalten (für /switches)
  POST /atom/save                  -> Atom-Board-Verbindung speichern (enabled/base_url)
  POST /server/save                -> Magic-Mirror-Server-Verbindung speichern (enabled/base_url, für Server-Status-Kachel)

Von main.py zu setzen (siehe README.md für ein Wiring-Beispiel):
  get_state      - Funktion ohne Argumente -> dict mit allen aktuellen Sensor-Messwerten
  get_ha_states  - Funktion(entity_ids) -> dict {entity_id: state}
  sd_reader      - sensors.sd_reader.SDReader-Instanz
  set_mode       - Funktion(mode) -> wirkt sofort auf den SensorManager
  iaq_reset      - Funktion() -> setzt die IAQ-Baseline zurück
  set_sensor_offsets - Funktion(dict) -> aktualisiert BME688-Korrekturfaktoren sofort
  wifi_mgr       - wifi_manager-Modul (oder WifiManager-Instanz) mit
                   status()/connect_sta_async()/start_ap()
  cfg_load / cfg_save - config.py-Funktionen
  relay_set_from_partner - Funktion(relay_id, is_on) -> Anzeige aktualisieren,
                           siehe atom_client.py-Docstring
  atom_get_status / atom_toggle - atom_client.py::AtomClient.get_status()/toggle()
  request_reload - Funktion() ohne Argumente -> signalisiert main.py, das
                    Dashboard mit der aktuellen config.json neu aufzubauen
                    (Live-Reload ohne Neustart, siehe main.py::_do_reload())
"""

import uasyncio as asyncio
import ujson as json

import widget_sources

get_state = None
get_ha_states = None
sd_reader = None
set_mode = None
iaq_reset = None
set_sensor_offsets = None
wifi_mgr = None
cfg_load = None
cfg_save = None
# Push-Updates vom gepairten Atom-Relais-Board (siehe atom_client.py-
# Docstring und die "/api/sync"-Route unten) - Funktion(relay_id, is_on).
relay_set_from_partner = None
# Für die /switches-Seite - Funktionen ohne bzw. mit einem Argument, siehe
# atom_client.py::AtomClient.get_status()/toggle().
atom_get_status = None
atom_toggle = None
# Live-Reload: Funktion() ohne Argumente, von main.py gesetzt - signalisiert
# main.py's reload_watcher_task(), dass das Dashboard mit der gerade
# gespeicherten config.json neu aufgebaut werden soll, OHNE dass der
# Nutzer den Tab5 manuell neu starten muss (siehe screens/widget_catalog.py::
# WidgetCatalogScreen.destroy() für die Aufräum-Seite davon). Absichtlich
# NUR ein Signal (kein direkter Aufruf von hier aus!) - der eigentliche
# Umbau darf nur im selben Tick wie main.py's übrige LVGL-Arbeit passieren,
# nicht mitten aus einem HTTP-Request-Handler heraus.
request_reload = None

CHART_HOUR_OPTIONS = [1, 6, 24, 72, 168]
MODE_LABELS = {
    "auto": "Automatisch (lokal bevorzugt, Fallback remote)",
    "local_only": "Nur lokal (BME688)",
    "remote_only": "Nur remote (T-Display-S3)",
    "both": "Beide gleichzeitig anzeigen",
}
IAQ_MODE_LABELS = {
    "fixed": "Fest (einmalig beim Boot kalibrieren)",
    "rolling": "Rollierend (passt sich laufend an)",
}
THEME_LABELS = {"dark": "Dunkel", "light": "Hell"}
LANGUAGE_LABELS = {"de": "Deutsch", "en": "English"}

# Fester Katalog aller "einfachen" Dashboard-Widget-Typen (kein Titel/Entity
# editierbar - nur Position/Breite/Ein-Aus). Deckt die frühere Trennung
# Environment (climate/air_quality/acoustic/equalizer/acceleration) und
# Magic Mirror (die übrigen 16) ab, jetzt EIN gemeinsamer Katalog. Home-
# Assistant-Kacheln (ha_entity/ha_switch) sind bewusst NICHT hier drin -
# die haben frei editierbaren Titel/Entity und werden separat behandelt
# (siehe _dashboard_layout_rows_html).
DASHBOARD_WIDGET_LABELS = {
    "climate": "Klima (Temperatur/Feuchte/Druck)",
    "climate_ext": "Raumklima Extern (anderes Gerät)",
    "pc_status": "Computer-Status (CPU/GPU)",
    "air_quality": "Luftqualitäts-Ampel (lokaler BME688)",
    "acoustic": "Akustik-Ampel",
    "equalizer": "Mikrofon-Equalizer",
    "acceleration": "Beschleunigung",
    "clock": "Uhr", "calendar": "Kalender", "news": "News", "crypto": "Krypto",
    "weather": "Wetter", "stocks": "Aktien", "quote": "Zitat des Tages",
    "server_status": "Server-Status", "warnings": "Warnungen",
    "air_quality_mirror": "Luftqualität (Magic-Mirror-Server)",
    "elbe_pegel": "Elbe-Pegel", "ews": "Apocalypse EWS", "defcon": "DEFCON",
    "compliments": "Komplimente", "todo": "Notizen",
    "env_sensor_mirror": "Umweltstation (Zusammenfassung)",
}
# Widget-Typen, die breiter als 1 Spalte sein dürfen (siehe SPAN_LIMITS in
# screens/widget_catalog.py) - dupliziert statt importiert, damit web_server.py
# nicht von einem lvgl-nahen Modul abhängt.
DASHBOARD_SPAN_LIMITS = {"news": 4, "compliments": 4, "quote": 4, "clock": 2}
HA_TYPE_LABELS = {"ha_entity": "Anzeige (Sensor)", "ha_switch": "Schalter (Licht/Steckdose)"}

# Gruppierung für den /dashboard-Editor (siehe _dashboard_layout_rows_html) -
# bei 25 Widgets war eine einzige lange Liste unübersichtlich geworden
# ("wo ist das neue Widget überhaupt?"). Jede Kategorie wird als
# auf-/zuklappbarer Abschnitt gerendert; ha_entity/ha_switch laufen
# separat (eigene, freie Titel/Entity-Felder statt eines festen Katalog-
# Eintrags) und bekommen ihre eigene Kategorie ganz unten.
DASHBOARD_CATEGORIES = [
    ("Uhr & Kalender", ("clock", "calendar")),
    ("News & Finanzen", ("news", "crypto", "stocks", "quote")),
    ("Wetter, Warnungen & Sonstige Online-Quellen",
     ("weather", "air_quality_mirror", "warnings", "elbe_pegel", "ews", "defcon", "server_status")),
    ("Sensoren (Tab5-eigen & extern)",
     ("climate", "air_quality", "acoustic", "equalizer", "acceleration", "climate_ext",
      "pc_status", "env_sensor_mirror")),
    ("Notizen & Komplimente", ("compliments", "todo")),
]



def _url_decode(s):
    s = s.replace('+', ' ')
    result = ''
    i = 0
    while i < len(s):
        if s[i] == '%' and i + 2 < len(s):
            try:
                result += chr(int(s[i + 1:i + 3], 16))
                i += 3
                continue
            except ValueError:
                pass
        result += s[i]
        i += 1
    return result


def _parse_qs(s):
    """Parst sowohl POST-Formulardaten als auch GET-Query-Strings -
    identisches key=value&key2=value2-Format."""
    fields = {}
    for part in s.split('&'):
        if '=' in part:
            k, v = part.split('=', 1)
            fields[_url_decode(k)] = _url_decode(v)
    return fields


def _option(value, selected_value, label):
    sel = "selected" if str(value) == str(selected_value) else ""
    return '<option value="%s" %s>%s</option>' % (value, sel, label)


def _position_options(cols, rows, selected):
    opts = []
    for r in range(1, rows + 1):
        for c in range(1, cols + 1):
            pos = "r%d-c%d" % (r, c)
            opts.append(_option(pos, selected, pos))
    return "".join(opts)


def _hour_label(h):
    if h < 24:
        return "%dh" % h
    days = h // 24
    return "%d Tag%s" % (days, "e" if days > 1 else "")


# ---------------------------------------------------------------------------
# Gemeinsames CSS/Grundgerüst für alle Seiten - dunkles Theme, Cards,
# metric-grid, Layout-Editor-Zeilen. Jede Seite bettet ihren eigenen Body
# über __BODY__ ein. Kopfzeile jetzt mit einer kleinen 3-Punkt-Navigation
# (Start/Dashboard/System) statt nur einem Zurück-Pfeil, seit es keine
# Index-Liste mit vielen einzelnen Dashboard-Seiten mehr gibt.
# ---------------------------------------------------------------------------
PAGE_SHELL = """<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.0/chart.umd.min.js"></script>
<style>
  :root { color-scheme: dark; }
  body { font-family: -apple-system, Segoe UI, Roboto, sans-serif; background:#111318; color:#eee;
         margin:0; padding:20px; max-width:760px; margin-inline:auto; }
  nav { font-size:0.85rem; margin-bottom:14px; }
  nav a { color:#c9a15a; text-decoration:none; margin-right:14px; }
  nav a.active { color:#eee; font-weight:bold; }
  h1 { font-size:1.3rem; margin-top:0; }
  h2 { font-size:1rem; color:#c9a15a; margin-top:28px; border-bottom:1px solid #333; padding-bottom:6px; }
  label { display:block; margin-top:10px; font-size:0.85rem; color:#bbb; }
  select, input[type=text] { box-sizing:border-box; padding:8px; margin-top:4px; border-radius:6px;
           border:1px solid #333; background:#1c1f26; color:#eee; font-size:0.95rem; }
  select { width:100%; }
  button { margin-top:16px; padding:10px 16px; border:none; border-radius:6px; background:#c9a15a;
           color:#1c1f26; font-weight:bold; font-size:0.95rem; cursor:pointer; }
  button.secondary { background:#333; color:#eee; }
  .hint { font-size:0.78rem; color:#777; margin-top:4px; }
  .card { background:#181b22; border-radius:10px; padding:14px; margin-top:14px; }
  .metric-grid { display:grid; grid-template-columns:repeat(auto-fit, minmax(120px, 1fr)); gap:10px; margin-top:10px; }
  .metric { background:#1c1f26; border-radius:8px; padding:10px 12px; }
  .metric .label { font-size:0.75rem; color:#888; }
  .metric .value { font-size:1.15rem; margin-top:2px; }
  .source-label { font-size:0.8rem; color:#c9a15a; text-transform:uppercase; letter-spacing:1px;
                  margin-top:18px; display:block; }
  .chart-wrap { position:relative; height:180px; margin-top:14px; }
  .save-status { font-size:0.85rem; color:#7fae7a; margin-top:10px; }
  .layout-row { display:flex; align-items:center; gap:10px; margin-top:10px; flex-wrap:wrap; }
  .layout-row-label { flex:2; font-size:0.9rem; }
  .layout-row input[type=text] { width:auto; }
  .layout-checkbox { display:flex; align-items:center; gap:6px; flex:0 0 auto; margin:0; font-size:0.85rem; }
  .layout-checkbox input { width:auto; margin:0; }
  .layout-row select { flex:1; margin:0; min-width:110px; }
  .layout-row button.remove { flex:0 0 auto; margin:0; padding:6px 10px; font-size:0.8rem; }
  .widget-block { border-bottom:1px solid #23262e; padding-bottom:10px; margin-bottom:4px; }
  .widget-block:last-child { border-bottom:none; }
  details.widget-category { margin-top:14px; border:1px solid #262a33; border-radius:8px;
    background:#15171d; overflow:hidden; }
  details.widget-category summary { cursor:pointer; padding:12px 14px; font-size:0.95rem;
    color:#eee; font-weight:600; list-style:none; display:flex; align-items:center; justify-content:space-between; }
  details.widget-category summary::-webkit-details-marker { display:none; }
  details.widget-category summary::after { content:'\25BE'; color:#c9a15a; margin-left:8px; }
  details.widget-category[open] summary::after { content:'\25B4'; }
  details.widget-category .category-count { font-size:0.78rem; color:#888; font-weight:400; margin-left:auto; padding-right:8px; }
  details.widget-category .category-body { padding:4px 14px 12px; border-top:1px solid #23262e; }
  details.widget-details { margin-top:6px; margin-left:2px; }
  details.widget-details summary { cursor:pointer; font-size:0.82rem; color:#c9a15a; padding:4px 0; }
  details.widget-details .row { display:flex; gap:10px; margin-top:8px; }
  details.widget-details .row > * { flex:1; }
  details.widget-details textarea { width:100%; box-sizing:border-box; padding:8px; margin-top:4px;
    border-radius:6px; border:1px solid #333; background:#1c1f26; color:#eee; font-size:0.9rem; font-family:inherit; }
  .search-results div { padding:8px; background:#1c1f26; border-radius:6px; margin-top:4px; cursor:pointer; font-size:0.85rem; }
  .search-results div:hover { background:#26304a; }
  .switch-row { display:flex; align-items:center; justify-content:space-between; gap:14px;
    padding:14px 0; border-bottom:1px solid #23262e; }
  .switch-row:last-child { border-bottom:none; }
  .switch-row .name { font-size:1.05rem; }
  .switch-row .state { font-size:0.8rem; color:#888; margin-top:2px; }
  .toggle { position:relative; display:inline-block; width:52px; height:30px; flex:0 0 auto; }
  .toggle input { opacity:0; width:0; height:0; }
  .toggle-slider { position:absolute; cursor:pointer; inset:0; background:#333; border-radius:30px; transition:0.15s; }
  .toggle-slider:before { content:""; position:absolute; height:22px; width:22px; left:4px; bottom:4px;
    background:#eee; border-radius:50%; transition:0.15s; }
  .toggle input:checked + .toggle-slider { background:#c9a15a; }
  .toggle input:checked + .toggle-slider:before { transform:translateX(22px); }
</style>
</head>
<body>
<nav>
  <a href="/" class="__NAV_START__">Start</a>
  <a href="/dashboard" class="__NAV_DASHBOARD__">Dashboard</a>
  <a href="/switches" class="__NAV_SWITCHES__">Schalter</a>
  <a href="/system" class="__NAV_SYSTEM__">System</a>
</nav>
<h1>__TITLE__</h1>
__BODY__
</body></html>"""


def _shell(title, body, active):
    html = PAGE_SHELL.replace("__TITLE__", title).replace("__BODY__", body)
    for key in ("start", "dashboard", "switches", "system"):
        html = html.replace("__NAV_%s__" % key.upper(), "active" if key == active else "")
    return html


# ---------------------------------------------------------------------------
# Start-Seite - Live-Sensorwerte, Zeitverlauf-Grafiken, IAQ-Kalibrierung.
# Inhaltlich unverändert gegenüber der früheren /environment-Seite, nur
# ohne den Kachel-Layout-Teil (der ist jetzt auf /dashboard, zusammen mit
# allen anderen Widgets).
# ---------------------------------------------------------------------------
def _start_page_html(cfg):
    rs = cfg["room_sensor"]
    mode = rs.get("mode", "auto")
    hours = rs.get("web_chart_hours", 6)
    iaq_mode = cfg.get("iaq", {}).get("baseline_mode", "fixed")
    offsets = rs.get("offsets", {})

    mode_options = "".join(_option(m, mode, label) for m, label in MODE_LABELS.items())
    hour_options = "".join(_option(h, hours, _hour_label(h)) for h in CHART_HOUR_OPTIONS)
    iaq_mode_options = "".join(_option(m, iaq_mode, label) for m, label in IAQ_MODE_LABELS.items())

    body = START_BODY
    body = body.replace("__MODE_OPTIONS__", mode_options)
    body = body.replace("__HOUR_OPTIONS__", hour_options)
    body = body.replace("__IAQ_MODE_OPTIONS__", iaq_mode_options)
    body = body.replace("__CURRENT_HOURS__", str(hours))
    body = body.replace("__OFFSET_TEMP__", str(offsets.get("temp_c", 0.0)))
    body = body.replace("__OFFSET_HUMIDITY__", str(offsets.get("humidity", 0.0)))
    body = body.replace("__OFFSET_PRESSURE__", str(offsets.get("pressure_hpa", 0.0)))
    return _shell("Tab5 - Sensoren", body, "start")


START_BODY = """
<div class="card">
  <h2>Live-Werte</h2>
  <div id="live-sources">lade...</div>
  <div id="live-sources-ext"></div>
  <div class="hint">Welche Quelle(n) hier erscheinen, hängt vom Sensor-Modus unten ab.
  "Extern" ist ein separates Gerät (z.B. Core2-MiniDash mit eigenem BME688),
  siehe /dashboard - Kategorie "Sensoren".</div>
</div>

<div class="card">
  <h2>Zeitverlauf (von der SD-Karte, letzte __CURRENT_HOURS__h)</h2>
  <div class="chart-wrap"><canvas id="chart-temp"></canvas></div>
  <div class="chart-wrap"><canvas id="chart-humidity"></canvas></div>
  <div class="chart-wrap"><canvas id="chart-iaq"></canvas></div>
  <div class="chart-wrap"><canvas id="chart-sound"></canvas></div>
  <div class="chart-wrap"><canvas id="chart-accel"></canvas></div>
</div>

<div class="card">
  <h2>Einstellungen</h2>
  <label>Sensor-Modus</label>
  <select id="mode-select">__MODE_OPTIONS__</select>
  <div class="hint">"Beide gleichzeitig" zeigt lokalen und remote Sensor
  nebeneinander an (z.B. zum Vergleichen), statt automatisch umzuschalten.</div>
  <label>Zeitverlauf in den Grafiken</label>
  <select id="hours-select">__HOUR_OPTIONS__</select>
  <div class="hint">Wie weit die Grafiken oben in die auf der SD-Karte
  gespeicherte Historie zurückschauen. Lange Zeiträume werden automatisch
  heruntergerechnet (siehe sensors/sd_reader.py).</div>
  <button onclick="saveSettings()">Speichern</button>
  <div id="save-status" class="save-status"></div>
</div>

<div class="card">
  <h2>IAQ-Kalibrierung (lokaler BME688)</h2>
  <label>Baseline-Modus</label>
  <select id="iaq-mode-select">__IAQ_MODE_OPTIONS__</select>
  <div class="hint">Fest: einmalige Kalibrierung aus den ersten Messungen
  nach dem Boot. Rollierend: die Baseline passt sich laufend an.</div>
  <div style="margin-top:10px;">Kalibrierungs-Konfidenz: <span id="iaq-confidence">-</span></div>
  <button onclick="saveSettings()">Speichern</button>
  <button class="secondary" onclick="recalibrate()" style="margin-left:8px;">Jetzt neu kalibrieren</button>
  <div class="hint">Am besten direkt nach dem Lüften des Raums drücken.</div>
  <div id="recalibrate-status" class="save-status"></div>
</div>

<div class="card">
  <h2>Korrekturfaktoren (lokaler BME688)</h2>
  <div class="hint">Additiv auf den Rohwert angewendet, z.B. gegen einen
  Eigenwärme-Versatz durch die Nähe zu Display/Elektronik im Gehäuse - wirkt
  sofort, kein Neustart nötig.</div>
  <div class="row">
    <div><label>Temperatur (°C)</label><input type="number" step="0.1" id="offset-temp" value="__OFFSET_TEMP__"></div>
    <div><label>Feuchte (%)</label><input type="number" step="0.1" id="offset-humidity" value="__OFFSET_HUMIDITY__"></div>
    <div><label>Luftdruck (hPa)</label><input type="number" step="0.1" id="offset-pressure" value="__OFFSET_PRESSURE__"></div>
  </div>
  <button onclick="saveSensorOffsets()">Speichern</button>
  <div id="offsets-save-status" class="save-status"></div>
</div>

<p style="margin-top:16px;"><a href="/api/status" style="color:#c9a15a;">Aktuelle Messwerte (JSON)</a></p>

<script>
const chartDefaults = {
  responsive: true, maintainAspectRatio: false, animation: false,
  interaction: {intersect: false},
  plugins: {legend: {labels: {color: '#ccc', boxWidth: 12, font: {size: 11}}}},
  scales: {
    x: {ticks: {color: '#888', maxTicksLimit: 6, font: {size: 10}}, grid: {color: '#2a2d35'}},
    y: {ticks: {color: '#888', font: {size: 10}}, grid: {color: '#2a2d35'}}
  }
};

function makeLineChart(elId, label, color) {
  return new Chart(document.getElementById(elId), {
    type: 'line',
    data: {labels: [], datasets: [{label: label, data: [], borderColor: color,
      backgroundColor: 'transparent', tension: 0.2, pointRadius: 0}]},
    options: chartDefaults,
  });
}

function addChartDataset(chart, label, color) {
  chart.data.datasets.push({label: label, data: [], borderColor: color,
    backgroundColor: 'transparent', tension: 0.2, pointRadius: 0});
  return chart.data.datasets.length - 1;
}

const tempChart = makeLineChart('chart-temp', 'Temperatur (°C)', '#c9a15a');
const humidityChart = makeLineChart('chart-humidity', 'Luftfeuchtigkeit (%)', '#5aa9c9');
const iaqChart = makeLineChart('chart-iaq', 'Luftqualitäts-Score', '#7fae7a');
const soundChart = makeLineChart('chart-sound', 'Schallpegel', '#f5b942');
const accelChart = makeLineChart('chart-accel', 'Beschleunigung |a| (g)', '#b96a5a');
const ALL_CHARTS = [tempChart, humidityChart, iaqChart, soundChart, accelChart];

// Zweite Linie für das externe Raumklima-Gerät (z.B. Core2-MiniDash,
// siehe /api/climate-ext-status) - eigene Farbe, damit lokal/entfernt auf
// einen Blick unterscheidbar bleiben. Nur Live-Punkte (kein Eintrag in
// /api/history, da das externe Gerät nicht auf der Tab5-SD-Karte
// mitgeloggt wird) - die Linie füllt sich also erst, während die Seite
// geöffnet ist, ohne rückwirkende Historie.
const tempChartExtIdx = addChartDataset(tempChart, 'Extern (Core2-MiniDash)', '#3d7fff');
const humidityChartExtIdx = addChartDataset(humidityChart, 'Extern (Core2-MiniDash)', '#3d7fff');
const iaqChartExtIdx = addChartDataset(iaqChart, 'Extern (Core2-MiniDash)', '#3d7fff');

function timeLabel(ts) { return new Date(ts * 1000).toTimeString().slice(0, 5); }

function loadHistory() {
  const hours = document.getElementById('hours-select').value;
  fetch('/api/history?hours=' + hours).then(r => r.json()).then(rows => {
    const labels = rows.map(r => timeLabel(r.timestamp));
    tempChart.data.labels = labels; tempChart.data.datasets[0].data = rows.map(r => r.temp_c);
    humidityChart.data.labels = labels; humidityChart.data.datasets[0].data = rows.map(r => r.humidity);
    iaqChart.data.labels = labels; iaqChart.data.datasets[0].data = rows.map(r => r.iaq_score);
    soundChart.data.labels = labels; soundChart.data.datasets[0].data = rows.map(r => r.sound_db);
    accelChart.data.labels = labels; accelChart.data.datasets[0].data = rows.map(r => r.accel_magnitude);
    ALL_CHARTS.forEach(c => c.update('none'));
  }).catch(() => {});
}

function appendLivePoint(chart, value, datasetIdx) {
  if (value == null) return;
  const idx = datasetIdx || 0;
  if (idx === 0) chart.data.labels.push(timeLabel(Date.now() / 1000));
  chart.data.datasets[idx].data.push(value);
  const maxLen = chart.data.labels.length;
  if (chart.data.datasets[idx].data.length > maxLen) {
    chart.data.datasets[idx].data.shift();
  }
  if (idx === 0 && chart.data.labels.length > 300) {
    chart.data.labels.shift();
    chart.data.datasets.forEach(ds => { if (ds.data.length > 300) ds.data.shift(); });
  }
  chart.update('none');
}

function metricHtml(label, value, unit) {
  return '<div class="metric"><div class="label">' + label + '</div><div class="value">' +
    (value != null ? value : '-') + (unit || '') + '</div></div>';
}

function sourceBlockHtml(title, reading) {
  if (!reading) return '';
  if (!reading.ok) {
    return '<span class="source-label">' + title + '</span><div class="hint">nicht verfügbar' +
      (reading.msg ? (': ' + reading.msg) : '') + '</div>';
  }
  return '<span class="source-label">' + title + '</span><div class="metric-grid">' +
    metricHtml('Temperatur', reading.temp_c != null ? reading.temp_c.toFixed(1) : null, '°C') +
    metricHtml('Feuchte', reading.humidity != null ? reading.humidity.toFixed(0) : null, '%') +
    metricHtml('Luftdruck', reading.pressure_hpa != null ? reading.pressure_hpa.toFixed(0) : null, ' hPa') +
    metricHtml('IAQ-Score', reading.iaq_score != null ? reading.iaq_score.toFixed(0) : null, '') +
    '</div>';
}

function updateStatus() {
  fetch('/api/status').then(r => r.json()).then(s => {
    let html = '';
    const air = s.air || {};
    if (air.local) html += sourceBlockHtml('Lokal (BME688)', air.local);
    if (air.remote) html += sourceBlockHtml('Remote (T-Display-S3)', air.remote);
    html += '<span class="source-label">Akustik / Beschleunigung</span><div class="metric-grid">' +
      metricHtml('Schallpegel', s.mic && s.mic.ok && s.mic.db != null ? s.mic.db.toFixed(0) : null) +
      metricHtml('Beschleunigung', s.accel && s.accel.ok && s.accel.magnitude != null ? s.accel.magnitude.toFixed(2) : null, 'g') +
      metricHtml('Erschütterung', s.accel && s.accel.ok ? (s.accel.quake.triggered ? 'JA' : 'nein') : null) +
      '</div>';
    document.getElementById('live-sources').innerHTML = html;

    const localAir = air.local;
    if (localAir && localAir.ok && localAir.iaq_confidence != null) {
      document.getElementById('iaq-confidence').textContent = localAir.iaq_confidence.toFixed(0) + '%';
    }

    const primaryAir = (air.local && air.local.ok) ? air.local : air.remote;
    if (primaryAir && primaryAir.ok) {
      appendLivePoint(tempChart, primaryAir.temp_c);
      appendLivePoint(humidityChart, primaryAir.humidity);
      appendLivePoint(iaqChart, primaryAir.iaq_score);
    }
    if (s.mic && s.mic.ok) appendLivePoint(soundChart, s.mic.db);
    if (s.accel && s.accel.ok) appendLivePoint(accelChart, s.accel.magnitude);
  }).catch(() => {});

  // Externes Raumklima-Gerät (z.B. Core2-MiniDash) - eigener Abruf, damit
  // ein nicht erreichbares externes Gerät die übrige Anzeige oben nicht
  // blockiert (zwei unabhängige fetch()-Aufrufe statt einem gemeinsamen).
  fetch('/api/climate-ext-status').then(r => r.json()).then(ext => {
    document.getElementById('live-sources-ext').innerHTML = sourceBlockHtml('Extern (Core2-MiniDash)', ext);
    if (ext && ext.ok) {
      appendLivePoint(tempChart, ext.temp_c, tempChartExtIdx);
      appendLivePoint(humidityChart, ext.humidity, humidityChartExtIdx);
      appendLivePoint(iaqChart, ext.iaq_score, iaqChartExtIdx);
    }
  }).catch(() => {
    document.getElementById('live-sources-ext').innerHTML = '';
  });
}

function saveSettings() {
  const mode = document.getElementById('mode-select').value;
  const hours = document.getElementById('hours-select').value;
  const iaqMode = document.getElementById('iaq-mode-select').value;
  fetch('/save', {
    method: 'POST', headers: {'Content-Type': 'application/x-www-form-urlencoded'},
    body: 'mode=' + encodeURIComponent(mode) + '&web_chart_hours=' + encodeURIComponent(hours) +
      '&iaq_baseline_mode=' + encodeURIComponent(iaqMode),
  }).then(r => r.json()).then(() => {
    document.getElementById('save-status').textContent =
      'Gespeichert um ' + new Date().toLocaleTimeString() + ' - wirkt sofort, kein Neustart nötig.';
    loadHistory();
  }).catch(() => {
    document.getElementById('save-status').textContent = 'Gerät nicht erreichbar.';
  });
}

function recalibrate() {
  fetch('/recalibrate', {method: 'POST'}).then(r => r.json()).then(() => {
    document.getElementById('recalibrate-status').textContent =
      'Neu kalibriert um ' + new Date().toLocaleTimeString() + '.';
  }).catch(() => {
    document.getElementById('recalibrate-status').textContent = 'Gerät nicht erreichbar.';
  });
}

function saveSensorOffsets() {
  const body = 'temp_c=' + encodeURIComponent(document.getElementById('offset-temp').value) +
    '&humidity=' + encodeURIComponent(document.getElementById('offset-humidity').value) +
    '&pressure_hpa=' + encodeURIComponent(document.getElementById('offset-pressure').value);
  fetch('/sensor-offsets/save', {
    method: 'POST', headers: {'Content-Type': 'application/x-www-form-urlencoded'}, body: body,
  }).then(r => r.json()).then(() => {
    document.getElementById('offsets-save-status').textContent =
      'Gespeichert um ' + new Date().toLocaleTimeString() + ' - wirkt sofort, kein Neustart nötig.';
  }).catch(() => {
    document.getElementById('offsets-save-status').textContent = 'Gerät nicht erreichbar.';
  });
}

loadHistory();
updateStatus();
setInterval(updateStatus, 4000);
</script>
"""


# ---------------------------------------------------------------------------
# Dashboard-Seite - EIN Kachel-Layout-Editor für ALLE Widgets des einen
# Tab5-Dashboards (siehe screens/dashboard.py/config.py "screens.dashboard").
# Ersetzt die früheren drei getrennten Seiten /environment, /room_dashboard,
# /magic_mirror.
# ---------------------------------------------------------------------------
def _widget_details_html(w):
    """Zusätzliche, typspezifische Einstellungsfelder unterhalb der
    Position/Breite/Ein-Aus-Zeile - z.B. RSS-Quellen bei News, Standort
    bei Wetter, Symbole bei Krypto/Aktien. Kommt als <details> (auf-/
    zuklappbar), damit das Layout bei 25 Widgets nicht endlos lang wird.
    Alle Felder werden beim Speichern anhand ihrer CSS-Klasse aus dem
    umschließenden .widget-block gelesen (siehe saveDashboardLayout() in
    DASHBOARD_BODY) - KEINE IDs, da ein Widget-Typ theoretisch mehrfach
    vorkommen könnte (z.B. zwei News-Kacheln mit unterschiedlichen Quellen).
    """
    wtype = w.get("type")

    if wtype in ("weather", "air_quality_mirror"):
        lat, lon = w.get("latitude"), w.get("longitude")
        location = w.get("location", "")
        units_field = (
            '<label>Einheit</label>'
            '<select class="f-units">%s</select>' % "".join(
                _option(u, w.get("units", "celsius"), lbl)
                for u, lbl in (("celsius", "°C"), ("fahrenheit", "°F")))
        ) if wtype == "weather" else ""
        return (
            '<details class="widget-details" open>'
            '<summary>Standort &amp; Einstellungen</summary>'
            '<label>Ort suchen</label>'
            '<input type="text" class="f-geosearch" placeholder="z.B. Hamburg">'
            '<div class="search-results f-georesults"></div>'
            '<div class="hint f-geoselected">Aktuell: %s%s</div>'
            '<input type="hidden" class="f-latitude" value="%s">'
            '<input type="hidden" class="f-longitude" value="%s">'
            '<input type="hidden" class="f-location" value="%s">'
            '%s'
            '</details>'
        ) % (location or "kein Standort", (" (%.4f, %.4f)" % (lat, lon)) if lat is not None else "",
             lat if lat is not None else "", lon if lon is not None else "", location, units_field)

    if wtype == "warnings":
        return (
            '<details class="widget-details" open>'
            '<summary>Standort &amp; Einstellungen</summary>'
            '<label>Ort/PLZ suchen</label>'
            '<input type="text" class="f-agssearch" placeholder="z.B. 20095 oder Hamburg">'
            '<div class="search-results f-agsresults"></div>'
            '<div class="hint f-agsselected">Regionalschlüssel (ARS): %s</div>'
            '<input type="hidden" class="f-ars" value="%s">'
            '</details>'
        ) % (w.get("ars") or "keiner ausgewählt", w.get("ars", ""))

    if wtype == "news":
        sources = (w.get("sources") or []) + [{}, {}, {}]
        rows_html = "".join(
            '<div class="row"><input type="text" class="f-news-name" placeholder="Name" value="%s">'
            '<input type="text" class="f-news-url" placeholder="Feed-URL" value="%s"></div>'
            % (s.get("name", ""), s.get("feedUrl", ""))
            for s in sources[:3]
        )
        return (
            '<details class="widget-details" open>'
            '<summary>RSS-Quellen &amp; Einstellungen</summary>'
            '<div class="hint">Bis zu 3 Quellen, rotiert bei jedem Refresh eine weiter.</div>'
            '%s'
            '<label>Max. Schlagzeilen pro Quelle</label>'
            '<input type="number" class="f-max-items" value="%s">'
            '</details>'
        ) % (rows_html, w.get("max_items", 5))

    if wtype == "crypto":
        return (
            '<details class="widget-details" open>'
            '<summary>Coins &amp; Einstellungen</summary>'
            '<label>Coin-IDs (CoinGecko, kommagetrennt, z.B. bitcoin,ethereum)</label>'
            '<input type="text" class="f-symbols" value="%s">'
            '<label>Währung</label>'
            '<select class="f-currency">%s</select>'
            '</details>'
        ) % (",".join(w.get("symbols") or []),
             "".join(_option(c, w.get("currency", "usd"), c.upper()) for c in ("usd", "eur")))

    if wtype == "stocks":
        return (
            '<details class="widget-details" open>'
            '<summary>Symbole</summary>'
            '<label>Symbole (Yahoo-Finance-Format, kommagetrennt, z.B. AAPL,SAP.DE)</label>'
            '<input type="text" class="f-symbols" value="%s">'
            '</details>'
        ) % ",".join(w.get("symbols") or [])

    if wtype == "defcon":
        return (
            '<details class="widget-details" open>'
            '<summary>Quelle</summary>'
            '<div class="hint">Für <a href="https://ai-defcon.com" target="_blank" style="color:#c9a15a;">ai-defcon.com</a> '
            'gebaut - andere Endpunkte mit demselben JSON-Format funktionieren auch.</div>'
            '<label>Quell-URL</label>'
            '<input type="text" class="f-url" value="%s" placeholder="https://ai-defcon.com/api/status.json">'
            '<label>API-Key (optional, als X-API-Key-Header gesendet)</label>'
            '<input type="text" class="f-api-key" value="%s">'
            '</details>'
        ) % (w.get("url", ""), w.get("api_key", ""))

    if wtype == "calendar":
        return (
            '<details class="widget-details" open>'
            '<summary>Kalender-Adresse</summary>'
            '<label>Private iCal-URL</label>'
            '<input type="text" class="f-ical-url" value="%s" placeholder="https://calendar.google.com/.../basic.ics">'
            '<div class="hint">Google Kalender → Einstellungen → dein Kalender → "Kalender integrieren" → "Geheime Adresse im iCal-Format".</div>'
            '<div class="row">'
            '<div><label>Max. Termine</label><input type="number" class="f-max-events" value="%s"></div>'
            '<div><label>Vorschau (Tage)</label><input type="number" class="f-days-ahead" value="%s"></div>'
            '</div>'
            '</details>'
        ) % (w.get("ical_url", ""), w.get("max_events", 5), w.get("days_ahead", 14))

    if wtype == "elbe_pegel":
        return (
            '<details class="widget-details" open>'
            '<summary>Pegel-Station</summary>'
            '<label>Stationsname (Teilstring reicht, z.B. "ST. PAULI")</label>'
            '<input type="text" class="f-station-name" value="%s">'
            '<div class="hint">Vollständige Liste: '
            '<a href="https://www.pegelonline.wsv.de/webservices/rest-api/v2/stations.json?waters=ELBE" '
            'target="_blank" style="color:#c9a15a;">Elbe-Stationen (JSON)</a></div>'
            '</details>'
        ) % w.get("station_name", "ST. PAULI")

    if wtype == "climate_ext":
        return (
            '<details class="widget-details" open>'
            '<summary>Externes Gerät</summary>'
            '<label>IP-Adresse / Basis-URL</label>'
            '<input type="text" class="f-base-url" value="%s" placeholder="http://192.168.1.30">'
            '<div class="hint">Muss unter /api/status ein JSON mit "bme680"/"iaq"-Feldern liefern '
            '(z.B. Core2-MiniDash-Referenzprojekt).</div>'
            '</details>'
        ) % w.get("base_url", "")

    if wtype == "pc_status":
        return (
            '<details class="widget-details" open>'
            '<summary>PC-Adresse</summary>'
            '<label>IP-Adresse / Basis-URL (inkl. Port)</label>'
            '<input type="text" class="f-base-url" value="%s" placeholder="http://192.168.1.100:80">'
            '<div class="hint">Erwartet den AIDA64-RemoteSensor-kompatiblen "/sse"-Endpunkt, siehe '
            '<a href="https://github.com/oxinon/knob-esp32s3-aida-sse-server-linux" target="_blank" '
            'style="color:#c9a15a;">knob-esp32s3-aida-sse-server-linux</a>.</div>'
            '</details>'
        ) % w.get("base_url", "")

    if wtype == "compliments":
        return (
            '<details class="widget-details">'
            '<summary>Komplimente</summary>'
            '<div class="hint">Eine Nachricht pro Zeile, es wird jedes Mal eine zufällige gezeigt.</div>'
            '<textarea class="f-messages" rows="5">%s</textarea>'
            '</details>'
        ) % "\n".join(w.get("items") or [])

    if wtype == "todo":
        items = [it.get("text", "") if isinstance(it, dict) else str(it) for it in (w.get("items") or [])]
        return (
            '<details class="widget-details">'
            '<summary>Notizen</summary>'
            '<div class="hint">Eine Notiz pro Zeile.</div>'
            '<textarea class="f-items" rows="5">%s</textarea>'
            '</details>'
        ) % "\n".join(items)

    if wtype == "clock":
        return (
            '<details class="widget-details">'
            '<summary>Anzeige</summary>'
            '<label class="layout-checkbox"><input type="checkbox" class="f-format24h" %s>24-Stunden-Format</label>'
            '<label class="layout-checkbox"><input type="checkbox" class="f-show-seconds" %s>Sekunden anzeigen</label>'
            '<label class="layout-checkbox"><input type="checkbox" class="f-show-date" %s>Datum anzeigen</label>'
            '</details>'
        ) % (
            "checked" if w.get("format24h", True) else "",
            "checked" if w.get("show_seconds", True) else "",
            "checked" if w.get("show_date", True) else "",
        )

    if wtype == "acceleration":
        return (
            '<details class="widget-details">'
            '<summary>Empfindlichkeit (STA/LTA-Erschütterungserkennung)</summary>'
            '<div class="hint">Wirkt erst nach einem Neustart des Tab5 (der Sensor wird nur beim '
            'Boot mit diesen Werten eingerichtet, siehe main.py). Höherer Auslöse-Faktor = '
            'unempfindlicher (weniger Fehlalarme, verpasst aber leichtere Erschütterungen).</div>'
            '<div class="row">'
            '<div><label>Kurzzeit-Fenster (s)</label><input type="number" step="0.1" class="f-sta-tau" value="%s"></div>'
            '<div><label>Langzeit-Fenster (s)</label><input type="number" step="1" class="f-lta-tau" value="%s"></div>'
            '<div><label>Auslöse-Faktor</label><input type="number" step="0.1" class="f-trigger-ratio" value="%s"></div>'
            '</div>'
            '</details>'
            '<details class="widget-details">'
            '<summary>Alarm-Ton bei Auslösung</summary>'
            '<label class="layout-checkbox"><input type="checkbox" class="f-alarm-beep" %s>Alarm-Ton aktiv</label>'
            '<div class="row">'
            '<div><label>Ton 1 (Hz)</label><input type="number" class="f-alarm-tone1" value="%s"></div>'
            '<div><label>Ton 2 (Hz)</label><input type="number" class="f-alarm-tone2" value="%s"></div>'
            '</div>'
            '<div class="row">'
            '<div><label>Ton-Dauer (ms)</label><input type="number" class="f-alarm-beep-ms" value="%s"></div>'
            '<div><label>Pause (ms)</label><input type="number" class="f-alarm-gap-ms" value="%s"></div>'
            '</div>'
            '<div class="row">'
            '<div><label>Wiederholungen</label><input type="number" class="f-alarm-repeats" value="%s"></div>'
            '<div><label>Lautstärke (%%)</label><input type="number" min="0" max="100" class="f-alarm-volume" value="%s"></div>'
            '</div>'
            '</details>'
        ) % (
            w.get("sta_tau_s", 0.5), w.get("lta_tau_s", 30.0), w.get("trigger_ratio", 3.0),
            "checked" if w.get("alarm_beep", True) else "",
            w.get("alarm_tone1_hz", 1800), w.get("alarm_tone2_hz", 1200),
            w.get("alarm_beep_ms", 150), w.get("alarm_gap_ms", 100),
            w.get("alarm_repeats", 3), w.get("alarm_volume_percent", 80),
        )

    return ""


def _dashboard_layout_rows_html(cfg):
    screen_cfg = cfg.get("screens", {}).get("dashboard", {})
    grid = screen_cfg.get("grid", {"cols": 4, "rows": 3})
    cols = grid["cols"]

    def _build_block(w):
        wtype = w.get("type")
        is_ha = wtype in ("ha_entity", "ha_switch")
        checked = "checked" if w.get("enabled", True) else ""
        pos_options = _position_options(cols, grid["rows"], w.get("position", "r1-c1"))

        type_max = DASHBOARD_SPAN_LIMITS.get(wtype, 1)
        col = int(w.get("position", "r1-c1").split("-c")[1])  # 1-indiziert
        fit_max = cols - col + 1
        max_span = max(1, min(type_max, fit_max))
        current_span = min(w.get("col_span", 1), max_span)
        if type_max > 1:
            span_field = '<select class="span-select" data-type-max="%d">%s</select>' % (
                type_max, "".join(_option(s, current_span, "%dx breit" % s) for s in range(1, max_span + 1)))
        else:
            span_field = '<span class="span-fixed">1x</span>'

        if is_ha:
            type_options = "".join(_option(t, wtype, label) for t, label in HA_TYPE_LABELS.items())
            if wtype == "ha_switch":
                # Schalter können zwei Backends haben: das gepairte Atom-
                # Relais-Board (siehe atom_client.py) oder Home Assistant
                # (aktuell deaktiviert, siehe config.py). Je nach Auswahl
                # ist entweder das Relais-Nummer- oder das entity_id-Feld
                # sichtbar (siehe toggleSwitchBackend() unten).
                backend = w.get("backend", "ha")
                backend_options = "".join(_option(b, backend, lbl) for b, lbl in
                                           (("atom", "Atom-Relais"), ("ha", "Home Assistant")))
                label_field = (
                    '<input type="text" class="title-input" value="%s" placeholder="Titel">'
                    '<input type="hidden" class="type-select" value="ha_switch">'
                    '<select class="backend-select" onchange="toggleSwitchBackend(this)">%s</select>'
                    '<input type="number" class="relay-input" value="%s" placeholder="Relais 1/2" '
                    'style="display:%s;" min="1" max="2">'
                    '<input type="text" class="entity-input" value="%s" placeholder="entity_id" '
                    'style="display:%s;">'
                ) % (
                    w.get("title", ""), backend_options,
                    w.get("relay_id", "") or "", "block" if backend == "atom" else "none",
                    w.get("entity_id", ""), "block" if backend == "ha" else "none",
                )
            else:
                label_field = (
                    '<input type="text" class="title-input" value="%s" placeholder="Titel">'
                    '<input type="text" class="entity-input" value="%s" placeholder="entity_id">'
                    '<select class="type-select">%s</select>'
                ) % (w.get("title", ""), w.get("entity_id", ""), type_options)
        else:
            label = DASHBOARD_WIDGET_LABELS.get(wtype, wtype)
            label_field = '<div class="layout-row-label">%s</div>' % label

        # Komplette Original-Config als JSON im data-Attribut mitführen (HTML-
        # escaped), damit beim Speichern Felder, die dieser Editor (noch)
        # nicht kennt, NICHT verloren gehen - alles Bekannte wird beim
        # Speichern gezielt überschrieben (siehe saveDashboardLayout()).
        widget_json = json.dumps(w).replace('"', "&quot;")
        row_html = (
            '<div class="layout-row" data-cols="%d" data-ha="%s">'
            '%s'
            '<label class="layout-checkbox"><input type="checkbox" class="enabled-cb" %s>an</label>'
            '<select class="position-select">%s</select>'
            '%s'
            '%s'
            '</div>' % (
                cols, "true" if is_ha else "false", label_field, checked,
                pos_options, span_field,
                '<button type="button" class="remove secondary" onclick="this.closest(\'.widget-block\').remove()">&times;</button>' if is_ha else "",
            )
        )
        details_html = "" if is_ha else _widget_details_html(w)
        return '<div class="widget-block" data-widget="%s">%s%s</div>' % (widget_json, row_html, details_html)

    widgets = screen_cfg.get("widgets", [])
    by_type = {}
    for w in widgets:
        by_type.setdefault(w.get("type"), []).append(w)

    sections = []
    seen_types = set()
    for category_name, types in DASHBOARD_CATEGORIES:
        cat_widgets = []
        for t in types:
            cat_widgets.extend(by_type.get(t, []))
            seen_types.add(t)
        if not cat_widgets:
            continue
        enabled_count = sum(1 for w in cat_widgets if w.get("enabled", True))
        blocks_html = "".join(_build_block(w) for w in cat_widgets)
        sections.append(
            '<details class="widget-category" open>'
            '<summary>%s <span class="category-count">(%d/%d aktiv)</span></summary>'
            '<div class="category-body">%s</div>'
            '</details>' % (category_name, enabled_count, len(cat_widgets), blocks_html)
        )

    # Home-Assistant/Atom-Schalter + Anzeigen (ha_entity/ha_switch) sind
    # NICHT über einen festen Typ in DASHBOARD_CATEGORIES gelistet (freie,
    # nutzerdefinierte Kacheln) - eigene Kategorie ganz unten, direkt vor
    # dem "+ Hinzufügen"-Button.
    ha_widgets = [w for w in widgets if w.get("type") in ("ha_entity", "ha_switch")]
    if ha_widgets:
        enabled_count = sum(1 for w in ha_widgets if w.get("enabled", True))
        blocks_html = "".join(_build_block(w) for w in ha_widgets)
        sections.append(
            '<details class="widget-category" open>'
            '<summary>Home Assistant / Atom-Relais <span class="category-count">(%d/%d aktiv)</span></summary>'
            '<div class="category-body">%s</div>'
            '</details>' % (enabled_count, len(ha_widgets), blocks_html)
        )

    # Sicherheitsnetz: jeder Widget-Typ, der (noch) in keiner Kategorie
    # oben auftaucht (z.B. nach einem künftigen neuen Widget-Typ, den
    # DASHBOARD_CATEGORIES noch nicht kennt), landet hier - damit nie
    # wieder ein Widget "einfach unauffindbar" im Editor verschwindet.
    leftover = [w for w in widgets if w.get("type") not in seen_types
                and w.get("type") not in ("ha_entity", "ha_switch")]
    if leftover:
        blocks_html = "".join(_build_block(w) for w in leftover)
        sections.append(
            '<details class="widget-category" open>'
            '<summary>Sonstige (noch nicht kategorisiert)</summary>'
            '<div class="category-body">%s</div>'
            '</details>' % blocks_html
        )

    return "".join(sections)


def _dashboard_page_html(cfg):
    screen_cfg = cfg.get("screens", {}).get("dashboard", {})
    grid = screen_cfg.get("grid", {"cols": 4, "rows": 3})
    default_position_options = _position_options(grid["cols"], grid["rows"], "r1-c1")

    body = DASHBOARD_BODY.replace("__DASHBOARD_LAYOUT_ROWS__", _dashboard_layout_rows_html(cfg))
    body = body.replace("__POSITION_OPTIONS_PLACEHOLDER__", default_position_options)
    body = body.replace("__COLS__", str(grid["cols"]))
    return _shell("Dashboard", body, "dashboard")


DASHBOARD_BODY = r"""
<div class="card">
  <h2>Kachel-Layout &amp; Widget-Einstellungen</h2>
  <div class="hint">Nach Kategorie sortiert und zum Auf-/Zuklappen - Position,
  Breite (in Spalten, nur bei Uhr/News/Zitat/Komplimente wählbar) und Sichtbarkeit
  jeder Kachel wirken sofort, kein Neustart nötig. Zum Aufklappen auf "Details"
  bei einem Widget klicken - dort z.B. RSS-Quellen, Standort, Symbole oder
  Kalender-Adresse eintragen.</div>
  <div id="dashboard-layout-rows">__DASHBOARD_LAYOUT_ROWS__</div>
  <button class="secondary" onclick="addHaRow()">+ Home-Assistant-Kachel hinzufügen</button>
  <button onclick="saveDashboardLayout()">Alles speichern</button>
  <button class="secondary" onclick="rebootDevice()">Tab5 neu starten</button>
  <div id="layout-status" class="save-status"></div>
</div>

<script>
function recomputeSpanOptions(row) {
  const select = row.querySelector('.span-select');
  if (!select) return; // Widget-Typ ohne Breiten-Auswahl (fester 1x)
  const cols = parseInt(row.dataset.cols, 10);
  const typeMax = parseInt(select.dataset.typeMax, 10);
  const posMatch = /-c(\d+)$/.exec(row.querySelector('.position-select').value);
  const col = posMatch ? parseInt(posMatch[1], 10) : 1;
  const fitMax = cols - col + 1;
  const maxSpan = Math.max(1, Math.min(typeMax, fitMax));

  const current = Math.min(parseInt(select.value, 10) || 1, maxSpan);
  select.innerHTML = '';
  for (let s = 1; s <= maxSpan; s++) {
    const opt = document.createElement('option');
    opt.value = s;
    opt.textContent = s + 'x breit';
    if (s === current) opt.selected = true;
    select.appendChild(opt);
  }
}

function addHaRow() {
  const block = document.createElement('div');
  block.className = 'widget-block';
  block.innerHTML =
    '<div class="layout-row" data-cols="__COLS__" data-ha="true">' +
    '<input type="text" class="title-input" placeholder="Titel">' +
    '<input type="text" class="entity-input" placeholder="entity_id">' +
    '<select class="type-select"><option value="ha_entity">Anzeige (Sensor)</option>' +
    '<option value="ha_switch">Schalter (Licht/Steckdose)</option></select>' +
    '<label class="layout-checkbox"><input type="checkbox" class="enabled-cb" checked>an</label>' +
    '<select class="position-select">__POSITION_OPTIONS_PLACEHOLDER__</select>' +
    '<button type="button" class="remove secondary" onclick="this.closest(\'.widget-block\').remove()">&times;</button>' +
    '</div>';
  // In die "Home Assistant / Atom-Relais"-Kategorie einhängen, falls es
  // schon eine gibt (an ihrem Titel erkannt) - sonst direkt in den
  // Container (passiert nur, wenn noch gar keine HA/Atom-Kachel existiert).
  let targetBody = null;
  document.querySelectorAll('.widget-category summary').forEach(summary => {
    if (summary.textContent.indexOf('Home Assistant') !== -1) {
      targetBody = summary.parentElement.querySelector('.category-body');
    }
  });
  (targetBody || document.getElementById('dashboard-layout-rows')).appendChild(block);
}

function toggleSwitchBackend(select) {
  const row = select.closest('.layout-row');
  const relayInput = row.querySelector('.relay-input');
  const entityInput = row.querySelector('.entity-input');
  const isAtom = select.value === 'atom';
  relayInput.style.display = isAtom ? 'block' : 'none';
  entityInput.style.display = isAtom ? 'none' : 'block';
}

function debounce(fn, ms) {  let t;
  return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
}

function wireGeoSearch(block) {
  const input = block.querySelector('.f-geosearch');
  if (!input) return;
  const resultsEl = block.querySelector('.f-georesults');
  const selectedEl = block.querySelector('.f-geoselected');
  const latEl = block.querySelector('.f-latitude');
  const lonEl = block.querySelector('.f-longitude');
  const locEl = block.querySelector('.f-location');
  input.addEventListener('input', debounce(async (e) => {
    resultsEl.innerHTML = '';
    const q = e.target.value;
    if (!q || q.length < 2) return;
    const res = await (await fetch('/api/geocode?q=' + encodeURIComponent(q))).json();
    (res.results || []).forEach(r => {
      const div = document.createElement('div');
      div.textContent = r.label;
      div.onclick = () => {
        latEl.value = r.latitude;
        lonEl.value = r.longitude;
        // Nur den Ortsnamen selbst (erster Teil vor dem Komma) als
        // Kachel-Titel merken, nicht "Ort, Region, Land" komplett.
        locEl.value = r.label.split(',')[0].trim();
        selectedEl.textContent = 'Aktuell: ' + r.label + ' (' + r.latitude.toFixed(4) + ', ' + r.longitude.toFixed(4) + ')';
        resultsEl.innerHTML = '';
        input.value = '';
      };
      resultsEl.appendChild(div);
    });
  }, 400));
}

function wireAgsSearch(block) {
  const input = block.querySelector('.f-agssearch');
  if (!input) return;
  const resultsEl = block.querySelector('.f-agsresults');
  const selectedEl = block.querySelector('.f-agsselected');
  const arsEl = block.querySelector('.f-ars');
  input.addEventListener('input', debounce(async (e) => {
    resultsEl.innerHTML = '';
    const q = e.target.value;
    if (!q || q.length < 2) return;
    const res = await (await fetch('/api/ags-search?q=' + encodeURIComponent(q))).json();
    (res.results || []).forEach(r => {
      const div = document.createElement('div');
      div.textContent = r.label;
      div.onclick = () => {
        arsEl.value = r.ars;
        selectedEl.textContent = 'Regionalschlüssel (ARS): ' + r.ars + ' (' + r.label + ')';
        resultsEl.innerHTML = '';
        input.value = '';
      };
      resultsEl.appendChild(div);
    });
  }, 400));
}

function applyWidgetDetails(widget, block) {
  const type = widget.type;
  if (type === 'weather' || type === 'air_quality_mirror') {
    const lat = block.querySelector('.f-latitude').value;
    const lon = block.querySelector('.f-longitude').value;
    if (lat) widget.latitude = parseFloat(lat);
    if (lon) widget.longitude = parseFloat(lon);
    const locEl = block.querySelector('.f-location');
    if (locEl && locEl.value) widget.location = locEl.value;
    const unitsEl = block.querySelector('.f-units');
    if (unitsEl) widget.units = unitsEl.value;
  } else if (type === 'warnings') {
    widget.ars = block.querySelector('.f-ars').value;
  } else if (type === 'news') {
    const sources = [];
    block.querySelectorAll('.f-news-name').forEach((nameEl, i) => {
      const urlEl = block.querySelectorAll('.f-news-url')[i];
      const url = urlEl.value.trim();
      if (url) sources.push({ name: nameEl.value.trim() || ('Quelle ' + (i + 1)), feedUrl: url });
    });
    widget.sources = sources;
    widget.max_items = parseInt(block.querySelector('.f-max-items').value || '5', 10);
  } else if (type === 'crypto') {
    widget.symbols = block.querySelector('.f-symbols').value.split(',').map(s => s.trim()).filter(Boolean);
    widget.currency = block.querySelector('.f-currency').value;
  } else if (type === 'stocks') {
    widget.symbols = block.querySelector('.f-symbols').value.split(',').map(s => s.trim()).filter(Boolean);
  } else if (type === 'defcon') {
    widget.url = block.querySelector('.f-url').value.trim();
    widget.api_key = block.querySelector('.f-api-key').value.trim();
  } else if (type === 'calendar') {
    widget.ical_url = block.querySelector('.f-ical-url').value.trim();
    widget.max_events = parseInt(block.querySelector('.f-max-events').value || '5', 10);
    widget.days_ahead = parseInt(block.querySelector('.f-days-ahead').value || '14', 10);
  } else if (type === 'elbe_pegel') {
    widget.station_name = block.querySelector('.f-station-name').value.trim();
  } else if (type === 'climate_ext' || type === 'pc_status') {
    widget.base_url = block.querySelector('.f-base-url').value.trim();
  } else if (type === 'compliments') {
    widget.items = block.querySelector('.f-messages').value.split(String.fromCharCode(10)).map(s => s.trim()).filter(Boolean);
  } else if (type === 'todo') {
    widget.items = block.querySelector('.f-items').value.split(String.fromCharCode(10)).map(s => s.trim()).filter(Boolean)
      .map(text => ({ text: text, done: false }));
  } else if (type === 'clock') {
    widget.format24h = block.querySelector('.f-format24h').checked;
    widget.show_seconds = block.querySelector('.f-show-seconds').checked;
    widget.show_date = block.querySelector('.f-show-date').checked;
  } else if (type === 'acceleration') {
    widget.sta_tau_s = parseFloat(block.querySelector('.f-sta-tau').value);
    widget.lta_tau_s = parseFloat(block.querySelector('.f-lta-tau').value);
    widget.trigger_ratio = parseFloat(block.querySelector('.f-trigger-ratio').value);
    widget.alarm_beep = block.querySelector('.f-alarm-beep').checked;
    widget.alarm_tone1_hz = parseInt(block.querySelector('.f-alarm-tone1').value, 10);
    widget.alarm_tone2_hz = parseInt(block.querySelector('.f-alarm-tone2').value, 10);
    widget.alarm_beep_ms = parseInt(block.querySelector('.f-alarm-beep-ms').value, 10);
    widget.alarm_gap_ms = parseInt(block.querySelector('.f-alarm-gap-ms').value, 10);
    widget.alarm_repeats = parseInt(block.querySelector('.f-alarm-repeats').value, 10);
    widget.alarm_volume_percent = parseInt(block.querySelector('.f-alarm-volume').value, 10);
  }
}

function saveDashboardLayout() {
  const widgets = [];
  document.querySelectorAll('.widget-block').forEach((block, idx) => {
    const row = block.querySelector('.layout-row');
    const isHa = row.dataset.ha === 'true';
    let widget;
    if (isHa) {
      const existing = block.dataset.widget ? JSON.parse(block.dataset.widget) : null;
      widget = {
        id: existing ? existing.id : ('custom_' + Date.now() + '_' + idx),
        type: row.querySelector('.type-select').value,
        title: row.querySelector('.title-input').value,
      };
      const backendSelect = row.querySelector('.backend-select');
      if (backendSelect) {
        // Schalter (ha_switch) mit Backend-Auswahl: Atom-Relais-Board
        // oder Home Assistant (siehe atom_client.py/toggleSwitchBackend()).
        widget.backend = backendSelect.value;
        if (backendSelect.value === 'atom') {
          widget.relay_id = parseInt(row.querySelector('.relay-input').value, 10);
        } else {
          widget.entity_id = row.querySelector('.entity-input').value;
        }
      } else {
        // ha_entity - nur Home Assistant, kein Backend-Umschalter nötig.
        widget.entity_id = row.querySelector('.entity-input').value;
      }
    } else {
      widget = JSON.parse(block.dataset.widget);
      applyWidgetDetails(widget, block);
    }
    widget.position = row.querySelector('.position-select').value;
    const spanSelect = row.querySelector('.span-select');
    if (spanSelect) widget.col_span = parseInt(spanSelect.value, 10);
    widget.enabled = row.querySelector('.enabled-cb').checked;
    widgets.push(widget);
  });
  fetch('/layout/dashboard/save', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(widgets),
  }).then(r => r.json()).then(() => {
    document.getElementById('layout-status').textContent =
      'Gespeichert um ' + new Date().toLocaleTimeString() + ' - wirkt sofort.';
  }).catch(() => {
    document.getElementById('layout-status').textContent = 'Gerät nicht erreichbar.';
  });
}

function rebootDevice() {
  if (!confirm('Tab5 wirklich neu starten? Nicht gespeicherte Änderungen gehen dabei verloren.')) return;
  document.getElementById('layout-status').textContent = 'Neustart angefordert...';
  fetch('/system/reboot', {method: 'POST'}).catch(() => {});
  // Die Verbindung bricht durch den Neustart normalerweise sofort ab -
  // das ist erwartet, kein Fehler. Kurz warten und dann die Seite neu
  // laden, in der Hoffnung, dass der Tab5 bis dahin wieder online ist
  // (im Heimnetz meist ~10-15s, je nach WLAN-Verbindungsdauer).
  setTimeout(() => location.reload(), 15000);
}

document.querySelectorAll('.widget-block').forEach(block => {
  const row = block.querySelector('.layout-row');
  row.querySelector('.position-select').addEventListener('change', () => recomputeSpanOptions(row));
  wireGeoSearch(block);
  wireAgsSearch(block);
});
</script>
"""


# ---------------------------------------------------------------------------
# System-Seite - WLAN (Status/Verbinden/Access Point) + Theme + Sprache.
# Ersetzt die frühere eigenständige /wifi-Seite und den früheren Tab5-
# Settings-Screen (siehe screens/settings.py-Historie/HANDOFF.md - Theme/
# Sprache werden jetzt hier statt auf dem Tab5-Display selbst eingestellt).
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Schalter-Seite - Licht/Steckdose (gepairtes Atom-Relais-Board, siehe
# atom_client.py) mit Live-Status + Verbindungseinstellungen. Findet die
# Schalter-Kacheln automatisch aus der Dashboard-Konfiguration (Backend
# "atom") statt sie hier fest zu verdrahten - passt sich also automatisch
# an, wenn im /dashboard-Editor mal ein drittes Relais/Widget dazukommt.
# ---------------------------------------------------------------------------
def _switches_page_html(cfg):
    atom_cfg = cfg.get("atom", {})
    widgets = cfg.get("screens", {}).get("dashboard", {}).get("widgets", [])
    atom_switches = [w for w in widgets if w.get("type") == "ha_switch" and w.get("backend") == "atom"]

    rows_html = "".join(
        '<div class="switch-row" data-relay="%d">'
        '<div><div class="name">%s</div><div class="state">Relais %d - lade...</div></div>'
        '<label class="toggle"><input type="checkbox" onchange="toggleRelay(%d, this)">'
        '<span class="toggle-slider"></span></label>'
        '</div>' % (w.get("relay_id", 0), w.get("title") or w["id"], w.get("relay_id", 0), w.get("relay_id", 0))
        for w in atom_switches
    )
    if not rows_html:
        rows_html = ('<div class="hint">Keine Atom-Relais-Kacheln im Dashboard konfiguriert - '
                     'siehe /dashboard, Backend "Atom-Relais" bei einer Schalter-Kachel wählen.</div>')

    body = SWITCHES_BODY.replace("__SWITCH_ROWS__", rows_html)
    body = body.replace("__ATOM_ENABLED_CHECKED__", "checked" if atom_cfg.get("enabled") else "")
    body = body.replace("__ATOM_BASE_URL__", atom_cfg.get("base_url", ""))
    return _shell("Schalter", body, "switches")


SWITCHES_BODY = """
<div class="card">
  <h2>Licht &amp; Steckdose</h2>
  <div id="switch-rows">__SWITCH_ROWS__</div>
  <div class="hint" style="margin-top:12px;">Zustand wird alle 5s vom Atom-Board
  abgefragt - Änderungen am physischen Taster oder über das Atom-eigene
  Web-UI erscheinen automatisch, sobald das Atom das Tab5 als "partner_ip"
  eingetragen hat (siehe unten), sonst erst beim nächsten Abruf.</div>
</div>

<div class="card">
  <h2>Atom-Board-Verbindung</h2>
  <label class="layout-checkbox"><input type="checkbox" id="atom-enabled" __ATOM_ENABLED_CHECKED__>Aktiviert</label>
  <label>IP-Adresse / Basis-URL des Atom-Boards</label>
  <input type="text" id="atom-base-url" value="__ATOM_BASE_URL__" placeholder="http://192.168.1.20">
  <div class="hint">Zusätzlich muss in der <code>config.json</code> DES ATOM-BOARDS SELBST
  <code>partner_ip</code> auf die IP-Adresse dieses Tab5 gesetzt werden, damit der Atom
  bei einer lokalen Änderung sofort Bescheid gibt, statt dass das Tab5 nur alle 5s nachfragt.</div>
  <button onclick="saveAtomSettings()">Speichern</button>
  <div id="atom-save-status" class="save-status"></div>
</div>

<script>
function relayLabel(row) {
  return row.querySelector('.name').textContent;
}

function updateSwitchStatus() {
  fetch('/api/atom-status').then(r => r.json()).then(status => {
    document.querySelectorAll('.switch-row').forEach(row => {
      const relay = parseInt(row.dataset.relay, 10);
      const stateEl = row.querySelector('.state');
      const checkbox = row.querySelector('input[type=checkbox]');
      if (!status.ok) {
        stateEl.textContent = status.msg || 'nicht erreichbar';
        return;
      }
      const isOn = !!status['relay' + relay + '_state'];
      checkbox.checked = isOn;
      stateEl.textContent = isOn ? 'AN' : 'AUS';
    });
  }).catch(() => {
    document.querySelectorAll('.switch-row .state').forEach(el => el.textContent = 'Gerät nicht erreichbar');
  });
}

function toggleRelay(relay, checkbox) {
  fetch('/api/atom-toggle/' + relay, {method: 'POST'}).then(r => r.json()).then(() => {
    updateSwitchStatus();
  }).catch(() => {
    checkbox.checked = !checkbox.checked;  // Optimistischen Klick zurücknehmen
  });
}

function saveAtomSettings() {
  const enabled = document.getElementById('atom-enabled').checked;
  const baseUrl = document.getElementById('atom-base-url').value;
  fetch('/atom/save', {
    method: 'POST', headers: {'Content-Type': 'application/x-www-form-urlencoded'},
    body: 'enabled=' + (enabled ? '1' : '0') + '&base_url=' + encodeURIComponent(baseUrl),
  }).then(r => r.json()).then(() => {
    document.getElementById('atom-save-status').textContent =
      'Gespeichert - wirkt innerhalb ca. 1s ohne Neustart.';
  }).catch(() => {
    document.getElementById('atom-save-status').textContent = 'Gerät nicht erreichbar.';
  });
}

updateSwitchStatus();
setInterval(updateSwitchStatus, 5000);
</script>
"""


def _system_page_html(cfg):
    wifi_cfg = cfg.get("wifi", {})
    server_cfg = cfg.get("server", {})
    theme_options = "".join(_option(k, cfg.get("theme_mode", "dark"), v) for k, v in THEME_LABELS.items())
    lang_options = "".join(_option(k, cfg.get("language", "de"), v) for k, v in LANGUAGE_LABELS.items())

    body = SYSTEM_BODY.replace("__CURRENT_SSID__", wifi_cfg.get("ssid", ""))
    body = body.replace("__THEME_OPTIONS__", theme_options)
    body = body.replace("__LANG_OPTIONS__", lang_options)
    body = body.replace("__SERVER_ENABLED_CHECKED__", "checked" if server_cfg.get("enabled") else "")
    body = body.replace("__SERVER_BASE_URL__", server_cfg.get("base_url", ""))
    return _shell("System", body, "system")


SYSTEM_BODY = """
<div class="card">
  <h2>WLAN-Status</h2>
  <div id="wifi-status">lade...</div>
</div>

<div class="card">
  <h2>Mit Heimnetz verbinden</h2>
  <div class="hint">Verbindungsversuch dauert bis zu 15s und blockiert die
  Seite nicht - Status oben aktualisiert sich automatisch.</div>
  <label>WLAN-Name (SSID)</label>
  <input type="text" id="wifiSsid" value="__CURRENT_SSID__" autocomplete="off">
  <label>Passwort</label>
  <input type="text" id="wifiPassword" placeholder="Passwort" autocomplete="off">
  <button onclick="connectWifi()">Verbinden</button>
  <div id="connect-status" class="save-status"></div>
</div>

<div class="card">
  <h2>Access Point</h2>
  <div class="hint">Öffnet sofort einen eigenen Access Point ("Tab5-Setup",
  Passwort "configure123") - z.B. um von einem anderen Gerät aus neu zu
  konfigurieren, falls kein bekanntes WLAN erreichbar ist.</div>
  <button class="secondary" onclick="startAp()">Access Point starten</button>
  <div id="ap-status" class="save-status"></div>
</div>

<div class="card">
  <h2>Magic-Mirror-Server (Docker-Status)</h2>
  <label class="layout-checkbox"><input type="checkbox" id="server-enabled" __SERVER_ENABLED_CHECKED__>Aktiviert</label>
  <label>IP-Adresse / Basis-URL</label>
  <input type="text" id="server-base-url" value="__SERVER_BASE_URL__" placeholder="http://192.168.1.10:5031">
  <div class="hint">Für die "Server-Status"-Kachel im Dashboard (Docker-Container,
  z.B. dein DEFCON/Crypto-Report-Assistant unter /api/server-status). Wirkt
  innerhalb ca. 1s ohne Neustart (Live-Reload).</div>
  <button onclick="saveServerSettings()">Speichern</button>
  <div id="server-save-status" class="save-status"></div>
</div>

<div class="card">
  <h2>Darstellung</h2>
  <label>Theme</label>
  <select id="theme-select">__THEME_OPTIONS__</select>
  <label>Sprache</label>
  <select id="lang-select">__LANG_OPTIONS__</select>
  <div class="hint">Baut das Dashboard automatisch innerhalb ca. 1 Sekunde neu
  auf (Live-Reload, kein Neustart nötig). Klappt der Neuaufbau ausnahmsweise
  nicht, hilft "Tab5 neu starten" auf der Dashboard-Seite als Rückfalloption.</div>
  <button onclick="saveSystemSettings()">Speichern</button>
  <div id="system-save-status" class="save-status"></div>
</div>

<script>
function modeLabel(mode) {
  if (mode === 'sta') return 'Verbunden (Station)';
  if (mode === 'ap') return 'Eigener Access Point';
  return 'Unbekannt';
}

function updateWifiStatus() {
  fetch('/api/wifi-status').then(r => r.json()).then(s => {
    let html = '<div class="metric-grid">' +
      '<div class="metric"><div class="label">Modus</div><div class="value">' + modeLabel(s.mode) + '</div></div>' +
      '<div class="metric"><div class="label">Netzwerk</div><div class="value">' + (s.ssid || '-') + '</div></div>' +
      '<div class="metric"><div class="label">IP-Adresse</div><div class="value">' + (s.ip || '-') + '</div></div>';
    if (s.rssi != null) {
      html += '<div class="metric"><div class="label">Signal</div><div class="value">' + s.rssi + ' dBm</div></div>';
    }
    html += '</div>';
    document.getElementById('wifi-status').innerHTML = html;
  }).catch(() => {
    document.getElementById('wifi-status').innerHTML = '<div class="hint">Gerät nicht erreichbar.</div>';
  });
}

function connectWifi() {
  const ssid = document.getElementById('wifiSsid').value;
  const password = document.getElementById('wifiPassword').value;
  if (!ssid) return;
  document.getElementById('connect-status').textContent = 'Verbinde... (bis zu 15s)';
  fetch('/wifi/connect', {
    method: 'POST', headers: {'Content-Type': 'application/x-www-form-urlencoded'},
    body: 'ssid=' + encodeURIComponent(ssid) + '&password=' + encodeURIComponent(password),
  }).then(r => r.json()).then(() => {
    // Verbindung läuft im Hintergrund weiter - Status pollt automatisch.
    // Achtung: falls die Verbindung klappt, wechselt das Gerät die IP -
    // diese Seite (auf 192.168.4.1) ist dann evtl. nicht mehr erreichbar,
    // das ist normal, kein Fehler.
    setTimeout(updateWifiStatus, 3000);
    setTimeout(updateWifiStatus, 8000);
    setTimeout(updateWifiStatus, 15000);
  }).catch(() => {
    document.getElementById('connect-status').textContent =
      'Anfrage gesendet - falls die Verbindung klappt, wechselt die IP-Adresse und diese Seite lädt nicht mehr neu (normal).';
  });
}

function startAp() {
  document.getElementById('ap-status').textContent = 'Wechsle in Access-Point-Modus...';
  fetch('/wifi/ap', {method: 'POST'}).then(r => r.json()).then(() => {
    updateWifiStatus();
    document.getElementById('ap-status').textContent = 'Access Point aktiv.';
  }).catch(() => {
    document.getElementById('ap-status').textContent = 'Gerät nicht erreichbar.';
  });
}

function saveSystemSettings() {
  const theme = document.getElementById('theme-select').value;
  const lang = document.getElementById('lang-select').value;
  fetch('/system/save', {
    method: 'POST', headers: {'Content-Type': 'application/x-www-form-urlencoded'},
    body: 'theme_mode=' + encodeURIComponent(theme) + '&language=' + encodeURIComponent(lang),
  }).then(r => r.json()).then(() => {
    document.getElementById('system-save-status').textContent =
      'Gespeichert um ' + new Date().toLocaleTimeString() + ' - wirkt innerhalb ca. 1s ohne Neustart.';
  }).catch(() => {
    document.getElementById('system-save-status').textContent = 'Gerät nicht erreichbar.';
  });
}

function saveServerSettings() {
  const enabled = document.getElementById('server-enabled').checked;
  const baseUrl = document.getElementById('server-base-url').value;
  fetch('/server/save', {
    method: 'POST', headers: {'Content-Type': 'application/x-www-form-urlencoded'},
    body: 'enabled=' + (enabled ? '1' : '0') + '&base_url=' + encodeURIComponent(baseUrl),
  }).then(r => r.json()).then(() => {
    document.getElementById('server-save-status').textContent =
      'Gespeichert - wirkt innerhalb ca. 1s ohne Neustart.';
  }).catch(() => {
    document.getElementById('server-save-status').textContent = 'Gerät nicht erreichbar.';
  });
}

updateWifiStatus();
setInterval(updateWifiStatus, 5000);
</script>
"""


async def _delayed_reboot():
    # Kurze Pause, damit _send() oben die HTTP-Antwort sicher noch über
    # die (dann gleich getrennte) Verbindung hinausschreiben kann, bevor
    # machine.reset() den kompletten Prozess sofort beendet.
    await asyncio.sleep(1)
    import machine
    machine.reset()


async def _send(writer, status, content_type, body):
    if isinstance(body, str):
        body = body.encode()
    writer.write("HTTP/1.1 {}\r\nContent-Type: {}\r\nContent-Length: {}\r\nConnection: close\r\n\r\n".format(
        status, content_type, len(body)).encode())
    writer.write(body)
    await writer.drain()


async def _handle_client(reader, writer):
    try:
        request_line = await reader.readline()
        if not request_line:
            writer.close()
            return
        parts = request_line.decode().split()
        method, raw_path = parts[0], parts[1]
        path, _, query = raw_path.partition("?")
        query_fields = _parse_qs(query) if query else {}

        headers = {}
        while True:
            line = await reader.readline()
            if line in (b"\r\n", b""):
                break
            k, v = line.decode().split(":", 1)
            headers[k.strip().lower()] = v.strip()

        body = b""
        if method == "POST":
            length = int(headers.get("content-length", 0))
            body = await reader.readexactly(length)

        if path == "/" and method == "GET":
            cfg = cfg_load()
            await _send(writer, "200 OK", "text/html; charset=utf-8", _start_page_html(cfg))

        elif path == "/dashboard" and method == "GET":
            cfg = cfg_load()
            await _send(writer, "200 OK", "text/html; charset=utf-8", _dashboard_page_html(cfg))

        elif path == "/switches" and method == "GET":
            cfg = cfg_load()
            await _send(writer, "200 OK", "text/html; charset=utf-8", _switches_page_html(cfg))

        elif path == "/system" and method == "GET":
            cfg = cfg_load()
            await _send(writer, "200 OK", "text/html; charset=utf-8", _system_page_html(cfg))

        elif path == "/wifi/connect" and method == "POST":
            fields = _parse_qs(body.decode())
            ssid = fields.get("ssid", "")
            password = fields.get("password", "")
            if ssid and wifi_mgr is not None:
                cfg = cfg_load()
                cfg.setdefault("wifi", {})["ssid"] = ssid
                cfg["wifi"]["password"] = password
                cfg_save(cfg)
                # Nicht-blockierend im Hintergrund verbinden (siehe
                # wifi_manager.py::connect_sta_async) - blockiert NICHT den
                # Webserver, kann aber bis zu 15s dauern, bis der Status
                # sich ändert. Läuft im selben asyncio-Loop wie main.py.
                asyncio.create_task(wifi_mgr.connect_sta_async(ssid, password))
            await _send(writer, "200 OK", "application/json", json.dumps({"ok": True}))

        elif path == "/wifi/ap" and method == "POST":
            if wifi_mgr is not None:
                wifi_mgr.start_ap()  # sofort, kein Warten nötig
            await _send(writer, "200 OK", "application/json", json.dumps({"ok": True}))

        elif path == "/api/wifi-status" and method == "GET":
            status = wifi_mgr.status() if wifi_mgr is not None else {}
            await _send(writer, "200 OK", "application/json", json.dumps(status))

        elif path == "/system/save" and method == "POST":
            fields = _parse_qs(body.decode())
            cfg = cfg_load()
            if fields.get("theme_mode") in THEME_LABELS:
                cfg["theme_mode"] = fields["theme_mode"]
            if fields.get("language") in LANGUAGE_LABELS:
                cfg["language"] = fields["language"]
            cfg_save(cfg)
            if request_reload:
                request_reload()
            await _send(writer, "200 OK", "application/json", json.dumps({"ok": True}))

        elif path == "/save" and method == "POST":
            fields = _parse_qs(body.decode())
            cfg = cfg_load()
            if fields.get("mode") in MODE_LABELS:
                cfg["room_sensor"]["mode"] = fields["mode"]
                if set_mode is not None:
                    set_mode(fields["mode"])
            if "web_chart_hours" in fields:
                try:
                    cfg["room_sensor"]["web_chart_hours"] = int(fields["web_chart_hours"])
                except ValueError:
                    pass
            if fields.get("iaq_baseline_mode") in IAQ_MODE_LABELS:
                cfg.setdefault("iaq", {})["baseline_mode"] = fields["iaq_baseline_mode"]
            cfg_save(cfg)
            await _send(writer, "200 OK", "application/json", json.dumps({"ok": True}))

        elif path == "/recalibrate" and method == "POST":
            if iaq_reset is not None:
                iaq_reset()
            await _send(writer, "200 OK", "application/json", json.dumps({"ok": True}))

        elif path == "/sensor-offsets/save" and method == "POST":
            fields = _parse_qs(body.decode())
            try:
                offsets = {
                    "temp_c": float(fields.get("temp_c", 0) or 0),
                    "humidity": float(fields.get("humidity", 0) or 0),
                    "pressure_hpa": float(fields.get("pressure_hpa", 0) or 0),
                }
            except ValueError:
                await _send(writer, "400 Bad Request", "application/json",
                             json.dumps({"ok": False, "msg": "Ungültige Zahl"}))
                return
            if set_sensor_offsets is not None:
                set_sensor_offsets(offsets)
            await _send(writer, "200 OK", "application/json", json.dumps({"ok": True}))

        elif path == "/layout/dashboard/save" and method == "POST":
            try:
                widgets = json.loads(body.decode())
            except Exception:
                widgets = None
            if not isinstance(widgets, list):
                await _send(writer, "400 Bad Request", "application/json", json.dumps({"ok": False}))
            else:
                cfg = cfg_load()
                cfg.setdefault("screens", {}).setdefault("dashboard", {})["widgets"] = widgets
                cfg_save(cfg)
                if request_reload:
                    request_reload()
                await _send(writer, "200 OK", "application/json", json.dumps({"ok": True}))

        elif path == "/api/status" and method == "GET":
            state = get_state() if get_state else {}
            await _send(writer, "200 OK", "application/json", json.dumps(state))

        elif path == "/api/history" and method == "GET":
            try:
                hours = float(query_fields.get("hours", 6))
            except ValueError:
                hours = 6
            rows = sd_reader.read_range(hours=hours) if sd_reader else []
            await _send(writer, "200 OK", "application/json", json.dumps(rows))

        elif path == "/api/ha-states" and method == "GET":
            cfg = cfg_load()
            entity_ids = [w["entity_id"] for w in
                          cfg.get("screens", {}).get("dashboard", {}).get("widgets", [])
                          if "entity_id" in w]
            states = get_ha_states(entity_ids) if get_ha_states and entity_ids else {}
            await _send(writer, "200 OK", "application/json", json.dumps(states))

        elif path == "/api/atom-status" and method == "GET":
            status = atom_get_status() if atom_get_status else {"ok": False, "msg": "nicht verdrahtet"}
            await _send(writer, "200 OK", "application/json", json.dumps(status))

        elif path == "/api/climate-ext-status" and method == "GET":
            # Für die Start-Seite (Live-Werte + Grafiken) - fragt das
            # externe Gerät (siehe widget_sources.py::fetch_climate_ext(),
            # z.B. Core2-MiniDash) frisch ab, unabhängig vom (selteneren)
            # Abruf-Takt des Dashboard-Widgets selbst.
            cfg = cfg_load()
            widgets = cfg.get("screens", {}).get("dashboard", {}).get("widgets", [])
            climate_ext_widget = next((w for w in widgets if w.get("type") == "climate_ext"), None)
            if climate_ext_widget is None:
                status = {"ok": False, "msg": "Kein Raumklima-Extern-Widget konfiguriert"}
            else:
                status = widget_sources.fetch_climate_ext(climate_ext_widget)
            await _send(writer, "200 OK", "application/json", json.dumps(status))

        elif path.startswith("/api/atom-toggle/") and method == "POST":
            try:
                relay_id = int(path.rsplit("/", 1)[-1])
            except ValueError:
                relay_id = 0
            if relay_id and atom_toggle:
                result = atom_toggle(relay_id)
            else:
                result = {"ok": False, "msg": "nicht verdrahtet"}
            await _send(writer, "200 OK", "application/json", json.dumps(result))

        elif path == "/atom/save" and method == "POST":
            fields = _parse_qs(body.decode())
            cfg = cfg_load()
            cfg.setdefault("atom", {})["enabled"] = fields.get("enabled") == "1"
            if "base_url" in fields:
                cfg["atom"]["base_url"] = fields["base_url"]
            cfg_save(cfg)
            if request_reload:
                request_reload()
            await _send(writer, "200 OK", "application/json", json.dumps({"ok": True}))

        elif path == "/server/save" and method == "POST":
            fields = _parse_qs(body.decode())
            cfg = cfg_load()
            cfg.setdefault("server", {})["enabled"] = fields.get("enabled") == "1"
            if "base_url" in fields:
                cfg["server"]["base_url"] = fields["base_url"]
            cfg_save(cfg)
            if request_reload:
                request_reload()
            await _send(writer, "200 OK", "application/json", json.dumps({"ok": True}))

        elif path == "/api/sync" and method == "GET":
            # Vom gepairten Atom-Relais-Board aufgerufen, wenn SICH DORT
            # lokal etwas geändert hat (Taster-Klick oder Atom-eigenes
            # Web-UI) - siehe atom_client.py-Docstring und die
            # sync_client.py::notify_partner()-Logik im Atom-Projekt
            # ("GET /api/sync?relay=<1|2>&state=<0|1>" an "partner_ip",
            # die dort auf DIESES Tab5 zeigen muss). Aktualisiert NUR die
            # Anzeige - löst KEINEN erneuten toggle() aus, sonst würden
            # sich Atom und Tab5 gegenseitig endlos hin- und herschalten.
            try:
                relay_id = int(query_fields.get("relay", 0))
                is_on = query_fields.get("state") == "1"
            except ValueError:
                relay_id, is_on = 0, False
            if relay_id and relay_set_from_partner is not None:
                relay_set_from_partner(relay_id, is_on)
            await _send(writer, "200 OK", "text/plain", "OK")

        elif path == "/api/geocode" and method == "GET":
            # Standortsuche fürs Wetter-/Luftqualität-Widget im /dashboard-
            # Editor (siehe widget_sources.py::geocode_search, Open-Meteo).
            q = query_fields.get("q", "")
            try:
                results = widget_sources.geocode_search(q) if q else []
            except Exception as e:
                results = []
                print("Geocode-Suche fehlgeschlagen:", e)
            await _send(writer, "200 OK", "application/json", json.dumps({"results": results}))

        elif path == "/api/ags-search" and method == "GET":
            # Regionalschlüssel-Suche fürs Warnungen-Widget im /dashboard-
            # Editor (siehe widget_sources.py::ags_search, openplzapi.org).
            q = query_fields.get("q", "")
            try:
                results = widget_sources.ags_search(q) if q else []
            except Exception as e:
                results = []
                print("ARS-Suche fehlgeschlagen:", e)
            await _send(writer, "200 OK", "application/json", json.dumps({"results": results}))

        elif path == "/system/reboot" and method == "POST":
            # Antwort ZUERST verschicken, dann erst neu starten (siehe
            # _delayed_reboot) - sonst bekäme der Browser gar keine
            # Bestätigung mehr, da machine.reset() sofort alles beendet.
            print("Web-UI: Neustart angefordert...")
            await _send(writer, "200 OK", "application/json", json.dumps({"ok": True}))
            asyncio.create_task(_delayed_reboot())

        else:
            await _send(writer, "404 Not Found", "text/plain", "Not found")

    except Exception as e:
        print("Web-UI-Fehler:", e)
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass


async def run(port=80):
    # Diagnose-Print VOR start_server(): damit im Log sichtbar wird, ob der
    # Task überhaupt anläuft, oder ob start_server() selbst hängt (z.B.
    # OSError: EADDRINUSE-Fall aus dem Handoff/Runbook - Port von einem
    # vorherigen main.py-Lauf noch belegt, weil nur ein Python-`reset`
    # statt eines echten Kabel-raus/rein-Power-Cycles gemacht wurde; der
    # darunterliegende lwIP-Netzwerk-Stack wird von einem reinen
    # MicroPython-Reset nicht zwangsläufig mitzurückgesetzt).
    print("Web-UI: starte Server auf Port", port, "...")
    try:
        server = await asyncio.start_server(_handle_client, "0.0.0.0", port)
    except Exception as e:
        print("Web-UI: start_server() fehlgeschlagen:", repr(e))
        raise
    print("Tab5 Web-UI läuft auf Port", port)
    while True:
        await asyncio.sleep(3600)
