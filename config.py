"""
Lädt/speichert config.json auf dem Flash. Enthält WLAN, Magic-Mirror-
Server-Adresse, Home-Assistant-Zugang und Room-Sensor-Einstellungen.

Fehlende Keys werden immer rekursiv mit DEFAULTS aufgefüllt, damit eine
config.json aus einer älteren Version nicht plötzlich Felder vermissen
lässt, auf die neuer Code zugreift.
"""

try:
    import ujson as json
except ImportError:
    import json

try:
    import time
except ImportError:
    time = None

try:
    import uos as _os
except ImportError:
    import os as _os

CONFIG_PATH = "/flash/config.json"
# Sicheres Schreiben (siehe save()): erst in .tmp, dann atomar umbenennen;
# die jeweils vorherige Fassung bleibt als .bak erhalten (siehe load()).
CONFIG_TMP_SUFFIX = ".tmp"
CONFIG_BAK_SUFFIX = ".bak"

# ---------------------------------------------------------------------------
# Standard-Widget-Liste für ein Dashboard-Profil (Multi-Dashboard-Feature,
# siehe DEFAULTS["screens"]["dashboards"] unten) - VOR DEFAULTS definiert,
# damit beide Standard-Profile ("Dashboard 1"/"Dashboard 2") beim
# Erstinstall dieselbe Startbelegung bekommen, ohne die ganze Liste
# zweimal im Quellcode zu pflegen. _clone_default_dashboard_widgets()
# macht bei jedem Aufruf eine ECHTE Kopie (über JSON, siehe dort - dieses
# Modul hat kein copy.deepcopy() importiert und MicroPython hat das
# copy-Modul nicht in jeder Firmware) - sonst würden beide Profile
# dieselben Dict-/Listen-Objekte TEILEN und eine spätere Änderung an
# Profil 1 könnte sich unbeabsichtigt auf Profil 2 auswirken.
# ---------------------------------------------------------------------------
_DEFAULT_DASHBOARD_WIDGETS = [
    # --- Aktiv (12, passen ins 4x3-Raster) ---
    {"id": "clock", "type": "clock", "position": "r1-c1", "enabled": True,
     "format24h": True, "show_seconds": True, "show_date": True},
    {"id": "calendar", "type": "calendar", "position": "r1-c3", "enabled": True,
     "ical_url": "", "max_events": 5, "days_ahead": 14},
    {"id": "weather", "type": "weather", "position": "r1-c4", "enabled": True,
     "location": "Hamburg", "latitude": 53.5511, "longitude": 9.9937, "units": "celsius"},
    {"id": "news", "type": "news", "position": "r2-c1", "enabled": True,
     "rotation_s": 8, "max_items": 5,
     "sources": [
         {"name": "Tagesschau", "feedUrl": "https://www.tagesschau.de/xml/rss2/"},
         {"name": "Spiegel", "feedUrl": "https://www.spiegel.de/schlagzeilen/index.rss"},
         {"name": "Heise", "feedUrl": "https://www.heise.de/rss/heise-atom.xml"},
     ]},
    {"id": "crypto", "type": "crypto", "position": "r2-c2", "enabled": True,
     "symbols": ["bitcoin", "ethereum"], "currency": "usd"},
    {"id": "quote", "type": "quote", "position": "r2-c3", "enabled": True},
    {"id": "env_sensor", "type": "env_sensor_mirror", "position": "r2-c4", "enabled": True},
    {"id": "climate", "type": "climate", "position": "r3-c1", "enabled": True},
    {"id": "air_quality", "type": "air_quality", "position": "r3-c2", "enabled": True},
    {"id": "atom1_switches", "type": "relay_pair", "position": "r3-c3",
     "board_index": 0, "labels": ["Licht", "Steckdose"], "enabled": True},
    {"id": "atom2_switches", "type": "relay_pair", "position": "r3-c4",
     "board_index": 1, "labels": ["Licht", "Steckdose"], "enabled": True},
    {"id": "acoustic", "type": "acoustic", "position": "r1-c2", "enabled": True},
    # --- Im Katalog, aber standardmäßig AUS (passen nicht mehr
    # ins 4x3-Raster) - über das Web-UI (/dashboard) aktivierbar,
    # z.B. anstelle eines der obigen zwölf ---
    {"id": "stocks", "type": "stocks", "position": "r1-c1", "enabled": False,
     "symbols": ["AAPL", "SAP.DE"]},
    {"id": "warnings", "type": "warnings", "position": "r1-c1", "enabled": False,
     "ars": ""},  # Amtlicher Regionalschlüssel - Suche unter warnung.bund.de
    {"id": "server_status", "type": "server_status", "position": "r1-c1", "enabled": False,
     "rotation_s": 15},
    {"id": "air_quality_mirror", "type": "air_quality_mirror", "position": "r1-c1", "enabled": False,
     "location": "Hamburg", "latitude": 53.5511, "longitude": 9.9937},
    {"id": "equalizer", "type": "equalizer", "position": "r1-c1", "enabled": False},
    {"id": "climate_ext", "type": "climate_ext", "position": "r1-c1", "enabled": False,
     "base_url": "http://192.168.1.30"},
    {"id": "pc_status", "type": "pc_status", "position": "r1-c1", "enabled": False,
     "base_url": "http://192.168.1.100:80"},
    {"id": "acceleration", "type": "acceleration", "position": "r1-c1", "enabled": False,
     "sta_tau_s": 0.5, "lta_tau_s": 30.0, "trigger_ratio": 3.0,
     # Alarm-Ton bei Auslösung (steigende Flanke), siehe
     # main.py::_play_alarm() - 1:1 nach dem Core2-Referenz-
     # projekt (config_store.py "quake"), nur mit M5.Speaker.tone()
     # statt eines eigenen Vibrationsmotors (den hat die Tab5 nicht).
     "alarm_beep": True,
     "alarm_tone1_hz": 1800,
     "alarm_tone2_hz": 1200,
     "alarm_beep_ms": 150,
     "alarm_gap_ms": 100,
     "alarm_repeats": 3,
     "alarm_volume_percent": 80},
    {"id": "temp", "type": "ha_entity", "position": "r1-c1", "enabled": False,
     "entity_id": "sensor.wohnzimmer_temperatur", "title": "TEMPERATUR"},
    {"id": "co2", "type": "ha_entity", "position": "r1-c1", "enabled": False,
     "entity_id": "sensor.buero_co2", "title": "CO2"},
    {"id": "elbe_pegel", "type": "elbe_pegel", "position": "r1-c1", "enabled": False,
     "station_name": "ST. PAULI"},
    {"id": "ews", "type": "ews", "position": "r1-c1", "enabled": False},
    {"id": "defcon", "type": "defcon", "position": "r1-c1", "enabled": False,
     "rotation_s": 15},
    {"id": "compliments", "type": "compliments", "position": "r1-c1", "enabled": False,
     "rotation_s": 10,
     "items": ["Du machst das großartig.", "Heute wird ein guter Tag."]},
    {"id": "todo", "type": "todo", "position": "r1-c1", "enabled": False,
     "items": [{"text": "Beispiel-Eintrag", "done": False}]},
    {"id": "logo", "type": "logo", "position": "r1-c1", "enabled": False,
     "file": "logo1.bin", "col_span": 1},
]


