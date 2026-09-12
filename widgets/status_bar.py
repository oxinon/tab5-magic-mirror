"""
Globale Statusleiste (oben auf jedem Screen sichtbar) - analog zur
Statusleiste auf einem Smartphone: Burger-Menü links, WLAN- und Akku-
Anzeige rechts.

Nutzt LVGLs eingebaute Symbol-Font (lv.SYMBOL.*) statt eigener Icon-
Dateien - BARS (Hamburger), WIFI und BATTERY_* stehen auf jeder
LVGL-Firmware zur Verfügung, ohne zusätzliche Fonts einbetten zu müssen.

Aktuell instanziiert jeder Screen seine eigene StatusBar (siehe
screens/environment.py). Sobald main.py/ein Screen-Manager existiert,
sollte das idealerweise EINE Instanz auf einer eigenen Top-Layer sein,
die über allen Screens liegen bleibt, statt bei jedem Screen-Wechsel neu
aufgebaut zu werden - siehe TODO in README.md.
"""

from theme import COLORS

try:
    import lvgl as lv
    _HAS_LVGL = True
except ImportError:
    _HAS_LVGL = False

BAR_HEIGHT = 44

if _HAS_LVGL:
    # (Schwellwert in %, Symbol) - von voll nach leer, erster Treffer gewinnt
    _BATTERY_SYMBOLS = [
        (85, lv.SYMBOL.BATTERY_FULL),
        (60, lv.SYMBOL.BATTERY_3),
        (35, lv.SYMBOL.BATTERY_2),
        (15, lv.SYMBOL.BATTERY_1),
        (0, lv.SYMBOL.BATTERY_EMPTY),
    ]
else:
    _BATTERY_SYMBOLS = []


def _battery_symbol(percent):
    for threshold, symbol in _BATTERY_SYMBOLS:
        if percent >= threshold:
            return symbol
    return _BATTERY_SYMBOLS[-1][1]


def _hex(color_hex):
    return lv.color_hex(int(color_hex.lstrip("#"), 16))


class StatusBar:
    """
    on_menu_pressed: Callback ohne Argumente, aufgerufen beim Antippen des
    Burger-Icons (öffnet burger_menu.py - noch nicht Teil dieses Pakets).
    """

    def __init__(self, parent, on_menu_pressed=None):
        if not _HAS_LVGL:
            raise RuntimeError("StatusBar benötigt lvgl/m5ui (nur auf der Tab5 verfügbar)")

        self.bar = lv.obj(parent)
        self.bar.set_size(1280, BAR_HEIGHT)
        self.bar.set_pos(0, 0)
        self.bar.set_style_bg_color(lv.color_hex(0x000000), 0)
        self.bar.set_style_border_width(0, 0)
        self.bar.set_style_pad_all(0, 0)
        self.bar.remove_flag(lv.obj.FLAG.SCROLLABLE)

        # Burger-Menü (oben links) - eigener klickbarer Container statt nur
        # des Symbol-Labels, damit der Touch-Bereich groß genug ist (min.
        # ca. 48x48px Richtwert für Touch-Ziele, hier großzügiger für den
        # Grove-typischen "auf großem Screen tippen"-Anwendungsfall)
        self.menu_btn = lv.obj(self.bar)
        self.menu_btn.set_size(64, BAR_HEIGHT)
        self.menu_btn.set_pos(0, 0)
        self.menu_btn.set_style_bg_opa(0, 0)
        self.menu_btn.set_style_border_width(0, 0)
        self.menu_btn.set_style_pad_all(0, 0)
        self.menu_icon = lv.label(self.menu_btn)
        self.menu_icon.set_text(lv.SYMBOL.BARS)
        self.menu_icon.set_style_text_font(lv.font_montserrat_16, 0)
        self.menu_icon.set_style_text_color(_hex(COLORS["fg"]), 0)
        self.menu_icon.center()
        if on_menu_pressed:
            self.menu_btn.add_flag(lv.obj.FLAG.CLICKABLE)
            self.menu_btn.add_event_cb(lambda e: on_menu_pressed(), lv.EVENT.CLICKED, None)

        # WLAN-Icon (oben rechts, links vom Akku)
        self.wifi_icon = lv.label(self.bar)
        self.wifi_icon.set_style_text_font(lv.font_montserrat_16, 0)
        self.wifi_icon.set_text(lv.SYMBOL.WIFI)
        self.wifi_icon.set_pos(1140, 20)

        # Lade-Symbol (nur sichtbar, wenn geladen wird - siehe set_battery)
        self.charge_icon = lv.label(self.bar)
        self.charge_icon.set_style_text_font(lv.font_montserrat_16, 0)
        self.charge_icon.set_text(lv.SYMBOL.CHARGE)
        self.charge_icon.set_pos(1178, 20)
        self.charge_icon.add_flag(lv.obj.FLAG.HIDDEN)

        # Akku-Icon + Prozent-Text (ganz rechts)
        self.battery_icon = lv.label(self.bar)
        self.battery_icon.set_style_text_font(lv.font_montserrat_16, 0)
        self.battery_icon.set_pos(1200, 20)

        self.battery_label = lv.label(self.bar)
        self.battery_label.set_style_text_font(lv.font_montserrat_16, 0)
        self.battery_label.set_pos(1200, 40)

        self.set_wifi_connected(False)
        self.set_battery(100)

    def set_wifi_connected(self, connected):
        color = COLORS["fg"] if connected else COLORS["fg_faint"]
        self.wifi_icon.set_style_text_color(_hex(color), 0)

    def set_battery(self, percent, charging=False):
        percent = max(0, min(100, percent))
        self.battery_icon.set_text(_battery_symbol(percent))
        color = COLORS["red"] if percent <= 15 and not charging else COLORS["fg"]
        self.battery_icon.set_style_text_color(_hex(color), 0)
        self.battery_label.set_text("%d%%" % percent)

        if charging:
            self.charge_icon.remove_flag(lv.obj.FLAG.HIDDEN)
        else:
            self.charge_icon.add_flag(lv.obj.FLAG.HIDDEN)
