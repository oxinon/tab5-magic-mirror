"""
Erschütterungs-Anzeige für den Konferenzraum-Screen: ruhiger grüner Punkt
im Normalzustand ("keine Erschütterung"), wechselt bei ausgelöstem
STA/LTA-Trigger (siehe sensors/quake_trigger.py) auf Rot.

Im Original (Magic Mirror mm-ews-*) pulsiert der rote Rahmen bei Trigger
zusätzlich - reiner Politur-Schritt über lv.anim, der erst ergänzt wird,
sobald sich auf der echten Tab5 prüfen lässt, wie sich lv.anim mit der
dort verbauten LVGL-Version verhält.
"""

from theme import COLORS

from widgets import lv_const

try:
    import lvgl as lv
    _HAS_LVGL = True
except ImportError:
    _HAS_LVGL = False


class QuakeIndicator:
    def __init__(self, parent, x, y, diameter=34):
        if not _HAS_LVGL:
            raise RuntimeError("QuakeIndicator benötigt lvgl/m5ui (nur auf der Tab5 verfügbar)")

        self.dot = lv.obj(parent)
        self.dot.set_size(diameter, diameter)
        self.dot.set_pos(x, y)
        self.dot.set_style_radius(lv_const.RADIUS_CIRCLE, 0)
        self.dot.set_style_border_width(0, 0)

        self.label = lv.label(parent)
        self.label.set_pos(x + diameter + 12, y + diameter // 4)
        self.label.set_style_text_font(lv.font_montserrat_24, 0)

        self.set_triggered(False)

    def set_triggered(self, triggered):
        color = COLORS["red"] if triggered else COLORS["up"]
        text = "ERSCHÜTTERUNG!" if triggered else "keine Erschütterung"
        self.dot.set_style_bg_color(lv.color_hex(int(color.lstrip("#"), 16)), 0)
        self.label.set_text(text)