def _clone_default_dashboard_widgets():
    return json.loads(json.dumps(_DEFAULT_DASHBOARD_WIDGETS))


DEFAULTS = {
    # ap_password: wird beim ersten Access-Point-Start zufaellig erzeugt (siehe
    # wifi_manager.ap_password()) - frueher fest "configure123" (oeffentlich im Repo).
    "wifi": {"ssid": "", "password": "", "ap_password": ""},
    "general": {
        # Für die Uhr (widgets/clock_widget.py) und den Kalender
        # (widget_sources.py::fetch_calendar) - siehe ntp_clock.py.
        # utc_offset=1 = Deutschland/MEZ (Winterzeit). dst_auto=True
        # schaltet automatisch nach der EU-Regel auf Sommerzeit um
        # (letzter Sonntag im März bis letzter Sonntag im Oktober),
        # unabhängig davon utc_offset umstellen zu müssen.
        "utc_offset": 1,
        "dst_auto": True,
    },
    "server": {
        # Standardmäßig AUS - kein separater Magic-Mirror-Flask-Server
        # geplant (siehe Chat-Verlauf: alles soll möglichst autark auf dem
        # Tab5 laufen, News/Krypto/Wetter/etc. später über direkte APIs
        # statt eines Proxy-Servers - eigenes Folgeprojekt). Solange "enabled"
        # hier False ist, schlagen alle Magic-Mirror-Server-Widgets (News,
        # Krypto, Wetter, Aktien, Zitat, Kalender, Server-Status, Warnungen,
        # Luftqualität-Mirror, Elbe-Pegel, EWS, DEFCON) und der Remote-
        # Fallback des Luftsensors SOFORT fehl statt zu versuchen, den Server
        # zu erreichen - siehe api_client.py::ApiClient.enabled.
        "enabled": False,
        "base_url": "http://192.168.1.10:5031",
    },
    "atom_boards": [
        # Bis zu 2 unabhängige M5Stack-Atom-2-Relais-Boards (Phase C, siehe
        # HANDOFF.md - Nutzer hat inzwischen ein zweites Board, je 2
        # Relais). Jedes Board bekommt genau EIN "relay_pair"-Dashboard-
        # Widget (siehe screens.dashboard.widgets weiter unten,
        # "board_index" verweist per Index hierher). Standardmäßig AUS,
        # bis eine echte IP-Adresse eingetragen ist. WICHTIG: In der
        # config.json JEDES ATOM-BOARDS SELBST muss zusätzlich
        # "partner_ip" auf die IP-Adresse DIESES Tab5 gesetzt werden,
        # damit der Atom bei einer lokalen Änderung (Taster/eigenes
        # Web-UI) automatisch Bescheid gibt (siehe web_server.py
        # "/api/sync") - sonst zeigt das Tab5 nur beim nächsten Refresh-
        # Zyklus (nicht sofort) den neuen Zustand. Das Sync-Protokoll
        # selbst verrät nicht, von welchem Board es kommt - web_server.py
        # ordnet eingehende /api/sync-Aufrufe daher über die Quell-IP der
        # Verbindung einem der beiden base_url-Hosts zu (unverifiziert,
        # bitte auf Hardware testen).
        {"name": "Atom-Switch-1", "base_url": "", "enabled": False},
        {"name": "Atom-Switch-2", "base_url": "", "enabled": False},
    ],
    "home_assistant": {
        # Standardmäßig AUS - noch kein Home-Assistant-Server vorhanden
        # (Nutzer ist noch unentschieden zwischen MQTT und einer eigenen
        # Lösung). Die ha_switch/ha_entity-Kacheln (Licht/Steckdose) im
        # Dashboard bleiben trotzdem sichtbar - siehe screens/dashboard.py -
        # sie zeigen nur "n/a" statt einen echten Zustand, solange "enabled"
        # hier False ist. Auf True setzen (z.B. per Hand in config.json oder
        # später über eine Web-UI-Erweiterung), sobald ein echter Home-
        # Assistant-Server (oder MQTT-Broker mit HA-kompatiblem REST-Proxy)
        # existiert - siehe ha_client.py::HomeAssistantClient.enabled.
        "enabled": False,
        "base_url": "http://homeassistant.local:8123",
        "token": "",  # Long-Lived Access Token, in HA unter Profil -> Sicherheit erzeugen
        "entities": [
            # Platzhalter - z.B. "sensor.wohnzimmer_temperatur", "light.buero"
            # Später gerne über einen kleinen Settings-Screen editierbar machen.
        ],
    },
    "room_sensor": {
        "mode": "auto",              # "auto" | "local_only" | "remote_only" | "both"
        "history_max_points": 4320,   # 24h Verlauf bei 20s-Takt (RAM-Ringpuffer für die Screens) -
                                        # bei >23MB freiem RAM (siehe Speicher-Check) unproblematisch
        "poll_interval_s": 20,
        "recheck_local_every_s": 30,   # wie oft neu geprüft wird, ob BME688 inzwischen da ist
        # Wie oft auf die SD-Karte geloggt werden soll, SOBALD das
        # MicroPython-P4-SD-Problem gelöst ist (siehe README) - schon
        # jetzt als Einstellung angelegt (Phase B, siehe HANDOFF.md:
        # "kann jetzt schon als Einstellung angelegt werden, auch wenn
        # sie mangels funktionierender SD-Karte noch nichts bewirkt").
        # sensors/sd_logger.py::SDLogger liest das, sobald SD-Logging
        # tatsächlich losgeht.
        "sd_log_interval_s": 60,
        "web_chart_hours": 6,           # Zeitfenster der Web-UI-Grafiken (von der SD-Karte)
        # Korrekturfaktoren für den lokalen BME688, wie im Core2-
        # Referenzprojekt (dort config.py "offsets") - additiv auf den
        # Rohwert, z.B. gegen einen selbst verursachten Eigenwärme-Versatz
        # durch die Nähe zu Display/Elektronik im Gehäuse.
        "offsets": {"temp_c": 0.0, "humidity": 0.0, "pressure_hpa": 0.0},
        # Bis zu 2 externe Core2-MiniDash-BME688-Sensoren fuer den
        # Sensor-Dashboard-Screen (Phase A, siehe HANDOFF.md) - dieselbe
        # API wie beim "climate_ext"-Widget im Haupt-Dashboard (siehe
        # widget_sources.py::fetch_climate_ext()), hier aber eigenstaendig
        # konfiguriert (unabhaengig davon, ob das climate_ext-Widget selbst
        # im Dashboard aktiv ist) und mit eigenem In-RAM-Ringpuffer pro
        # Quelle (siehe sensors/history.py, main.py::
        # external_sensor_log_task()). "enabled" pro Quelle einzeln, damit
        # z.B. nur eine der beiden konfiguriert werden kann. Kein
        # _migrate_widget_fields()-Eintrag noetig - das ist kein
        # Widget-Katalog-Eintrag, sondern ein normaler room_sensor-Key und
        # wird daher schon von _merge_defaults() automatisch nachgezogen,
        # sobald ein Geraet mit aelterer config.json neu bootet.
        "external_sources": [
            {"name": "Extern 1", "base_url": "", "enabled": False},
            {"name": "Extern 2", "base_url": "", "enabled": False},
        ],
    },
    "iaq": {
        # Gleiche Struktur wie im T-Display-S3-Projekt (config.py dort) -
        # siehe sensors/iaq_tracker.py
        "baseline_mode": "fixed",   # "fixed" (einmalig beim Boot) oder "rolling" (passt sich laufend an)
        "burn_in_readings": 10,     # Messungen, bevor der erste Score gezeigt wird
        "tau_up_h": 1.0,            # rolling: wie schnell die Baseline steigt, wenn die Luft besser wird
        "tau_down_h": 24.0,         # rolling: wie schnell die Baseline alte gute Luft "vergisst"
        # Zuletzt gelernte Gas-Baseline (Ohm) + Zeitstempel: ueberlebt Neustarts, damit nach
        # einem Stromausfall nicht jedes Mal neu kalibriert werden muss (siehe main.py).
        "saved_baseline": 0,
        "saved_at": 0,
    },
    "web_ui": {
        "enabled": True,
        "port": 80,
        # Optionaler Passwortschutz (Basic-Auth, Benutzer "admin") - gesetzt ueber
        # /system im Web-UI. Leer = kein Schutz. Gespeichert wird nur Salt + Hash.
        "auth_hash": "",
        "auth_salt": "",
    },
    "microphone": {
        "sample_window": 512,
    },
    "sd_logging": {
        "enabled": True,
        "base_path": "/sd/logs",
        "flush_every": 10,  # alle 10 Messungen auf SD flushen (Schreib-Lebensdauer schonen)
    },
    # Ein einziges konsolidiertes Dashboard (ersetzt die früheren drei
    # Screens Environment/Room-Dashboard/Magic-Mirror) - alle 25 Widgets
    # aus allen drei Vorgänger-Screens stehen als Katalog zur Verfügung,
    # aber es passen (wie vorher schon bei WidgetCatalogScreen) nur 12
    # gleichzeitig ins sichtbare 4x3-Raster (KEIN Scrollen - das Dashboard
    # soll auf einen Blick komplett sichtbar sein). Welche 12 aktiv sind
    # und wo, steuert "enabled"/"position" weiter unten und wird künftig
    # über das Web-UI unter /widgets bearbeitbar sein (siehe web_server.py)
    # - Web-UI überschreibt genau diesen Abschnitt, ohne dass die Tab5-
    # Firmware neu geflasht werden muss.
    "screens": {
        # Multi-Dashboard-Feature (Nutzerwunsch: "Dashboard 1"/"Dashboard 2"
        # umschaltbar, z.B. für Arbeit/Zuhause, ohne jedes Mal Widgets neu
        # einzutragen) - GENAU 2 Profile (wie bei "atom_boards", keine
        # beliebig lange Liste), jedes mit eigenem Grid + eigener Widget-
        # Liste. "active_dashboard_id" merkt sich die zuletzt gewählte
        # Ansicht über einen Neustart hinweg (siehe main.py).
        "dashboards": [
            {"id": "dashboard1", "name": "Dashboard 1",
             "grid": {"cols": 4, "rows": 3},
             "widgets": _clone_default_dashboard_widgets()},
            {"id": "dashboard2", "name": "Dashboard 2",
             "grid": {"cols": 4, "rows": 3},
             "widgets": _clone_default_dashboard_widgets()},
        ],
        "active_dashboard_id": "dashboard1",
    },
    "brightness": 80,
    "theme_mode": "dark",   # "dark" | "light" - siehe theme.py::set_mode(), jetzt im Web-UI (/system) statt auf dem Tab5-Screen
    "language": "de",       # "de" | "en" - siehe i18n.py::set_lang(), jetzt im Web-UI (/system) statt auf dem Tab5-Screen
}


