"""
Screen 4: Settings.

Deckt Theme (Dark/Light), Helligkeit, Sprache (DE/EN) und WLAN-STATUS
(nur Anzeige: Modus/SSID/IP/Signal) ab.

WICHTIG: Die frühere WLAN-Zugangsdaten-Bearbeitung (SSID/Passwort-Felder,
"Verbinden"/"Access Point"-Buttons) wurde bewusst aus dem Tab5-Settings-
Screen entfernt - das Eintippen von WLAN-Passwörtern über die
Bildschirmtastatur war umständlich. Das Bearbeiten von SSID/Passwort läuft
jetzt ausschließlich über das Web-UI (`/wifi`, siehe web_server.py), das
im Access-Point-Modus automatisch erreichbar ist, wenn kein bekanntes
Netz gefunden wird (siehe wifi_manager.connect_or_ap() bzw. main.py).
Der Status hier (IP-Adresse, verbundenes Netz) bleibt sichtbar, damit man
auf dem Tab5-Display selbst nachschauen kann, welche IP man im Browser
aufrufen muss, ohne dafür das Web-UI zu benötigen.

WICHTIG (siehe theme.py/i18n.py-Docstrings): Ein Theme- oder Sprachwechsel
färbt/übersetzt nur NEU erzeugte Widgets um. Damit die Änderung sofort
sichtbar wird, baut dieser Screen sich nach jedem Wechsel selbst neu auf
(siehe _on_theme_changed/_on_language_changed) - andere, bereits offene
Screens übernehmen NUR die Texte/Farben, die sie bei jedem refresh()-
Zyklus ohnehin neu setzen; fest beim Bau gesetzte Texte (Karten-
Überschriften) ändern sich dort erst beim nächsten eigenen Aufbau (z.B.
Screen-Wechsel über das Burger-Menü) - ein Screen-Manager, der "alles neu
aufbauen" anstoßen könnte, fehlt noch.

TODO auf Hardware prüfen: das LVGL-Bildschirmtastatur-Muster unten
(lv.keyboard + FOCUSED/DEFOCUSED/READY/CANCEL-Events an Textareas koppeln)
ist Standard-LVGL, Event-Namen können je nach Python-Binding-Version aber
leicht abweichen.
"""

try:
    import lvgl as lv
    _HAS_LVGL = True
except ImportError:
    _HAS_LVGL = False

import theme
import i18n
import wifi_manager
from theme import COLORS
from i18n import STRINGS
from widgets import lv_const
from widgets.status_bar import StatusBar

THEME_OPTIONS = ["dark", "light"]
LANGUAGE_OPTIONS = ["de", "en"]


def _hex(color_hex):
    return lv.color_hex(int(color_hex.lstrip("#"), 16))


