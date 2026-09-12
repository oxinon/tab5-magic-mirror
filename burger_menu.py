"""
Burger-Menü - seit dem Umbau auf ein konsolidiertes Haupt-Dashboard
(siehe screens/dashboard.py) im Kern ein Schnellzugriffs-Overlay für die
zwei Dinge, die man oft SOFORT auf dem Tab5-Display braucht, ohne erst
das Web-UI zu öffnen:

  1. WLAN-Status: Modus/Netzwerk/IP/Signal. Im Access-Point-Modus (kein
     bekanntes Heimnetz gefunden, siehe wifi_manager.connect_or_ap())
     zusätzlich das AP-Passwort - genau dann steht man ja typischerweise
     direkt vor dem Gerät und muss sich selbst erst mit "Tab5-Setup"
     verbinden, um die WLAN-Zugangsdaten übers Web-UI (/system)
     einzurichten. Alles Weitere an WLAN-Konfiguration passiert bewusst
     NICHT mehr hier (siehe screens/settings.py-Historie/HANDOFF.md),
     sondern ausschließlich im Web-UI.
  2. Helligkeit: Slider, identisch zum früheren Settings-Screen.

Zusätzlich jetzt auch ein einfacher SCREEN-WECHSLER zwischen genau zwei
m5ui.M5Page-Bildschirmen (Haupt-Dashboard und das Sensor-Dashboard für
den internen BME688, siehe screens/sensor_history_screen.py) - bewusst
nicht wieder die volle Vier-Screens-Architektur von früher, nur dieser
eine Zusatz-Screen. Das Menü merkt sich, auf welcher Page es gerade
geöffnet wurde ("_active_page"), damit sein eigenes Overlay auf dem
jeweils AKTUELL SICHTBAREN Screen erscheint, egal von welcher Seite aus
man es öffnet.

Live-Aktualisierung des WLAN-Status über einen LVGL-Timer, solange das
Menü offen ist (Timer wird beim Schließen sauber gelöscht).
"""

try:
    import lvgl as lv
    _HAS_LVGL = True
except ImportError:
    _HAS_LVGL = False

from theme import COLORS
from i18n import STRINGS
from widgets import lv_const
import wifi_manager
import sys


def _hex(color_hex):
    return lv.color_hex(int(color_hex.lstrip("#"), 16))


