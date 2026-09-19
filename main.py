"""
Haupteinstiegspunkt für die Tab5-Firmware. Task-Architektur (uasyncio)
nach dem Vorbild von main.py aus dem T-Display-S3-Projekt: mehrere
nebenläufige Tasks statt einer einzelnen Schleife, damit ein langsamer
Sensor (BME688, ~30s wegen Heizer-Selbsterwärmung) den Rest nicht ausbremst.

WICHTIG - Stand dieser Datei: Die reine Sensor-/Logik-Seite (Config,
Sensor-Abstraktion, Historie, SD-Logging, Web-UI) ist fertig und teils
sogar ohne Hardware testbar (siehe tools/sim_test.py). Der LVGL/m5ui-
Initialisierungsteil unten ist dagegen ein "bestes Vorbild" ohne
Möglichkeit, ihn vor Ankunft der echten Tab5 zu verifizieren - die
exakten m5ui/UIFlow2-Aufrufe (Display-Init, Screen-Aktivierung,
LVGL-Task-Pumpe im uasyncio-Loop) MÜSSEN auf dem echten Gerät
gegengeprüft werden. Entsprechend markierte TODO-Kommentare zeigen, wo.

Benötigte Dateien auf dem Gerät (Wurzel, siehe README.md für die
vollständige Liste inkl. sensors/ und widgets/):
  config.py, wifi_manager.py, api_client.py, ha_client.py, web_server.py,
  theme.py, main.py  <- diese Datei
"""

from machine import Pin, I2C
import sys
import time
import gc
import uasyncio as asyncio

# Boot-Zeitmessung (Nutzerfrage: "dauert beim Laden einige Sekunden, ist
# das normal?") - time.ticks_ms() statt time.time(), da die RTC vor dem
# NTP-Sync weiter unten noch keine sinnvolle Uhrzeit hat und ticks_ms()
# unabhängig davon ab Boot hochzählt. _boot_t0 so früh wie möglich im
# Skript gesetzt, um auch den Import-/Modul-Ladevorgang selbst mit
# einzuschließen, nicht erst den Code danach.
_boot_t0 = time.ticks_ms()


def _boot_log(label):
    print("[Boot-Zeit] %6d ms - %s" % (time.ticks_diff(time.ticks_ms(), _boot_t0), label))


def _early_wifi_start():
    """WLAN-Verbindung SOFORT anstossen, noch VOR den ~8s dauernden Modul-Imports:
    Der WLAN-Chip baut sie selbststaendig auf, waehrend hier noch Python-Dateien
    uebersetzt werden (Boot-Log: der Aufbau dauert ~9,5s - vorher startete er erst
    danach). Liest nur die SSID/das Passwort direkt aus config.json (kein config-
    Import noetig). Schlaegt etwas fehl, laeuft der normale Weg (boot_network_task)."""
    try:
        _boot_log("Fruehstart: Start")
        import ujson
        with open("/flash/config.json") as f:
            wifi_cfg = ujson.load(f).get("wifi", {})
        _boot_log("Fruehstart: config.json gelesen")
        import wifi_manager   # erzeugt die beiden WLAN-Objekte (Chip-Initialisierung)
        _boot_log("Fruehstart: wifi_manager importiert")
        if wifi_manager.early_connect(wifi_cfg.get("ssid", ""), wifi_cfg.get("password", "")):
            _boot_log("WLAN-Fruehstart angestossen")
    except Exception as e:
        print("WLAN-Fruehstart uebersprungen:", e)


_early_wifi_start()


import config
import wifi_manager
import web_server
import api_client
import ha_client
import atom_client
import theme
import i18n
import ntp_clock
import widget_sources
import fetch_worker
_boot_log("Basis-Module importiert")
# Hintergrund-Worker EINMAL jetzt anlegen (solange der Speicher noch nicht fragmentiert ist) und
# dauerhaft weiterverwenden. Frueher wurde fuer jeden Abruf ein neuer Thread gestartet - ueber
# Nacht schlug das nach 15-90 min dauerhaft mit "can't create thread" fehl.
_boot_log("Thread-Pool: %d Worker" % fetch_worker.start_pool())

from sensors.air_quality import SensorManager, LocalBME688Source, RemoteSensorSource, SensorReading
from sensors.accelerometer import LocalAccelSource
from sensors.microphone import LocalMicSource, MicReading
from sensors.history import SensorHistory
from sensors.sd_logger import SDLogger
from sensors.sd_reader import SDReader
_boot_log("Sensor-Module importiert")

from screens.dashboard import DashboardScreen
from screens.sensor_history_screen import SensorHistoryScreen
from burger_menu import BurgerMenu
_boot_log("Screens/Menü importiert")

# ---------------------------------------------------------------------------
# Konfiguration laden
# ---------------------------------------------------------------------------
cfg = config.load()
_boot_log("config geladen")

# Für die Web-UI (/system, siehe web_server.py::get_battery_info) - vom
# battery_task() unten regelmäßig aktualisiert, initial None/None bis der
# erste Zyklus gelaufen ist (Web-UI zeigt dann "nicht verfügbar" statt
# eines falschen Platzhalterwerts).
_last_battery_info = {"percent": None, "voltage_mv": None}

# Theme/Sprache aus der gespeicherten Config anwenden - MUSS vor dem Bau
# irgendeines Widgets passieren, da theme.py/i18n.py ihre Werte (COLORS/
# STRINGS) nur beim jeweiligen set_mode()/set_lang()-Aufruf aktualisieren,
# nicht laufend aus cfg nachschlagen (siehe theme.py-Docstring). Bisher
# gab es diesen Aufruf beim Boot GAR NICHT (nur der frühere, inzwischen
# entfernte Settings-Screen rief es bei einer Live-Änderung auf) - eine
# über das Web-UI (/system) gespeicherte Einstellung hätte also nie
# gewirkt, auch nicht nach einem Neustart.
theme.set_mode(cfg.get("theme_mode", "dark"))
i18n.set_lang(cfg.get("language", "de"))

# Für die Uhr (widgets/clock_widget.py) und den Kalender (widget_sources.py)
# - siehe ntp_clock.py. Muss VOR dem Bau des Dashboards passieren, damit die
# Uhr direkt beim ersten Tick den richtigen utc_offset/dst_auto nutzt.
ntp_clock.set_general(cfg.get("general", {}))

# ---------------------------------------------------------------------------
# I2C-Bus (Grove-Port/PORT.A)
#
# ECHTE PINS GEFUNDEN (recherchiert, mehrfach unabhängig bestätigt - siehe
# u.a. wiki.openelab.io/m5stack/m5stack-tab5-iot-development-kit,
# github.com/Jasionf/M5Stack-Tab5, hackster.io "Exploring M5Stack Tab5
# with MicroPython + LVGL"): PORT.A (Grove, HY2.0-4P) liegt auf
# SDA=GPIO53, SCL=GPIO54 - NICHT 21/22, wie ursprünglich geraten.
#
# Das erklärt auch RÜCKWIRKEND, warum die geratenen Pins 21/22 das Display
# lautlos ausgeschaltet hatten: GPIO21 ist auf der Tab5 RS485_RX und
# GPIO22 ist LCD_BL - die Backlight-Steuerleitung des Displays selbst!
# Ein I2C-Treiber auf GPIO22 hätte diese Funktion gekapert, was exakt zum
# beobachteten "Display geht aus, ohne Exception" passt (Hardware-
# Peripheriekonflikt statt sauberem Python-Fehler). Die internen Bus
# (Touch/IMU/RTC/Power-IC) liegen übrigens auf GPIO31/32 - auch nicht 21/22.
#
# DEBUG_SKIP_I2C_BUS/DEBUG_DISABLE_I2C_SENSORS jetzt auf False, da die
# Pins jetzt mit hoher Sicherheit stimmen und PORT.A für nichts anderes
# genutzt wird (anders als die alten 21/22). Bei erneuten Problemen (z.B.
# Display geht wieder aus) hier zuerst wieder auf True setzen, um I2C als
# Ursache aus- oder einzugrenzen - siehe HANDOFF.md für die Debug-Historie.
# ---------------------------------------------------------------------------
DEBUG_SKIP_I2C_BUS = False

if DEBUG_SKIP_I2C_BUS:
    print("DEBUG_SKIP_I2C_BUS=True - I2C(0) wird gar nicht erst erstellt, siehe main.py")
    i2c = None
else:
    try:
        i2c = I2C(0, sda=Pin(53), scl=Pin(54), freq=100000)
    except Exception as e:
        print("I2C-Init fehlgeschlagen:", e)
        i2c = None

# ---------------------------------------------------------------------------
# SD-Karte mounten - TODO: exakten UIFlow2/MicroPython-Aufruf für die Tab5
# gegenprüfen (üblich ist etwas wie `uos.mount(SDCard(...), "/sd")`, aber
# die Tab5 hat ggf. eine eigene Hilfsfunktion dafür in ihrem BSP-Paket).
# ---------------------------------------------------------------------------
_boot_log("I2C-Bus initialisiert")
sd_logging_cfg = cfg["sd_logging"]
sd_logger = None
sd_reader = None
try:
    # TODO: durch den echten Tab5-SD-Mount ersetzen, sobald bekannt
    import uos
    # uos.mount(SDCard(...), "/sd")
    if sd_logging_cfg.get("enabled", True):
        sd_logger = SDLogger(base_path=sd_logging_cfg["base_path"],
                              flush_every=sd_logging_cfg["flush_every"])
    sd_reader = SDReader(base_path=sd_logging_cfg["base_path"])
