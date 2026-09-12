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
import uasyncio as asyncio

import config
import wifi_manager
import web_server
import api_client
import ha_client
import atom_client
import theme
import i18n
import ntp_clock

from sensors.air_quality import SensorManager, LocalBME688Source, RemoteSensorSource
from sensors.accelerometer import LocalAccelSource
from sensors.microphone import LocalMicSource
from sensors.history import SensorHistory
from sensors.sd_logger import SDLogger
from sensors.sd_reader import SDReader

from screens.dashboard import DashboardScreen
from screens.sensor_history_screen import SensorHistoryScreen
from burger_menu import BurgerMenu

# ---------------------------------------------------------------------------
# Konfiguration laden
# ---------------------------------------------------------------------------
cfg = config.load()

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

def _find_widget_cfg(widget_id, default):
    """Sucht ein Widget anhand seiner id in der Dashboard-Konfiguration -
    z.B. für Sensor-Parameter, die zwar technisch beim Sensor selbst
    ansetzen (nicht bei der Anzeige), aber trotzdem im /dashboard-Editor
    bearbeitbar sein sollen wie jede andere Widget-Einstellung auch
    (siehe config.py "acceleration"-Widget: sta_tau_s/lta_tau_s/trigger_ratio)."""
    for w in cfg["screens"]["dashboard"]["widgets"]:
        if w.get("id") == widget_id:
            return w
    return default


accel_widget_cfg = _find_widget_cfg("acceleration", {})
accel_source = LocalAccelSource(
    i2c=_sensor_i2c,
    sta_tau_s=accel_widget_cfg.get("sta_tau_s", 0.5),
    lta_tau_s=accel_widget_cfg.get("lta_tau_s", 30.0),
    trigger_ratio=accel_widget_cfg.get("trigger_ratio", 3.0),
)

mic_cfg = cfg["microphone"]
# "i2s=None" ist jetzt korrekt so (kein TODO mehr) - sensors/microphone.py
# nutzt M5.Mic.begin()/record()/end() (offizielle UIFlow2-API, siehe
# m5-docs "Tab5 Mic"), kein manuelles I2S-Setup nötig.
mic_source = LocalMicSource(i2s=None, sample_window=mic_cfg["sample_window"])

history = SensorHistory(max_len=room_sensor_cfg["history_max_points"])

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
# Gepairtes M5Stack-Atom-2-Relais-Board (Licht/Steckdose) - siehe
# atom_client.py. Das ist aktuell das tatsächlich genutzte Backend für die
# Licht-/Steckdosen-Schalter (config.py "screens.dashboard.widgets[].backend"
# = "atom"), NICHT Home Assistant.
# ---------------------------------------------------------------------------
atom_cfg = cfg["atom"]
atom = atom_client.AtomClient(atom_cfg["base_url"])
atom.enabled = atom_cfg.get("enabled", False)

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


try:
    import M5
    from M5 import *
    import m5ui
    import lvgl as lv

    M5.begin()
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
        cfg_save=config.save,
        on_brightness_changed=_on_brightness_changed,
        switch_to_dashboard=lambda: _switch_to_dashboard(),
        switch_to_sensor_screen=lambda: _switch_to_sensor_screen(),
    )

    def _build_dashboard_screen():
        global dashboard_screen
        dashboard_screen = DashboardScreen(
            screen_config=config.load()["screens"]["dashboard"],
            api_client=api,
            air_sensor_manager=air_sensor_manager,
            accel_source=accel_source,
            mic_source=mic_source,
            history=history,
            ha_client=ha,
            atom_client=atom,
            sd_logger=sd_logger,
            parent=page,
            on_menu_pressed=burger_menu.open,
        )
        web_server.relay_set_from_partner = dashboard_screen.set_relay_state

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
        sensor_history_screen = SensorHistoryScreen(
            page,
            history=history,
            sd_reader=sd_reader,
            on_menu_pressed=burger_menu.open,
        )

    sensor_history_screen = None
    _build_dashboard_screen()

    page.screen_load()
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
    atom.enabled = cfg["atom"].get("enabled", False)

    try:
        dashboard_screen.destroy()
        page.clean()
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
    if not widget_cfg.get("alarm_beep", True):
        return
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


