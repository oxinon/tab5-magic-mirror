"""
Beschleunigungs-/Erdbeben-Widget für die Konferenzraum-Karte: zeigt das
STA/LTA-Verhältnis (aus sensors/quake_trigger.py), den bisherigen
Spitzenwert und den Zeitpunkt des letzten Ereignisses - angelehnt an das
vom Nutzer bereitgestellte Referenzprojekt (widgets.py::_quake_body),
nur mit LVGL-Widgets statt direktem Display-Zeichnen und ohne die
X/Y/Z-Rohbeschleunigungszeile (auf Wunsch entfernt, hat zu viel Platz
gebraucht).

Wie widgets/traffic_light.py und widgets/equalizer.py: erzeugt einen
eigenen Wrapper (`self.obj`), damit eine Card (siehe widgets/card.py) die
ganze Komponente als ein Flex-Element behandeln und je nach Spalten-
position links/mittig/rechts ausrichten kann.
"""

import time

from theme import COLORS
from i18n import STRINGS

try:
    import lvgl as lv
    _HAS_LVGL = True
except ImportError:
    _HAS_LVGL = False


def _hex(color_hex):
    return lv.color_hex(int(color_hex.lstrip("#"), 16))


class AccelerationWidget:
    def __init__(self, parent, width=268, align="left"):
        if not _HAS_LVGL:
            raise RuntimeError("AccelerationWidget benötigt lvgl/m5ui (nur auf der Tab5 verfügbar)")
        self.width = width
        # Gleiches Prinzip wie widgets/clock_widget.py::ClockWidget - align
        # kommt von der umschließenden Card (card.align). Ohne das bleibt
        # der Text immer linksbündig, egal in welcher Spalte die Kachel
        # steht (das war hier vergessen worden).
        text_align = {
            "left": lv.TEXT_ALIGN.LEFT,
            "center": lv.TEXT_ALIGN.CENTER,
            "right": lv.TEXT_ALIGN.RIGHT,
        }.get(align, lv.TEXT_ALIGN.LEFT)

        self.obj = lv.obj(parent)
        self.obj.set_size(width, 150)
        self.obj.set_style_bg_opa(0, 0)
        self.obj.set_style_border_width(0, 0)
        self.obj.set_style_pad_all(0, 0)
        self.obj.remove_flag(lv.obj.FLAG.SCROLLABLE)
        self.obj.set_flex_flow(lv.FLEX_FLOW.COLUMN)
        self.obj.set_flex_align(lv.FLEX_ALIGN.START, lv.FLEX_ALIGN.START, lv.FLEX_ALIGN.START)

        # Große, farbige Verhältnis-Zahl (grün = ruhig, amber/rot = aktiv) -
        # die "Hero-Zahl" dieser Kachel, wie bei Uhr/Wetter/Luftqualität.
        self.ratio_label = lv.label(self.obj)
        self.ratio_label.set_width(width)
        self.ratio_label.set_style_text_font(lv.font_montserrat_48, 0)
        self.ratio_label.set_style_text_align(text_align, 0)

        self.caption_label = lv.label(self.obj)
        self.caption_label.set_width(width)
        self.caption_label.set_style_text_font(lv.font_montserrat_24, 0)
        # Gleiche Farbe wie das Datum im Uhr-Widget (fg_dim) - siehe
        # widgets/clock_widget.py::date_label.
        self.caption_label.set_style_text_color(_hex(COLORS["fg_dim"]), 0)
        self.caption_label.set_style_text_align(text_align, 0)

        self.event_label = lv.label(self.obj)
        self.event_label.set_width(width)
        self.event_label.set_style_text_font(lv.font_montserrat_24, 0)
        self.event_label.set_style_text_color(_hex(COLORS["fg_faint"]), 0)
        self.event_label.set_style_text_align(text_align, 0)

        self.set_state(triggered=False, ratio=1.0, peak_ratio=1.0, last_event_ts=None)

    def set_state(self, triggered, ratio=None, peak_ratio=None, last_event_ts=None):
        r = ratio if ratio is not None else 0.0
        # Gleiche Schwelle/Farblogik wie im Referenzwidget: ab 1.5x schon
        # amber (Vorwarnung), triggered (>= trigger_ratio, meist 3.0) rot.
        color = COLORS["red"] if triggered else COLORS["amber"] if r >= 1.5 else COLORS["up"]
        self.ratio_label.set_style_text_color(_hex(color), 0)
        self.ratio_label.set_text("%.1fx" % r)
        self.caption_label.set_text(STRINGS["accel.ratio_caption"] % (peak_ratio if peak_ratio is not None else 1.0))

        if last_event_ts:
            elapsed = max(0, int(time.time() - last_event_ts))
            self.event_label.set_text(STRINGS["accel.last_event"] % elapsed)
        else:
            self.event_label.set_text(STRINGS["accel.no_events"])