except Exception as e:
    print("SD-Karte nicht verfügbar, Logging bleibt deaktiviert:", e)
_boot_log("SD-Logger/-Reader erstellt")

# ---------------------------------------------------------------------------
# Sensoren initialisieren
# ---------------------------------------------------------------------------
room_sensor_cfg = cfg["room_sensor"]

# DEBUG_DISABLE_I2C_SENSORS: siehe DEBUG_SKIP_I2C_BUS oben - jetzt
# ebenfalls False, da die echten Pins (53/54) mit den I2C-Timeout-
# Aussetzern nichts zu tun haben sollten, die ursächlich mit den FALSCHEN
# Pins (21/22, davon einer die Backlight-Leitung!) beobachtet wurden.
# Bei Problemen zuerst wieder auf True setzen, um Sensoren als Ursache
# auszuschließen.
DEBUG_DISABLE_I2C_SENSORS = False
_sensor_i2c = None if DEBUG_DISABLE_I2C_SENSORS else i2c
if DEBUG_DISABLE_I2C_SENSORS:
    print("DEBUG_DISABLE_I2C_SENSORS=True - Sensoren bekommen kein i2c-Objekt, siehe main.py")

api = api_client.ApiClient(cfg["server"]["base_url"])
# Siehe api_client.py::ApiClient.enabled - standardmäßig AUS
# (config.py-Default), da kein separater Magic-Mirror-Server geplant ist.
api.enabled = cfg["server"].get("enabled", False)
remote_air = RemoteSensorSource(api)
local_air = LocalBME688Source(
    i2c=_sensor_i2c,
    iaq_cfg=cfg["iaq"],
    sample_interval_s=float(room_sensor_cfg["poll_interval_s"]),
    offsets=room_sensor_cfg.get("offsets", {}),
)
air_sensor_manager = SensorManager(
    remote_air, local_air,
    history=None,  # Historie wird zentral im Screen aus allen 3 Quellen gemergt
    recheck_interval_s=room_sensor_cfg["recheck_local_every_s"],
    mode=room_sensor_cfg["mode"],
)

def _get_active_dashboard_profile(fresh_cfg):
    """Multi-Dashboard-Feature (Nutzerwunsch: "Dashboard 1"/"Dashboard 2"
    umschaltbar, z.B. Arbeit/Zuhause) - findet das gerade aktive Profil
    (config.py "screens.active_dashboard_id") in "screens.dashboards".
    Fällt auf das ERSTE Profil zurück, falls die aktive id aus irgendeinem
    Grund nicht (mehr) existiert (z.B. nach einem manuellen config.json-
    Edit) - lieber IRGENDEIN gültiges Dashboard zeigen als abzustürzen."""
    profiles = fresh_cfg["screens"]["dashboards"]
    active_id = fresh_cfg["screens"].get("active_dashboard_id")
    for profile in profiles:
        if profile.get("id") == active_id:
            return profile
    return profiles[0]


def _find_widget_cfg(widget_id, default):
    """Sucht ein Widget anhand seiner id in der Dashboard-Konfiguration -
    z.B. für Sensor-Parameter, die zwar technisch beim Sensor selbst
    ansetzen (nicht bei der Anzeige), aber trotzdem im /dashboard-Editor
    bearbeitbar sein sollen wie jede andere Widget-Einstellung auch
    (siehe config.py "acceleration"-Widget: sta_tau_s/lta_tau_s/trigger_ratio).

    Sucht seit dem Multi-Dashboard-Feature zuerst im AKTIVEN Profil (das
    ist zum Boot-Zeitpunkt, wenn diese Funktion läuft, relevant - eine
    physische Sensor-Kalibrierung wie hier ist pro GERÄT sinnvoll, nicht
    pro Dashboard-Profil), fällt aber auf ALLE Profile zurück, falls dort
    nichts gefunden wird (z.B. falls jemand das Widget nur in "Dashboard 2"
    konfiguriert hat, obwohl "Dashboard 1" aktiv ist - besser eine
    gefundene Einstellung verwenden als stumpf beim Default zu bleiben)."""
    active_profile = _get_active_dashboard_profile(cfg)
    for w in active_profile["widgets"]:
        if w.get("id") == widget_id:
            return w
    for profile in cfg["screens"]["dashboards"]:
        for w in profile["widgets"]:
            if w.get("id") == widget_id:
                return w
    return default


accel_widget_cfg = _find_widget_cfg("acceleration", {})
accel_source = LocalAccelSource(
    i2c=_sensor_i2c,
    sta_tau_s=accel_widget_cfg.get("sta_tau_s", 0.5),
    lta_tau_s=accel_widget_cfg.get("lta_tau_s", 30.0),
    trigger_ratio=accel_widget_cfg.get("trigger_ratio", 3.0),
    # Mindest-Amplitude in g (Widget-Feld "min_amplitude_g"): verhindert Fehlalarme in
    # sehr ruhiger Umgebung, siehe sensors/quake_trigger.py. 0 = ausgeschaltet.
    min_amplitude_g=accel_widget_cfg.get("min_amplitude_g", 0.02),
)

_boot_log("BME688/Beschleunigungssensor initialisiert")
mic_cfg = cfg["microphone"]
# "i2s=None" ist jetzt korrekt so (kein TODO mehr) - sensors/microphone.py
# nutzt M5.Mic.begin()/record()/end() (offizielle UIFlow2-API, siehe
# m5-docs "Tab5 Mic"), kein manuelles I2S-Setup nötig.
mic_source = LocalMicSource(i2s=None, sample_window=mic_cfg["sample_window"])
# Waehrend der Alarmton laeuft, NICHT aufnehmen (M5.Speaker.end() im Mic-Read
# wuerde den Ton abschneiden - Mic und Speaker teilen sich die Audio-Hardware).
mic_source.skip_if = lambda: _alarm_playing

history = SensorHistory(max_len=room_sensor_cfg["history_max_points"])
# Grober Langzeit-Verlauf im RAM: 1 Punkt alle HISTORY_LONG_STEP_S Sekunden,
# 288 Punkte = 24h. Die feine `history` deckt bei history_max_points=288 und
# 20s-Takt nur ~96 Minuten ab - ohne (funktionierende) SD-Karte zeigten
# "Letzte 3 Stunden"/"Gesamter Verlauf" im Sensor-Screen deshalb dasselbe.
HISTORY_LONG_STEP_S = 300
history_long = SensorHistory(max_len=288)

# ---------------------------------------------------------------------------
# Bis zu 2 externe Core2-MiniDash-BME688-Sensoren (Phase A, siehe
# HANDOFF.md) - jeweils ein eigener In-RAM-Ringpuffer, analog zu `history`
# oben, aber gefuellt von external_sensor_log_task() (siehe unten) statt
# von local_sensor_log_task(). Nur die ersten 2 Eintraege aus der Config
# werden beruecksichtigt, auch falls dort versehentlich mehr stehen.
# ---------------------------------------------------------------------------
external_sources_cfg = room_sensor_cfg.get("external_sources", [])[:2]
external_histories = [SensorHistory(max_len=room_sensor_cfg["history_max_points"])
                       for _ in external_sources_cfg]

# ---------------------------------------------------------------------------
# Home Assistant Client (Room Dashboard, siehe screens/room_dashboard.py)
# ---------------------------------------------------------------------------
ha_cfg = cfg["home_assistant"]
ha = ha_client.HomeAssistantClient(ha_cfg["base_url"], ha_cfg["token"])
# Siehe ha_client.py::HomeAssistantClient.enabled - standardmäßig AUS
# (config.py-Default), bis ein echter Home-Assistant-Server/MQTT-Proxy
# existiert. Die Licht-/Steckdosen-Schalter im Dashboard bleiben trotzdem
# sichtbar, zeigen nur "n/a" statt einen echten Zustand.
ha.enabled = ha_cfg.get("enabled", False)

# ---------------------------------------------------------------------------
# Gepairte M5Stack-Atom-2-Relais-Boards (Phase C, siehe HANDOFF.md - der
# Nutzer hat inzwischen ZWEI unabhängige physische Boards, je 2 Relais,
# z.B. "Schreibtisch" und "Regal") - siehe atom_client.py. Liste statt
# einer einzelnen Instanz; Index entspricht config.json "atom_boards"[i]
# bzw. dem "board_index"-Feld eines relay_pair-Dashboard-Widgets.
# ---------------------------------------------------------------------------
atom_boards_cfg = cfg.get("atom_boards", [])
atom_clients = [atom_client.AtomClient(board.get("base_url", "")) for board in atom_boards_cfg]
for _client, _board_cfg in zip(atom_clients, atom_boards_cfg):
    _client.enabled = _board_cfg.get("enabled", False)

# ---------------------------------------------------------------------------
# LVGL/m5ui-Grundgerüst
#
# Verifizierte Init-Reihenfolge (auf echter Hardware getestet, siehe
# README): M5.begin() MUSS vor m5ui.init() kommen, der "Screen" ist ein
# m5ui.M5Page (nicht lv.obj()), aktiviert wird er über page0.screen_load()
# (nicht lv.scr_load()). Alle anderen Widgets im Projekt (Card, StatusBar,
# TrafficLight, ...) nutzen ganz normales lv.obj()/lv.label()/... mit
# parent=<M5Page> - das funktioniert unverändert, da M5Page ein normaler
# LVGL-Parent ist (bestätigt getestet: lv.label(page0) zeigt Text an).
# ---------------------------------------------------------------------------