# ---------------------------------------------------------------------------
# Debounced Save (Optimierungs-Backlog Punkt 3, siehe HANDOFF.md) -
# request_save() merkt eine Änderung nur vor (Dirty-Flag), statt sofort zu
# schreiben; flush_pending() (periodisch von main.py::
# config_save_debounce_task() aufgerufen) schreibt sie erst, wenn seit der
# LETZTEN request_save()-Änderung mind. DEBOUNCE_S Sekunden Stille
# vergangen sind - bündelt so mehrere schnelle Änderungen (z.B. jedes
# einzelne Event beim Ziehen des Helligkeits-Sliders im Burger-Menü, siehe
# save()-Docstring unten) zu EINEM Flash-Schreibvorgang, statt bei jedem
# einzelnen Event sofort zu schreiben (Flash hat nur begrenzt viele
# Schreibzyklen) UND mitten im LVGL-Callback für die Dauer des
# Datei-Zugriffs zu blockieren.
# ---------------------------------------------------------------------------
_dirty_cfg = None
_dirty_since = None
DEBOUNCE_S = 2


def load():
    if _dirty_cfg is not None:
        # Es gibt eine per request_save() vorgemerkte, aber noch nicht auf
        # den Flash geschriebene Änderung - JEDER Aufrufer von load() soll
        # trotzdem sofort den NEUEN Stand sehen (z.B. eine Web-Seite, die
        # kurz nach einer Helligkeits-Slider-Bewegung geladen wird), nicht
        # die ältere, physisch gespeicherte Version. _dirty_cfg kommt
        # selbst aus einem früheren load()-Aufruf (siehe request_save()-
        # Aufrufer) und ist daher bereits vollständig mit DEFAULTS gemergt -
        # kein erneutes _merge_defaults()/_migrate_widget_fields() nötig.
        return _dirty_cfg
    cfg = _load_raw()
    cfg = _merge_defaults(cfg, DEFAULTS)
    _migrate_atom_config(cfg)
    _migrate_atom_switches_to_relay_pairs(cfg)
    _migrate_dashboard_profiles(cfg)
    _migrate_widget_fields(cfg)
    return cfg


