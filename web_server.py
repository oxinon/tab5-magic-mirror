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
  (Alle POST-Routen erfordern seit Optimierungs-Backlog Punkt 6 einen
  gültigen "X-CSRF-Token"-Header, siehe CSRF_TOKEN/_generate_csrf_token()
  weiter unten - wird von jeder selbst ausgelieferten Seite automatisch
  mitgeschickt, nicht einzeln pro Route dokumentiert.)
  GET  /                        -> Start: Sensorwerte, Grafiken, IAQ-Einstellungen
                                    (Phase B, siehe HANDOFF.md: übergangsweise
                                    identischer Inhalt wie /sensors, bis die
                                    Start-Seite zum Dashboard-Live-Spiegel wird)
  GET  /sensors                  -> Sensor-Dashboard: Sensorwerte, Grafiken,
                                    IAQ-Einstellungen, Datenerfassungs-Optionen
                                    (Phase B - Ziel-Adresse, Start-Seite zieht
                                    hierher um)
  GET  /dashboard                -> Kachel-Layout-Editor (Multi-Dashboard-Feature:
                                     ?profile=<id> wählt, welches der 2 Profile
                                     bearbeitet wird, siehe config.py "screens.dashboards")
  GET  /switches                 -> Licht/Steckdose (Atom-Relais-Board) schalten + Verbindung einstellen
  GET  /system                   -> WLAN + Theme + Sprache
  POST /wifi/connect             -> auf eingegebenes Heimnetz umschalten (nicht-blockierend)
  POST /wifi/ap                  -> sofort in den eigenen Access-Point-Modus wechseln
  GET  /api/system-info           -> freier Speicher (gc.mem_free()), für die
                                      Speicher-Karte auf /system
  GET  /api/wifi-status          -> aktueller WLAN-Status als JSON (Polling)
  POST /system/save              -> Theme/Sprache speichern (löst Live-Reload aus, siehe main.py)
  POST /system/reboot             -> Tab5 neu starten (z.B. nach Layout-/Widget-Änderungen,
                                      damit nicht jedes Mal das USB-Kabel gezogen werden muss)
  POST /save                     -> Sensor-Modus/Grafik-Zeitraum/IAQ-Modus
  POST /recalibrate              -> IAQ-Baseline sofort neu kalibrieren
  POST /sensor-offsets/save      -> Korrekturfaktoren für lokalen BME688 speichern (wirkt sofort)
  GET  /api/list-logos             -> bereits hochgeladene Logo-.bin-Dateien auflisten
                                     (Name + Größe), Ergänzung zu Punkt 7
  POST /api/delete-logo            -> eine hochgeladene Logo-.bin-Datei löschen
  POST /api/upload-logo           -> PNG-Datei hochladen, wird serverseitig ins
                                     RGB565A8-Binärformat fürs Logo-Widget umgewandelt
                                     (Optimierungs-Backlog Punkt 7, siehe png_convert.py)
  POST /layout/dashboard/save    -> Widget-Liste + Name EINES Profils ersetzen
                                     (Body: {"profile_id","name","widgets"})
  POST /dashboard/set-active     -> welches Profil am Gerät angezeigt wird umschalten
                                     (zusätzlich zum Burger-Menü-Button am Tab5 selbst)
  GET  /api/status                -> aktuelle Messwerte aller Sensoren als JSON
  GET  /api/history?hours=N       -> historische Messwerte von der SD-Karte als JSON
  GET  /api/ha-states              -> aktuelle Home-Assistant-Zustände der im
                                      Dashboard konfigurierten ha_entity/ha_switch-Kacheln
  GET  /api/geocode?q=...          -> Standortsuche für Wetter/Luftqualität (Open-Meteo)
  GET  /api/ags-search?q=...       -> Regionalschlüssel-Suche für Warnungen (openplzapi.org)
  GET  /api/sync?relay=N&state=0|1 -> Push-Update vom gepairten Atom-Relais-Board (siehe atom_client.py)
  GET  /api/atom-status            -> aktueller Zustand beider Atom-Relais (für /switches)
  GET  /api/climate-ext-status     -> aktueller Zustand des externen Raumklima-Geräts (für die Start-Seite)
  GET  /api/external-sensors-status -> aktuelle Werte ALLER konfigurierten externen
                                       Sensor-Quellen (Phase A, config.py room_sensor.
                                       external_sources) für die /sensors-Seite -
                                       liest aus dem Hintergrund-Ringpuffer, kein Live-Abruf
  GET  /static/<datei>              -> statische Dateien (Chart.js, gemeinsames Stylesheet,
                                       pro Seite ausgelagerte .js-Dateien - Optimierungs-
                                       Backlog Punkte 1 und 5/7), liegen auf dem Flash unter
                                       STATIC_DIR, siehe README für den Upload-Weg
  GET  /api/widget-snapshot         -> zuletzt abgerufene Roh-Daten ALLER Hintergrund-
                                       abgerufenen Dashboard-Widgets (Wetter, News, ...)
                                       für den Live-Spiegel auf "/" - kein Live-Abruf
  POST /api/atom-toggle/<1|2>      -> ein Atom-Relais umschalten (für /switches)
  POST /atom/save                  -> Atom-Board-Verbindung speichern (enabled/base_url)
  POST /server/save                -> Magic-Mirror-Server-Verbindung speichern (enabled/base_url, für Server-Status-Kachel)
  GET  /notes                      -> Notizen-Seite (Einträge abhaken/bearbeiten/hinzufügen/löschen)
  POST /notes/save                 -> Notizen-Liste komplett speichern (wirkt sofort)

Von main.py zu setzen (siehe README.md für ein Wiring-Beispiel):
  get_state      - Funktion ohne Argumente -> dict mit allen aktuellen Sensor-Messwerten
  get_ha_states  - Funktion(entity_ids) -> dict {entity_id: state}
  sd_reader      - sensors.sd_reader.SDReader-Instanz
  get_external_sensors_state - Funktion() -> Liste aktueller externer
                   Sensor-Werte (Phase A, siehe Docstring der Modulvariable
                   weiter unten)
  get_widget_snapshot - Funktion() -> Dict {widget_id: Roh-Daten} für den
                   Live-Spiegel (Phase B, siehe Docstring der Modulvariable
                   weiter unten)
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
import png_convert
import fetch_worker

# Optimierungs-Backlog Punkt 7 (siehe HANDOFF.md/"/api/upload-logo"-Route
# weiter unten) - großzügig genug für ein PNG-Logo-Upload (siehe
# png_convert.MAX_WIDTH/MAX_HEIGHT), aber nicht unbegrenzt.
MAX_POST_BODY_BYTES = 400 * 1024

# Lese-Timeouts: ohne sie wartet ein Handler auf eine Verbindung, die nie
# etwas sendet (z.B. spekulative, leere Browser-Verbindungen), unbegrenzt -
# jede haengende Verbindung belegt einen der wenigen lwIP-Sockets des ESP32
# und kann Web-UI UND ausgehende Abrufe ausbremsen.
REQUEST_LINE_TIMEOUT_S = 10   # bis Anfrage-/Header-Zeile ankommt
BODY_TIMEOUT_S = 30           # bis der (ggf. 400-KB-)Body komplett da ist
MAX_HEADER_LINES = 60

# Vom Watchdog in main.py::wifi_watchdog_task() gelesen: wann zuletzt eine
# Anfrage einging (kein Retry-Versuch, solange jemand das Web-UI benutzt) und
# ob gerade ein vom Nutzer angestossener WLAN-Wechsel laeuft.
last_request_time = 0
wifi_change_in_progress = False

# Maximale Anzahl Notizen im todo-Widget (Nutzerwunsch: bis zu 20). Mehr als
# eine Seite werden auf dem Gerät automatisch abwechselnd angezeigt (siehe
# widget_catalog.py::_build_todo()). Die Grenze gilt serverseitig (Speichern)
# UND wird der Notizen-Seite als data-max mitgegeben (kein Wert in notes.js
# fest verdrahtet).
MAX_TODO_ITEMS = 20

try:
    import os as _os
except ImportError:
    _os = None

try:
    import time as _time
except ImportError:
    _time = None


try:
    import hashlib as _hashlib
except ImportError:
    try:
        import uhashlib as _hashlib
    except ImportError:
        _hashlib = None

try:
    import binascii as _binascii
except ImportError:
    import ubinascii as _binascii

# Widget-Felder mit geheimen Werten: werden NIE an den Browser ausgeliefert
# (Editor zeigt nur "gespeichert", siehe _secret_placeholder()) und beim
# Speichern serverseitig aus der gespeicherten Config uebernommen, wenn der
# Browser sie nicht mitschickt.
SECRET_WIDGET_KEYS = ("ical_url", "api_key")

AUTH_USER = "admin"
AUTH_HASH_ROUNDS = 2000
AUTH_MAX_FAILS = 5
AUTH_LOCK_S = 60
_auth_fails = {}    # peer-ip -> [anzahl_fehlversuche, zeitpunkt_erster_fehler]
_auth_cache = None  # (auth_hash, letzter_gueltiger_authorization_header)


def _consteq(a, b):
    """Vergleich ohne vorzeitigen Abbruch (Timing) - fuer Token/Passwort-Hash."""
    if a is None or b is None:
        return False
    a = a if isinstance(a, str) else str(a)
    b = b if isinstance(b, str) else str(b)
    if len(a) != len(b):
        return False
    diff = 0
    for x, y in zip(a, b):
        diff |= ord(x) ^ ord(y)
    return diff == 0


def _hash_password(salt, password):
    """Gesalzener, iterierter SHA-256 (verlangsamt Ausprobieren)."""
    d = (salt + ":" + password).encode()
    salt_b = salt.encode()
    for _ in range(AUTH_HASH_ROUNDS):
        d = _hashlib.sha256(d + salt_b).digest()
    return _binascii.hexlify(d).decode()


def _generate_csrf_token():
    """Optimierungs-Backlog Punkt 6 (siehe HANDOFF.md) - EIN Token pro
    Boot (kein Session-Konzept vorhanden/nötig, siehe Modul-Docstring des
    CSRF-Abschnitts unten), bei jedem Seitenaufruf ins HTML eingebettet
    und bei jedem POST per Header verglichen. os.urandom() ist die
    bevorzugte Quelle (kryptographisch brauchbarer Zufall) - falls auf
    dieser Firmware nicht vorhanden, ein schwächerer Rückfall, der für
    den hier beabsichtigten, bescheidenen Zweck (siehe Docstring: "nur
    relevant, falls das Gerät in einem nicht 100% vertrauenswürdigen Netz
    steht", NICHT als Schutz gegen einen gezielten Angreifer im selben
    Netz gedacht) ausreicht."""
    try:
        return "".join("%02x" % b for b in _os.urandom(16))
    except Exception:
        # Rueckfall (os.urandom fehlt): so viel unvorhersagbare Zeit-/Speicher-
        # Information wie moeglich mischen und durch SHA-256 ziehen.
        parts = [str(_time.time() if _time else 0), str(id(_os))]
        try:
            parts.append(str(_time.ticks_us()))
        except Exception:
            pass
        try:
            import gc
            parts.append(str(gc.mem_free()))
        except Exception:
            pass
        seed = "-".join(parts)
        if _hashlib is not None:
            return _binascii.hexlify(_hashlib.sha256(seed.encode()).digest()).decode()[:32]
        return "".join("%02x" % (ord(c) % 256) for c in seed)[:32]


# ---------------------------------------------------------------------------
# CSRF-Schutz für alle POST-Endpunkte (Optimierungs-Backlog Punkt 6, siehe
# HANDOFF.md) - "Einfacher, bei Seitenaufruf generierter Token, der in
# Formularen mitgesendet wird": EIN Token pro Boot (siehe
# _generate_csrf_token()-Docstring, warum kein Pro-Session-Token nötig
# ist), von JEDER Seite über PAGE_SHELL eingebettet (siehe __CSRF_TOKEN__
# unten) und per window.fetch()-Patch automatisch als "X-CSRF-Token"-
# Header an jeden POST angehängt (siehe PAGE_SHELL - deckt dadurch ALLE
# bestehenden fetch()-Aufrufe in allen Seiten ab, ohne jeden einzelnen
# anfassen zu müssen). Serverseitig zentral in _handle_client() geprüft,
# BEVOR irgendeine POST-Route überhaupt behandelt wird - kein einzelner
# Routen-Handler muss selbst etwas dafür tun.
# Schützt gegen Cross-Site-Request-Forgery (eine andere, im selben
# Browser geöffnete bösartige Seite, die im Hintergrund einen Request an
# dieses Gerät abschickt) - NICHT gegen einen Angreifer, der bereits im
# selben Netz mitlesen/den Datenverkehr manipulieren kann (dafür bräuchte
# es HTTPS, was auf diesem Gerät nicht vorgesehen ist).
# ---------------------------------------------------------------------------
CSRF_TOKEN = _generate_csrf_token()

# Optimierungs-Backlog Punkte 1 und 5/7 (siehe HANDOFF.md/generische
# "/static/<datei>"-Route weiter unten) - alle statischen Dateien
# (Chart.js, gemeinsames Stylesheet, pro Seite ausgelagerte .js-Dateien)
# liegen unter diesem einen Verzeichnis auf dem Flash.
STATIC_DIR = "/flash/static"
STATIC_CONTENT_TYPES = {
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
}