def _on_brightness_changed(percent):
    # Widgets.setBrightness(0-255) ist die offizielle UIFlow2-API dafür
    # (dieselbe "Widgets"-Referenz, mit der main.py oben schon
    # setRotation() aufruft, siehe uiflow-micropython-Doku "Widgets - A
    # basic UI library"). Bewusst mit try/except abgesichert: ein
    # GitHub-Issue (m5stack/uiflow-micropython #85) berichtet, dass die
    # Helligkeit auf dem Tab5 in Kombination mit M5UI/LVGL evtl. nicht
    # greift - falls das hier ebenfalls auftritt, im REPL zur Diagnose
    # `dir(Widgets)` prüfen, ob/wie die Methode auf dieser Firmware
    # tatsächlich heißt.
    level = max(0, min(255, round(percent * 255 / 100)))
    try:
        Widgets.setBrightness(level)
    except Exception as e:
        print("Helligkeit: Widgets.setBrightness(%d) fehlgeschlagen: %r" % (level, e))
    else:
        print("Helligkeit gesetzt: %d%% (Widgets.setBrightness(%d))" % (percent, level))


_boot_log("Modul-Initialisierung fertig, vor M5-Import")
try:
    import M5
    from M5 import *
    import m5ui
    import lvgl as lv

    M5.begin()
    _boot_log("M5.begin() fertig")
    # Querformat (1280x720), aber um 180° gedreht gegenüber dem ursprünglich
    # bestätigten Wert 1 (Handoff Abschnitt 3). setRotation() Werte 0-3
    # entsprechen 90°-Schritten, d.h. 180° Differenz = +2 (mod 4).
    # 1 -> 3. Falls das Bild dadurch erneut falsch rum steht (z.B. weil
    # das Binding rotation-Werte anders zählt als Standard-M5GFX), im REPL
    # testweise 0/1/2/3 durchprobieren.
    Widgets.setRotation(3)
    # Gespeicherte Helligkeit direkt beim Boot anwenden (nicht erst nach
    # der ersten Slider-Bewegung im Burger-Menü) - siehe
    # _on_brightness_changed() oben für die Begründung/Fallback-Hinweise.
    _on_brightness_changed(cfg.get("brightness", 80))
    m5ui.init()
    _boot_log("m5ui.init() fertig")

    # NUR EINE m5ui.M5Page - zwei komplette 1280x720-Bildschirme
    # gleichzeitig im Speicher zu halten (jeweils mit eigenem Framebuffer)
    # hat beim ersten Test zu einem weißen, eingefrorenen Display geführt
    # (vermutlich Speichererschöpfung) - stattdessen wird auf DERSELBEN
    # Page zwischen Haupt-Dashboard und Sensor-Dashboard umgeschaltet,
    # genau wie beim Live-Reload schon bewährt: alten Screen sauber
    # abbauen (destroy() + page.clean()), neuen Screen aufbauen. Siehe
    # _switch_to_dashboard()/_switch_to_sensor_screen() unten.
    page = m5ui.M5Page(bg_c=0x000000)

    burger_menu = BurgerMenu(
        page=page,
        wifi_mgr=wifi_manager,
        cfg=cfg,
        cfg_save=config.request_save,
        on_brightness_changed=_on_brightness_changed,
        switch_to_dashboard=lambda: _switch_to_dashboard(),
        switch_to_sensor_screen=lambda: _switch_to_sensor_screen(),
        # Multi-Dashboard-Feature (siehe HANDOFF.md/config.py "screens.
        # dashboards") - Funktion(profile_id), siehe _switch_dashboard_
        # profile() unten. get_dashboard_profiles liefert die aktuelle
        # Liste (id+name je Profil) frisch bei jedem Menü-Öffnen, damit
        # ein zwischenzeitlich über das Web-UI umbenanntes Profil auch
        # ohne Neustart mit dem neuen Namen im Menü auftaucht.
        switch_dashboard_profile=lambda pid: _switch_dashboard_profile(pid),
        get_dashboard_profiles=lambda: [
            {"id": p["id"], "name": p.get("name", p["id"])}
            for p in config.load()["screens"]["dashboards"]
        ],
        get_active_dashboard_id=lambda: config.load()["screens"].get("active_dashboard_id"),
    )

    def _build_dashboard_screen():
        global dashboard_screen
        fresh_cfg = config.load()
        dashboard_screen = DashboardScreen(
            screen_config=_get_active_dashboard_profile(fresh_cfg),
            api_client=api,
            air_sensor_manager=air_sensor_manager,
            accel_source=accel_source,
            mic_source=mic_source,
            history=history,
            ha_client=ha,
            atom_clients=atom_clients,
            sd_logger=sd_logger,
            parent=page,
            on_menu_pressed=burger_menu.open,
        )
        web_server.relay_set_from_partner = dashboard_screen.set_relay_state
        web_server.get_widget_snapshot = dashboard_screen.get_snapshot

    def _switch_dashboard_profile(profile_id):
        """Multi-Dashboard-Feature - wechselt, WELCHES der (genau 2)
        Dashboard-Profile angezeigt wird, UND wechselt dabei IMMER zum
        Dashboard-Screen selbst, egal von welchem Screen aus aufgerufen
        (Burger-Menü-Button "Dashboard 1"/"Dashboard 2" - ein Klick
        darauf soll das jeweilige Dashboard auch tatsächlich ZEIGEN, nicht
        nur die Auswahl für später vormerken). Persistiert die Wahl über
        die debounced request_save() (Optimierungs-Backlog Punkt 3, siehe
        HANDOFF.md).

        BUGFIX: hier stand vorher "nur neu aufbauen, wenn dashboard_screen
        bereits sichtbar ist" - das hat genau dann NICHTS getan, wenn man
        sich gerade auf dem Sensor-Dashboard befand (dashboard_screen ist
        dann None), man landete beim Klick auf "Dashboard 1"/"Dashboard 2"
        also weiterhin auf dem Sensor-Dashboard. _switch_to_dashboard()
        selbst kümmert sich schon korrekt darum, JEDEN aktuell aktiven
        Screen (Dashboard ODER Sensor-Dashboard) sauber abzubauen, ein
        Aufrufer muss das also nicht selbst prüfen."""
        fresh_cfg = config.load()
        fresh_cfg["screens"]["active_dashboard_id"] = profile_id
        config.request_save(fresh_cfg)
        _switch_to_dashboard()

    def _switch_to_dashboard():
        global dashboard_screen, sensor_history_screen
        # WICHTIG: auch dashboard_screen selbst abbauen, falls es schon
        # aktiv ist (z.B. Bürger-Menü geöffnet, während man schon auf dem
        # Dashboard war, und dann trotzdem auf "DASHBOARD" getippt) -
        # sonst bleibt dessen eigener Uhr-Timer aktiv und läuft nach dem
        # gleich folgenden page.clean() gegen bereits gelöschte Objekte
        # (LvReferenceError, komplettes Einfrieren inkl. stehender Uhr -
        # genau das wurde gemeldet). Vorher wurde hier nur sensor_history_screen
        # geprüft, nie der Fall "schon auf dem Ziel-Screen" selbst.
        if dashboard_screen is not None:
            try:
                dashboard_screen.destroy()
            except Exception as e:
                print("Dashboard-Cleanup fehlgeschlagen:", e)
            dashboard_screen = None
        if sensor_history_screen is not None:
            try:
                sensor_history_screen.destroy()
            except Exception as e:
                print("Sensor-Dashboard-Cleanup fehlgeschlagen:", e)
            sensor_history_screen = None
        page.clean()
        # Optimierungs-Backlog Punkt 4 (siehe HANDOFF.md) - der alte
        # Screen ist gerade komplett abgebaut (destroy() + page.clean()
        # haben potenziell hunderte LVGL-Objekte freigegeben) - JETZT
        # aufräumen, BEVOR der neue Screen seinerseits wieder viele neue
        # Objekte anlegt, hält den Speicher-Spitzenwert während des
        # Übergangs niedriger und beugt Heap-Fragmentierung vor. Laut
        # Speicher-Check (>23MB frei) nicht dringend, aber billig.
        gc.collect()
        _build_dashboard_screen()

    def _switch_to_sensor_screen():
        global dashboard_screen, sensor_history_screen
        # Gleiches Prinzip wie oben: BEIDE möglichen aktuell aktiven
        # Screens abbauen, nicht nur den "anderen" - falls man schon auf
        # dem Sensor-Dashboard war und nochmal "SENSOR-DASHBOARD" tippt,
        # hätte sonst dessen eigener Refresh-Timer denselben Effekt.
        if dashboard_screen is not None:
            try:
                dashboard_screen.destroy()
            except Exception as e:
                print("Dashboard-Cleanup fehlgeschlagen:", e)
            dashboard_screen = None
        if sensor_history_screen is not None:
            try:
                sensor_history_screen.destroy()
            except Exception as e:
                print("Sensor-Dashboard-Cleanup fehlgeschlagen:", e)
            sensor_history_screen = None
        page.clean()
        gc.collect()  # siehe Kommentar in _switch_to_dashboard() oben
        sensor_history_screen = SensorHistoryScreen(
            page,
            history=history,
            history_long=history_long,
            sd_reader=sd_reader,
            external_sources=list(zip(external_sources_cfg, external_histories)),
            on_menu_pressed=burger_menu.open,
        )

    sensor_history_screen = None
    _boot_log("vor _build_dashboard_screen() (Widget-Aufbau)")
    _build_dashboard_screen()
    _boot_log("_build_dashboard_screen() fertig")

    page.screen_load()
    _boot_log("page.screen_load() fertig - Dashboard jetzt sichtbar")
    print("Screen geladen:", page)

    _HAS_DISPLAY = True