# ---------------------------------------------------------------------------
# load_readonly(): gecachte, NUR-LESE-Variante von load() fuer haeufig
# aufgerufene Stellen (Web-Polling, Watchdog, Logging-Tasks). load() parst
# bei JEDEM Aufruf die ganze config.json (~10 KB) und fuehrt alle Migrationen
# aus - bei mehreren offenen Browser-Tabs mit 4-5s-Polling sind das viele
# Parse-Vorgaenge pro Minute. Das zurueckgegebene Dict wird GETEILT: NIE
# veraendern (wer aendern will, nimmt load() und speichert mit save()).
# ---------------------------------------------------------------------------
RO_CACHE_TTL_MS = 5000
_ro_cache = None
_ro_cache_ms = 0


def _now_ms():
    if time is None:
        return 0
    if hasattr(time, "ticks_ms"):
        return time.ticks_ms()
    return int(time.time() * 1000)


def _ticks_diff_ms(a, b):
    if time is not None and hasattr(time, "ticks_diff"):
        return time.ticks_diff(a, b)
    return a - b


def load_readonly():
    global _ro_cache, _ro_cache_ms
    if _dirty_cfg is not None:
        return _dirty_cfg  # ausstehende (noch nicht geschriebene) Aenderung sofort sichtbar
    now = _now_ms()
    if _ro_cache is not None and _ticks_diff_ms(now, _ro_cache_ms) < RO_CACHE_TTL_MS:
        return _ro_cache
    _ro_cache = load()
    _ro_cache_ms = now
    return _ro_cache