get_state = None
cfg_load_ro = None   # main.py: config.load_readonly (gecacht, nur lesen)
get_ha_states = None
sd_reader = None
# main.py: Funktion(hours) -> Liste von Dicts (wie sd_reader.read_range), aus dem
# RAM-Verlauf - Rueckfall fuer die Web-Grafiken, wenn keine SD-Daten vorliegen.
get_ram_history = None
# main.py setzt dies auf eine Funktion() -> Liste von Dicts, eine pro
# konfigurierter externer Quelle (siehe config.py room_sensor.
# external_sources, Phase A) - jeweils {"name", "ok", "temp_c",
# "humidity", "pressure_hpa", "iaq_score"} bzw. {"name", "ok": False,
# "msg": ...}. Liefert den letzten Wert aus dem jeweiligen In-RAM-
# Ringpuffer (main.py::external_histories) - KEIN Live-Abruf hier, der
# läuft schon unabhängig im Hintergrund (main.py::
# external_sensor_log_task()) - so blockiert ein langsam antwortendes
# externes Gerät nicht auch noch diese Webserver-Anfrage (anders als
# /api/climate-ext-status weiter unten, das bewusst live abfragt, weil
# es das seltener genutzte Dashboard-Widget betrifft, nicht die
# durchgehend laufende Sensor-Seite).
get_external_sensors_state = None
# main.py setzt dies auf dashboard_screen.get_snapshot (siehe
# screens/widget_catalog.py::get_snapshot()) - Funktion() -> Dict
# {widget_id: zuletzt empfangenes Roh-Daten-Dict}, für ALLE Hintergrund-
# abgerufenen Widget-Typen (Wetter, News, Krypto, ...). Für den Web-UI-
# Live-Spiegel auf "/" (Phase B) - liest nur, was ohnehin schon für das
# Tab5-Display abgerufen wurde, kein zusätzlicher Netzwerk-Traffic.
get_widget_snapshot = None
set_mode = None
iaq_reset = None
set_sensor_offsets = None
wifi_mgr = None
cfg_load = None
cfg_save = None
# main.py setzt dies auf config.flush_pending - siehe _delayed_reboot()
# unten (Optimierungs-Backlog Punkt 3, Debounced Config-Save).
cfg_flush_pending = None
# main.py setzt dies auf gc.mem_free (MicroPythons eingebautes gc-Modul) -
# für die neue Speicher-Karte auf /system (Optimierungs-Backlog Punkt 4,
# siehe HANDOFF.md - macht die Wirkung der neu eingebauten gc.collect()-
# Aufrufe an mehreren Stellen sichtbar).
get_mem_free = None
# main.py setzt dies auf eine Funktion() -> {"percent", "voltage_mv"} -
# regelmäßig vom Battery-Task in main.py aktualisiert (siehe dort). Beide
# Werte können None sein (z.B. direkt nach dem Boot, bevor der erste
# Zyklus gelaufen ist, oder falls M5.Power.getBatteryVoltage() auf einem
# anderen Gerät fehlschlägt).
get_battery_info = None
# Push-Updates von einem der gepairten Atom-Relais-Boards (Phase C, siehe
# atom_client.py-Docstring und die "/api/sync"-Route unten) -
# Funktion(board_index, relay_id, is_on).
relay_set_from_partner = None
# Für die /switches-Seite und den Live-Spiegel - Funktionen
# atom_get_status(board_index) / atom_toggle(board_index, relay_id), siehe
# atom_client.py::AtomClient.get_status()/toggle() (main.py verdrahtet pro
# Board-Index den jeweils richtigen Client, siehe main.py::
# _atom_get_status()/_atom_toggle()).
atom_get_status = None
atom_toggle = None
# Funktion() -> aktuelle Liste der config.json "atom_boards"-Einträge, für
# _match_atom_board_by_peer() weiter unten (Zuordnung eines eingehenden
# "/api/sync"-Aufrufs zu einem Board anhand der Quell-IP).
get_atom_boards_cfg = None
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
    "logo": "Logo (eigenes Bild)",
    "env_sensor_mirror": "Umweltstation (Zusammenfassung)",
    "relay_pair": "Atom-Relais-Paar (Licht/Steckdose o.ä.)",
}
# Widget-Typen, die breiter als 1 Spalte sein dürfen (siehe SPAN_LIMITS in
# screens/widget_catalog.py) - dupliziert statt importiert, damit web_server.py
# nicht von einem lvgl-nahen Modul abhängt.
DASHBOARD_SPAN_LIMITS = {"news": 4, "compliments": 4, "quote": 4, "clock": 2, "todo": 2, "logo": 2}
# Analoge Begrenzung für row_span (Nutzerwunsch: Notiz-Widget soll auch
# nach unten 2 Kacheln nutzen dürfen) - siehe screens/widget_catalog.py::
# ROW_SPAN_LIMITS für dieselbe Begrenzung auf der Geräte-Seite.
DASHBOARD_ROW_SPAN_LIMITS = {"todo": 2}
HA_TYPE_LABELS = {"ha_entity": "Anzeige (Sensor)", "ha_switch": "Schalter (Home Assistant)"}

# Gruppierung für den /dashboard-Editor (siehe _dashboard_layout_rows_html) -
# auf Nutzerwunsch zurück zu thematischen, aufklappbaren Kategorien statt
# der reinen config.json-Reihenfolge aus Phase B. ha_entity/ha_switch
# laufen weiterhin separat (eigene, freie Titel/Entity-Felder statt eines
# festen Katalog-Eintrags) und bekommen ihre eigene Kategorie ganz unten.
DASHBOARD_CATEGORIES = [
    ("Uhr & Kalender", ("clock", "calendar")),
    ("News & Finanzen", ("news", "crypto", "stocks", "quote")),
    ("Wetter, Warnungen & Sonstige Online-Quellen",
     ("weather", "air_quality_mirror", "warnings", "elbe_pegel", "ews", "defcon")),
    ("Sensoren (Tab5-eigen & extern)",
     ("climate", "air_quality", "acoustic", "equalizer", "acceleration", "climate_ext",
      "env_sensor_mirror")),
    # Beide Widgets beziehen ihre Daten vom selben aida_sse_server.py
    # (/api/server-status bzw. /sse) - daher gemeinsame Kategorie.
    ("PC & Server (AIDA-SSE-Server)", ("server_status", "pc_status")),
    ("Schalter & Relais", ("relay_pair",)),
    ("Notizen & Komplimente", ("compliments", "todo", "logo")),
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


def _esc(value):
    """HTML-Escaping fuer JEDEN Wert, der aus der Config/vom Nutzer stammt
    und in HTML-Text oder ein "..."-Attribut eingesetzt wird. Ohne das
    zerstoert z.B. eine Notiz mit Anfuehrungszeichen (Milch "Bio") das
    value-Attribut: der Browser zeigt nur "Milch " an, und das naechste
    Speichern (collectNotes()) schreibt die abgeschnittene Fassung zurueck
    (Datenverlust) - ausserdem Einfallstor fuer XSS."""
    if value is None:
        return ""
    s = value if isinstance(value, str) else str(value)
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
             .replace('"', "&quot;").replace("'", "&#39;"))