class SettingsScreen:
    """
    cfg / cfg_save: config.py-Funktionen bzw. das geladene Config-Dict.
    wifi_mgr: wifi_manager.WifiManager-Instanz (dieselbe, die main.py beim
    Boot für connect_or_ap() genutzt hat - wichtig, damit status() den
    tatsächlichen Laufzeitzustand zeigt statt eines frischen, leeren
    WLAN-Objekts). Fällt auf das Modul-Singleton zurück, falls keine
    übergeben wird.
    on_brightness_changed: optionaler Callback(percent) - main.py verdrahtet
    das später mit der tatsächlichen Backlight-PWM (TODO, siehe main.py).
    on_menu_pressed: siehe widgets/status_bar.py.
    """

    def __init__(self, cfg, cfg_save, wifi_mgr=None, on_brightness_changed=None,
                 parent=None, on_menu_pressed=None):
        if not _HAS_LVGL:
            raise RuntimeError("SettingsScreen benötigt lvgl/m5ui (nur auf der Tab5 verfügbar)")

        self.cfg = cfg
        self.cfg_save = cfg_save
        self.wifi_mgr = wifi_mgr or wifi_manager
        self.on_brightness_changed = on_brightness_changed
        self._on_menu_pressed = on_menu_pressed
        self._parent = parent
        self._status_timer = None
        self._build()

    def _build(self):
        # Vor einem Neuaufbau (Theme-/Sprachwechsel) den alten WLAN-Status-
        # Timer löschen - LVGL-Timer sind unabhängig vom Widget-Baum, würden
        # sonst weiterlaufen und auf ungültige/veraltete Label-Referenzen
        # zugreifen (Speicherleck + potenzieller Absturz bei jedem Wechsel).
        if self._status_timer is not None:
            self._status_timer.delete()
            self._status_timer = None

        # TODO WAR: `parent` wurde entgegengenommen, aber nie benutzt - immer
        # ein eigenständiges lv.obj() erzeugt. Jetzt: falls ein parent (i.d.R.
        # ein m5ui.M5Page vom Burger-Menü) übergeben wurde, den nutzen, damit
        # burger_menu.py über screen_load() konsistent zwischen allen vier
        # Screens umschalten kann.
        self.screen = self._parent if self._parent is not None else lv.obj()
        self.screen.set_style_bg_color(_hex(COLORS["bg"]), 0)
        # Bewusst NICHT clear_flag(SCROLLABLE) wie bei den Dashboard-Screens:
        # Settings hat mehrere Abschnitte untereinander (Theme, Helligkeit,
        # Sprache, WLAN) und kann legitim länger als der Bildschirm werden -
        # da soll man scrollen können, statt dass Inhalte unerreichbar
        # abgeschnitten werden.

        self.status_bar = StatusBar(self.screen, on_menu_pressed=self._on_menu_pressed)

        self.panel = lv.obj(self.screen)
        self.panel.set_pos(40, 60)
        self.panel.set_size(1200, 600)
        self.panel.set_style_bg_opa(0, 0)
        self.panel.set_style_border_width(0, 0)
        self.panel.set_style_pad_all(0, 0)
        self.panel.set_flex_flow(lv.FLEX_FLOW.COLUMN)
        self.panel.set_flex_align(lv.FLEX_ALIGN.START, lv.FLEX_ALIGN.START, lv.FLEX_ALIGN.START)
        self.panel.set_style_pad_row(28, 0)

        # Keine Bildschirmtastatur mehr nötig - seit dem Entfernen der
        # WLAN-Zugangsdaten-Felder gibt es auf diesem Screen keine
        # Textareas mehr, die sie bräuchten.

        self._build_theme_section()
        self._build_brightness_section()
        self._build_language_section()
        self._build_wifi_section()

    def _section_title(self, text):
        title = lv.label(self.panel)
        title.set_text(text)
        title.set_style_text_font(lv.font_montserrat_24, 0)
        title.set_style_text_color(_hex(COLORS["accent"]), 0)
        title.set_style_text_letter_space(2, 0)
        return title

    def _build_theme_section(self):
        self._section_title(STRINGS["settings.appearance"])

        self.theme_dropdown = lv.dropdown(self.panel)
        self.theme_dropdown.set_options("\n".join(
            STRINGS["theme.dark"] if m == "dark" else STRINGS["theme.light"] for m in THEME_OPTIONS))
        self.theme_dropdown.set_style_text_font(lv.font_montserrat_24, 0)
        current_mode = self.cfg.get("theme_mode", "dark")
        self.theme_dropdown.set_selected(
            THEME_OPTIONS.index(current_mode) if current_mode in THEME_OPTIONS else 0,
            lv_const.ANIM_OFF,
        )
        self.theme_dropdown.add_event_cb(self._on_theme_changed, lv.EVENT.VALUE_CHANGED, None)

    def _build_brightness_section(self):
        self._section_title(STRINGS["settings.brightness"])

        row = lv.obj(self.panel)
        row.set_size(600, 60)
        row.set_style_bg_opa(0, 0)
        row.set_style_border_width(0, 0)
        row.set_style_pad_all(0, 0)

        self.brightness_slider = lv.slider(row)
        self.brightness_slider.set_pos(0, 16)
        self.brightness_slider.set_size(480, 20)
        self.brightness_slider.set_range(10, 100)  # unter 10% wäre das Display kaum noch lesbar
        self.brightness_slider.set_value(self.cfg.get("brightness", 80), lv_const.ANIM_OFF)
        self.brightness_slider.add_event_cb(self._on_brightness_changed, lv.EVENT.VALUE_CHANGED, None)

        self.brightness_value_label = lv.label(row)
        self.brightness_value_label.set_pos(500, 12)
        self.brightness_value_label.set_style_text_font(lv.font_montserrat_24, 0)
        self.brightness_value_label.set_style_text_color(_hex(COLORS["fg"]), 0)
        self.brightness_value_label.set_text("%d%%" % self.cfg.get("brightness", 80))

    def _build_language_section(self):
        self._section_title(STRINGS["settings.language"])

        self.language_dropdown = lv.dropdown(self.panel)
        self.language_dropdown.set_options("\n".join(
            STRINGS["lang.de"] if lang == "de" else STRINGS["lang.en"] for lang in LANGUAGE_OPTIONS))
        self.language_dropdown.set_style_text_font(lv.font_montserrat_24, 0)
        current_lang = self.cfg.get("language", "de")
        self.language_dropdown.set_selected(
            LANGUAGE_OPTIONS.index(current_lang) if current_lang in LANGUAGE_OPTIONS else 0,
            lv_const.ANIM_OFF,
        )
        self.language_dropdown.add_event_cb(self._on_language_changed, lv.EVENT.VALUE_CHANGED, None)

    def _build_wifi_section(self):
        self._section_title(STRINGS["settings.wifi"])

        # Nur noch Status (Modus/SSID/IP/Signal), live über Timer
        # aktualisiert - KEINE Zugangsdaten-Eingabe mehr auf dem Tab5
        # selbst (siehe Modul-Docstring oben): SSID/Passwort ändern läuft
        # jetzt ausschließlich über das Web-UI unter /wifi.
        status_box = lv.obj(self.panel)
        status_box.set_size(700, 140)
        status_box.set_style_bg_opa(0, 0)
        status_box.set_style_border_width(0, 0)
        status_box.set_style_pad_all(0, 0)

        self.wifi_mode_label = lv.label(status_box)
        self.wifi_mode_label.set_pos(0, 0)
        self.wifi_mode_label.set_style_text_font(lv.font_montserrat_24, 0)
        self.wifi_mode_label.set_style_text_color(_hex(COLORS["fg"]), 0)

        self.wifi_ssid_label = lv.label(status_box)
        self.wifi_ssid_label.set_pos(0, 34)
        self.wifi_ssid_label.set_style_text_font(lv.font_montserrat_24, 0)
        self.wifi_ssid_label.set_style_text_color(_hex(COLORS["fg_dim"]), 0)

        self.wifi_ip_label = lv.label(status_box)
        self.wifi_ip_label.set_pos(0, 68)
        self.wifi_ip_label.set_style_text_font(lv.font_montserrat_24, 0)
        self.wifi_ip_label.set_style_text_color(_hex(COLORS["fg_dim"]), 0)

        self.wifi_signal_label = lv.label(status_box)
        self.wifi_signal_label.set_pos(0, 102)
        self.wifi_signal_label.set_style_text_font(lv.font_montserrat_24, 0)
        self.wifi_signal_label.set_style_text_color(_hex(COLORS["fg_faint"]), 0)

        self._refresh_wifi_status()
        self._status_timer = lv.timer_create(lambda t: self._refresh_wifi_status(), 5000, None)

        hint = lv.label(self.panel)
        hint.set_size(700, 60)
        hint.set_long_mode(lv_const.LABEL_LONG_WRAP)
        hint.set_text(STRINGS["wifi.edit_via_web_hint"])
        hint.set_style_text_font(lv.font_montserrat_24, 0)
        hint.set_style_text_color(_hex(COLORS["fg_faint"]), 0)

    # ------------------------------------------------------------------
    # WLAN (nur Statusanzeige - Bearbeitung läuft über das Web-UI, siehe
    # web_server.py::_wifi_page_html unter /wifi)
    # ------------------------------------------------------------------
    def _refresh_wifi_status(self):
        status = self.wifi_mgr.status()
        mode = status.get("mode")
        mode_text = {
            "sta": STRINGS["wifi.mode_sta"],
            "ap": STRINGS["wifi.mode_ap"],
        }.get(mode, STRINGS["wifi.mode_unknown"])
        self.wifi_mode_label.set_text("%s: %s" % (STRINGS["wifi.mode"], mode_text))
        self.wifi_ssid_label.set_text("%s: %s" % (STRINGS["wifi.ssid"], status.get("ssid") or "--"))
        self.wifi_ip_label.set_text("%s: %s" % (STRINGS["wifi.ip"], status.get("ip") or "--"))
        rssi = status.get("rssi")
        self.wifi_signal_label.set_text(
            "%s: %d dBm" % (STRINGS["wifi.signal"], rssi) if rssi is not None else "")

    # ------------------------------------------------------------------
    # Theme / Sprache / Helligkeit
    # ------------------------------------------------------------------
    def _on_theme_changed(self, e):
        selected = self.theme_dropdown.get_selected()
        mode = THEME_OPTIONS[selected]
        theme.set_mode(mode)
        self.cfg["theme_mode"] = mode
        self.cfg_save(self.cfg)
        self._build()  # neu aufbauen, damit die neue Palette sofort sichtbar wird

    def _on_language_changed(self, e):
        selected = self.language_dropdown.get_selected()
        lang = LANGUAGE_OPTIONS[selected]
        i18n.set_lang(lang)
        self.cfg["language"] = lang
        self.cfg_save(self.cfg)
        self._build()  # neu aufbauen, damit die neue Sprache sofort sichtbar wird

    def _on_brightness_changed(self, e):
        percent = self.brightness_slider.get_value()
        self.brightness_value_label.set_text("%d%%" % percent)
        self.cfg["brightness"] = percent
        self.cfg_save(self.cfg)
        if self.on_brightness_changed is not None:
            self.on_brightness_changed(percent)