except Exception as e:
    print("LVGL/m5ui nicht verfügbar (erwartet auf dem Desktop, NICHT auf der Tab5):")
    sys.print_exception(e)  # vollständiger Traceback mit Zeilennummer, nicht nur str(e)
    dashboard_screen = None
    burger_menu = None
    sensor_history_screen = None
    _HAS_DISPLAY = False


# ---------------------------------------------------------------------------
# Live-Reload ohne Neustart - siehe web_server.py::request_reload-Docstring
# und screens/widget_catalog.py::WidgetCatalogScreen.destroy(). Die Web-UI
# setzt nach dem Speichern nur ein Flag; die eigentliche Arbeit passiert
# unten in reload_watcher_task(), synchron und im selben uasyncio-Tick wie
# alles andere LVGL-bezogene - NIE direkt aus einem HTTP-Request-Handler
# heraus (web_server.py läuft technisch im selben Loop, aber ein Reload
# mitten in einer laufenden Response wäre unnötig riskant).
# ---------------------------------------------------------------------------
_reload_requested = False


def _request_reload():
    global _reload_requested
    _reload_requested = True


web_server.request_reload = _request_reload


def _do_reload():
    """Baut das Dashboard mit der aktuellen config.json komplett neu auf,
    OHNE das Gerät neu zu starten - siehe destroy() für die Aufräum-Seite.
    Bewusst NICHT async und ohne jedes await dazwischen: läuft in einem
    Rutsch, damit kein anderer Task (Refresh, Touch-Event, ...) mitten in
    einem halb aufgeräumten/halb neu aufgebauten Screen dazwischenfunkt."""
    global cfg, dashboard_screen

    if not _HAS_DISPLAY:
        return

    if dashboard_screen is None:
        # Gerade ist das Sensor-Dashboard aktiv (siehe _switch_to_sensor_screen()
        # in main()), nicht das Haupt-Dashboard - hier nichts umbauen,
        # sonst würde mitten im sichtbaren Sensor-Screen unerwartet
        # page.clean() aufgerufen. config.json ist ja schon gespeichert;
        # sobald wieder zum Dashboard gewechselt wird, baut
        # _build_dashboard_screen() ohnehin mit der frischen Konfiguration.
        print("Live-Reload übersprungen (Sensor-Dashboard ist gerade aktiv).")
        return

    print("Live-Reload: baue Dashboard neu auf...")
    cfg = config.load()

    # Theme/Sprache/Zeitzone VOR dem Neuaufbau anwenden - siehe Kommentar
    # bei der ersten Anwendung beim Boot weiter oben (COLORS/STRINGS werden
    # nur beim set_mode()/set_lang()-Aufruf aktualisiert, nicht laufend aus
    # cfg nachgeschlagen - ein Neuaufbau VOR dieser Anwendung würde also mit
    # der alten Farbe/Sprache neu bauen).
    theme.set_mode(cfg.get("theme_mode", "dark"))
    i18n.set_lang(cfg.get("language", "de"))
    ntp_clock.set_general(cfg.get("general", {}))

    # Backend-"enabled"-Flags neu ziehen, falls sich das gerade über
    # /system oder /atom/save geändert hat.
    api.enabled = cfg["server"].get("enabled", False)
    api.base_url = cfg["server"].get("base_url", api.base_url).rstrip("/")
    ha.enabled = cfg["home_assistant"].get("enabled", False)
    for _client, _board_cfg in zip(atom_clients, cfg.get("atom_boards", [])):
        _client.enabled = _board_cfg.get("enabled", False)
        _client.base_url = _board_cfg.get("base_url", _client.base_url).rstrip("/")

    try:
        dashboard_screen.destroy()
        page.clean()
        gc.collect()  # siehe Kommentar in _switch_to_dashboard() (Optimierungs-Backlog Punkt 4)
        _build_dashboard_screen()
    except Exception as e:
        # Absichtlich KEIN Absturz des ganzen Geräts, falls der Neuaufbau
        # scheitert (z.B. eine kaputt gespeicherte Widget-Config) - dann
        # bleibt der Screen bis zum nächsten harten Neustart leer, statt
        # auf den alten Zustand zurückzufallen (bekannter Schwachpunkt).
        print("Live-Reload fehlgeschlagen - Dashboard bleibt leer, bis reboot passiert:")
        sys.print_exception(e)
        return

    # BurgerMenu selbst nicht neu erzeugen (baut sein Overlay erst beim
    # nächsten Öffnen neu auf, kein bestehender Screen-Aufbau, der
    # aufgeräumt werden müsste) - nur die cfg-Referenz auffrischen, damit
    # z.B. eine neu gespeicherte Helligkeit beim nächsten Öffnen stimmt.
    burger_menu.cfg = cfg
    print("Live-Reload abgeschlossen.")


async def reload_watcher_task():
    global _reload_requested
    while True:
        if _reload_requested:
            _reload_requested = False
            try:
                _do_reload()
            except Exception as e:
                print("Live-Reload-Task-Fehler (alter Zustand bleibt bestehen):", e)
        await asyncio.sleep_ms(500)


# ---------------------------------------------------------------------------
# Task: Beschleunigung/Erschütterung, schnell (~20 Hz)
#
# Bewusst getrennt vom restlichen Poll-Takt (poll_interval_s, oft 20s) -
# ein kurzes Klopfen/Erschütterung würde bei so seltenem Polling locker
# zwischen zwei Messungen durchrutschen. Der STA/LTA-Trigger selbst
# (sensors/quake_trigger.py) rechnet ohnehin mit echter Zeit, nicht mit
# Sample-Anzahl - das Intervall hier ist daher unkritisch für die Trigger-
# Mathematik, aber wichtig für die Reaktionsgeschwindigkeit.
# ---------------------------------------------------------------------------
_latest_accel_reading = None
ACCEL_POLL_HZ = 20
_accel_was_triggered = False  # für die steigende Flanke (Alarm-Ton), siehe accel_task()


_alarm_playing = False  # True, solange der Erdbeben-Alarmton laeuft (Mic-Lesen pausiert dann)


async def _play_alarm(widget_cfg):
    """Alarm-Ton bei einer erkannten Erschütterung (steigende Flanke, siehe
    accel_task() unten) - 1:1 nach dem Core2-Referenzprojekt
    (config_store.py "quake": alarm_tone1_hz/tone2_hz/alarm_beep_ms/...),
    nur über M5.Speaker.tone() (offizielle, in der M5Unified-/UIFlow2-Doku
    gut belegte API - u.a. Fire/PaperS3/StickS3 nutzen sie unverändert)
    statt eines Vibrationsmotors, den die Tab5 nicht hat.
    M5.Speaker.tone(frequenz_hz, dauer_ms) läuft im Hintergrund über I2S/
    DMA - der Aufruf selbst blockiert nicht lange, trotzdem zwischen den
    Tönen mit asyncio.sleep_ms() warten, damit die Sequenz nicht
    überlappt und der Rest der App (Touch, Web-UI) weiterläuft.

    WICHTIG - Mic/Speaker-Exklusivität (siehe M5Stack-Doku "Tab5 Mic"):
    Mikrofon und Lautsprecher können nicht gleichzeitig aktiv sein. Da
    sensors/microphone.py das Mikrofon ohnehin nur für die kurze Dauer
    einer einzelnen Messung aktiviert (nicht dauerhaft), reicht ein
    einmaliges M5.Mic.end() hier VOR dem ersten Ton als Sicherheitsnetz -
    danach übernimmt der nächste reguläre Mic-Lesezyklus das erneute
    M5.Mic.begin() ganz von selbst."""
    global _alarm_playing
    if not widget_cfg.get("alarm_beep", True):
        return
    _alarm_playing = True
    try:
        try:
            M5.Mic.end()
        except Exception:
            pass
        M5.Speaker.setVolumePercentage(widget_cfg.get("alarm_volume_percent", 80))
        beep_ms = widget_cfg.get("alarm_beep_ms", 150)
        gap_ms = widget_cfg.get("alarm_gap_ms", 100)
        tone1_hz = widget_cfg.get("alarm_tone1_hz", 1800)
        tone2_hz = widget_cfg.get("alarm_tone2_hz", 1200)
        for _ in range(widget_cfg.get("alarm_repeats", 3)):
            M5.Speaker.tone(tone1_hz, beep_ms)
            await asyncio.sleep_ms(beep_ms + gap_ms)
            M5.Speaker.tone(tone2_hz, beep_ms)
            await asyncio.sleep_ms(beep_ms + gap_ms)
    except Exception as e:
        print("Alarm-Ton fehlgeschlagen:", e)
    finally:
        _alarm_playing = False


