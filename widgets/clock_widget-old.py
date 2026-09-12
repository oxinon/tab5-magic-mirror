"""
Uhr-Widget für den Magic-Mirror-Screen. Tickt selbstständig über einen
eigenen LVGL-Timer (1x pro Sekunde) statt vom äußeren refresh()-Zyklus
abzuhängen - der läuft (wie bei den anderen Magic-Mirror-Widgets) nur alle
paar Sekunden bis Minuten, was für eine Uhr sichtbar nachhinken würde.

TODO auf Hardware prüfen: `lv.timer_create(callback, period_ms, user_data)`
ist die übliche LVGL-Timer-API - Signatur/Rückgabewert je nach Python-
Binding-Version ggf. leicht abweichend. Ebenso: MicroPythons `time.localtime()`
liefert tm_wday mit 0=Montag (nicht 0=Sonntag wie in JS) - beim Mapping auf
WEEKDAYS berücksichtigt.
"""

import ntp_clock
from theme import COLORS
from i18n import get_lang
from widgets import lv_const

try:
    import lvgl as lv
    _HAS_LVGL = True
except ImportError:
    _HAS_LVGL = False

# Reihenfolge beginnt bei Montag (MicroPython tm_wday: 0=Montag)
WEEKDAYS = {
    "de": ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"],
    "en": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"],
}
MONTHS = {
    "de": ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August",
           "September", "Oktober", "November", "Dezember"],
    "en": ["January", "February", "March", "April", "May", "June", "July", "August",
           "September", "October", "November", "December"],
}


def _hex(color_hex):
    return lv.color_hex(int(color_hex.lstrip("#"), 16))


class ClockWidget:
    def __init__(self, parent, format24h=True, show_seconds=True, show_date=True, align="left"):
        if not _HAS_LVGL:
            raise RuntimeError("ClockWidget benötigt lvgl/m5ui (nur auf der Tab5 verfügbar)")

        self.format24h = format24h
        self.show_seconds = show_seconds
        self.show_date = show_date

        # align kommt von der umschließenden Card (card.align, siehe
        # widgets/grid_layout.GridLayout.h_align) - Randspalten richten
        # sich zum Rand aus, alles dazwischen zentriert sich. Betrifft
        # sowohl die Position der eigenen Box (per flex_align) als auch
        # die Textausrichtung DARIN (set_style_text_align) - beides nötig,
        # sonst bleibt der Text innerhalb der Box linksbündig, selbst wenn
        # die Box selbst schon nach rechts gerückt ist.
        cross_align = {
            "left": lv.FLEX_ALIGN.START,
            "center": lv.FLEX_ALIGN.CENTER,
            "right": lv.FLEX_ALIGN.END,
        }.get(align, lv.FLEX_ALIGN.START)
        text_align = {
            "left": lv.TEXT_ALIGN.LEFT,
            "center": lv.TEXT_ALIGN.CENTER,
            "right": lv.TEXT_ALIGN.RIGHT,
        }.get(align, lv.TEXT_ALIGN.LEFT)

        # Deutlich größer als vorher (war 280x130 @ 24pt/16pt). Die Box
        # bleibt bewusst innerhalb der Breite einer normalen 1-spaltigen
        # Magic-Mirror-Kachel (Zellbreite bei 4 Spalten/1280px ~304px,
        # siehe widgets/grid_layout.py) - sonst würde die Uhr optisch in
        # die Nachbarkachel hineinragen, da Card.body nicht clippt.
        self.obj = lv.obj(parent)
        self.obj.set_size(290, 170)
        self.obj.set_style_bg_opa(0, 0)
        self.obj.set_style_border_width(0, 0)
        self.obj.set_style_pad_all(0, 0)
        self.obj.remove_flag(lv.obj.FLAG.SCROLLABLE)
        self.obj.set_flex_flow(lv.FLEX_FLOW.COLUMN)
        self.obj.set_flex_align(lv.FLEX_ALIGN.START, cross_align, lv.FLEX_ALIGN.START)

        self.time_label = lv.label(self.obj)
        self.time_label.set_style_text_font(lv.font_montserrat_48, 0)
        self.time_label.set_style_text_color(_hex(COLORS["fg"]), 0)
        self.time_label.set_style_text_align(text_align, 0)

        # Datum bekommt eine kleinere Schrift als die Uhrzeit UND darf
        # umbrechen ("Montag, 31. Dezember 2024" ist bei 290px Breite auch
        # bei 24pt zu lang für eine Zeile - bricht dann einfach in eine
        # zweite Zeile um statt einen Scrollbalken auszulösen). War vorher
        # 16pt, jetzt an die restlichen Dashboard-Schriftgrößen (24pt wie
        # in screens/widget_catalog.py) angeglichen.
        self.date_label = lv.label(self.obj)
        self.date_label.set_width(290)
        self.date_label.set_long_mode(lv_const.LABEL_LONG_WRAP)
        self.date_label.set_style_text_font(lv.font_montserrat_24, 0)
        self.date_label.set_style_text_color(_hex(COLORS["fg_dim"]), 0)
        self.date_label.set_style_text_align(text_align, 0)

        self._timer = None
        self._tick()
        self._timer = lv.timer_create(lambda t: self._tick(), 1000, None)

    def _tick(self):
        # ntp_clock.now_local() statt time.localtime() direkt: MicroPython
        # führt die interne Uhr in UTC (siehe ntp_clock.py-Docstring) - erst
        # now_local() rechnet den konfigurierten utc_offset + automatische
        # EU-Sommerzeit dazu (config.json "general", siehe main.py).
        now = ntp_clock.now_local()
        hour = now[3]
        suffix = ""
        if not self.format24h:
            suffix = " PM" if hour >= 12 else " AM"
            hour = hour % 12 or 12

        text = "%02d:%02d" % (hour, now[4])
        if self.show_seconds:
            text += ":%02d" % now[5]
        text += suffix
        self.time_label.set_text(text)

        if self.show_date:
            lang = get_lang() if get_lang() in WEEKDAYS else "de"
            weekday = WEEKDAYS[lang][now[6] % 7]
            month = MONTHS[lang][now[1] - 1]
            if lang == "en":
                self.date_label.set_text("%s, %s %d, %d" % (weekday, month, now[2], now[0]))
            else:
                self.date_label.set_text("%s, %d. %s %d" % (weekday, now[2], month, now[0]))

    def deinit(self):
        if self._timer is not None:
            self._timer.delete()
            self._timer = None