def _esc_deep(obj):
    """Wie _esc(), aber rekursiv fuer Dicts/Listen (Zahlen/Bools bleiben)."""
    if isinstance(obj, str):
        return _esc(obj)
    if isinstance(obj, list):
        return [_esc_deep(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _esc_deep(v) for k, v in obj.items()}
    return obj


def _json_script(obj):
    """json.dumps fuer die Einbettung in ein <script>-Element: "<", ">" und
    "&" werden als \\u-Escapes geschrieben, damit ein Wert wie "</script>"
    das Element nicht vorzeitig beendet."""
    return json.dumps(obj).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


_LOGO_NAME_CHARS = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"


def _safe_logo_basename(name):
    """Reiner Logo-Dateiname ohne Endung, nur [A-Za-z0-9_-], max. 32
    Zeichen - oder None. Der Name landet spaeter in der Editor-Seite; ein
    Name mit ' oder < war dort ein Einfallstor fuer XSS."""
    name = (name or "").strip().rsplit("/", 1)[-1]
    if name.endswith(".bin"):
        name = name[:-4]
    if not name or len(name) > 32:
        return None
    for ch in name:
        if ch not in _LOGO_NAME_CHARS:
            return None
    return name


def _option(value, selected_value, label):
    sel = "selected" if str(value) == str(selected_value) else ""
    return '<option value="%s" %s>%s</option>' % (_esc(value), sel, _esc(label))


def _position_grid_html(cols, rows, selected):
    """Klick-Raster statt Dropdown/Textfeld für die Widget-Position (Phase
    B, siehe HANDOFF.md - Vorbild oxinon/magic-mirror-3000). Ein
    verstecktes <input> trägt weiterhin den eigentlichen Wert
    ("r{row}-c{col}", wie zuvor beim <select>) - die komplette bestehende
    JS-Logik (saveDashboardLayout(), recomputeSpanOptions(), das
    "change"-Event) bleibt dadurch UNVERÄNDERT, sie liest/schreibt einfach
    weiterhin '.position-select'.value, ob das nun ein <select> oder ein
    <input type="hidden"> ist, macht dafür keinen Unterschied."""
    cells = []
    for r in range(1, rows + 1):
        for c in range(1, cols + 1):
            pos = "r%d-c%d" % (r, c)
            cls = "pos-cell selected" if pos == selected else "pos-cell"
            cells.append('<button type="button" class="%s" data-pos="%s"></button>' % (cls, pos))
    return (
        '<input type="hidden" class="position-select" value="%s">'
        '<div class="pos-grid" style="grid-template-columns:repeat(%d, 20px);" data-cols="%d" data-rows="%d">%s</div>'
    ) % (selected, cols, cols, rows, "".join(cells))


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
__CHART_JS_TAG__
<link rel="stylesheet" href="/static/style.css">
</head>
<body>
<nav>
  <a href="/" class="__NAV_START__">Start</a>
  <a href="/sensors" class="__NAV_SENSORS__">Sensoren</a>
  <a href="/dashboard" class="__NAV_DASHBOARD__">Dashboard</a>
  <a href="/notes" class="__NAV_NOTES__">Notizen</a>
  <a href="/switches" class="__NAV_SWITCHES__">Schalter</a>
  <a href="/system" class="__NAV_SYSTEM__">System</a>
</nav>
<h1>__TITLE__</h1>
<script>
window.CSRF_TOKEN = "__CSRF_TOKEN__";
</script>
<script src="/static/csrf.js"></script>
__BODY__
</body></html>"""


def _shell(title, body, active, needs_chart=False):
    html = PAGE_SHELL.replace("__TITLE__", _esc(title)).replace("__BODY__", body)
    html = html.replace("__CSRF_TOKEN__", CSRF_TOKEN)
    # Chart.js (Optimierungs-Backlog Punkt 1, siehe HANDOFF.md) wird jetzt
    # LOKAL ausgeliefert (siehe "/static/chart.min.js"-Route) statt von
    # cdnjs.cloudflare.com - macht das Projekt offline-fähig UND spart auf
    # jeder Seite, die es gar nicht braucht (alle außer /sensors), einen
    # ~200KB-Download, der vorher unbedingt auf JEDER Seite mitgeladen
    # wurde, egal ob dort überhaupt ein <canvas> existierte.
    html = html.replace(
        "__CHART_JS_TAG__",
        '<script src="/static/chart.min.js"></script>' if needs_chart else "")
    for key in ("start", "sensors", "dashboard", "notes", "switches", "system"):
        html = html.replace("__NAV_%s__" % key.upper(), "active" if key == active else "")
    return html


# ---------------------------------------------------------------------------
# Sensor-Dashboard-Seite (Phase B, siehe HANDOFF.md) - Live-Sensorwerte,
# Zeitverlauf-Grafiken, IAQ-Kalibrierung UND alle Datenerfassungs-
# Einstellungen, umgezogen von der früheren Start-Seite. Anders als dort
# jetzt mit dynamisch VIELEN externen Quellen (0-2, siehe config.py
# room_sensor.external_sources, Phase A) statt fest einer einzigen
# hartcodierten "Extern (Core2-MiniDash)"-Linie.
# ---------------------------------------------------------------------------
def _sensors_page_html(cfg):
    return _shell("Tab5 - Sensor-Dashboard", _build_sensors_body(cfg), "sensors", needs_chart=True)


# ---------------------------------------------------------------------------
# Start-Seite (Phase B, siehe HANDOFF.md) - "Live-Spiegel" des aktuellen
# Tab5-Dashboard-Zustands: zeigt die Kachel-Anordnung (Position/Breite) der
# AKTUELL AKTIVEN Widgets, MIT echten Live-Werten für praktisch alle Typen:
# - Direkt aus main.py-Funktionen (schon vor Phase B vorhanden): climate,
#   air_quality, acoustic, acceleration, climate_ext, ha_entity, ha_switch,
#   env_sensor_mirror (nutzt dieselben Werte wie climate/acceleration).
# - Aus screens/widget_catalog.py::get_snapshot() (neu, siehe dort) für
#   ALLE Hintergrund-abgerufenen Typen (siehe _BACKGROUND_FETCHERS dort):
#   weather, news, crypto, stocks, quote, calendar, warnings,
#   air_quality_mirror, elbe_pegel, ews, defcon, server_status, pc_status.
# - Direkt aus der Config (kein Abruf nötig): compliments, todo.
# NICHT abgedeckt: clock (zeigt stattdessen die Browser-Uhrzeit, praktisch
# gleichwertig), logo (Bilddaten - eigener Endpunkt wäre nötig, siehe
# Optimierungs-Backlog).
# ---------------------------------------------------------------------------
# Typen, die aus get_widget_snapshot() (siehe main.py/widget_catalog.py)
# bedient werden, per data-id (Widget-id) statt data-type zugeordnet.
SNAPSHOT_TYPES = ("weather", "news", "crypto", "stocks", "quote", "calendar",
                   "warnings", "air_quality_mirror", "elbe_pegel", "ews",
                   "defcon", "server_status", "pc_status")
# Typen mit Live-Werten direkt aus main.py-Funktionen (kein Snapshot nötig).
MIRROR_DIRECT_TYPES = ("climate", "air_quality", "acoustic", "acceleration",
                        "climate_ext", "env_sensor_mirror")
# Typen, deren Inhalt schon beim Rendern aus der Config feststeht (siehe
# _mirror_grid_html() - "items" ist in config.json schon vorhanden, kein
# Abruf/Snapshot nötig).
MIRROR_CONFIG_TYPES = ("compliments", "todo")


def _mirror_grid_html(cfg):
    # Multi-Dashboard-Feature (siehe HANDOFF.md) - der Live-Spiegel zeigt
    # immer das Profil, das gerade AM GERÄT aktiv ist (nicht zwangsläufig
    # "das erste") - dieselbe Logik wie main.py::_get_active_dashboard_
    # profile(), hier auf der Web-Seite dupliziert statt importiert (kein
    # main.py-Import in web_server.py, siehe Modul-Docstring: bewusst
    # unabhängige Kontrollebene).
    profiles = cfg.get("screens", {}).get("dashboards", [])
    active_id = cfg.get("screens", {}).get("active_dashboard_id")
    screen_cfg = next((p for p in profiles if p.get("id") == active_id),
                       profiles[0] if profiles else {})
    grid = screen_cfg.get("grid", {"cols": 4, "rows": 3})
    cols, rows = grid["cols"], grid["rows"]
    widgets = [w for w in screen_cfg.get("widgets", []) if w.get("enabled", True)]

    tiles = []
    for w in widgets:
        wtype = w.get("type")
        wid = w.get("id", "")
        row, col = (int(p) for p in w.get("position", "r1-c1").replace("r", "").split("-c"))
        col_span = min(w.get("col_span", 1), DASHBOARD_SPAN_LIMITS.get(wtype, 1), cols - col + 1)
        row_span = min(w.get("row_span", 1), DASHBOARD_ROW_SPAN_LIMITS.get(wtype, 1), rows - row + 1)

        is_ha = wtype in ("ha_entity", "ha_switch")
        if is_ha:
            title = w.get("title") or HA_TYPE_LABELS.get(wtype, wtype)
        else:
            title = DASHBOARD_WIDGET_LABELS.get(wtype, wtype)

        data_attrs = 'data-type="%s" data-id="%s"' % (_esc(wtype), _esc(wid))
        if wtype in ("ha_entity", "ha_switch"):
            data_attrs += ' data-entity="%s"' % _esc(w.get("entity_id", ""))
        elif wtype == "relay_pair":
            data_attrs += ' data-board="%d"' % w.get("board_index", 0)

        if wtype == "compliments":
            items = [i for i in (w.get("items") or []) if i]
            content = ('<div class="mirror-value">%s</div>' % _esc(items[0])) if items else \
                '<div class="mirror-value hint">Keine Komplimente konfiguriert.</div>'
        elif wtype == "todo":
            items = w.get("items") or []
            open_count = sum(1 for i in items if not i.get("done"))
            content = '<div class="mirror-value">%d offen / %d gesamt</div>' % (open_count, len(items))
        elif wtype == "clock":
            content = '<div class="mirror-value mirror-clock">--:--</div>'
        elif wtype == "logo":
            content = '<div class="mirror-value hint">Bild-Vorschau noch nicht verdrahtet - siehe Tab5.</div>'
        elif wtype == "relay_pair":
            labels = w.get("labels") or ["Relais 1", "Relais 2"]
            content = '<div class="mirror-value" data-relay="1">%s: lädt...</div><div class="mirror-value" data-relay="2">%s: lädt...</div>' % (
                _esc(labels[0] if len(labels) > 0 else "Relais 1"), _esc(labels[1] if len(labels) > 1 else "Relais 2"))
        elif wtype in MIRROR_DIRECT_TYPES or wtype in SNAPSHOT_TYPES or is_ha:
            content = '<div class="mirror-value">lädt...</div>'
        else:
            content = '<div class="mirror-value hint">Live-Vorschau für diesen Typ noch nicht verdrahtet - siehe Tab5.</div>'

        tiles.append(
            '<div class="mirror-tile" style="grid-column:%d/span %d;grid-row:%d/span %d;" %s>'
            '<div class="mirror-title">%s</div>%s</div>'
            % (col, col_span, row, row_span, data_attrs, _esc(title), content)
        )

    return (
        '<div class="mirror-grid" style="grid-template-columns:repeat(%d, 1fr);">%s</div>'
        % (cols, "".join(tiles))
    )


def _start_page_html(cfg):
    # Multi-Dashboard-Feature (siehe HANDOFF.md) - Titel/Hinweistext
    # nennen das gerade gespiegelte (=aktive) Profil, damit auf einen
    # Blick klar ist, WELCHES der beiden Dashboards man hier sieht.
    profiles = cfg.get("screens", {}).get("dashboards", [])
    active_id = cfg.get("screens", {}).get("active_dashboard_id")
    active_profile = next((p for p in profiles if p.get("id") == active_id),
                           profiles[0] if profiles else {"name": "Dashboard"})
    body = START_MIRROR_BODY.replace("__MIRROR_GRID__", _mirror_grid_html(cfg))
    body = body.replace("__SNAPSHOT_TYPES_JSON__", _json_script(list(SNAPSHOT_TYPES)))
    body = body.replace("__ACTIVE_PROFILE_NAME__", _esc(active_profile.get("name", "Dashboard")))
    return _shell("Tab5 - %s" % active_profile.get("name", "Dashboard-Spiegel"), body, "start")


START_MIRROR_BODY = """
<div class="card">
  <h2>Was das Tab5 gerade anzeigt (__ACTIVE_PROFILE_NAME__)</h2>
  <div class="hint">Live-Spiegel des gerade AKTIVEN Dashboard-Profils - Position/Breite
  wie auf dem Gerät, nur aktive Kacheln, mit Live-Werten für praktisch
  alle Typen (Ausnahmen: Uhr zeigt die Browser-Zeit, Logo-Bilder noch
  nicht verdrahtet).
  Anderes Profil aktivieren, Einstellungen &amp; Layout bearbeiten: <a href="/dashboard" style="color:#c9a15a;">Dashboard-Editor</a>,
  Sensor-Grafiken &amp; -Einstellungen: <a href="/sensors" style="color:#c9a15a;">Sensor-Dashboard</a>.</div>
  __MIRROR_GRID__
</div>

<script>
window.PAGE_DATA = {snapshotTypes: __SNAPSHOT_TYPES_JSON__};
</script>
<script src="/static/start.js"></script>
"""


def _build_sensors_body(cfg):
    rs = cfg["room_sensor"]
    mode = rs.get("mode", "auto")
    hours = rs.get("web_chart_hours", 6)
    iaq_mode = cfg.get("iaq", {}).get("baseline_mode", "fixed")
    offsets = rs.get("offsets", {})
    external_sources = [s for s in rs.get("external_sources", []) if s.get("base_url")]

    mode_options = "".join(_option(m, mode, label) for m, label in MODE_LABELS.items())
    hour_options = "".join(_option(h, hours, _hour_label(h)) for h in CHART_HOUR_OPTIONS)
    iaq_mode_options = "".join(_option(m, iaq_mode, label) for m, label in IAQ_MODE_LABELS.items())

    # Für jede konfigurierte externe Quelle: eine eigene Chart.js-Farbe
    # (Blauton-Abstufung, analog zur Farbabstufung im Tab5-eigenen
    # sensor_history_screen.py::_blend_white()) UND ein eigener
    # <div>-Platzhalter für die Live-Werte-Karte - beides wird per JS
    # (EXTERNAL_SOURCES, siehe unten) dynamisch erzeugt, nicht mehr fest
    # im HTML verdrahtet.
    external_sources_json = _json_script([
        {"name": s.get("name") or "Extern %d" % (i + 1)}
        for i, s in enumerate(external_sources)
    ])

    body = SENSORS_BODY
    body = body.replace("__MODE_OPTIONS__", mode_options)
    body = body.replace("__HOUR_OPTIONS__", hour_options)
    body = body.replace("__IAQ_MODE_OPTIONS__", iaq_mode_options)
    body = body.replace("__CURRENT_HOURS__", str(hours))
    body = body.replace("__OFFSET_TEMP__", str(offsets.get("temp_c", 0.0)))
    body = body.replace("__OFFSET_HUMIDITY__", str(offsets.get("humidity", 0.0)))
    body = body.replace("__OFFSET_PRESSURE__", str(offsets.get("pressure_hpa", 0.0)))
    body = body.replace("__SD_LOG_INTERVAL__", str(rs.get("sd_log_interval_s", 60)))
    body = body.replace("__EXTERNAL_SOURCES_JSON__", external_sources_json)
    return body


SENSORS_BODY = """
<div class="card">
  <h2>Live-Werte</h2>

  <div id="live-local">lade...</div>
  <div id="live-external"></div>
  <div class="hint">Welche Quelle(n) hier zusätzlich zu "Lokal" erscheinen,
  hängt von den unter /dashboard → "Sensoren" konfigurierten externen
  Quellen ab (Phase A, siehe HANDOFF.md).</div>
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
  <label>SD-Log-Intervall (Sekunden)</label>
  <input type="number" min="5" id="sd-log-interval" value="__SD_LOG_INTERVAL__">
  <div class="hint">Wie oft ein Messpunkt auf die SD-Karte geschrieben
  werden soll, SOBALD SD-Logging tatsächlich funktioniert (aktuell noch
  nicht - siehe README) - Einstellung schon vorbereitet, wirkt sich
  automatisch aus, sobald es losgeht.</div>
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
window.PAGE_DATA = {externalSources: __EXTERNAL_SOURCES_JSON__};
</script>
<script src="/static/sensors.js"></script>
"""


# ---------------------------------------------------------------------------
# Dashboard-Seite - EIN Kachel-Layout-Editor für ALLE Widgets des einen
# Tab5-Dashboards (siehe screens/dashboard.py/config.py "screens.dashboard").
# Ersetzt die früheren drei getrennten Seiten /environment, /room_dashboard,
# /magic_mirror.
# ---------------------------------------------------------------------------
def _secret_placeholder(value, default=""):
    """Platzhaltertext fuer ein geheimes Eingabefeld: der Wert selbst wird nie
    ausgeliefert (weder als value noch im data-widget-JSON)."""
    if value:
        return "•••• gespeichert - leer lassen = unverändert, - = löschen"
    return default


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
    # Alle Werte dieser Funktion landen in value="..."/<textarea> - daher
    # EINMAL zentral escapen (Zahlen/Bools bleiben unveraendert).
    w = _esc_deep(w)
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
        sources = (w.get("sources") or []) + [{}] * 6
        rows_html = "".join(
            '<div class="row"><input type="text" class="f-news-name" placeholder="Name" value="%s">'
            '<input type="text" class="f-news-url" placeholder="Feed-URL" value="%s"></div>'
            % (s.get("name", ""), s.get("feedUrl", ""))
            for s in sources[:6]
        )
        return (
            '<details class="widget-details" open>'
            '<summary>RSS-Quellen &amp; Einstellungen</summary>'
            '<div class="hint">Bis zu 6 Quellen, rotiert bei jedem Refresh eine weiter.</div>'
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
            '<input type="text" class="f-api-key" value="" placeholder="%s" autocomplete="off">'
            '</details>'
        ) % (w.get("url", ""), _secret_placeholder(w.get("api_key")))

    if wtype == "calendar":
        return (
            '<details class="widget-details" open>'
            '<summary>Kalender-Adresse</summary>'
            '<label>Private iCal-URL</label>'
            '<input type="text" class="f-ical-url" value="" placeholder="%s" autocomplete="off">'
            '<div class="hint">Google Kalender → Einstellungen → dein Kalender → "Kalender integrieren" → "Geheime Adresse im iCal-Format". Aus Sicherheitsgründen wird sie nach dem Speichern nie wieder angezeigt.</div>'
            '<div class="row">'
            '<div><label>Max. Termine</label><input type="number" class="f-max-events" value="%s"></div>'
            '<div><label>Vorschau (Tage)</label><input type="number" class="f-days-ahead" value="%s"></div>'
            '</div>'
            '</details>'
        ) % (_secret_placeholder(w.get("ical_url"), "https://calendar.google.com/.../basic.ics"),
             w.get("max_events", 5), w.get("days_ahead", 14))

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
        return (
            '<details class="widget-details">'
            '<summary>Notizen</summary>'
            '<div class="hint">Einträge abhaken, bearbeiten, hinzufügen oder löschen geht jetzt auf der '
            '<a href="/notes" style="color:#c9a15a;">eigenen Notizen-Seite</a> - dort bleibt der Abhak-Status '
            'beim Bearbeiten erhalten (hier würde ein Speichern alle Haken zurücksetzen).</div>'
            '</details>'
        )

    if wtype == "logo":
        return (
            '<details class="widget-details" open>'
            '<summary>Bild-Datei</summary>'
            '<label>PNG direkt hochladen (wird automatisch umgewandelt)</label>'
            '<input type="file" class="f-logo-upload" accept="image/png" '
            'onchange="uploadLogo(this)">'
            '<div class="hint">Max. %dx%d Pixel - größere Bilder vorher verkleinern. '
            'Wird automatisch ins passende Format umgewandelt und unten eingetragen.</div>'
            '<div class="f-logo-upload-status hint"></div>'
            '<label>Dateiname (aktuell verwendete Datei)</label>'
            '<input type="text" class="f-file" value="%s" placeholder="logo1.bin">'
            '<label>Vorhandene hochgeladene Logos</label>'
            '<div class="f-logo-list hint">lädt...</div>'
            '<div class="hint">Alternativ weiterhin manuell möglich: erst mit '
            '<code>python3 tools/png_to_lvgl.py meinlogo.png %s --size 160x160</code> '
            '(eine Kachel) bzw. <code>--size 560x160</code> (zwei Kacheln) umwandeln, '
            'dann per <code>mpremote cp</code> auf den Tab5 kopieren.</div>'
            '</details>'
        ) % (png_convert.MAX_WIDTH, png_convert.MAX_HEIGHT, w.get("file", ""), w.get("file") or "logo1.bin")

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


def _dashboard_layout_rows_html(widgets, grid):
    cols = grid["cols"]

    def _build_block(w):
        wtype = w.get("type")
        is_ha = wtype in ("ha_entity", "ha_switch")
        checked = "checked" if w.get("enabled", True) else ""
        pos_field_html = _position_grid_html(cols, grid["rows"], w.get("position", "r1-c1"))

        type_max = DASHBOARD_SPAN_LIMITS.get(wtype, 1)
        col = int(w.get("position", "r1-c1").split("-c")[1])  # 1-indiziert
        fit_max = cols - col + 1
        max_span = max(1, min(type_max, fit_max))
        current_span = min(w.get("col_span", 1), max_span)
        # Breiten-Auswahl als Radio-Buttons ("als Auswahl" per Checkbox-
        # artiger UI, statt Dropdown) - eindeutiger Radio-"name" pro Zeile
        # (Widget-id), da HTML sonst mehrere Radio-Gruppen auf derselben
        # Seite durcheinanderbringt.
        radio_name = "span_%s" % _esc(w.get("id") or ("row_%d" % id(w)))
        if type_max > 1:
            options = "".join(
                '<label class="span-option"><input type="radio" class="span-radio" name="%s" value="%d" %s>%dx</label>'
                % (radio_name, s, "checked" if s == current_span else "", s)
                for s in range(1, max_span + 1)
            )
            span_field = '<div class="span-radio-group" data-type-max="%d">%s</div>' % (type_max, options)
        else:
            span_field = '<span class="span-fixed">1x</span>'

        # Höhen-Auswahl (row_span) - Nutzerwunsch: Notiz-Widget soll auch
        # nach UNTEN mehrere Kacheln nutzen dürfen, nicht nur nach rechts.
        # Exakt dieselbe Bauweise wie bei der Breite oben, nur mit
        # ROW_SPAN_LIMITS/Zeilen statt SPAN_LIMITS/Spalten.
        row_type_max = DASHBOARD_ROW_SPAN_LIMITS.get(wtype, 1)
        row = int(w.get("position", "r1-c1").split("-c")[0][1:])  # 1-indiziert
        row_fit_max = grid["rows"] - row + 1
        max_row_span = max(1, min(row_type_max, row_fit_max))
        current_row_span = min(w.get("row_span", 1), max_row_span)
        row_radio_name = "rowspan_%s" % _esc(w.get("id") or ("row_%d" % id(w)))
        if row_type_max > 1:
            row_options = "".join(
                '<label class="span-option"><input type="radio" class="row-span-radio" name="%s" value="%d" %s>%dx</label>'
                % (row_radio_name, s, "checked" if s == current_row_span else "", s)
                for s in range(1, max_row_span + 1)
            )
            row_span_field = '<div class="row-span-radio-group" data-type-max="%d">%s</div>' % (row_type_max, row_options)
        else:
            row_span_field = ""

        if is_ha:
            type_options = "".join(_option(t, wtype, label) for t, label in HA_TYPE_LABELS.items())
            # Schalter/Anzeigen sind seit Phase C (siehe HANDOFF.md) NUR
            # noch Home Assistant - Atom-Relais laufen über das eigene
            # "relay_pair"-Widget (siehe Zweig unten), kein Backend-
            # Auswahlfeld hier mehr nötig.
            label_field = (
                '<input type="text" class="title-input" value="%s" placeholder="Titel">'
                '<input type="text" class="entity-input" value="%s" placeholder="entity_id">'
                '<select class="type-select">%s</select>'
            ) % (_esc(w.get("title", "")), _esc(w.get("entity_id", "")), type_options)
        elif wtype == "relay_pair":
            # Ein Widget pro physischem Atom-Board mit ZWEI frei
            # beschriftbaren Relais (Phase C, siehe HANDOFF.md) -
            # "board_index" (welches der beiden Boards) ist fest und wird
            # hier nur angezeigt, nicht editiert - welches Board gemeint
            # ist, ergibt sich aus der Config, nicht aus dem Editor.
            labels = w.get("labels") or ["Relais 1", "Relais 2"]
            board_index = w.get("board_index", 0)
            label_field = (
                '<div class="layout-row-label">Atom-Switch %d</div>'
                '<input type="text" class="relay-label-input" data-slot="0" value="%s" '
                'placeholder="Bezeichnung Relais 1" style="width:150px;">'
                '<input type="text" class="relay-label-input" data-slot="1" value="%s" '
                'placeholder="Bezeichnung Relais 2" style="width:150px;">'
            ) % (board_index + 1, _esc(labels[0] if len(labels) > 0 else ""), _esc(labels[1] if len(labels) > 1 else ""))
        else:
            label = DASHBOARD_WIDGET_LABELS.get(wtype, wtype)
            label_field = '<div class="layout-row-label">%s</div>' % label

        # Komplette Original-Config als JSON im data-Attribut mitführen (HTML-
        # escaped), damit beim Speichern Felder, die dieser Editor (noch)
        # nicht kennt, NICHT verloren gehen - alles Bekannte wird beim
        # Speichern gezielt überschrieben (siehe saveDashboardLayout()).
        widget_json = _esc(json.dumps({k: v for k, v in w.items() if k not in SECRET_WIDGET_KEYS}))
        # Reihenfolge in der Zeile (Nutzerwunsch): Titel, dann eine Spalte
        # mit "an"-Checkbox + Breiten-Auswahl darunter, dann das
        # Klick-Raster IMMER ganz rechts (margin-left:auto in CSS), zum
        # Schluss der Entfernen-Button bei HA-Kacheln.
        controls_col = (
            '<div class="widget-controls-col">'
            '<label class="layout-checkbox"><input type="checkbox" class="enabled-cb" %s>an</label>'
            '%s'
            '%s'
            '</div>'
        ) % (checked, span_field, row_span_field)
        row_html = (
            '<div class="layout-row" data-cols="%d" data-rows="%d" data-ha="%s">'
            '%s'
            '%s'
            '<div class="pos-grid-wrap">%s</div>'
            '%s'
            '</div>' % (
                cols, grid["rows"], "true" if is_ha else "false", label_field, controls_col, pos_field_html,
                '<button type="button" class="remove secondary" onclick="this.closest(\'.widget-block\').remove()">&times;</button>' if is_ha else "",
            )
        )
        details_html = "" if is_ha else _widget_details_html(w)
        return '<div class="widget-block" data-widget="%s">%s%s</div>' % (widget_json, row_html, details_html)

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
            '<summary>Home Assistant <span class="category-count">(%d/%d aktiv)</span></summary>'
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


def _get_active_dashboard_widgets(cfg):
    """Multi-Dashboard-Feature (siehe HANDOFF.md) - Widget-Liste des
    gerade AKTIVEN Profils (config.py "screens.active_dashboard_id").
    Zentrale Hilfsfunktion, NICHT mehr an jeder Stelle einzeln
    nachgebaut - genau das mehrfache Kopieren der (kurzen, aber leicht
    falsch abzutippenden) Lookup-Logik hat dazu geführt, dass bei der
    Umstellung von "screens.dashboard" (einzeln) auf "screens.dashboards"
    (Liste) mehrere Fundstellen übersehen wurden: /notes, /switches,
    /api/ha-states, /api/climate-ext-status - die suchten noch nach dem
    längst entfernten Einzel-Schlüssel und fanden dadurch NIE etwas
    (leere Liste), ohne dass das laut/sichtbar fehlgeschlagen wäre. Fällt
    auf das erste Profil zurück, falls die aktive id (aus welchem Grund
    auch immer) nicht gefunden wird."""
    profiles = cfg.get("screens", {}).get("dashboards", [])
    active_id = cfg.get("screens", {}).get("active_dashboard_id")
    active_profile = next((p for p in profiles if p.get("id") == active_id),
                           profiles[0] if profiles else {})
    return active_profile.get("widgets", [])


def _dashboard_page_html(cfg, profile_id=None):
    profiles = cfg.get("screens", {}).get("dashboards", [])
    active_id = cfg.get("screens", {}).get("active_dashboard_id")
    profile_ids = [p.get("id") for p in profiles]
    if not profile_id or profile_id not in profile_ids:
        profile_id = active_id if active_id in profile_ids else (profile_ids[0] if profile_ids else None)
    profile = next((p for p in profiles if p.get("id") == profile_id),
                    {"id": "dashboard1", "name": "Dashboard 1", "grid": {"cols": 4, "rows": 3}, "widgets": []})
    grid = profile.get("grid", {"cols": 4, "rows": 3})

    # Reiter zum Umschalten, WELCHES der (genau 2) Profile gerade
    # bearbeitet wird (Multi-Dashboard-Feature, siehe HANDOFF.md) - das
    # gerade am Gerät AKTIVE Profil bekommt einen Stern, unabhängig davon,
    # welches man sich hier gerade ansieht/bearbeitet (beides kann
    # auseinanderfallen: man kann "Dashboard 2" bearbeiten, während am
    # Tab5 weiterhin "Dashboard 1" läuft).
    tabs_html = "".join(
        '<a class="profile-tab%s" href="/dashboard?profile=%s">%s%s</a>' % (
            " active" if p.get("id") == profile_id else "",
            _esc(p.get("id")), _esc(p.get("name", p.get("id"))),
            " \u2605" if p.get("id") == active_id else "")
        for p in profiles
    )
    activate_button_html = "" if profile_id == active_id else (
        '<button class="secondary" onclick="setActiveDashboard()">Als aktives Dashboard am Gerät verwenden</button>'
    )

    body = DASHBOARD_BODY.replace("__DASHBOARD_TABS__", tabs_html)
    body = body.replace("__PROFILE_NAME__", _esc(profile.get("name", profile_id)))
    body = body.replace("__ACTIVATE_BUTTON__", activate_button_html)
    body = body.replace("__DASHBOARD_LAYOUT_ROWS__", _dashboard_layout_rows_html(profile.get("widgets", []), grid))
    body = body.replace("__COLS__", str(grid["cols"]))
    body = body.replace("__ROWS__", str(grid["rows"]))
    body = body.replace("__PROFILE_ID_JSON__", _json_script(profile_id or "dashboard1"))
    return _shell("Dashboard - %s" % profile.get("name", profile_id), body, "dashboard")


DASHBOARD_BODY = r"""
<div class="profile-tabs">__DASHBOARD_TABS__</div>

<div class="card">
  <h2>Kachel-Layout &amp; Widget-Einstellungen</h2>
  <label>Name dieses Profils</label>
  <input type="text" id="profile-name-input" value="__PROFILE_NAME__" style="max-width:300px;">
  __ACTIVATE_BUTTON__
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
window.PAGE_DATA = {cols: __COLS__, rows: __ROWS__, profileId: __PROFILE_ID_JSON__};
</script>
<script src="/static/dashboard.js"></script>

"""


# ---------------------------------------------------------------------------
# System-Seite - WLAN (Status/Verbinden/Access Point) + Theme + Sprache.
# Ersetzt die frühere eigenständige /wifi-Seite und den früheren Tab5-
# Settings-Screen (siehe screens/settings.py-Historie/HANDOFF.md - Theme/
# Sprache werden jetzt hier statt auf dem Tab5-Display selbst eingestellt).
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Schalter-Seite - Licht/Steckdose je gepairtem Atom-Relais-Board (Phase C,
# siehe HANDOFF.md - der Nutzer hat inzwischen ZWEI unabhängige physische
# Boards, je 2 Relais) - Live-Status + zwei getrennte Verbindungs-
# Einstellungsblöcke. Findet die relay_pair-Kacheln automatisch aus der
# Dashboard-Konfiguration statt sie hier fest zu verdrahten.
# ---------------------------------------------------------------------------
def _switches_page_html(cfg):
    atom_boards = cfg.get("atom_boards", [])
    # Bugfix (Multi-Dashboard-Feature, siehe HANDOFF.md/
    # _get_active_dashboard_widgets()) - suchte vorher nach der nicht
    # mehr existierenden Einzel-Dashboard-Struktur, fand dadurch NIE ein
    # relay_pair-Widget und zeigte deshalb immer die generischen Labels
    # ("Relais 1"/"Relais 2") statt der vom Nutzer vergebenen.
    widgets = _get_active_dashboard_widgets(cfg)
    relay_pairs = sorted((w for w in widgets if w.get("type") == "relay_pair"),
                          key=lambda w: w.get("board_index", 0))

    rows_html = ""
    for w in relay_pairs:
        board_index = w.get("board_index", 0)
        labels = w.get("labels") or ["Relais 1", "Relais 2"]
        board_name = (atom_boards[board_index].get("name") if board_index < len(atom_boards) else None) \
            or ("Board %d" % (board_index + 1))
        rows_html += '<div class="switch-group-title">%s</div>' % _esc(board_name)
        for i, relay_id in enumerate((1, 2)):
            label = labels[i] if i < len(labels) else ("Relais %d" % relay_id)
            rows_html += (
                '<div class="switch-row" data-board="%d" data-relay="%d">'
                '<div><div class="name">%s</div><div class="state">lade...</div></div>'
                '<label class="toggle"><input type="checkbox" onchange="toggleRelay(%d, %d, this)">'
                '<span class="toggle-slider"></span></label>'
                '</div>' % (board_index, relay_id, _esc(label), board_index, relay_id)
            )
    if not rows_html:
        rows_html = ('<div class="hint">Keine Atom-Relais-Kacheln im Dashboard konfiguriert - '
                     'siehe /dashboard, Widget-Typ "Atom-Relais-Paar" aktivieren.</div>')

    board_blocks_html = "".join(_atom_board_block_html(i, b) for i, b in enumerate(atom_boards))

    body = SWITCHES_BODY.replace("__SWITCH_ROWS__", rows_html)
    body = body.replace("__ATOM_BOARD_BLOCKS__", board_blocks_html)
    body = body.replace("__ATOM_BOARD_COUNT__", str(len(atom_boards)))
    return _shell("Schalter", body, "switches")


def _atom_board_block_html(board_index, board_cfg):
    name = board_cfg.get("name") or ("Atom-Switch-%d" % (board_index + 1))
    return (
        '<div class="card">'
        '<h2>%s</h2>'
        '<label class="layout-checkbox"><input type="checkbox" class="atom-enabled" data-board="%d" %s>Aktiviert</label>'
        '<label>IP-Adresse / Basis-URL</label>'
        '<input type="text" class="atom-base-url" data-board="%d" value="%s" placeholder="http://192.168.1.20">'
        '<button onclick="saveAtomSettings(%d)">Speichern</button>'
        '<div class="save-status" id="atom-save-status-%d"></div>'
        '</div>'
    ) % (
        _esc(name), board_index, "checked" if board_cfg.get("enabled") else "",
        board_index, _esc(board_cfg.get("base_url", "")), board_index, board_index,
    )


def _notes_page_html(cfg):
    # Bugfix (Multi-Dashboard-Feature, siehe HANDOFF.md): hier stand noch
    # cfg["screens"]["dashboard"]["widgets"] - die alte Einzel-Struktur,
    # die es seit "screens.dashboards" (Liste aus 2 Profilen) nicht mehr
    # gibt, lieferte also immer eine leere Liste. Notizen kommen jetzt
    # aus dem gerade AKTIVEN Profil (siehe _get_active_dashboard_widgets()) -
    # falls beide Profile eigene "todo"-Widgets mit unterschiedlichen
    # Einträgen haben, zeigt diese Seite die des Profils, das gerade am
    # Gerät läuft.
    widgets = _get_active_dashboard_widgets(cfg)
    todo_widget = next((w for w in widgets if w.get("id") == "todo"), None)
    items = (todo_widget or {}).get("items") or []

    rows_html = "".join(
        '<div class="note-row" data-index="%d">'
        '<label class="toggle-check"><input type="checkbox" %s onchange="toggleNote(%d, this)">'
        '<span></span></label>'
        '<input type="text" class="note-text" value="%s" onchange="editNoteText(%d, this)">'
        '<button class="note-delete" onclick="deleteNote(%d)">&times;</button>'
        '</div>'
        % (i, "checked" if it.get("done") else "", i, _esc(it.get("text", "")), i, i)
        for i, it in enumerate(items)
    )
    if not rows_html:
        rows_html = '<div class="hint">Noch keine Notizen - unten eine neue hinzufügen.</div>'

    body = (NOTES_BODY
            .replace("__NOTE_ROWS__", rows_html)
            .replace("__NOTE_COUNT__", str(len(items)))
            .replace("__NOTE_MAX__", str(MAX_TODO_ITEMS)))
    return _shell("Notizen", body, "notes")


NOTES_BODY = """
<div class="card">
  <h2>Notizen</h2>
  <div class="hint">Änderungen (Abhaken, Text, Hinzufügen, Löschen) werden sofort
  gespeichert und erscheinen innerhalb ca. 1s auf dem Tab5 - kein separater
  Speichern-Button nötig. Auf dem Dashboard erledigte Einträge werden dort
  ausgegraut dargestellt.</div>
  <div id="notes-count" class="hint" data-max="__NOTE_MAX__" style="margin-top:10px;">__NOTE_COUNT__ / __NOTE_MAX__ Notizen</div>
  <div id="note-rows" style="margin-top:14px;">__NOTE_ROWS__</div>
  <div style="display:flex; gap:8px; margin-top:14px;">
    <input type="text" id="new-note-text" placeholder="Neue Notiz..." style="flex:1;"
      onkeydown="if(event.key==='Enter'){addNote();}">
    <button onclick="addNote()">Hinzufügen</button>
  </div>
  <div id="notes-save-status" class="save-status"></div>
</div>

<script src="/static/notes.js"></script>
"""

SWITCHES_BODY = """
<div class="card">
  <h2>Schalter</h2>
  <div id="switch-rows">__SWITCH_ROWS__</div>
  <div class="hint" style="margin-top:12px;">Zustand wird alle 5s pro Board abgefragt -
  Änderungen am physischen Taster oder über das Atom-eigene Web-UI erscheinen
  automatisch, sobald das jeweilige Atom-Board das Tab5 als "partner_ip"
  eingetragen hat (siehe unten), sonst erst beim nächsten Abruf.</div>
</div>

__ATOM_BOARD_BLOCKS__

<script>
window.PAGE_DATA = {atomBoardCount: __ATOM_BOARD_COUNT__};
</script>
<script src="/static/switches.js"></script>
"""


def _system_page_html(cfg):
    wifi_cfg = cfg.get("wifi", {})
    server_cfg = cfg.get("server", {})
    theme_options = "".join(_option(k, cfg.get("theme_mode", "dark"), v) for k, v in THEME_LABELS.items())
    lang_options = "".join(_option(k, cfg.get("language", "de"), v) for k, v in LANGUAGE_LABELS.items())

    body = SYSTEM_BODY.replace("__CURRENT_SSID__", _esc(wifi_cfg.get("ssid", "")))
    body = body.replace("__WEB_AUTH_STATE__",
                        "aktiv" if cfg.get("web_ui", {}).get("auth_hash") else "aus (jeder im Netzwerk hat Zugriff)")
    body = body.replace("__THEME_OPTIONS__", theme_options)
    body = body.replace("__LANG_OPTIONS__", lang_options)
    body = body.replace("__SERVER_ENABLED_CHECKED__", "checked" if server_cfg.get("enabled") else "")
    body = body.replace("__SERVER_BASE_URL__", _esc(server_cfg.get("base_url", "")))
    return _shell("System", body, "system")


SYSTEM_BODY = """
<div class="card">
  <h2>Speicher</h2>
  <div id="mem-status">lade...</div>
  <div class="hint">Freier Heap-Speicher (gc.mem_free()) - zur groben
  Einschätzung, ob sich Speicher über die Zeit unerwartet verknappt
  (Heap-Fragmentierung). Ein einzelner niedriger Wert direkt nach einem
  Bildschirmwechsel/Live-Reload ist normal (siehe Optimierungs-Backlog
  Punkt 4, HANDOFF.md), interessant wäre ein Trend über Tage/Wochen.</div>
</div>

<div class="card">
  <h2>Akku</h2>
  <div id="battery-status">lade...</div>
  <div class="hint">Spannung über M5.Power.getBatteryVoltage() (Millivolt,
  hier in Volt angezeigt) - passend zu einem 2S-Akkupack (zwei Zellen in
  Reihe, ca. 8.4V bei Vollladung, ca. 6.0-6.4V nahezu leer).</div>
</div>

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
  <input type="password" id="wifiPassword" placeholder="Passwort" autocomplete="off">
  <button onclick="connectWifi()">Verbinden</button>
  <div id="connect-status" class="save-status"></div>
</div>

<div class="card">
  <h2>Access Point</h2>
  <div class="hint">Öffnet sofort einen eigenen Access Point ("Tab5-Setup") - z.B. um
  von einem anderen Gerät aus neu zu konfigurieren, falls kein bekanntes WLAN
  erreichbar ist. Das Passwort ist für dieses Gerät zufällig erzeugt und steht
  im Burger-Menü auf dem Tab5-Display (sobald der Access Point aktiv ist).</div>
  <button class="secondary" onclick="startAp()">Access Point starten</button>
  <div id="ap-status" class="save-status"></div>
</div>

<div class="card">
  <h2>UIFlow2 starten</h2>
  <div class="hint">Startet den Tab5 neu und zeigt das UIFlow2-Startmenü der Firmware (Netzwerk-Einrichtung,
  UIFlow2-Verbindung). Dieses Programm startet danach wieder: Wählst du im Menü "starten/weiter", läuft es
  gleich an; bleibst du im UIFlow2-Modus, startet es beim nächsten Neustart (Reset-Taste oder Strom aus/an)
  von selbst. Dieses Web-UI ist im UIFlow2-Modus nicht erreichbar. Wichtig: In UIFlow2 nur RUN benutzen, nicht
  DOWNLOAD - das würde die Startdatei main.py dieses Programms überschreiben.</div>
  <button class="secondary" onclick="startUiflowMode()">UIFlow2 starten</button>
  <div id="uiflow-status" class="save-status"></div>
</div>

<div class="card">
  <h2>Web-UI-Passwortschutz</h2>
  <div class="hint">Status: <b>__WEB_AUTH_STATE__</b>. Schützt alle Seiten dieses Web-UI
  mit dem Benutzernamen <code>admin</code> und dem hier gesetzten Passwort (HTTP Basic -
  im lokalen Netz nicht verschlüsselt, hält aber Unbefugte und fremde Webseiten fern).
  Leer lassen und speichern = Schutz aus. Passwort vergessen? In /flash/config.json die
  Einträge web_ui.auth_hash und web_ui.auth_salt auf "" setzen.</div>
  <label>Neues Passwort (mind. 8 Zeichen)</label>
  <input type="password" id="web-password" autocomplete="new-password">
  <label>Wiederholen</label>
  <input type="password" id="web-password2" autocomplete="new-password">
  <button onclick="saveWebPassword()">Speichern</button>
  <div id="web-password-status" class="save-status"></div>
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

<script src="/static/system.js"></script>
"""


def _match_atom_board_by_peer(writer):
    """Bestimmt, von WELCHEM der konfigurierten Atom-Boards ein "/api/sync"-
    Aufruf kam - anhand der Quell-IP der eingehenden Verbindung, da das
    Atom-Sync-Protokoll selbst keine Board-Kennung mitschickt (siehe
    "/api/sync"-Docstring oben). Peer-IP wird gegen den Host-Teil jeder
    konfigurierten atom_boards[i].base_url verglichen. Gibt None zurück,
    falls keine passt (z.B. unbekannter Absender, oder get_extra_info()
    liefert auf dieser Firmware nichts Brauchbares - siehe UNVERIFIZIERT-
    Hinweis oben) - der Aufrufer ignoriert den Sync dann sicherheitshalber,
    statt zu raten, welches Board gemeint war."""
    try:
        peer = writer.get_extra_info("peername")
        peer_ip = peer[0] if peer else None
    except Exception:
        peer_ip = None
    if not peer_ip or not get_atom_boards_cfg:
        return None
    for i, board in enumerate(get_atom_boards_cfg()):
        base_url = board.get("base_url", "")
        host = base_url.split("//")[-1].split(":")[0].split("/")[0]
        if host and host == peer_ip:
            return i
    return None


# UIFlow2-Startmenue (Knopf auf /system): setzt im NVS boot_option = 1 (siehe /flash/boot.py der
# Firmware: 0 = main.py direkt, 1 = Startmenue + Netzwerk-Einrichtung, 2 = nur Netzwerk) und legt
# einen Merker an; nach dem Neustart zeigt boot.py das UIFlow2-Startmenue, und das Startskript
# main.py (siehe tools/main_stub.py) stellt boot_option wieder auf 0 zurueck (einmalig).
UIFLOW_MARKER = "/flash/uiflow_modus"
UIFLOW_BOOT_OPTION_MENU = 1


def _set_uiflow_boot_option(value):
    import esp32
    nvs = esp32.NVS("uiflow")
    nvs.set_u8("boot_option", value)
    nvs.commit()
APP_FILES = ("/flash/app.mpy", "/flash/app.py")   # ohne diese Aufteilung kann main.py nichts ueberspringen


def _file_exists(path):
    try:
        _os.stat(path)
        return True
    except Exception:
        return False


async def _delayed_reboot():
    # Kurze Pause, damit _send() oben die HTTP-Antwort sicher noch über
    # die (dann gleich getrennte) Verbindung hinausschreiben kann, bevor
    # machine.reset() den kompletten Prozess sofort beendet.
    await asyncio.sleep(1)
    # Eine per config.request_save() vorgemerkte, aber noch nicht
    # geschriebene Änderung (Debounce, siehe config.py) darf durch einen
    # Neustart nicht verloren gehen - force=True erzwingt den Schreib-
    # vorgang JETZT, unabhängig von der sonst üblichen Wartezeit.
    if cfg_flush_pending is not None:
        try:
            cfg_flush_pending(force=True)
        except Exception as e:
            print("Web-UI: flush_pending() vor Neustart fehlgeschlagen:", e)
    # WLAN sauber abmelden: sonst haelt der Router die alte Sitzung noch fuer gueltig und
    # die erste Anmeldung nach dem Neustart stockt ~5 s (siehe wifi_manager.disconnect_cleanly).
    if wifi_mgr is not None and hasattr(wifi_mgr, "disconnect_cleanly"):
        try:
            wifi_mgr.disconnect_cleanly()
        except Exception as e:
            print("Web-UI: WLAN-Abmeldung vor Neustart fehlgeschlagen:", e)
    import machine
    machine.reset()


# Sicherheits-Header fuer JEDE Antwort: kein Einbetten in fremde Seiten
# (Clickjacking auf Reboot-/WLAN-Buttons), kein MIME-Sniffing, kein Referrer.
_SECURITY_HEADERS = ("X-Frame-Options: DENY\r\nX-Content-Type-Options: nosniff\r\n"
                     "Referrer-Policy: no-referrer\r\n")
# Content-Security-Policy fuer HTML-Seiten: nur eigene Ressourcen laden und nur
# an das eigene Geraet senden - ein eingeschleustes Skript kann so keine Daten
# an fremde Server schicken oder fremden Code nachladen. ('unsafe-inline' ist
# noetig, weil die Seiten kleine Inline-Skripte/onclick-Handler nutzen.)
_CSP_HEADER = ("Content-Security-Policy: default-src 'self'; script-src 'self' 'unsafe-inline'; "
               "style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; "
               "frame-ancestors 'none'; base-uri 'none'; form-action 'self'\r\n")


async def _send(writer, status, content_type, body, extra_headers=""):
    if isinstance(body, str):
        body = body.encode()
    head = "HTTP/1.1 {}\r\nContent-Type: {}\r\nContent-Length: {}\r\nConnection: close\r\n".format(
        status, content_type, len(body))
    head += _SECURITY_HEADERS + "Cache-Control: no-store\r\n" + extra_headers
    if content_type.startswith("text/html"):
        head += _CSP_HEADER
    writer.write((head + "\r\n").encode())
    writer.write(body)
    await writer.drain()


def _parse_multipart(body, content_type):
    """Sehr einfacher multipart/form-data-Parser (Optimierungs-Backlog
    Punkt 7, siehe HANDOFF.md) - dieser minimale HTTP-Server hatte bisher
    gar keine Multipart-Unterstützung, nur x-www-form-urlencoded/JSON.
    Reicht für einen simplen Datei-Upload mit ein paar Textfeldern, ist
    aber NICHT RFC-vollständig (z.B. keine verschachtelten Multipart-
    Teile, kein Base64/Quoted-Printable-Transfer-Encoding). Gibt
    {feldname: {"data": bytes, "filename": str-oder-None}} zurück."""
    marker = "boundary="
    idx = content_type.find(marker)
    if idx == -1:
        raise ValueError("Kein boundary in Content-Type gefunden")
    boundary = content_type[idx + len(marker):].strip().strip('"')
    delimiter = ("--" + boundary).encode()

    fields = {}
    # Body an den Boundary-Markern zerlegen - der ERSTE (vor dem ersten
    # Marker) und der LETZTE (nur "--\r\n" nach dem Schluss-Marker) Teil
    # sind leer/irrelevant und werden unten übersprungen.
    for part in body.split(delimiter):
        part = part.strip(b"\r\n")
        if not part or part == b"--":
            continue
        header_end = part.find(b"\r\n\r\n")
        if header_end == -1:
            continue
        # decode() OHNE Keyword-Argument aufgerufen (kein "errors=...") -
        # MicroPythons eingebaute decode()-Methode akzeptiert keine
        # Keyword-Argumente ("function doesn't take keyword arguments"),
        # anders als CPython. Multipart-Header sind ohnehin praktisch
        # immer reines ASCII, daher hier unproblematisch.
        header_block = part[:header_end].decode()
        data = part[header_end + 4:]
        name = None
        filename = None
        for line in header_block.split("\r\n"):
            if line.lower().startswith("content-disposition:"):
                for piece in line.split(";"):
                    piece = piece.strip()
                    if piece.startswith("name="):
                        name = piece[5:].strip('"')
                    elif piece.startswith("filename="):
                        filename = piece[9:].strip('"')
        if name:
            fields[name] = {"data": data, "filename": filename}
    return fields


def _peer_ip(writer):
    try:
        peer = writer.get_extra_info("peername")
        return peer[0] if peer else "?"
    except Exception:
        return "?"


def _auth_configured():
    web = _cfg_ro().get("web_ui", {})
    return bool(web.get("auth_hash"))


def _auth_locked(ip):
    rec = _auth_fails.get(ip)
    if rec is None or _time is None:
        return False
    if _time.time() - rec[1] >= AUTH_LOCK_S:
        _auth_fails.pop(ip, None)
        return False
    return rec[0] >= AUTH_MAX_FAILS


def _auth_failed(ip):
    if _time is None:
        return
    now = _time.time()
    rec = _auth_fails.get(ip)
    if rec is None or now - rec[1] >= AUTH_LOCK_S:
        if len(_auth_fails) > 40:
            _auth_fails.clear()
        _auth_fails[ip] = [1, now]
    else:
        rec[0] += 1


def _auth_ok(headers):
    """True, wenn der Passwortschutz aus ist ODER gueltige Basic-Zugangsdaten
    (Benutzer "admin" + gesetztes Passwort) mitgeschickt wurden."""
    global _auth_cache
    web = _cfg_ro().get("web_ui", {})
    stored = web.get("auth_hash", "")
    if not stored:
        return True
    if _hashlib is None:
        return False  # Schutz ist gesetzt, kann aber nicht geprueft werden -> sicherheitshalber sperren
    header = headers.get("authorization", "")
    if not header.startswith("Basic "):
        return False
    if _auth_cache is not None and _auth_cache[0] == stored and _consteq(_auth_cache[1], header):
        return True  # gleiche Zugangsdaten wie zuletzt - nicht bei jedem Request neu hashen
    try:
        raw = _binascii.a2b_base64(header[6:].strip()).decode()
    except Exception:
        return False
    user, _sep, password = raw.partition(":")
    if user != AUTH_USER:
        return False
    if _consteq(_hash_password(web.get("auth_salt", ""), password), stored):
        _auth_cache = (stored, header)
        return True
    return False


def _cfg_ro():
    """Gecachte NUR-LESE-Config (siehe config.load_readonly) - fuer die haeufig
    gepollten Endpunkte; Fallback auf cfg_load(). Ergebnis NIE veraendern."""
    return (cfg_load_ro or cfg_load)()


_bg_token = 0


async def _bg_call(key, fn, timeout_s=20):
    """Fuehrt fn() in einem HINTERGRUND-Thread aus und wartet (async, ohne die
    Event-Loop zu blockieren) auf das Ergebnis. Frueher liefen z.B. Geocode-
    Suche (bis 12s), Home-Assistant-, Atom- und Historien-Abfragen direkt im
    Web-Handler und froren waehrenddessen das ganze Geraet (Display, Touch,
    Uhr) ein. Rueckgabe: (True, wert) oder (False, fehlertext)."""
    global _bg_token
    _bg_token += 1
    token = _bg_token

    def run():
        try:
            return {"t": token, "v": fn()}
        except Exception as e:
            return {"t": token, "err": str(e)}

    waited = 0.0
    while not fetch_worker.bg_submit(key, run):
        if waited >= timeout_s:
            return False, "Gerät ist gerade beschäftigt"
        await asyncio.sleep(0.1)
        waited += 0.1
    while waited < timeout_s:
        value, _seq = fetch_worker.bg_get(key)
        if isinstance(value, dict) and value.get("t") == token:
            if "err" in value:
                return False, value["err"]
            return True, value["v"]
        await asyncio.sleep(0.05)
        waited += 0.05
    return False, "Zeitüberschreitung"


async def _send_static(writer, filename, req_headers):
    """Statische Datei blockweise senden (statt die komplette Datei - z.B.
    Chart.js, ~200 KB - erst als String UND dann nochmal als Bytes in den RAM
    zu laden), mit ETag (Browser fragt nur nach, laedt bei unveraenderter
    Datei nichts neu) und optional vorkomprimierter .gz-Variante."""
    ext = filename[filename.rfind("."):] if "." in filename else ""
    content_type = STATIC_CONTENT_TYPES.get(ext, "application/octet-stream")
    path = STATIC_DIR + "/" + filename
    encoding = None
    try:
        if "gzip" in req_headers.get("accept-encoding", ""):
            _os.stat(path + ".gz")
            path += ".gz"
            encoding = "gzip"
    except Exception:
        pass
    try:
        st = _os.stat(path)
    except Exception:
        await _send(writer, "404 Not Found", "text/plain",
                     "%s nicht gefunden - bitte die Datei nach %s/%s hochladen "
                     "(siehe README)." % (filename, STATIC_DIR, filename))
        return
    size = st[6]
    etag = '"%x-%x"' % (size, st[8])
    common = ("ETag: %s\r\nCache-Control: no-cache\r\nVary: Accept-Encoding\r\n" % etag) + _SECURITY_HEADERS
    if req_headers.get("if-none-match") == etag:
        writer.write(("HTTP/1.1 304 Not Modified\r\n%sConnection: close\r\n\r\n" % common).encode())
        await writer.drain()
        return
    head = "HTTP/1.1 200 OK\r\nContent-Type: %s\r\nContent-Length: %d\r\n%s" % (content_type, size, common)
    if encoding:
        head += "Content-Encoding: %s\r\n" % encoding
    writer.write((head + "Connection: close\r\n\r\n").encode())
    with open(path, "rb") as f:
        while True:
            chunk = f.read(2048)
            if not chunk:
                break
            writer.write(chunk)
            await writer.drain()


async def _apply_wifi_change(ssid, password):
    """Auf ein neues WLAN wechseln. Speichert die neuen Zugangsdaten nur bei
    Erfolg; bei einem Fehlschlag stellt wifi_manager.try_sta_async() den
    Access Point wieder her, die bisherigen (gespeicherten) Zugangsdaten
    bleiben erhalten und main.py::wifi_watchdog_task() versucht sie spaeter
    erneut."""
    global wifi_change_in_progress
    wifi_change_in_progress = True
    try:
        ok = await wifi_mgr.try_sta_async(ssid, password)
        if ok:
            cfg = cfg_load()
            cfg.setdefault("wifi", {})["ssid"] = ssid
            cfg["wifi"]["password"] = password
            cfg_save(cfg)
            print("Web-UI: WLAN gewechselt zu", ssid)
        else:
            print("Web-UI: Verbindung zu %r fehlgeschlagen - Access Point wiederhergestellt, "
                  "bisherige Zugangsdaten bleiben gespeichert." % ssid)
    except Exception as e:
        print("Web-UI: WLAN-Wechsel fehlgeschlagen:", e)
    finally:
        wifi_change_in_progress = False


async def _handle_client(reader, writer):
    global last_request_time
    try:
        if _time is not None:
            last_request_time = _time.time()
        request_line = await asyncio.wait_for(reader.readline(), REQUEST_LINE_TIMEOUT_S)
        if not request_line:
            writer.close()
            return
        parts = request_line.decode().split()
        method, raw_path = parts[0], parts[1]
        path, _, query = raw_path.partition("?")
        query_fields = _parse_qs(query) if query else {}

        headers = {}
        header_lines = 0
        while True:
            line = await asyncio.wait_for(reader.readline(), REQUEST_LINE_TIMEOUT_S)
            if line in (b"\r\n", b""):
                break
            header_lines += 1
            if header_lines > MAX_HEADER_LINES:
                await _send(writer, "431 Request Header Fields Too Large", "text/plain", "Zu viele Header")
                return
            k, v = line.decode().split(":", 1)
            headers[k.strip().lower()] = v.strip()

        # Optionaler Passwortschutz (siehe /system, "Web-UI-Passwortschutz"). Vor dem
        # Lesen des Bodys geprueft. /api/sync bleibt frei: das Atom-Board kann keine
        # Zugangsdaten senden (es aktualisiert nur die Anzeige, siehe dortige Route).
        if path != "/api/sync":
            peer_ip = _peer_ip(writer)
            if _auth_locked(peer_ip):
                await _send(writer, "429 Too Many Requests", "text/plain",
                             "Zu viele Fehlversuche - bitte eine Minute warten.", "Retry-After: 60\r\n")
                return
            if not _auth_ok(headers):
                if headers.get("authorization"):
                    _auth_failed(peer_ip)
                await _send(writer, "401 Unauthorized", "text/plain", "Anmeldung erforderlich",
                             'WWW-Authenticate: Basic realm="Tab5"\r\n')
                return
            _auth_fails.pop(peer_ip, None)

        body = b""
        if method == "POST":
            try:
                length = int(headers.get("content-length", 0))
            except ValueError:
                length = -1
            if length < 0:
                await _send(writer, "400 Bad Request", "application/json",
                             json.dumps({"ok": False, "msg": "Ungültige Content-Length."}))
                return
            # Generelle Obergrenze für JEDEN POST-Body (Optimierungs-
            # Backlog Punkt 7, siehe HANDOFF.md - der Anlass war der neue
            # PNG-Upload-Endpunkt, gilt aber sinnvollerweise für alle
            # POST-Routen: die meisten erwarten ohnehin nur ein paar KB
            # JSON/Formulardaten, ein PNG-Logo-Upload braucht großzügiger
            # Platz, aber auch nicht unbegrenzt).
            if length > MAX_POST_BODY_BYTES:
                await _send(writer, "413 Payload Too Large", "application/json",
                             json.dumps({"ok": False, "msg": "Anfrage zu groß (max. %d KB)."
                                         % (MAX_POST_BODY_BYTES // 1024)}))
                writer.close()
                return
            body = await asyncio.wait_for(reader.readexactly(length), BODY_TIMEOUT_S)

            # CSRF-Schutz (Optimierungs-Backlog Punkt 6, siehe HANDOFF.md) -
            # zentral hier für ALLE POST-Routen auf einmal, bevor der
            # Dispatch unten überhaupt beginnt. Der Header wird vom
            # PAGE_SHELL-fetch()-Patch automatisch mitgeschickt (siehe
            # dort) - fehlt er oder stimmt er nicht, kam der Request nicht
            # von einer selbst ausgelieferten Seite dieses Geräts.
            if not _consteq(headers.get("x-csrf-token"), CSRF_TOKEN):
                await _send(writer, "403 Forbidden", "application/json",
                             json.dumps({"ok": False, "msg": "Ungültiges oder fehlendes CSRF-Token - "
                                         "bitte die Seite neu laden und erneut versuchen."}))
                writer.close()
                return
            # Zusaetzlich (Defense in Depth): schickt der Browser einen Origin-Header
            # (immer bei seitenuebergreifenden POSTs), muss er zu diesem Geraet passen.
            origin = headers.get("origin")
            if origin is not None and origin.split("://", 1)[-1] != headers.get("host", ""):
                await _send(writer, "403 Forbidden", "application/json",
                             json.dumps({"ok": False, "msg": "Ungültiger Origin."}))
                writer.close()
                return

        if path == "/" and method == "GET":
            cfg = cfg_load()
            await _send(writer, "200 OK", "text/html; charset=utf-8", _start_page_html(cfg))

        elif path == "/sensors" and method == "GET":
            cfg = cfg_load()
            await _send(writer, "200 OK", "text/html; charset=utf-8", _sensors_page_html(cfg))

        elif path == "/dashboard" and method == "GET":
            cfg = cfg_load()
            await _send(writer, "200 OK", "text/html; charset=utf-8",
                         _dashboard_page_html(cfg, query_fields.get("profile")))

        elif path == "/notes" and method == "GET":
            cfg = cfg_load()
            await _send(writer, "200 OK", "text/html; charset=utf-8", _notes_page_html(cfg))

        elif path == "/notes/save" and method == "POST":
            try:
                payload = json.loads(body.decode())
                items = payload.get("items", [])
                # Nur "text"/"done" übernehmen, alles andere ignorieren -
                # verhindert, dass über diesen Endpunkt beliebige Felder
                # ins Widget geschmuggelt werden.
                clean_items = [
                    {"text": str(it.get("text", ""))[:200], "done": bool(it.get("done"))}
                    for it in items if isinstance(it, dict) and str(it.get("text", "")).strip()
                ]
            except Exception:
                await _send(writer, "400 Bad Request", "application/json",
                             json.dumps({"ok": False, "msg": "Ungültige Daten"}))
                return
            if len(clean_items) > MAX_TODO_ITEMS:
                await _send(writer, "400 Bad Request", "application/json",
                             json.dumps({"ok": False,
                                         "msg": "Maximal %d Notizen möglich." % MAX_TODO_ITEMS}))
                return
            cfg = cfg_load()
            # Gleicher Bugfix wie in _notes_page_html() oben.
            widgets = _get_active_dashboard_widgets(cfg)
            todo_widget = next((w for w in widgets if w.get("id") == "todo"), None)
            if todo_widget is None:
                await _send(writer, "404 Not Found", "application/json",
                             json.dumps({"ok": False, "msg": "Notiz-Widget nicht gefunden"}))
                return
            todo_widget["items"] = clean_items
            cfg_save(cfg)
            if request_reload:
                request_reload()
            await _send(writer, "200 OK", "application/json", json.dumps({"ok": True}))

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
                # Zugangsdaten werden erst NACH erfolgreicher Verbindung
                # gespeichert (siehe _apply_wifi_change) - frueher sofort:
                # ein Tippfehler ueberschrieb das funktionierende Netz und
                # das Geraet war danach nicht mehr erreichbar.
                asyncio.create_task(_apply_wifi_change(ssid, password))
            await _send(writer, "200 OK", "application/json", json.dumps({"ok": True}))

        elif path == "/wifi/ap" and method == "POST":
            if wifi_mgr is not None:
                wifi_mgr.start_ap()  # sofort, kein Warten nötig
            await _send(writer, "200 OK", "application/json", json.dumps({"ok": True}))

        elif path == "/api/system-info" and method == "GET":
            info = {"mem_free": get_mem_free() if get_mem_free else None}
            info.update(get_battery_info() if get_battery_info else {"percent": None, "voltage_mv": None})
            await _send(writer, "200 OK", "application/json", json.dumps(info))

        elif path == "/api/wifi-status" and method == "GET":
            status = wifi_mgr.status() if wifi_mgr is not None else {}
            await _send(writer, "200 OK", "application/json", json.dumps(status))

        elif path == "/system/web-password" and method == "POST":
            global _auth_cache
            fields = _parse_qs(body.decode())
            pw = fields.get("password", "")
            pw2 = fields.get("confirm", "")
            if pw != pw2:
                await _send(writer, "400 Bad Request", "application/json",
                             json.dumps({"ok": False, "msg": "Die Passwörter stimmen nicht überein."}))
            elif pw and len(pw) < 8:
                await _send(writer, "400 Bad Request", "application/json",
                             json.dumps({"ok": False, "msg": "Mindestens 8 Zeichen."}))
            elif pw and (_hashlib is None or _os is None):
                await _send(writer, "400 Bad Request", "application/json",
                             json.dumps({"ok": False, "msg": "Auf dieser Firmware nicht verfügbar (hashlib/urandom fehlt)."}))
            else:
                cfg = cfg_load()
                web = cfg.setdefault("web_ui", {})
                if pw:
                    salt = _binascii.hexlify(_os.urandom(16)).decode()
                    web["auth_salt"] = salt
                    web["auth_hash"] = _hash_password(salt, pw)
                    msg = "Passwortschutz aktiv - beim nächsten Seitenaufruf Benutzer admin und dieses Passwort eingeben."
                else:
                    web["auth_salt"] = ""
                    web["auth_hash"] = ""
                    msg = "Passwortschutz ausgeschaltet."
                cfg_save(cfg)
                _auth_cache = None
                _auth_fails.clear()
                await _send(writer, "200 OK", "application/json", json.dumps({"ok": True, "msg": msg}))

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
            if "sd_log_interval_s" in fields:
                try:
                    cfg["room_sensor"]["sd_log_interval_s"] = max(5, int(fields["sd_log_interval_s"]))
                except ValueError:
                    pass
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

        elif path == "/api/list-logos" and method == "GET":
            # Für die Logo-Verwaltung im Dashboard-Editor (Ergänzung zu
            # Optimierungs-Backlog Punkt 7, siehe HANDOFF.md) - zeigt alle
            # bereits hochgeladenen/umgewandelten .bin-Dateien, damit man
            # nicht mehr gebrauchte per "/api/delete-logo" wieder
            # loswerden kann (Flash-Speicher ist begrenzt).
            logos = []
            if _os is not None:
                try:
                    for name in _os.listdir(STATIC_DIR):
                        if name.endswith(".bin"):
                            try:
                                size = _os.stat(STATIC_DIR + "/" + name)[6]
                            except Exception:
                                size = None
                            logos.append({"name": name, "path": STATIC_DIR + "/" + name, "size": size})
                except Exception as e:
                    print("Web-UI: /api/list-logos - Verzeichnis konnte nicht gelesen werden:", e)
            await _send(writer, "200 OK", "application/json", json.dumps(logos))

        elif path == "/api/delete-logo" and method == "POST":
            fields = _parse_qs(body.decode())
            filename = fields.get("filename", "")
            # Nur .bin-Dateien (Logos) - sonst liesse sich z.B. csrf.js oder
            # style.css loeschen und das Web-UI unbenutzbar machen.
            if (not filename or "/" in filename or "\\" in filename or ".." in filename
                    or not filename.endswith(".bin")):
                await _send(writer, "400 Bad Request", "application/json",
                             json.dumps({"ok": False, "msg": "Ungültiger Dateiname."}))
            else:
                try:
                    if _os is not None:
                        _os.remove(STATIC_DIR + "/" + filename)
                    await _send(writer, "200 OK", "application/json", json.dumps({"ok": True}))
                except Exception as e:
                    await _send(writer, "400 Bad Request", "application/json",
                                 json.dumps({"ok": False, "msg": "Löschen fehlgeschlagen: %s" % e}))

        elif path == "/api/upload-logo" and method == "POST":
            # Optimierungs-Backlog Punkt 7 (siehe HANDOFF.md) - ersetzt
            # den bisherigen manuellen Weg (tools/png_to_lvgl.py lokal
            # laufen lassen, dann per mpremote hochladen) durch einen
            # direkten Browser-Upload. Ziel-Binärformat bestätigt aus
            # screens/widget_catalog.py::_build_logo() (siehe
            # png_convert.py-Modul-Docstring für Details).
            content_type = headers.get("content-type", "")
            if "multipart/form-data" not in content_type:
                await _send(writer, "400 Bad Request", "application/json",
                             json.dumps({"ok": False, "msg": "Erwartet multipart/form-data (Datei-Upload)."}))
            else:
                try:
                    fields = _parse_multipart(body, content_type)
                    file_field = fields.get("file")
                    if not file_field or not file_field["data"]:
                        raise ValueError("Keine Datei hochgeladen.")
                    filename_field = fields.get("filename")
                    filename = (filename_field["data"].decode().strip()
                                if filename_field and filename_field["data"] else "") or "logo1.bin"
                    # Auf den reinen Dateinamen reduzieren statt einen
                    # ggf. enthaltenen Pfad rundheraus abzulehnen - das
                    # Web-UI trägt nach einem erfolgreichen Upload den
                    # VOLLEN Pfad ins Dateifeld ein (siehe dashboard.js::
                    # uploadLogo()), ein erneuter Upload sendet diesen bei
                    # einem JS-Bug sonst versehentlich als "Dateinamen"
                    # mit - lieber automatisch auf den Dateinamen kürzen
                    # als mit einer verwirrenden Fehlermeldung abbrechen.
                    # ".." bleibt verboten (echter Pfad-Ausbruchsversuch,
                    # kein normaler Anwendungsfall).
                    filename = _safe_logo_basename(filename)
                    if filename is None:
                        raise ValueError("Ungültiger Dateiname (erlaubt: Buchstaben, Ziffern, _ und -, max. 32 Zeichen).")
                    filename += ".bin"
                    png_data = file_field["data"]
                    save_path = STATIC_DIR + "/" + filename

                    def _convert_and_save():
                        # Laeuft im Hintergrund-Thread: die Umwandlung ist reine
                        # Python-Pixelarithmetik (bis 180.000 Pixel) und blockierte
                        # frueher mehrere Sekunden lang die ganze Oberflaeche.
                        converted = png_convert.convert(png_data)
                        with open(save_path, "wb") as f:
                            f.write(converted)
                        return save_path

                    ok, res = await _bg_call("png_convert", _convert_and_save, timeout_s=60)
                    fetch_worker.bg_clear("png_convert")  # keine ~0,5 MB im Cache behalten
                    if not ok:
                        raise ValueError(res)
                    await _send(writer, "200 OK", "application/json",
                                 json.dumps({"ok": True, "path": save_path}))
                except Exception as e:
                    await _send(writer, "400 Bad Request", "application/json",
                                 json.dumps({"ok": False, "msg": str(e)}))

        elif path == "/layout/dashboard/save" and method == "POST":
            # Multi-Dashboard-Feature (siehe HANDOFF.md) - Body ist jetzt
            # ein JSON-OBJEKT {"profile_id", "name", "widgets"} statt
            # einer nackten Liste, damit klar ist, FÜR WELCHES der
            # (genau 2) Profile gespeichert wird. Löst nur dann einen
            # Live-Reload aus, wenn das gespeicherte Profil auch das
            # gerade AKTIVE ist - sonst würde man z.B. beim Umbauen von
            # "Dashboard 2" fürs Zuhause jedes Mal unnötig einen Reload
            # auf dem gerade laufenden "Dashboard 1" auslösen.
            try:
                payload = json.loads(body.decode())
                profile_id = payload["profile_id"]
                widgets = payload["widgets"]
                name = payload.get("name")
            except Exception:
                payload = None
            if not payload or not isinstance(widgets, list):
                await _send(writer, "400 Bad Request", "application/json", json.dumps({"ok": False}))
            else:
                cfg = cfg_load()
                profiles = cfg.setdefault("screens", {}).setdefault("dashboards", [])
                profile = next((p for p in profiles if p.get("id") == profile_id), None)
                if profile is None:
                    await _send(writer, "404 Not Found", "application/json",
                                 json.dumps({"ok": False, "msg": "Unbekanntes Profil."}))
                else:
                    # Geheime Felder kommen nie im Browser an (siehe SECRET_WIDGET_KEYS):
                    # fehlt ein solcher Schluessel im gesendeten Widget, den
                    # gespeicherten Wert behalten. (Ein leerer String loescht ihn.)
                    widgets = [w for w in widgets if isinstance(w, dict)]
                    old_by_id = {ow.get("id"): ow for ow in profile.get("widgets", []) if isinstance(ow, dict)}
                    for w in widgets:
                        old = old_by_id.get(w.get("id"))
                        if old:
                            for k in SECRET_WIDGET_KEYS:
                                if k not in w and k in old:
                                    w[k] = old[k]
                    profile["widgets"] = widgets
                    name = str(name).strip()[:40] if name else ""
                    if name:
                        profile["name"] = name
                    cfg_save(cfg)
                    if request_reload and profile_id == cfg["screens"].get("active_dashboard_id"):
                        request_reload()
                    await _send(writer, "200 OK", "application/json", json.dumps({"ok": True}))

        elif path == "/dashboard/set-active" and method == "POST":
            # Umschalten, welches Profil am GERÄT angezeigt wird, direkt
            # aus dem Web-UI heraus (zusätzlich zum Burger-Menü-Button am
            # Tab5 selbst - Nutzerwunsch, siehe HANDOFF.md).
            fields = _parse_qs(body.decode())
            profile_id = fields.get("profile_id", "")
            cfg = cfg_load()
            profiles = cfg.get("screens", {}).get("dashboards", [])
            if profile_id not in [p.get("id") for p in profiles]:
                await _send(writer, "404 Not Found", "application/json",
                             json.dumps({"ok": False, "msg": "Unbekanntes Profil."}))
            else:
                cfg["screens"]["active_dashboard_id"] = profile_id
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
                hours = 6.0
            if hours != hours:  # NaN
                hours = 6.0
            hours = max(0.05, min(24 * 366.0, hours))

            def _load_history():
                rows = sd_reader.read_range(hours=hours) if sd_reader else []
                if not rows and get_ram_history:
                    rows = get_ram_history(hours)
                return rows

            ok, rows = await _bg_call("history_web", _load_history)
            await _send(writer, "200 OK", "application/json", json.dumps(rows if ok else []))

        elif path == "/api/ha-states" and method == "GET":
            cfg = _cfg_ro()
            # Bugfix (Multi-Dashboard-Feature) - suchte vorher nach der
            # nicht mehr existierenden Einzel-Dashboard-Struktur, fand
            # dadurch NIE ha_entity/ha_switch-Widgets, der Live-Spiegel
            # zeigte deren Werte deshalb nie an.
            entity_ids = [w["entity_id"] for w in _get_active_dashboard_widgets(cfg)
                          if "entity_id" in w]
            if get_ha_states and entity_ids:
                # Aus dem Hintergrund-Cache (alle 15s frisch) statt bei jedem
                # Browser-Poll (alle 5s pro Tab) synchron HA zu befragen.
                states, _seq = fetch_worker.submit_cached(
                    "ha_states_web", lambda: get_ha_states(entity_ids), 15)
                states = states or {}
            else:
                states = {}
            await _send(writer, "200 OK", "application/json", json.dumps(states))

        elif path.startswith("/api/atom-status/") and method == "GET":
            # Phase C (siehe HANDOFF.md): zwei unabhängige Boards statt
            # einem - Board-Index jetzt Teil des Pfads.
            try:
                board_index = int(path.rsplit("/", 1)[-1])
            except ValueError:
                board_index = -1
            if atom_get_status:
                # Gleicher Cache-Key wie das Dashboard (screens/dashboard.py) - ein
                # einziger Abruf alle 15s bedient Display UND alle offenen Browser-Tabs.
                status, _seq = fetch_worker.submit_cached(
                    "atom_status:%d" % board_index, lambda: atom_get_status(board_index), 15)
                if status is None:
                    status = {"ok": False, "msg": "wird geladen..."}
            else:
                status = {"ok": False, "msg": "nicht verdrahtet"}
            await _send(writer, "200 OK", "application/json", json.dumps(status))

        elif path.startswith("/static/") and method == "GET":
            # Ein GENERISCHER Handler für ALLE statischen Dateien
            # (Optimierungs-Backlog Punkte 1 und 5/7, siehe HANDOFF.md) -
            # Chart.js, das gemeinsame Stylesheet und alle pro Seite
            # ausgelagerten .js-Dateien liegen alle einfach als normale
            # Dateien auf dem Flash (siehe STATIC_DIR) statt im Python-
            # Quellcode eingebettet zu sein, und alle brauchen dieselbe,
            # simple "Datei lesen und ausliefern"-Logik - ein einzelner
            # Handler statt einer eigenen elif-Zeile pro Datei.
            filename = path[len("/static/"):]
            if not filename or "/" in filename or ".." in filename:
                # Rudimentärer Schutz gegen Pfad-Ausbruch (z.B.
                # "/static/../config.json") - dieser Server hat sonst
                # keinerlei Zugriffskontrolle auf Dateipfade.
                await _send(writer, "400 Bad Request", "text/plain", "Ungültiger Dateiname")
            else:
                await _send_static(writer, filename, headers)

        elif path == "/api/widget-snapshot" and method == "GET":
            # Für den Live-Spiegel auf "/" (Phase B) - siehe
            # get_widget_snapshot-Docstring oben.
            snapshot = get_widget_snapshot() if get_widget_snapshot else {}
            await _send(writer, "200 OK", "application/json", json.dumps(snapshot))

        elif path == "/api/external-sensors-status" and method == "GET":
            # Für die /sensors-Seite (Phase B) - im Unterschied zu
            # /api/climate-ext-status HIER kein Live-Abruf, sondern die
            # zuletzt vom Hintergrund-Task geholten Werte (siehe
            # get_external_sensors_state-Docstring oben) - blockiert
            # diese Anfrage nie, egal wie langsam/unerreichbar eine
            # externe Quelle gerade ist.
            state = get_external_sensors_state() if get_external_sensors_state else []
            await _send(writer, "200 OK", "application/json", json.dumps(state))

        elif path == "/api/climate-ext-status" and method == "GET":
            # Für die Start-Seite (Live-Werte + Grafiken) - fragt das
            # externe Gerät (siehe widget_sources.py::fetch_climate_ext(),
            # z.B. Core2-MiniDash) frisch ab, unabhängig vom (selteneren)
            # Abruf-Takt des Dashboard-Widgets selbst.
            cfg = _cfg_ro()
            # Bugfix (Multi-Dashboard-Feature) - suchte vorher nach der
            # nicht mehr existierenden Einzel-Dashboard-Struktur, fand
            # dadurch NIE das climate_ext-Widget, der Live-Spiegel zeigte
            # dessen Werte deshalb nie an (immer "kein Widget konfiguriert").
            widgets = _get_active_dashboard_widgets(cfg)
            climate_ext_widget = next((w for w in widgets if w.get("type") == "climate_ext"), None)
            if climate_ext_widget is None:
                status = {"ok": False, "msg": "Kein Raumklima-Extern-Widget konfiguriert"}
            else:
                status, _seq = fetch_worker.submit_cached(
                    "climate_ext_web", lambda: widget_sources.fetch_climate_ext(climate_ext_widget), 10)
                if status is None:
                    status = {"ok": False, "msg": "wird geladen..."}
            await _send(writer, "200 OK", "application/json", json.dumps(status))

        elif path.startswith("/api/atom-toggle/") and method == "POST":
            # Phase C: Pfad jetzt "/api/atom-toggle/<board_index>/<relay_id>"
            # statt nur "/api/atom-toggle/<relay_id>".
            parts_of_path = path.split("/")
            try:
                board_index = int(parts_of_path[-2])
                relay_id = int(parts_of_path[-1])
            except (ValueError, IndexError):
                board_index, relay_id = -1, 0
            if relay_id and atom_toggle:
                ok, result = await _bg_call("web_atom_toggle:%d" % board_index,
                                             lambda: atom_toggle(board_index, relay_id))
                if not ok:
                    result = {"ok": False, "msg": result}
                fetch_worker.bg_expire("atom_status:%d" % board_index)
            else:
                result = {"ok": False, "msg": "nicht verdrahtet"}
            await _send(writer, "200 OK", "application/json", json.dumps(result))

        elif path == "/atom/save" and method == "POST":
            # Phase C: EIN Board pro Aufruf, per "board_index"-Formularfeld
            # unterschieden (siehe SWITCHES_BODY-JS - zwei getrennte
            # Verbindungs-Blöcke rufen dieselbe Route mit unterschiedlichem
            # board_index auf).
            fields = _parse_qs(body.decode())
            cfg = cfg_load()
            try:
                board_index = int(fields.get("board_index", 0))
            except ValueError:
                board_index = 0
            boards = cfg.setdefault("atom_boards", [])
            while len(boards) <= board_index:
                boards.append({"name": "Atom-Switch-%d" % (len(boards) + 1), "base_url": "", "enabled": False})
            boards[board_index]["enabled"] = fields.get("enabled") == "1"
            if "base_url" in fields:
                boards[board_index]["base_url"] = fields["base_url"]
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
            #
            # PHASE C (siehe HANDOFF.md): Mit ZWEI Boards verrät die
            # Anfrage selbst NICHT, von welchem der beiden sie kommt (das
            # Sync-Protokoll kennt kein Board-Feld) - daher wird die
            # QUELL-IP der eingehenden Verbindung gegen die konfigurierten
            # "atom_boards[i].base_url"-Hosts abgeglichen (siehe
            # _match_atom_board_by_peer() unten). UNVERIFIZIERT, ob
            # get_extra_info("peername") auf dieser MicroPython-
            # Portierung zuverlässig funktioniert - bitte auf Hardware
            # testen (z.B. beide Boards kurz nacheinander am physischen
            # Taster betätigen und prüfen, ob jeweils der RICHTIGE
            # Schalter im Dashboard reagiert).
            try:
                relay_id = int(query_fields.get("relay", 0))
                is_on = query_fields.get("state") == "1"
            except ValueError:
                relay_id, is_on = 0, False
            board_index = _match_atom_board_by_peer(writer)
            if relay_id and board_index is not None and relay_set_from_partner is not None:
                relay_set_from_partner(board_index, relay_id, is_on)
            await _send(writer, "200 OK", "text/plain", "OK")

        elif path == "/api/geocode" and method == "GET":
            # Standortsuche fürs Wetter-/Luftqualität-Widget im /dashboard-
            # Editor (siehe widget_sources.py::geocode_search, Open-Meteo).
            q = query_fields.get("q", "")
            results = []
            if q:
                ok, res = await _bg_call("geocode", lambda: widget_sources.geocode_search(q))
                if ok:
                    results = res
                else:
                    print("Geocode-Suche fehlgeschlagen:", res)
            await _send(writer, "200 OK", "application/json", json.dumps({"results": results}))

        elif path == "/api/ags-search" and method == "GET":
            # Regionalschlüssel-Suche fürs Warnungen-Widget im /dashboard-
            # Editor (siehe widget_sources.py::ags_search, openplzapi.org).
            q = query_fields.get("q", "")
            results = []
            if q:
                ok, res = await _bg_call("ags_search", lambda: widget_sources.ags_search(q))
                if ok:
                    results = res
                else:
                    print("ARS-Suche fehlgeschlagen:", res)
            await _send(writer, "200 OK", "application/json", json.dumps({"results": results}))

        elif path == "/system/uiflow-mode" and method == "POST":
            if not any(_file_exists(f) for f in APP_FILES):
                await _send(writer, "400 Bad Request", "application/json", json.dumps({
                    "ok": False, "msg": "Nicht verfügbar: main.py ist nicht in Startskript + app aufgeteilt "
                                        "(siehe tools/main_stub.py bzw. tools/build_mpy.sh)."}))
            else:
                try:
                    with open(UIFLOW_MARKER, "w") as f:
                        f.write("1")
                    try:
                        _set_uiflow_boot_option(UIFLOW_BOOT_OPTION_MENU)
                    except Exception:
                        try:
                            _os.remove(UIFLOW_MARKER)   # ohne gesetzte boot_option waere der Merker sinnlos
                        except Exception:
                            pass
                        raise
                except Exception as e:
                    await _send(writer, "500 Internal Server Error", "application/json",
                                 json.dumps({"ok": False, "msg": "UIFlow2-Startmenü konnte nicht vorbereitet werden: %s" % e}))
                else:
                    print("Web-UI: UIFlow2-Startmenü angefordert - Neustart...")
                    await _send(writer, "200 OK", "application/json", json.dumps({"ok": True}))
                    asyncio.create_task(_delayed_reboot())

        elif path == "/system/reboot" and method == "POST":
            # Antwort ZUERST verschicken, dann erst neu starten (siehe
            # _delayed_reboot) - sonst bekäme der Browser gar keine
            # Bestätigung mehr, da machine.reset() sofort alles beendet.
            print("Web-UI: Neustart angefordert...")
            await _send(writer, "200 OK", "application/json", json.dumps({"ok": True}))
            asyncio.create_task(_delayed_reboot())

        else:
            await _send(writer, "404 Not Found", "text/plain", "Not found")

    except asyncio.TimeoutError:
        pass  # Verbindung hat nichts (mehr) gesendet - still schliessen (siehe REQUEST_LINE_TIMEOUT_S)
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