async def accel_task():
    global _latest_accel_reading, _accel_was_triggered
    period_ms = int(1000 / ACCEL_POLL_HZ)
    last_err_print = 0
    while True:
        # Frueher OHNE try/except: eine einzige Exception aus accel_source.read()
        # beendete diesen Task dauerhaft (Erschuetterungsalarm bis zum
        # Neustart tot, ohne jede Meldung).
        try:
            _latest_accel_reading = accel_source.read()
            if _latest_accel_reading.ok:
                is_triggered = _latest_accel_reading.quake.get("triggered", False)
                if is_triggered and not _accel_was_triggered:
                    # Steigende Flanke - Alarm als eigener Task, damit ein
                    # langsamer Ton diesen 20Hz-Loop nicht blockiert.
                    asyncio.create_task(_play_alarm(accel_widget_cfg))
                _accel_was_triggered = is_triggered
        except Exception as e:
            now = time.time()
            if now - last_err_print > 60:  # nicht 20x pro Sekunde loggen
                last_err_print = now
                print("Fehler in accel_task() (Zyklus übersprungen):", e)
        await asyncio.sleep_ms(period_ms)


# ---------------------------------------------------------------------------
# Task: Beschleunigungs-Widget-Anzeige, eigener ~1s-Takt (siehe
# screens/dashboard.py::update_acceleration()-Docstring für die
# Begründung) - unabhängig vom 20Hz-Sensor-Takt (accel_task() oben, für
# eine genaue STA/LTA-Berechnung) UND vom trägen poll_interval_s-Takt der
# übrigen Widgets (dashboard_refresh_task() unten).
# ---------------------------------------------------------------------------
async def accel_display_task():
    while True:
        if dashboard_screen is not None:
            try:
                dashboard_screen.update_acceleration(_latest_accel_reading)
            except Exception as e:
                print("Fehler in accel_display_task() (Zyklus übersprungen):", e)
        await asyncio.sleep(1)


# ---------------------------------------------------------------------------
# Task: Computer-Status-Widget (CPU/GPU eines PCs im lokalen Netz), eigener
# 3s-Takt - der normale Online-Widget-Mechanismus (WidgetCatalogScreen.
# refresh(), siehe screens/widget_catalog.py) läuft nur alle poll_interval_s
# (20s), das lässt sich über FETCH_INTERVAL_S nicht auf 3s herunterdrehen.
# Gleiches Prinzip wie bei der Beschleunigung (accel_display_task oben) -
# eigener, entkoppelter Takt für genau EIN zeitkritisches Widget, statt den
# Grundtakt für alle anderen (deutlich weniger dringenden) Widgets
# mitzuziehen.
# ---------------------------------------------------------------------------
async def pc_status_task():
    while True:
        if dashboard_screen is not None:
            try:
                dashboard_screen.update_pc_status()
            except Exception as e:
                print("Fehler in pc_status_task() (Zyklus übersprungen):", e)
        await asyncio.sleep(3)


# ---------------------------------------------------------------------------
# Task: Ergebnisse der Hintergrund-Abfragen (Home Assistant/Atom-Relais) alle
# 2s im Hauptthread auf die Widgets anwenden - der eigentliche Netzwerkzugriff
# laeuft im Hintergrund-Thread (fetch_worker.submit_cached), dieser Task
# blockiert nie. Siehe screens/dashboard.py::poll_network_state().
# ---------------------------------------------------------------------------
async def network_state_task():
    while True:
        if dashboard_screen is not None:
            try:
                dashboard_screen.poll_network_state()
            except Exception as e:
                print("Fehler in network_state_task() (Zyklus übersprungen):", e)
        await asyncio.sleep(2)


# ---------------------------------------------------------------------------
# Task: Dashboard-Refresh (lokale Sensoren/HA-Entitäten jeden Zyklus +
# Magic-Mirror-API-Widgets throttled nach FETCH_INTERVAL_S, siehe
# screens/dashboard.py::refresh()/screens/widget_catalog.py). Ein einziger
# Task genügt jetzt (früher zwei: environment- und magic-mirror-Refresh
# für die früheren getrennten Screens) - der poll_interval_s-Takt ist
# schnell genug, dass auch der 60s-Krypto-Refresh nicht spürbar nachhinkt,
# dank interner Throttling-Logik in WidgetCatalogScreen.refresh().
# ---------------------------------------------------------------------------
_latest_air_reading = None
_latest_mic_reading = None


def _persist_iaq_baseline():
    """Gelernte Gas-Baseline hoechstens stuendlich speichern (nur bei >1% Aenderung
    und gestellter Uhr) - so ueberlebt sie Neustarts/Stromausfaelle."""
    baseline = local_air.iaq_tracker.gas_baseline
    if not baseline or not ntp_clock.is_synced():
        return
    fresh = config.load()
    iaq = fresh.setdefault("iaq", {})
    old = iaq.get("saved_baseline") or 0
    if old and abs(old - baseline) < 0.01 * baseline:
        return
    iaq["saved_baseline"] = round(baseline, 1)
    iaq["saved_at"] = int(time.time())
    config.request_save(fresh)


async def local_sensor_log_task():
    """Liest BME688 (Luft) + Mikrofon EINMAL pro Zyklus, UNABHÄNGIG davon,
    ob gerade das Haupt-Dashboard oder das Sensor-Dashboard sichtbar ist
    (siehe screens/sensor_history_screen.py) - vorher passierte das nur
    innerhalb von DashboardScreen.refresh(), das komplett aussetzt,
    sobald dashboard_screen beim Bildschirmwechsel auf None gesetzt wird
    (siehe main() unten). Ergebnis: der Verlaufspuffer (history) fror
    ein, sobald man länger auf dem Sensor-Dashboard blieb - "nur einmal
    aktualisiert, danach keine neuen Werte". Genau das gleiche Prinzip
    wie schon bei accel_task() für die Beschleunigung.

    dashboard_refresh_task() unten nutzt die hier zwischengespeicherten
    _latest_air_reading/_latest_mic_reading beim Anzeige-Update, statt
    selbst nochmal zu lesen - sonst würde der BME688 doppelt so oft
    gelesen wie beabsichtigt (siehe frühere "nicht zu oft heizen"-Beratung)."""
    global _latest_air_reading, _latest_mic_reading
    poll_interval_s = room_sensor_cfg["poll_interval_s"]
    sd_last_ms = None
    long_last_ms = None
    iaq_saved_ms = None
    startup_retries = 0
    while True:
        try:
            _latest_air_reading = air_sensor_manager.read() if air_sensor_manager is not None else None
            _latest_mic_reading = mic_source.read() if mic_source is not None else None

            combined = {}
            if _latest_air_reading is not None and _latest_air_reading.ok:
                combined.update(_latest_air_reading.as_dict())
            if _latest_accel_reading is not None and _latest_accel_reading.ok:
                combined.update(_latest_accel_reading.as_dict())
            if _latest_mic_reading is not None and _latest_mic_reading.ok:
                combined.update(_latest_mic_reading.as_dict())

            if combined:
                history.add(combined)
                now_ms = time.ticks_ms()
                if long_last_ms is None or time.ticks_diff(now_ms, long_last_ms) >= HISTORY_LONG_STEP_S * 1000:
                    history_long.add(combined)
                    long_last_ms = now_ms
                if iaq_saved_ms is None or time.ticks_diff(now_ms, iaq_saved_ms) >= 3600 * 1000:
                    iaq_saved_ms = now_ms
                    _persist_iaq_baseline()
                if sd_logger is not None:
                    # "sd_log_interval_s" (Web-UI /sensors) wurde bisher gespeichert
                    # aber nie gelesen - es wurde bei JEDEM Poll (20s) geschrieben.
                    sd_interval_s = config.load_readonly().get("room_sensor", {}).get("sd_log_interval_s", 60)
                    if sd_last_ms is None or time.ticks_diff(now_ms, sd_last_ms) >= sd_interval_s * 1000:
                        sd_logger.log(combined)
                        sd_last_ms = now_ms
        except Exception as e:
            print("Fehler in local_sensor_log_task() (Zyklus übersprungen):", e)
        # Ist der erste Messwert nach dem Boot noch nicht da (Sensor braucht einen Moment),
        # in den ersten Versuchen nach 3 s erneut lesen statt erst nach poll_interval_s (20 s).
        wait_s = poll_interval_s
        if startup_retries < 8 and (_latest_air_reading is None or not _latest_air_reading.ok):
            startup_retries += 1
            wait_s = 3
        await asyncio.sleep(wait_s)


_EXTERNAL_SOURCE_IDS = ["ext_climate_%d" % i for i in range(len(external_sources_cfg))]


def _fetch_external_climate(screen, parts):
    # screen wird hier nicht gebraucht (kein LVGL-Widget-Objekt dahinter) -
    # nur um dieselbe fetcher_fn(screen, parts)-Signatur wie
    # widget_catalog.py::_BACKGROUND_FETCHERS einzuhalten (siehe
    # fetch_worker.py::submit()-Docstring). "parts" ist hier direkt der
    # jeweilige external_sources-Config-Eintrag (hat schon "base_url").
    return widget_sources.fetch_climate_ext(parts)