def _invalidate_ro_cache():
    global _ro_cache
    _ro_cache = None


def _try_read_json(path):
    """Liefert das geparste Dict oder None (Datei fehlt/kaputt/leer)."""
    try:
        with open(path) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except OSError:
        return None  # Datei existiert nicht - normal (z.B. erster Start)
    except Exception as e:
        print("config: %s ist beschaedigt (%r)" % (path, e))
        return None


def _load_raw():
    """Liest die Config mit Rueckfall-Kette: config.json -> config.json.tmp
    (fertig geschrieben, aber noch nicht umbenannt - Stromausfall mitten in
    save()) -> config.json.bak (vorherige Fassung). Frueher wurde bei JEDEM
    Lesefehler still auf leere Defaults zurueckgefallen - und der naechste
    save() hat die (eigentlich noch rettbare) Config dann endgueltig
    ueberschrieben."""
    cfg = _try_read_json(CONFIG_PATH)
    if cfg is not None:
        return cfg
    for suffix in (CONFIG_TMP_SUFFIX, CONFIG_BAK_SUFFIX):
        cfg = _try_read_json(CONFIG_PATH + suffix)
        if cfg is not None:
            print("config: %s nicht lesbar - verwende %s" % (CONFIG_PATH, CONFIG_PATH + suffix))
            return cfg
    return {}


def _remove_quiet(path):
    try:
        _os.remove(path)
    except OSError:
        pass


def _write_atomic(cfg):
    """config.json ersetzen, OHNE dass sie zwischendurch leer/halb ist:
    1) komplett nach .tmp schreiben (nur wenn das klappt, geht es weiter),
    2) alte config.json -> .bak (FAT verweigert Umbenennen auf eine
       existierende Datei, daher .bak vorher entfernen),
    3) .tmp -> config.json.
    Bricht der Strom irgendwo ab, existiert immer mindestens eine
    vollstaendige Fassung (siehe _load_raw())."""
    tmp = CONFIG_PATH + CONFIG_TMP_SUFFIX
    bak = CONFIG_PATH + CONFIG_BAK_SUFFIX
    with open(tmp, "w") as f:
        json.dump(cfg, f)
    try:
        _os.stat(CONFIG_PATH)
        have_old = True
    except OSError:
        have_old = False
    if have_old:
        _remove_quiet(bak)
        _os.rename(CONFIG_PATH, bak)
    _os.rename(tmp, CONFIG_PATH)


def save(cfg):
    """
    WICHTIG: Darf NIEMALS eine unbehandelte Exception werfen. Ein Aufrufer
    ist u.a. ein LVGL-Event-Callback (z.B. Helligkeits-Slider) über
    m5ui/port.py::task_handler - eine Exception dort hat sich als fatal
    herausgestellt: sie reißt den kompletten m5ui/LVGL-Scheduler mit
    ("RuntimeError: schedule queue full" als Folgefehler direkt danach im
    Log), was auch die gesamte übrige asyncio-Verarbeitung (inkl. Web-
    Server, der dadurch für den Browser unerreichbar wurde) lahmgelegt hat.
    Ursache des ursprünglichen ENODEV-Fehlers: "/" ist auf dieser Firmware
    nur ein virtueller Wurzel-Mountpunkt (siehe `os.listdir("/")` ->
    ["system", "flash"]) - der eigentlich beschreibbare interne Speicher
    liegt unter "/flash" (siehe CONFIG_PATH oben). Der try/except bleibt
    trotzdem als Sicherheitsnetz stehen: sollte sich der Mountpunkt in
    einer künftigen Firmware-Version nochmal ändern, geht dadurch nur die
    Persistenz verloren (Einstellung gilt nur für die aktuelle Sitzung)
    statt die komplette Oberfläche mitzureißen.

    Schreibt SOFORT und SYNCHRON - für Änderungen, die unmittelbar
    anderswo sichtbar sein müssen (z.B. bevor main.py::request_reload()
    das Dashboard mit der gerade gespeicherten config.json neu aufbaut,
    siehe web_server.py-Routen) oder die nur EINMAL passieren (kein
    Debounce-Vorteil). Für potenziell sehr schnell wiederholte Aufrufe
    (z.B. ein per Touch gezogener Slider) stattdessen request_save()
    weiter unten verwenden.
    """
    _invalidate_ro_cache()
    try:
        _write_atomic(cfg)
    except Exception as e:
        print("config.save(): Speichern fehlgeschlagen (Einstellung bleibt nur für diese "
              "Sitzung aktiv):", repr(e))