async def accel_task():
    global _latest_accel_reading, _accel_was_triggered
    period_ms = int(1000 / ACCEL_POLL_HZ)
    while True:
        _latest_accel_reading = accel_source.read()
        if _latest_accel_reading.ok:
            is_triggered = _latest_accel_reading.quake.get("triggered", False)
            if is_triggered and not _accel_was_triggered:
                # Steigende Flanke - Alarm als eigener Task, damit ein
                # langsamer Ton diesen 20Hz-Loop nicht blockiert.
                asyncio.create_task(_play_alarm(accel_widget_cfg))
            _accel_was_triggered = is_triggered
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
                if sd_logger is not None:
                    sd_logger.log(combined)
        except Exception as e:
            print("Fehler in local_sensor_log_task() (Zyklus übersprungen):", e)
        await asyncio.sleep(poll_interval_s)


async def dashboard_refresh_task():
    poll_interval_s = room_sensor_cfg["poll_interval_s"]
    while True:
        if dashboard_screen is not None:
            try:
                dashboard_screen.refresh(accel_reading=_latest_accel_reading,
                                          air_reading=_latest_air_reading,
                                          mic_reading=_latest_mic_reading)
            except Exception as e:
                # KRITISCH: asyncio.create_task() ist "fire-and-forget" - eine
                # unbehandelte Exception hier würde diesen Task für IMMER
                # beenden (beobachtet: "Task exception wasn't retrieved"),
                # d.h. ab dann friert JEDE Anzeige auf dem Dashboard für den
                # Rest der Laufzeit ein, ohne dass irgendwas mehr passiert -
                # nur der nächste Neustart hilft. Ein einzelner fehlerhafter
                # Refresh-Zyklus soll das nicht für immer kaputt machen.
                print("Fehler in dashboard_refresh_task() (Zyklus übersprungen):", e)
        await asyncio.sleep(poll_interval_s)


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
        if dashboard_screen is not None:
            status_bar = dashboard_screen.status_bar
            status_bar.set_wifi_connected(wifi_mode == "sta")
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
            status_bar.set_battery(battery_level)
        await asyncio.sleep(5)


# ---------------------------------------------------------------------------
# Web-UI wiring (siehe README.md, Abschnitt "Web-UI wiring")
# ---------------------------------------------------------------------------
def _get_full_state():
    result = {"air": {}}
    if air_sensor_manager.mode == "both":
        both = air_sensor_manager.read_all()
        result["air"]["local"] = both["local"].to_json()
        result["air"]["remote"] = both["remote"].to_json()
    else:
        reading = air_sensor_manager.read()
        result["air"][reading.source] = reading.to_json()
    result["accel"] = (_latest_accel_reading or accel_source.read()).to_json()
    result["mic"] = mic_source.read().to_json()
    return result


web_server.get_state = _get_full_state
web_server.get_ha_states = ha.get_states
web_server.sd_reader = sd_reader
web_server.set_mode = air_sensor_manager.set_mode
web_server.iaq_reset = local_air.iaq_tracker.reset


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
web_server.cfg_save = config.save
# Push-Updates vom gepairten Atom-Relais-Board (siehe atom_client.py-
# Docstring/web_server.py "/api/sync") - aktualisiert nur die Anzeige,
# ohne selbst wieder einen Toggle auszulösen. dashboard_screen ist None,
# wenn LVGL/m5ui nicht verfügbar ist (Desktop-Test) - dann einfach
# ignorieren statt AttributeError.
web_server.relay_set_from_partner = (
    dashboard_screen.set_relay_state if dashboard_screen is not None else None)