async def external_sensor_log_task():
    """Fragt bis zu 2 externe Core2-MiniDash-BME688-Sensoren (Phase A,
    siehe HANDOFF.md) im Hintergrund-Thread ab (fetch_worker.py, wie die
    übrigen Netzwerk-Widgets - blockiert den Hauptthread NICHT) und füllt
    für jede konfigurierte+aktivierte Quelle ihren eigenen In-RAM-
    Ringpuffer (external_histories), unabhängig davon, welcher Screen
    gerade sichtbar ist - analog zu local_sensor_log_task() oben, nur
    eben nicht-blockierend, da hier (anders als beim lokalen I2C-Sensor)
    ein Netzwerk-Request dahinter steckt."""
    if not _EXTERNAL_SOURCE_IDS:
        return  # keine externen Quellen konfiguriert - Task beendet sich sofort
    poll_interval_s = room_sensor_cfg["poll_interval_s"]
    while True:
        try:
            # 1. Ergebnisse vom letzten Zyklus abholen und in den
            # jeweiligen Ringpuffer schreiben. Nur "ok"-Ergebnisse mit
            # echten Werten aufnehmen, sonst würde eine kurze Störung der
            # externen Quelle sofort eine Lücke/einen None-Ausreißer im
            # Verlauf hinterlassen statt einfach diesen einen Messpunkt
            # auszulassen (_bucketed_series() füllt Lücken ohnehin schon
            # sauber fort, siehe screens/sensor_history_screen.py).
            for widget_id, data in fetch_worker.collect_results(widget_ids=_EXTERNAL_SOURCE_IDS).items():
                idx = _EXTERNAL_SOURCE_IDS.index(widget_id)
                if data and data.get("ok"):
                    values = {k: data.get(k) for k in ("temp_c", "humidity", "pressure_hpa", "iaq_score")
                              if data.get(k) is not None}
                    if values:
                        external_histories[idx].add(values)

            # 2. Neue Abrufe anstoßen (nicht abwarten!) - nur für
            # Quellen, die sowohl konfiguriert (base_url gesetzt) als
            # auch aktiviert sind.
            for idx, src_cfg in enumerate(external_sources_cfg):
                if src_cfg.get("enabled") and src_cfg.get("base_url"):
                    fetch_worker.submit(_EXTERNAL_SOURCE_IDS[idx], _fetch_external_climate, None, src_cfg)
        except Exception as e:
            print("Fehler in external_sensor_log_task() (Zyklus übersprungen):", e)
        await asyncio.sleep(poll_interval_s)


async def config_save_debounce_task():
    """Schreibt eine per config.request_save() vorgemerkte Änderung
    spätestens config.DEBOUNCE_S Sekunden nach der letzten Änderung auf
    den Flash (Optimierungs-Backlog Punkt 3, siehe HANDOFF.md/
    config.py::flush_pending() für das Warum). 1s-Takt reicht locker (die
    eigentliche Wartezeit ist ohnehin config.DEBOUNCE_S=2s, ein 1s-Raster
    verzögert das Schreiben dadurch um höchstens ~1s zusätzlich)."""
    while True:
        try:
            config.flush_pending()
        except Exception as e:
            print("Fehler in config_save_debounce_task() (Zyklus übersprungen):", e)
        await asyncio.sleep(1)


EARLY_REFRESH_S = 4


async def dashboard_refresh_task():
    poll_interval_s = room_sensor_cfg["poll_interval_s"]
    _first_cycle_logged = False
    while True:
        if dashboard_screen is not None:
            try:
                dashboard_screen.refresh(accel_reading=_latest_accel_reading,
                                          air_reading=_latest_air_reading,
                                          mic_reading=_latest_mic_reading)
                if not _first_cycle_logged:
                    # Nur beim ALLERERSTEN Zyklus geloggt (siehe
                    # widget_catalog.py::refresh()-Docstring "Boot-Burst" -
                    # hier werden alle fälligen Netzwerk-Abrufe auf einmal
                    # ANGESTOSSEN, die Ergebnisse selbst kommen aber erst
                    # asynchron über die nächsten paar Zyklen zurück, siehe
                    # HANDOFF-Antwort zur Update-Zyklus-Frage) - für die
                    # Boot-Zeitmessung trotzdem der relevante Meilenstein:
                    # ab hier "arbeitet" das Dashboard sichtbar.
                    _boot_log("erster dashboard_refresh_task()-Zyklus fertig (Boot-Burst angestoßen)")
                    _first_cycle_logged = True
            except Exception as e:
                # KRITISCH: asyncio.create_task() ist "fire-and-forget" - eine
                # unbehandelte Exception hier würde diesen Task für IMMER
                # beenden (beobachtet: "Task exception wasn't retrieved"),
                # d.h. ab dann friert JEDE Anzeige auf dem Dashboard für den
                # Rest der Laufzeit ein, ohne dass irgendwas mehr passiert -
                # nur der nächste Neustart hilft. Ein einzelner fehlerhafter
                # Refresh-Zyklus soll das nicht für immer kaputt machen.
                print("Fehler in dashboard_refresh_task() (Zyklus übersprungen):", e)
        # In den ersten 90s nach dem Boot ALLE 4s statt alle 20s: Beim Start sind alle
        # Widgets auf einmal faellig, aber nur wenige Abrufe laufen gleichzeitig - mit
        # dem 20s-Takt fuellte sich das Dashboard ueber mehrere Minuten.
        uptime_s = time.ticks_diff(time.ticks_ms(), _boot_t0) / 1000
        await asyncio.sleep(EARLY_REFRESH_S if uptime_s < 90 else poll_interval_s)


# ---------------------------------------------------------------------------
# Task: Statusleiste (WLAN/Akku) - unabhängig vom Sensor-Takt aktuell halten
#
# TODO: echten Akkustand lesen, sobald bekannt ist, wie UIFlow2 den
# NP-F550-Wechselakku-Füllstand exponiert (vermutlich über eine PMIC-
# I2C-Registerabfrage im BSP-Paket) - aktuell nur ein Platzhalter.
# ---------------------------------------------------------------------------
async def status_bar_task(wifi_mode):
    while dashboard_screen is None:
        await asyncio.sleep(1)
    while True:
        # dashboard_screen.status_bar bei JEDEM Durchlauf frisch lesen,
        # NICHT einmalig vor der Schleife zwischenspeichern - nach einem
        # Live-Reload (siehe _do_reload()) ist "dashboard_screen" ein
        # KOMPLETT NEUES Objekt mit einer neuen StatusBar; eine einmalig
        # gespeicherte alte Referenz würde nach dem Neuaufbau auf ein
        # bereits gelöschtes LVGL-Objekt zeigen (LvReferenceError, genau
        # das ist beim ersten Test nach einem Reload so passiert).
        try:
            await _status_bar_update()
        except Exception as e:
            # Frueher OHNE try/except um den ganzen Block: z.B. ein
            # LvReferenceError waehrend eines Live-Reloads beendete den Task
            # dauerhaft - Statusleiste (WLAN/Akku) fror bis zum Neustart ein.
            print("Fehler in status_bar_task() (Zyklus übersprungen):", e)
        await asyncio.sleep(5)


async def _status_bar_update():
    if dashboard_screen is not None:
        status_bar = dashboard_screen.status_bar
        status_bar.set_wifi_connected(wifi_manager.sta_connected())
        # M5.Power.getBatteryLevel() - per diagnose_battery.py bestätigt
        # echte, sich leicht bewegende Werte (anders als der bekannte
        # M5.Imu/M5.Mic-"meldet Erfolg, liefert aber nichts"-Bug auf
        # dieser Firmware). Trotzdem defensiv mit Fallback, falls sich
        # das auf einem anderen Gerät/einer anderen Firmware anders
        # verhält.
        try:
            battery_level = M5.Power.getBatteryLevel()
        except Exception as e:
            print("M5.Power.getBatteryLevel() fehlgeschlagen:", e)
            battery_level = 100
        # getBatteryVoltage() (Millivolt) - separat per diagnose_
        # battery_voltage.py bestätigt: mehrfach hintereinander leicht
        # schwankende Werte (~8377-8378 bei 100%, passend zu einem
        # 2S-Akkupack - 2x ~4.2V Vollladeschluss), kein verdächtiger
        # Fixwert wie beim IMU/Mic-Bug. Trotzdem defensiv, falls sich
        # das auf einem anderen Gerät anders verhält.
        try:
            battery_voltage_mv = M5.Power.getBatteryVoltage()
        except Exception as e:
            print("M5.Power.getBatteryVoltage() fehlgeschlagen:", e)
            battery_voltage_mv = None
        # Ladesymbol: wurde bisher nie gesetzt (charging nie uebergeben). isCharging()
        # kann in M5Unified auch -1 ("unbekannt") liefern -> nur 1/True zaehlt.
        try:
            charging = M5.Power.isCharging() == 1
        except Exception:
            charging = False
        status_bar.set_battery(battery_level, charging=charging, voltage_mv=battery_voltage_mv)
        # Für die Web-UI (/system, siehe web_server.py::get_battery_info)
        # - global statt eines Rückgabewerts, da dieser Task in einer
        # eigenen Endlosschleife läuft und niemand auf sein Ergebnis wartet.
        global _last_battery_info
        _last_battery_info = {"percent": battery_level, "voltage_mv": battery_voltage_mv, "charging": charging}


# ---------------------------------------------------------------------------
# Web-UI wiring (siehe README.md, Abschnitt "Web-UI wiring")
# ---------------------------------------------------------------------------
def _cached_remote_air():
    """Remote-Sensor (HTTP zum Magic-Mirror-Server) NICHT blockierend: liefert den
    zuletzt im Hintergrund geholten Wert (siehe fetch_worker.submit_cached)."""
    reading, _seq = fetch_worker.submit_cached(
        "air_remote", air_sensor_manager.remote.read, room_sensor_cfg["poll_interval_s"])
    if reading is None:
        return SensorReading(source="remote", ok=False, msg="wird geladen...")
    return reading