def request_save(cfg):
    """Wie save(cfg), aber gebündelt (siehe Modul-Docstring oben zum
    Debounce) - merkt cfg nur als "zu speichern" vor, der eigentliche
    Schreibvorgang passiert erst über flush_pending(), spätestens
    DEBOUNCE_S Sekunden nach der LETZTEN request_save()-Änderung. Für
    Aufrufer aus einem LVGL-Event-Callback gedacht, die potenziell SEHR
    SCHNELL hintereinander aufgerufen werden (z.B. der Helligkeits-Slider
    im Burger-Menü) - direktes save() bei JEDEM einzelnen Slider-Event
    würde sowohl den Flash unnötig oft beschreiben als auch bei jedem
    Event kurz blockieren (Datei-I/O mitten im LVGL-Callback). load()
    gibt eine noch ausstehende Änderung sofort zurück (siehe dort), sie
    ist für andere Aufrufer also nicht "unsichtbar", nur noch nicht
    physisch geschrieben."""
    global _dirty_cfg, _dirty_since
    _dirty_cfg = cfg
    _invalidate_ro_cache()
    if time is not None:
        _dirty_since = time.time()  # bei JEDEM Aufruf neu gesetzt (echtes Debounce, kein Throttle) -
        # der Schreibvorgang wartet auf DEBOUNCE_S Sekunden Stille NACH
        # der letzten Änderung, erfasst also den endgültigen Endzustand
        # (z.B. die Slider-Position beim Loslassen), nicht einen
        # Zwischenstand mitten in der Bewegung.


def flush_pending(force=False):
    """Schreibt eine per request_save() vorgemerkte Änderung, FALLS seit
    der letzten request_save()-Änderung mind. DEBOUNCE_S Sekunden Stille
    vergangen sind (oder force=True, z.B. main.py vor einem Neustart über
    /system/reboot, damit eine gerade erst gezogene, aber noch nicht
    geschriebene Änderung nicht verloren geht) - von main.py periodisch
    aufgerufen (siehe main.py::config_save_debounce_task()). Reine No-Op,
    wenn gerade nichts aussteht."""
    global _dirty_cfg, _dirty_since
    if _dirty_cfg is None:
        return
    if not force and (time is None or (time.time() - _dirty_since) < DEBOUNCE_S):
        return
    cfg_to_save = _dirty_cfg
    _dirty_cfg = None
    _dirty_since = None
    save(cfg_to_save)


