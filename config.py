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

CONFIG_PATH = "/flash/config.json"

DEFAULTS = {
    "wifi": {"ssid": "", "password": ""},
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
    "atom": {
        # M5Stack-Atom-2-Relais-Board (Licht/Steckdose) - siehe
        # atom_client.py für die Protokoll-Details. Standardmäßig AUS, bis
        # eine echte IP-Adresse eingetragen ist. WICHTIG: In der config.json
        # DES ATOM SELBST muss zusätzlich "partner_ip" auf die IP-Adresse
        # DIESES Tab5 gesetzt werden, damit der Atom bei einer lokalen
        # Änderung (Taster/eigenes Web-UI) automatisch Bescheid gibt (siehe
        # web_server.py "/api/sync") - sonst zeigt das Tab5 nur beim
        # nächsten Refresh-Zyklus (nicht sofort) den neuen Zustand.
        "enabled": False,
        "base_url": "http://192.168.1.20",
    },
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
        "web_chart_hours": 6,           # Zeitfenster der Web-UI-Grafiken (von der SD-Karte)
        # Korrekturfaktoren für den lokalen BME688, wie im Core2-
        # Referenzprojekt (dort config.py "offsets") - additiv auf den
        # Rohwert, z.B. gegen einen selbst verursachten Eigenwärme-Versatz
        # durch die Nähe zu Display/Elektronik im Gehäuse.
        "offsets": {"temp_c": 0.0, "humidity": 0.0, "pressure_hpa": 0.0},
    },
    "iaq": {
        # Gleiche Struktur wie im T-Display-S3-Projekt (config.py dort) -
        # siehe sensors/iaq_tracker.py
        "baseline_mode": "fixed",   # "fixed" (einmalig beim Boot) oder "rolling" (passt sich laufend an)
        "burn_in_readings": 10,     # Messungen, bevor der erste Score gezeigt wird
        "tau_up_h": 1.0,            # rolling: wie schnell die Baseline steigt, wenn die Luft besser wird
        "tau_down_h": 24.0,         # rolling: wie schnell die Baseline alte gute Luft "vergisst"
    },
    "web_ui": {
        "enabled": True,
        "port": 80,
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
        "dashboard": {
            "grid": {"cols": 4, "rows": 3},
            "widgets": [
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
                {"id": "licht", "type": "ha_switch", "position": "r3-c3",
                 "backend": "atom", "relay_id": 1, "enabled": True},
                {"id": "steckdose", "type": "ha_switch", "position": "r3-c4",
                 "backend": "atom", "relay_id": 2, "enabled": True},
                {"id": "acoustic", "type": "acoustic", "position": "r1-c2", "enabled": True},
                # --- Im Katalog, aber standardmäßig AUS (passen nicht mehr
                # ins 4x3-Raster) - über das Web-UI (/widgets) aktivierbar,
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
            ],
        },
    },
    "brightness": 80,
    "theme_mode": "dark",   # "dark" | "light" - siehe theme.py::set_mode(), jetzt im Web-UI (/system) statt auf dem Tab5-Screen
    "language": "de",       # "de" | "en" - siehe i18n.py::set_lang(), jetzt im Web-UI (/system) statt auf dem Tab5-Screen
}


def load():
    try:
        with open(CONFIG_PATH) as f:
            cfg = json.load(f)
    except Exception:
        cfg = {}
    cfg = _merge_defaults(cfg, DEFAULTS)
    _migrate_widget_fields(cfg)
    return cfg


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
    """
    try:
        with open(CONFIG_PATH, "w") as f:
            json.dump(cfg, f)
    except Exception as e:
        print("config.save(): Speichern fehlgeschlagen (Einstellung bleibt nur für diese "
              "Sitzung aktiv):", repr(e))


def _merge_defaults(cfg, defaults):
    result = dict(defaults)
    for k, v in cfg.items():
        if isinstance(v, dict) and isinstance(defaults.get(k), dict):
            result[k] = _merge_defaults(v, defaults[k])
        else:
            result[k] = v
    return result


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
    entspricht, ein selbst gewählter Titel bleibt also unangetastet."""
    default_widgets = {w["id"]: w for w in DEFAULTS["screens"]["dashboard"]["widgets"]}
    widgets = cfg.setdefault("screens", {}).setdefault("dashboard", {}).setdefault("widgets", [])
    existing_ids = set()
    for w in widgets:
        existing_ids.add(w.get("id"))
        default_w = default_widgets.get(w.get("id"))
        if not default_w:
            continue
        for key, value in default_w.items():
            w.setdefault(key, value)
        stale_title = _STALE_HARDCODED_TITLES.get(w.get("id"))
        if stale_title is not None and w.get("title") == stale_title:
            del w["title"]

    for widget_id, default_w in default_widgets.items():
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