web_server.atom_get_status = atom.get_status
web_server.atom_toggle = atom.toggle


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
    if DEBUG_DISABLE_WIFI:
        print("DEBUG_DISABLE_WIFI=True - WLAN/AP-Start übersprungen, siehe main.py")
        wifi_mode, wifi_ip = "unknown", None
    else:
        print("Verbinde WLAN...")
        wifi_mode, wifi_ip = wifi_manager.connect_or_ap(cfg)
        print("WLAN:", wifi_mode, wifi_ip)

    # Im Access-Point-/Setup-Modus (kein Heimnetz gefunden) gibt es
    # keinerlei Pfad zum Magic-Mirror-Server oder zu Home Assistant -
    # jeder Versuch würde nur den (bis zu 5s langen) Sicherheits-Timeout
    # abwarten und dabei die komplette UI einfrieren lassen (siehe
    # api_client.py/ha_client.py). Deshalb hier explizit abschalten, damit
    # alle betroffenen Aufrufe sofort statt erst nach Timeout fehlschlagen.
    network_available = (wifi_mode == "sta")
    api.network_available = network_available
    ha.network_available = network_available
    atom.network_available = network_available
    if not network_available:
        print("Kein Heimnetz (AP-/Setup-Modus) - Magic-Mirror-Server- und "
              "Home-Assistant-Aufrufe sind deaktiviert, bis eine echte WLAN-Verbindung steht.")

    # NTP-Zeitsync - nur sinnvoll mit echtem Internetzugang (STA-Modus).
    # Ohne das läuft die interne RTC seit dem letzten Boot einfach weiter
    # (bzw. steht auf ihrem Hardware-Default) - die Uhr hätte sonst nie
    # die richtige Zeit gezeigt. Kurzer, einmaliger blockierender Aufruf
    # (bis zu ~3s bei mehreren Versuchen) - passiert hier noch vor dem
    # Start des asyncio-Loops, stört also nichts.
    if network_available:
        if ntp_clock.sync():
            print("NTP-Zeitsync erfolgreich:", ntp_clock.now_local())
        else:
            print("NTP-Zeitsync fehlgeschlagen - Uhr zeigt evtl. falsche Zeit.")

    # WICHTIG: Bewusst NICHT mehr asyncio.gather(*tasks) - auf diesem
    # UIFlow2-uasyncio-Binding hat sich gezeigt, dass gather() die
    # Coroutinen NICHT zuverlässig nebenläufig ausführt: Der allererste
    # Eintrag (accel_task(), eine "while True"-Endlosschleife) blockierte
    # den kompletten Ablauf für immer, noch bevor web_server.run() jemals
    # drankam - kein Fehler, einfach ein stiller Deadlock (erkennbar daran,
    # dass "Tab5 Web-UI läuft auf Port X" NIE ausgegeben wurde, selbst nach
    # langem Warten). Stattdessen jede Coroutine explizit über
    # asyncio.create_task() einplanen - das ist das robuste Standard-Muster
    # für uasyncio und unabhängig von gather()-Eigenheiten dieses Bindings.
    # Web-UI bewusst ZUERST starten, damit es so früh wie möglich läuft,
    # selbst falls eine der Sensor-/Screen-Refresh-Aufgaben später doch
    # mal (kurz) blockiert.
    if cfg["web_ui"]["enabled"]:
        asyncio.create_task(web_server.run(port=cfg["web_ui"]["port"]))
    asyncio.create_task(accel_task())
    asyncio.create_task(accel_display_task())
    asyncio.create_task(pc_status_task())
    asyncio.create_task(local_sensor_log_task())
    asyncio.create_task(dashboard_refresh_task())
    asyncio.create_task(reload_watcher_task())
    asyncio.create_task(status_bar_task(wifi_mode))
    print("Alle Tasks eingeplant (create_task) - main() geht jetzt in die Keep-Alive-Schleife")

    # main() selbst muss am Leben bleiben, sonst würde asyncio.run(main())
    # zurückkehren und der Event-Loop beendet - die oben erzeugten Tasks
    # liefen sonst nicht weiter.
    while True:
        await asyncio.sleep(3600)


asyncio.run(main())
