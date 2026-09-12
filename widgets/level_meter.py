"""
Horizontaler Pegel-Balken fürs Mikrofon (Konferenzraum: "wie laut ist es
hier gerade"). Gleiche Farblogik wie die Luftqualitäts-Ampel, nur als
Balken statt Kreis - ein kontinuierlicher Pegel liest sich als Balken
intuitiver ab als über einen Ampel-Zustand.
"""

from theme import COLORS
from widgets import lv_const

THRESHOLDS = {
    "quiet": 40,     # < 40   -> grün (leise/normal fürs Meeting)
    "moderate": 70,  # 40-69  -> amber
    # >= 70              -> rot (laut)
}


def level_to_color(value):
    if value is None:
        return COLORS["fg_faint"]
    if value < THRESHOLDS["quiet"]:
        return COLORS["up"]
    if value < THRESHOLDS["moderate"]:
        return COLORS["amber"]
    return COLORS["red"]


try:
    import lvgl as lv
    _HAS_LVGL = True
except ImportError:
    _HAS_LVGL = False


class LevelMeter:
    def __init__(self, parent, x, y, w, h):
        if not _HAS_LVGL:
            raise RuntimeError("LevelMeter benötigt lvgl/m5ui (nur auf der Tab5 verfügbar)")

        self.bar = lv.bar(parent)
        self.bar.set_pos(x, y)
        self.bar.set_size(w, h)
        self.bar.set_range(0, 100)
        self.bar.set_value(0, lv_const.ANIM_OFF)

    def set_value(self, value):
        value = 0 if value is None else max(0, min(100, value))
        self.bar.set_value(int(value), lv_const.ANIM_ON)
        color = level_to_color(value)
        self.bar.set_style_bg_color(
            lv.color_hex(int(color.lstrip("#"), 16)), lv.PART.INDICATOR
        )