air_sensor_manager.remote_read_fn = _cached_remote_air


def _get_full_state():
    """Zustand fuer die Web-UI (/api/status, alle 4-5s pro offener Seite).
    Liefert AUSSCHLIESSLICH die zuletzt von den Hintergrund-Tasks gemessenen
    Werte. Frueher wurde hier bei JEDEM Aufruf frisch gemessen: BME688
    (Messzyklus mit Heizung, verfaelschte zudem die IAQ-Baseline, die pro
    Aufruf statt pro Poll-Intervall zaehlt), Mikrofon (bis 0,5s Aufnahme) und
    - im Modus "both" - der Remote-Sensor per HTTP, alles im Hauptthread."""
    result = {"air": {}}
    primary = _latest_air_reading
    if air_sensor_manager.mode == "both":
        if primary is not None and primary.source == "local":
            local = primary
        else:
            local = SensorReading(source="local", ok=False, msg="lokaler Sensor nicht verfügbar")
        result["air"]["local"] = local.to_json()
        result["air"]["remote"] = _cached_remote_air().to_json()
    elif primary is not None:
        result["air"][primary.source] = primary.to_json()
    else:
        result["air"]["local"] = SensorReading(source="local", ok=False, msg="noch keine Messung").to_json()
    result["accel"] = (_latest_accel_reading or accel_source.read()).to_json()
    result["mic"] = (_latest_mic_reading or MicReading(ok=False, msg="noch keine Messung")).to_json()
    return result


web_server.get_state = _get_full_state
web_server.get_ha_states = ha.get_states
web_server.sd_reader = sd_reader
# Sicherer Default, bis _build_dashboard_screen() weiter unten das echte
# dashboard_screen.get_snapshot() verdrahtet (z.B. falls die erste
# Web-Anfrage zufällig zwischen Boot und erstem Dashboard-Aufbau kommt).
web_server.get_widget_snapshot = lambda: {}


def _get_external_sensors_state():
    """Für /api/external-sensors-status (Phase B, siehe web_server.py) -
    liefert den letzten Wert aus jedem externen In-RAM-Ringpuffer
    (external_histories, Phase A), OHNE selbst live nachzufragen - der
    Hintergrund-Task (external_sensor_log_task() oben) hält diese Werte
    schon aktuell."""
    out = []
    for src_cfg, hist in zip(external_sources_cfg, external_histories):
        if not src_cfg.get("base_url"):
            continue
        latest = hist.latest()
        name = src_cfg.get("name") or "Extern"
        if latest is None:
            out.append({"name": name, "ok": False, "msg": "Noch keine Daten"})
            continue
        values = latest[1]
        entry = {"name": name, "ok": True}
        entry["temp_c"] = values.get("temp_c")
        entry["humidity"] = values.get("humidity")
        entry["pressure_hpa"] = values.get("pressure_hpa")
        entry["iaq_score"] = values.get("iaq_score")
        out.append(entry)
    return out


web_server.get_external_sensors_state = _get_external_sensors_state
web_server.set_mode = air_sensor_manager.set_mode
def _iaq_reset():
    """Kalibrierung neu starten UND die gespeicherte Baseline verwerfen (sonst
    wuerde sie beim naechsten Neustart wieder geladen)."""
    local_air.iaq_tracker.reset()
    fresh = config.load()
    fresh.setdefault("iaq", {})["saved_baseline"] = 0
    fresh["iaq"]["saved_at"] = 0
    config.request_save(fresh)


web_server.iaq_reset = _iaq_reset


def _set_sensor_offsets(offsets):
    """Aktualisiert die Korrekturfaktoren SOFORT, ohne Neustart nötig -
    local_air.offsets ist ein mutables Dict (siehe sensors/air_quality.py::
    LocalBME688Source.__init__-Docstring), read() liest es bei jedem Aufruf
    frisch. Zusätzlich in config.json speichern, damit es einen Neustart
    übersteht."""
    local_air.offsets.update(offsets)
    fresh_cfg = config.load()
    fresh_cfg.setdefault("room_sensor", {}).setdefault("offsets", {}).update(offsets)
    config.save(fresh_cfg)


web_server.set_sensor_offsets = _set_sensor_offsets
web_server.wifi_mgr = wifi_manager
web_server.cfg_load = config.load
web_server.cfg_load_ro = config.load_readonly