class BurgerMenu:
    """
    page: die (einzige) m5ui.M5Page, über der das Overlay erscheint - wird
    auch für den Bildschirmwechsel wiederverwendet (siehe
    switch_to_dashboard/switch_to_sensor_screen unten), NICHT durch eine
    zweite Page ersetzt (zwei komplette 1280x720-Bildschirme gleichzeitig
    im Speicher haben beim ersten Test zu einem weißen, eingefrorenen
    Display geführt - vermutlich Speichererschöpfung).
    switch_to_dashboard / switch_to_sensor_screen: Callback()-Funktionen
    aus main.py, die den aktuellen Screen sauber abbauen (destroy()) und
    den jeweils anderen auf DERSELBEN Page neu aufbauen - genau das
    Prinzip, das der Live-Reload (siehe main.py::_do_reload()) schon
    nutzt. None -> kein "SENSOR-DASHBOARD"-Button.
    wifi_mgr: wifi_manager.WifiManager-Instanz (dasselbe Singleton, das
    main.py beim Boot für connect_or_ap() genutzt hat) oder None -> fällt
    auf das Modul-Singleton wifi_manager zurück.
    cfg / cfg_save: config.py-Funktionen bzw. das geladene Config-Dict,
    fürs Speichern der Helligkeit.
    on_brightness_changed: optionaler Callback(percent) für die echte
    Backlight-PWM (siehe main.py - TODO, noch ein reiner Print-Stub).
    """

    def __init__(self, page, wifi_mgr=None, cfg=None, cfg_save=None,
                 on_brightness_changed=None, switch_to_dashboard=None,
                 switch_to_sensor_screen=None):
        if not _HAS_LVGL:
            raise RuntimeError("BurgerMenu benötigt lvgl/m5ui (nur auf der Tab5 verfügbar)")
        self.page = page
        self.wifi_mgr = wifi_mgr or wifi_manager
        self.cfg = cfg or {}
        self.cfg_save = cfg_save
        self.on_brightness_changed = on_brightness_changed
        self.switch_to_dashboard = switch_to_dashboard
        self.switch_to_sensor_screen = switch_to_sensor_screen
        self._overlay = None
        self._status_timer = None

    def open(self):
        if self._overlay is not None:
            return  # schon offen - zweites Antippen ignorieren

        self._overlay = lv.obj(self.page)
        self._overlay.set_size(1280, 720)
        self._overlay.set_pos(0, 0)
        self._overlay.set_style_bg_color(lv.color_hex(0x000000), 0)
        self._overlay.set_style_bg_opa(180, 0)  # halbtransparent abdunkeln
        self._overlay.set_style_border_width(0, 0)
        self._overlay.set_style_pad_all(0, 0)
        self._overlay.add_flag(lv.obj.FLAG.CLICKABLE)  # Klicks aufs Overlay selbst schlucken

        panel = lv.obj(self._overlay)
        panel.set_size(460, 560)
        panel.set_pos(20, 60)
        panel.set_style_bg_color(_hex(COLORS["bg"]), 0)
        panel.set_style_border_width(1, 0)
        panel.set_style_border_color(_hex(COLORS["accent"]), 0)
        panel.set_style_pad_all(24, 0)
        panel.remove_flag(lv.obj.FLAG.SCROLLABLE)
        panel.set_flex_flow(lv.FLEX_FLOW.COLUMN)
        panel.set_style_pad_row(14, 0)

        self._build_wifi_section(panel)
        self._build_brightness_section(panel)
        self._build_screen_switch_section(panel)

        close_btn = lv.button(panel)
        close_btn.set_size(360, 56)
        close_btn.set_style_bg_color(_hex(COLORS["fg_faint"]), 0)
        close_label = lv.label(close_btn)
        close_label.set_text(STRINGS.get("menu.close", "Schließen"))
        close_label.set_style_text_font(lv.font_montserrat_24, 0)
        close_label.center()
        close_btn.add_event_cb(lambda e: self.close(), lv.EVENT.CLICKED, None)

    # ------------------------------------------------------------------
    # WLAN-Status (+ AP-Passwort im AP-Modus)
    # ------------------------------------------------------------------
    def _build_wifi_section(self, panel):
        title = lv.label(panel)
        title.set_text(STRINGS["settings.wifi"])
        title.set_style_text_font(lv.font_montserrat_24, 0)
        title.set_style_text_color(_hex(COLORS["accent"]), 0)
        title.set_style_text_letter_space(2, 0)

        self._wifi_mode_label = lv.label(panel)
        self._wifi_mode_label.set_style_text_font(lv.font_montserrat_24, 0)
        self._wifi_mode_label.set_style_text_color(_hex(COLORS["fg"]), 0)

        self._wifi_ssid_label = lv.label(panel)
        self._wifi_ssid_label.set_style_text_font(lv.font_montserrat_24, 0)
        self._wifi_ssid_label.set_style_text_color(_hex(COLORS["fg_dim"]), 0)

        self._wifi_ip_label = lv.label(panel)
        self._wifi_ip_label.set_style_text_font(lv.font_montserrat_24, 0)
        self._wifi_ip_label.set_style_text_color(_hex(COLORS["fg_dim"]), 0)

        self._wifi_signal_label = lv.label(panel)
        self._wifi_signal_label.set_style_text_font(lv.font_montserrat_24, 0)
        self._wifi_signal_label.set_style_text_color(_hex(COLORS["fg_faint"]), 0)

        # Nur im AP-Modus sichtbar/gefüllt (siehe _refresh_wifi_status) -
        # genau dann braucht man das Passwort, um sich selbst erst mit
        # "Tab5-Setup" zu verbinden.
        self._wifi_password_label = lv.label(panel)
        self._wifi_password_label.set_style_text_font(lv.font_montserrat_24, 0)
        self._wifi_password_label.set_style_text_color(_hex(COLORS["amber"]), 0)

        self._refresh_wifi_status()
        self._status_timer = lv.timer_create(lambda t: self._refresh_wifi_status(), 3000, None)

    def _refresh_wifi_status(self):
        status = self.wifi_mgr.status()
        mode = status.get("mode")
        mode_text = {
            "sta": STRINGS["wifi.mode_sta"],
            "ap": STRINGS["wifi.mode_ap"],
        }.get(mode, STRINGS["wifi.mode_unknown"])
        self._wifi_mode_label.set_text("%s: %s" % (STRINGS["wifi.mode"], mode_text))
        self._wifi_ssid_label.set_text("%s: %s" % (STRINGS["wifi.ssid"], status.get("ssid") or "--"))
        self._wifi_ip_label.set_text("%s: %s" % (STRINGS["wifi.ip"], status.get("ip") or "--"))
        rssi = status.get("rssi")
        self._wifi_signal_label.set_text(
            "%s: %d dBm" % (STRINGS["wifi.signal"], rssi) if rssi is not None else "")

        if mode == "ap":
            self._wifi_password_label.set_text(
                "%s: %s" % (STRINGS["wifi.ap_password"], wifi_manager.AP_PASSWORD))
        else:
            self._wifi_password_label.set_text("")

    # ------------------------------------------------------------------
    # Helligkeit
    # ------------------------------------------------------------------
    def _build_brightness_section(self, panel):
        title = lv.label(panel)
        title.set_text(STRINGS["settings.brightness"])
        title.set_style_text_font(lv.font_montserrat_24, 0)
        title.set_style_text_color(_hex(COLORS["accent"]), 0)
        title.set_style_text_letter_space(2, 0)

        row = lv.obj(panel)
        row.set_size(400, 60)
        row.set_style_bg_opa(0, 0)
        row.set_style_border_width(0, 0)
        row.set_style_pad_all(0, 0)

        self._brightness_slider = lv.slider(row)
        self._brightness_slider.set_pos(0, 16)
        self._brightness_slider.set_size(280, 20)
        self._brightness_slider.set_range(10, 100)
        self._brightness_slider.set_value(self.cfg.get("brightness", 80), lv_const.ANIM_OFF)
        self._brightness_slider.add_event_cb(self._on_brightness_changed, lv.EVENT.VALUE_CHANGED, None)

        self._brightness_value_label = lv.label(row)
        self._brightness_value_label.set_pos(300, 14)
        self._brightness_value_label.set_style_text_font(lv.font_montserrat_24, 0)
        self._brightness_value_label.set_style_text_color(_hex(COLORS["fg"]), 0)
        self._brightness_value_label.set_text("%d%%" % self.cfg.get("brightness", 80))

    def _build_screen_switch_section(self, panel):
        title = lv.label(panel)
        title.set_text("BILDSCHIRM")
        title.set_style_text_font(lv.font_montserrat_24, 0)
        title.set_style_text_color(_hex(COLORS["accent"]), 0)
        title.set_style_text_letter_space(2, 0)

        dashboard_btn = lv.button(panel)
        dashboard_btn.set_size(360, 56)
        dashboard_btn.set_style_bg_color(_hex(COLORS["accent"]), 0)
        dashboard_label = lv.label(dashboard_btn)
        dashboard_label.set_text("DASHBOARD")
        dashboard_label.set_style_text_font(lv.font_montserrat_24, 0)
        dashboard_label.center()
        dashboard_btn.add_event_cb(lambda e: self._switch(self.switch_to_dashboard), lv.EVENT.CLICKED, None)

        if self.switch_to_sensor_screen is not None:
            sensor_btn = lv.button(panel)
            sensor_btn.set_size(360, 56)
            sensor_btn.set_style_bg_color(_hex(COLORS["accent"]), 0)
            sensor_label = lv.label(sensor_btn)
            sensor_label.set_text("SENSOR-DASHBOARD")
            sensor_label.set_style_text_font(lv.font_montserrat_24, 0)
            sensor_label.center()
            sensor_btn.add_event_cb(lambda e: self._switch(self.switch_to_sensor_screen), lv.EVENT.CLICKED, None)

    def _switch(self, switch_fn):
        # KRITISCH: eine Exception in einem LVGL-Touch-Callback reißt den
        # kompletten m5ui/LVGL-Scheduler mit runter (beobachtet: Gerät
        # reagiert komplett nicht mehr) - besonders abgesichert, da das
        # Sensor-Dashboard als erster Screen in diesem Projekt lv.chart
        # nutzt und noch nicht auf echter Hardware bewährt ist.
        if switch_fn is None:
            return
        try:
            self.close()
            switch_fn()
        except Exception as e:
            # sys.print_exception() statt nur print(e) - liefert den
            # vollständigen Traceback mit Zeilennummer, sonst sehen wir
            # nur "function takes 3 positional arguments but 2 were
            # given" OHNE zu wissen, WELCHER Aufruf das war.
            print("Bildschirmwechsel fehlgeschlagen:")
            sys.print_exception(e)

    def _on_brightness_changed(self, e):
        percent = self._brightness_slider.get_value()
        self._brightness_value_label.set_text("%d%%" % percent)
        self.cfg["brightness"] = percent
        if self.cfg_save is not None:
            self.cfg_save(self.cfg)
        if self.on_brightness_changed is not None:
            self.on_brightness_changed(percent)

    # ------------------------------------------------------------------
    def close(self):
        if self._status_timer is not None:
            self._status_timer.delete()
            self._status_timer = None
        if self._overlay is not None:
            self._overlay.delete()
            self._overlay = None