def _clone(obj):
    """Tiefe Kopie fuer JSON-artige Daten (dict/list/Grundtypen) - dieses Modul
    hat kein copy.deepcopy() (nicht in jeder MicroPython-Firmware vorhanden)."""
    if isinstance(obj, dict):
        return {k: _clone(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_clone(v) for v in obj]
    return obj


def _merge_defaults(cfg, defaults):
    """Fehlende Schluessel aus DEFAULTS auffuellen. Werte, die NICHT aus der
    Datei kommen, werden KOPIERT: frueher zeigte das Ergebnis dafuer direkt auf
    die Objekte in DEFAULTS (dict(defaults) ist nur eine flache Kopie) - wer die
    Config dann im Speicher veraenderte (z.B. Notiz abhaken), veraenderte
    damit unbemerkt die Standardwerte des ganzen Programms."""
    result = {}
    for k, dv in defaults.items():
        if k in cfg:
            v = cfg[k]
            if isinstance(v, dict) and isinstance(dv, dict):
                result[k] = _merge_defaults(v, dv)
            else:
                result[k] = v
        else:
            result[k] = _clone(dv)
    for k, v in cfg.items():
        if k not in result:
            result[k] = v
    return result


def _migrate_atom_config(cfg):
    """Frühere Version: EIN "atom"-Key mit einer einzigen Board-Verbindung
    - jetzt "atom_boards", eine Liste (Phase C, siehe HANDOFF.md - Nutzer
    hat inzwischen ein zweites physisches Atom-Relais-Board). Reine No-Op,
    sobald einmal migriert (dann gibt es "atom" nicht mehr, nur noch
    "atom_boards")."""
    legacy = cfg.pop("atom", None)
    if legacy is None:
        return
    boards = cfg.setdefault("atom_boards", [])
    if boards and boards[0].get("base_url"):
        return  # atom_boards wurde inzwischen schon anderweitig befüllt
    if not boards:
        boards.append({})
    boards[0]["name"] = boards[0].get("name") or "Atom-Switch-1"
    boards[0]["base_url"] = legacy.get("base_url", "")
    boards[0]["enabled"] = legacy.get("enabled", False)


def _migrate_atom_switches_to_relay_pairs(cfg):
    """Frühere Version: Licht/Steckdose waren zwei GETRENNTE ha_switch-
    Widgets (jeweils backend="atom", eigener relay_id) - jetzt EIN
    "relay_pair"-Widget pro physischem Atom-Board (Phase C, siehe
    HANDOFF.md). Führt gefundene Alt-Widgets zu GENAU EINEM relay_pair-
    Widget zusammen (board_index=0, da es vorher nur ein einziges Board
    gab), unter Beibehaltung selbst vergebener Titel als "labels" und der
    Position/aktiviert-Status des ERSTEN (niedrigste relay_id) Alt-
    Widgets. Läuft VOR _migrate_widget_fields(), damit dessen "fehlt
    komplett -> aus DEFAULTS anhängen"-Regel nicht zusätzlich ein
    doppeltes relay_pair-Widget für board_index=0 anhängt. Reine No-Op,
    sobald einmal migriert (dann gibt es keine ha_switch/atom-Widgets
    mehr zu finden).

    WICHTIG: liest über .get() statt .setdefault() - .setdefault() hätte
    den alten Einzel-Schlüssel "dashboard" nach der Umstellung auf
    mehrere Profile (siehe _migrate_dashboard_profiles()) bei JEDEM
    weiteren Hochfahren künstlich als leeres {"widgets": []} wieder
    angelegt (reiner Lesezugriff, der versehentlich schreibt) - das hat
    _migrate_dashboard_profiles() fälschlich glauben lassen, es gäbe noch
    echte Alt-Daten zu migrieren, und die bereits migrierten "dashboards"
    mit einer leeren Neu-Migration überschrieben (dabei gingen alle
    nutzereigenen Widgets verloren, nur die Katalog-Standardwidgets kamen
    zurück) - beim Testen aufgefallen, siehe HANDOFF.md-Empfehlung, jede
    Migration auf Idempotenz zu prüfen."""
    widgets = cfg.get("screens", {}).get("dashboard", {}).get("widgets", [])
    legacy_widgets = [w for w in widgets if w.get("type") == "ha_switch" and w.get("backend") == "atom"]
    if not legacy_widgets:
        return
    legacy_widgets.sort(key=lambda w: w.get("relay_id") or 0)
    first = legacy_widgets[0]
    labels = [w.get("title") or ("Relais %s" % w.get("relay_id", "?")) for w in legacy_widgets]
    merged = {
        "id": "atom1_switches",
        "type": "relay_pair",
        "position": first.get("position", "r3-c3"),
        "board_index": 0,
        "labels": labels,
        "enabled": any(w.get("enabled", True) for w in legacy_widgets),
    }
    insert_at = widgets.index(first)
    for w in legacy_widgets:
        widgets.remove(w)
    widgets.insert(insert_at, merged)


def _migrate_dashboard_profiles(cfg):
    """Frühere Version: EIN Dashboard (cfg["screens"]["dashboard"]) - jetzt
    "dashboards", eine Liste mit genau 2 Profilen (Multi-Dashboard-
    Feature, z.B. Arbeit/Zuhause, umschaltbar ohne Widgets neu
    einzutragen). Das bisherige Dashboard wird 1:1 in BEIDE Profile
    kopiert (über JSON, siehe _clone_default_dashboard_widgets()-Docstring
    für die Begründung - kein geteiltes Objekt zwischen beiden Profilen) -
    nichts geht verloren, "Dashboard 2" kann der Nutzer danach frei
    umbauen. Läuft VOR _migrate_widget_fields(), damit dessen Default-
    Auffüll-Logik schon die neue "dashboards"-Struktur vorfindet.

    WICHTIG: KEIN "schon migriert"-Guard über cfg["screens"]["dashboards"]
    selbst (anders als z.B. bei _migrate_atom_config()) - _merge_defaults()
    oben füllt "dashboards" bei einer alten config.json bereits MIT
    FRISCHEN STANDARD-PROFILEN auf (da "dashboards" ein neuer Top-Level-
    Key in DEFAULTS ist, den die alte gespeicherte config.json noch nicht
    hat), BEVOR diese Funktion überhaupt läuft - ein Guard "ist schon
    gefüllt?" würde also fälschlich JEDES ERSTE Hochfahren nach dem
    Update überspringen und die echten, gerade erst gefundenen Nutzer-
    Widgets stillschweigend verwerfen. Der korrekte Guard ist stattdessen
    "gibt es überhaupt noch die alte, einzelne 'dashboard'-Angabe zu
    migrieren" (siehe legacy-Prüfung unten) - danach wird "dashboards"
    IMMER aus der gefundenen Legacy-Angabe neu aufgebaut, auch wenn schon
    (Default-)Werte drinstehen. Reine No-Op ab dem zweiten Hochfahren
    (dann gibt es "dashboard" nicht mehr, nur noch "dashboards")."""
    screens = cfg.setdefault("screens", {})
    legacy = screens.pop("dashboard", None)
    if legacy is None:
        return
    profile1 = legacy
    profile1["id"] = "dashboard1"
    profile1.setdefault("name", "Dashboard 1")
    profile2 = json.loads(json.dumps(legacy))
    profile2["id"] = "dashboard2"
    profile2["name"] = "Dashboard 2"
    screens["dashboards"] = [profile1, profile2]
    screens.setdefault("active_dashboard_id", "dashboard1")


def _migrate_widget_fields(cfg):
    """Behebt ein wiederkehrendes Problem: _merge_defaults() oben mischt
    nur fehlende TOP-LEVEL-Keys ein - eine Liste wie "screens.dashboard.
    widgets" wird komplett 1:1 aus der gespeicherten config.json
    übernommen, sobald sie einmal existiert. Wird später ein neues Feld zu
    einem in DEFAULTS bereits vorhandenen Widget hinzugefügt (z.B.
    "backend"/"relay_id" bei den Schalter-Widgets, als das Atom-Relais-
    Board dazukam), erreicht dieses Feld ein Gerät, das schon einmal eine
    config.json gespeichert hat, NIEMALS automatisch - sichtbar z.B.
    daran, dass ein Schalter-Widget sich nicht mit dem echten Relais
    synchronisiert, weil es beim fehlenden "backend"-Feld auf den alten
    Home-Assistant-Pfad zurückfällt.

    Holt für jedes gespeicherte Widget (per "id" gegen DEFAULTS
    abgeglichen) alle Felder nach, die im Default-Eintrag existieren, im
    gespeicherten aber fehlen - OHNE etwas zu überschreiben, das der
    Nutzer bereits selbst gesetzt hat (Position, enabled, Titel, ein
    bewusst gewähltes anderes Backend, ...).

    ZUSÄTZLICH (das hat gerade tatsächlich gefehlt): fügt jedes Widget aus
    DEFAULTS, das in der gespeicherten Liste noch GAR NICHT vorkommt (z.B.
    "climate_ext", ein komplett neuer Katalog-Eintrag, den es vorher nicht
    gab), am Ende hinzu - sonst bleibt ein neu hinzugefügter Widget-Typ auf
    einem Gerät, das schon einmal eine config.json gespeichert hat, für
    immer unsichtbar im /dashboard-Editor, ganz unabhängig davon, wie gut
    die Seite sonst aufgeräumt ist.

    DRITTENS: räumt bekannte, inzwischen entfernte fest einprogrammierte
    Alt-Titel auf (siehe _STALE_HARDCODED_TITLES) - z.B. war "PC STATUS"
    früher als Text direkt in config.py hinterlegt, damit ein Gerät mit
    bereits gespeicherter config.json den späteren, übersetzbaren Titel
    ("Computer Status"/"Computer Status" je nach Sprache) nie zu sehen
    bekäme, weil der alte feste Titel weiterhin Vorrang hätte (exakt das
    Problem, das schon bei LICHT/STECKDOSE/RAUMKLIMA EXT. auftrat) - nur
    entfernt, wenn der gespeicherte Titel GENAU dem alten Hardcode
    entspricht, ein selbst gewählter Titel bleibt also unangetastet.

    Läuft seit dem Multi-Dashboard-Feature (siehe
    _migrate_dashboard_profiles()) für JEDES der (genau 2) Profile
    einzeln - jedes Profil bekommt unabhängig dieselben fehlenden Felder/
    neuen Widget-Typen nachgezogen, unabhängig davon, wie unterschiedlich
    der Nutzer die beiden Profile inzwischen bestückt hat."""
    default_widgets_by_id = {w["id"]: w for w in _DEFAULT_DASHBOARD_WIDGETS}
    profiles = cfg.setdefault("screens", {}).setdefault("dashboards", [])
    for profile in profiles:
        widgets = profile.setdefault("widgets", [])
        existing_ids = set()
        for w in widgets:
            existing_ids.add(w.get("id"))
            default_w = default_widgets_by_id.get(w.get("id"))
            if not default_w:
                continue
            for key, value in default_w.items():
                w.setdefault(key, value)
            stale_title = _STALE_HARDCODED_TITLES.get(w.get("id"))
            if stale_title is not None and w.get("title") == stale_title:
                del w["title"]

        for widget_id, default_w in default_widgets_by_id.items():
            if widget_id not in existing_ids:
                widgets.append(dict(default_w))


# Frühere fest einprogrammierte Titel, die inzwischen durch übersetzbare
# STRINGS-Fallbacks ersetzt wurden (siehe _migrate_widget_fields()-
# Docstring, Punkt DRITTENS) - bei Bedarf hier weitere Einträge ergänzen,
# falls nochmal ein hart codierter Titel nachträglich übersetzbar gemacht
# wird.
_STALE_HARDCODED_TITLES = {
    "licht": "LICHT",
    "steckdose": "STECKDOSE",
    "climate_ext": "RAUMKLIMA EXT.",
    "pc_status": "PC STATUS",
}