def _ram_history_rows(hours):
    """Web-Grafiken ohne (funktionierende) SD-Karte: Verlauf aus dem RAM, im
    selben Format wie sd_reader.read_range() (Liste von Dicts, max. 300 Punkte)."""
    seconds = hours * 3600.0
    buf = history._buf
    fine_cover_s = (buf[-1][0] - buf[0][0]) if len(buf) > 1 else 0
    if history_long._buf and seconds > fine_cover_s:
        buf = history_long._buf
    cutoff = time.time() - seconds
    rows = [(ts, vals) for ts, vals in buf if ts >= cutoff]
    step = max(1, len(rows) // 300 + (1 if len(rows) % 300 else 0))
    out = []
    for ts, vals in rows[::step]:
        row = dict(vals)
        row["timestamp"] = ts
        out.append(row)
    return out


web_server.get_ram_history = _ram_history_rows
web_server.cfg_save = config.save
web_server.cfg_flush_pending = config.flush_pending
web_server.get_mem_free = gc.mem_free
web_server.get_battery_info = lambda: _last_battery_info
# Push-Updates vom gepairten Atom-Relais-Board (siehe atom_client.py-
# Docstring/web_server.py "/api/sync") - aktualisiert nur die Anzeige,
# ohne selbst wieder einen Toggle auszulösen. dashboard_screen ist None,
# wenn LVGL/m5ui nicht verfügbar ist (Desktop-Test) - dann einfach
# ignorieren statt AttributeError.
web_server.relay_set_from_partner = (
    dashboard_screen.set_relay_state if dashboard_screen is not None else None)


def _atom_get_status(board_index):
    """Für web_server.py::/api/atom-status/<board_index> (Phase C, siehe
    HANDOFF.md - zwei unabhängige Boards statt einem)."""
    if 0 <= board_index < len(atom_clients):
        return atom_clients[board_index].get_status()
    return {"ok": False, "msg": "Unbekanntes Atom-Board (Index %d)" % board_index}


def _atom_toggle(board_index, relay_id):
    """Für web_server.py::/api/atom-toggle/<board_index>/<relay_id>."""
    if 0 <= board_index < len(atom_clients):
        return atom_clients[board_index].toggle(relay_id)
    return {"ok": False, "msg": "Unbekanntes Atom-Board (Index %d)" % board_index}


web_server.atom_get_status = _atom_get_status
web_server.atom_toggle = _atom_toggle
# Für web_server.py::_match_atom_board_by_peer() (Zuordnung eines
# eingehenden /api/sync-Aufrufs zu einem Board anhand der Quell-IP, siehe
# dortigen Docstring) - eine frische Kopie der Board-Konfiguration, kein
# Zugriff auf main.py-interne Variablen von außen nötig.
web_server.get_atom_boards_cfg = lambda: config.load().get("atom_boards", [])


# ---------------------------------------------------------------------------
# Supervisor: startet einen abgestuerzten Hintergrund-Task nach kurzer Pause
# neu, statt dass er still fuer immer beendet ist (unbehandelte Exception in
# einem create_task()-Task -> Task weg, Anzeige/Sensor/Alarm bleibt bis zum
# Neustart tot). Die meisten Tasks fangen Fehler zusaetzlich selbst pro
# Zyklus ab - das hier ist das Netz darunter.
# ---------------------------------------------------------------------------
async def _supervised(name, coro_fn, *args):
    while True:
        try:
            await coro_fn(*args)
            return  # Task ist regulaer beendet
        except Exception as e:
            print("TASK-ABSTURZ in %s - Neustart in 5s:" % name)
            try:
                sys.print_exception(e)
            except Exception:
                print(repr(e))
            await asyncio.sleep(5)


# ---------------------------------------------------------------------------
# Task: WLAN-Watchdog. WLAN und NTP wurden bisher NUR beim Boot versucht:
# Nach einem Stromausfall ist der Tab5 oft schneller als der Router, findet
# in den 15s kein Netz und blieb DAUERHAFT im Access-Point-Modus (ohne
# Internet, ohne NTP, ohne Magic-Mirror-/HA-/Atom-Zugriff) bis zum
# manuellen Neustart. Jetzt:
#   - AP-Modus + gespeicherte SSID: alle WIFI_AP_RETRY_INTERVAL_S erneut
#     versuchen (aber NICHT, solange jemand am AP haengt bzw. kuerzlich das
#     Web-UI benutzt wurde - sonst wuerde man einen Anwender mitten in der
#     Einrichtung rauswerfen). Klappt es nicht, wird der AP wiederhergestellt.
#   - STA-Modus, aber Verbindung weg: nach einem Durchlauf Zeit zur
#     Selbstheilung neu verbinden (ohne AP).
#   - network_available der Clients folgt dem ECHTEN Verbindungsstatus.
# ---------------------------------------------------------------------------
WIFI_WATCHDOG_INTERVAL_S = 30
WIFI_AP_RETRY_INTERVAL_S = 60
WIFI_WEB_IDLE_S = 180


def _apply_network_available(available):
    fetch_worker.set_network_ready(available)
    api.network_available = available
    ha.network_available = available
    for _client in atom_clients:
        _client.network_available = available


_ntp_last_try_ms = None
_ntp_last_ok_ms = None
NTP_RESYNC_INTERVAL_S = 24 * 3600   # die interne RTC driftet - einmal taeglich neu stellen


def _sync_time_if_needed(force=False, retries=1):
    """Zeit per NTP stellen: nachholen, falls sie beim Boot nicht geklappt hat, UND
    einmal taeglich auffrischen (bisher nur EINMAL beim Boot - ueber Wochen driftet
    die Uhr). ntp_clock.sync() blockiert bei einem Fehlschlag ~2s - deshalb
    hoechstens alle 10 Minuten versuchen. Gibt True zurueck bei Erfolg."""
    global _ntp_last_try_ms, _ntp_last_ok_ms
    now = time.ticks_ms()
    if not force:
        resync_due = (_ntp_last_ok_ms is not None
                      and time.ticks_diff(now, _ntp_last_ok_ms) >= NTP_RESYNC_INTERVAL_S * 1000)
        if ntp_clock.is_synced() and not resync_due:
            return False
        if _ntp_last_try_ms is not None and time.ticks_diff(now, _ntp_last_try_ms) < 600000:
            return False
    _ntp_last_try_ms = now
    if ntp_clock.sync(retries=retries):
        _ntp_last_ok_ms = time.ticks_ms()
        print("NTP-Zeitsync erfolgreich:", ntp_clock.now_local())
        return True
    return False


async def wifi_watchdog_task():
    ap_next_try = 0
    down_since = None
    while True:
        await asyncio.sleep(WIFI_WATCHDOG_INTERVAL_S)
        try:
            if web_server.wifi_change_in_progress:
                continue  # Nutzer wechselt gerade das Netz (siehe web_server.py)
            mode = wifi_manager.mode()
            wifi_cfg = config.load_readonly().get("wifi", {})
            ssid = wifi_cfg.get("ssid", "")
            password = wifi_cfg.get("password", "")

            if mode == "sta":
                connected = wifi_manager.sta_connected()
                _apply_network_available(connected)
                if connected:
                    down_since = None
                    _sync_time_if_needed()
                    continue
                if down_since is None:
                    down_since = time.time()  # erst beim naechsten Durchlauf eingreifen
                    continue
                if ssid:
                    print("WLAN-Watchdog: Verbindung weg - verbinde neu mit", ssid)
                    if await wifi_manager.reconnect_sta_async(ssid, password):
                        print("WLAN-Watchdog: wieder verbunden")
                        _apply_network_available(True)
                        down_since = None
                        _sync_time_if_needed()
            elif mode == "ap":
                if not ssid or time.time() < ap_next_try:
                    continue
                ap_next_try = time.time() + WIFI_AP_RETRY_INTERVAL_S
                if wifi_manager.ap_has_clients():
                    continue
                if time.time() - web_server.last_request_time < WIFI_WEB_IDLE_S:
                    continue
                print("WLAN-Watchdog: Access-Point-Modus - versuche Heimnetz", ssid)
                if await wifi_manager.try_sta_async(ssid, password):
                    print("WLAN-Watchdog: Heimnetz wieder erreichbar")
                    _apply_network_available(True)
                    _sync_time_if_needed(force=True)
        except Exception as e:
            print("Fehler in wifi_watchdog_task() (Zyklus übersprungen):", e)


async def boot_network_task():
    """WLAN aufbauen (nicht blockierend), danach NTP, Web-UI und Freigabe der
    Netzwerk-Abrufe. Laeuft einmal beim Boot; spaetere Ausfaelle behandelt
    wifi_watchdog_task()."""
    print("Verbinde WLAN...")
    _boot_log("vor WLAN-Verbindung (asynchron)")
    mode, ip = await wifi_manager.connect_or_ap_async(cfg)
    _boot_log("WLAN fertig (%s, %s)" % (mode, ip))
    print("WLAN:", mode, ip)
    available = (mode == "sta")
    _apply_network_available(available)
    if available:
        _boot_log("vor ntp_clock.sync()")
        if not _sync_time_if_needed(force=True, retries=3):
            print("NTP-Zeitsync fehlgeschlagen - Uhr zeigt evtl. falsche Zeit.")
        _boot_log("ntp_clock.sync() fertig")
    else:
        print("Kein Heimnetz (AP-/Setup-Modus) - Magic-Mirror-Server- und "
              "Home-Assistant-Aufrufe sind deaktiviert, bis eine echte WLAN-Verbindung steht.")
    if cfg["web_ui"]["enabled"]:
        asyncio.create_task(_supervised("web_server", web_server.run, cfg["web_ui"]["port"]))
    fetch_worker.set_network_ready(available)  # im AP-Modus bleibt es gesperrt (bis der Watchdog das Heimnetz findet)


async def main():
    # DEBUG_DISABLE_WIFI: zweiter Isolations-Test, nachdem I2C als Ursache
    # für "Display geht aus" ausgeschlossen wurde (Display blieb auch ohne
    # jede I2C-Aktivität aus). Testete, ob stattdessen der WLAN/Access-Point-
    # Start beteiligt ist (z.B. gemeinsame Stromversorgung/Antennenschaltung
    # mit dem Display). Jetzt auf False: WLAN-Verbindung wird beim Boot
    # versucht; findet wifi_manager.connect_or_ap() kein bekanntes Netz
    # bzw. schlägt die Verbindung innerhalb von STA_TIMEOUT_S fehl, wechselt
    # es automatisch in den Access-Point-Modus (SSID/Passwort siehe
    # wifi_manager.AP_SSID/AP_PASSWORD), damit WLAN-Zugangsdaten und Web-UI
    # trotzdem erreichbar bleiben.
    DEBUG_DISABLE_WIFI = False
    # WLAN + NTP laufen jetzt ASYNCHRON im Hintergrund-Task boot_network_task():
    # Frueher blockierte connect_or_ap() hier 5-10s - das Dashboard war schon
    # sichtbar, aber Touch und Uhr eingefroren, und KEIN Task lief. Jetzt starten die
    # Sensor-/Anzeige-Tasks sofort; Netzwerk-Abrufe (Widgets, HA, Atom) warten, bis
    # das WLAN steht (fetch_worker.set_network_ready), das Web-UI startet danach.
    fetch_worker.set_network_ready(False)
    _apply_network_available(False)
    if DEBUG_DISABLE_WIFI:
        print("DEBUG_DISABLE_WIFI=True - WLAN/AP-Start übersprungen, siehe main.py")
        fetch_worker.set_network_ready(True)
    else:
        asyncio.create_task(_supervised("boot_network_task", boot_network_task))

    if DEBUG_DISABLE_WIFI and cfg["web_ui"]["enabled"]:
        asyncio.create_task(_supervised("web_server", web_server.run, cfg["web_ui"]["port"]))
    asyncio.create_task(_supervised("accel_task", accel_task))
    asyncio.create_task(_supervised("accel_display_task", accel_display_task))
    asyncio.create_task(_supervised("pc_status_task", pc_status_task))
    asyncio.create_task(_supervised("network_state_task", network_state_task))
    asyncio.create_task(_supervised("local_sensor_log_task", local_sensor_log_task))
    asyncio.create_task(_supervised("external_sensor_log_task", external_sensor_log_task))
    asyncio.create_task(_supervised("config_save_debounce_task", config_save_debounce_task))
    asyncio.create_task(_supervised("dashboard_refresh_task", dashboard_refresh_task))
    asyncio.create_task(_supervised("reload_watcher_task", reload_watcher_task))
    asyncio.create_task(_supervised("status_bar_task", status_bar_task, "unknown"))
    if not DEBUG_DISABLE_WIFI:
        asyncio.create_task(_supervised("wifi_watchdog_task", wifi_watchdog_task))
    print("Alle Tasks eingeplant (create_task) - main() geht jetzt in die Keep-Alive-Schleife")
    _boot_log("alle Tasks eingeplant - main() startet Keep-Alive-Schleife")

    # main() selbst muss am Leben bleiben, sonst würde asyncio.run(main())
    # zurückkehren und der Event-Loop beendet - die oben erzeugten Tasks
    # liefen sonst nicht weiter.
    while True:
        await asyncio.sleep(3600)


def _fatal_restart():
    """Letzte Rettung: wenn die Hauptschleife unerwartet abstuerzt (z.B. OSError EIO aus
    dem asyncio-Poller nach Ressourcenerschoepfung - Log ueber Nacht), laeuft sonst NICHTS
    mehr (Web-UI, Sensoren, Widgets tot; nur LVGL-Timer zeigen weiter alte Werte). Ein
    unbeaufsichtigtes Geraet startet dann sauber neu."""
    try:
        config.flush_pending(force=True)   # vorgemerkte Config-Aenderungen nicht verlieren
    except Exception:
        pass
    try:
        wifi_manager.disconnect_cleanly()  # Router meldet die Sitzung ab (schnellere Neuanmeldung)
    except Exception:
        pass
    time.sleep(2)
    import machine
    machine.reset()


try:
    asyncio.run(main())
except KeyboardInterrupt:
    raise                       # Entwicklung: Strg-C soll wie gewohnt zur REPL fuehren
except BaseException as _e:
    print("FATAL: Hauptschleife abgestürzt - starte neu:")
    try:
        sys.print_exception(_e)
    except Exception:
        print(repr(_e))
    _fatal_restart()
