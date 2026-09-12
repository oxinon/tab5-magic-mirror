"""
Kleiner Equalizer (vertikale Pegel-Balken je Frequenzband), zeigt live,
was das Mikrofon gerade erkennt - reine Anzeige, kein Aufnahme-/Analyse-
Verlauf (die Bänder werden nicht auf die SD-Karte geloggt, siehe
sensors/microphone.py).

Wie widgets/traffic_light.py: erzeugt einen eigenen Wrapper (`self.obj`),
damit eine Card das ganze Balken-Set als ein Element behandeln kann.

Hinweis: Ob lv.bar bei h > w automatisch vertikal (von unten nach oben)
füllt, hängt von der LVGL-Binding-Version ab - auf der echten Tab5
gegenchecken, sonst ggf. auf lv.obj + manuelle Höhenänderung ausweichen.
"""

from theme import COLORS
from widgets import lv_const

try:
    import lvgl as lv
    _HAS_LVGL = True
except ImportError:
    _HAS_LVGL = False


class Equalizer:
    def __init__(self, parent, w, h, num_bands=12, gap=3):
        if not _HAS_LVGL:
            raise RuntimeError("Equalizer benötigt lvgl/m5ui (nur auf der Tab5 verfügbar)")

        self.obj = lv.obj(parent)
        self.obj.set_size(w, h)
        self.obj.set_style_bg_opa(0, 0)
        self.obj.set_style_border_width(0, 0)
        self.obj.set_style_pad_all(0, 0)

        self.bars = []
        bar_w = (w - gap * (num_bands - 1)) // num_bands
        accent = lv.color_hex(int(COLORS["accent"].lstrip("#"), 16))
        track = lv.color_hex(int(COLORS["fg_faint"].lstrip("#"), 16))

        for i in range(num_bands):
            bar = lv.bar(self.obj)
            bar.set_size(bar_w, h)
            bar.set_pos(i * (bar_w + gap), 0)
            bar.set_range(0, 100)
            bar.set_value(0, lv_const.ANIM_OFF)
            bar.set_style_bg_color(track, 0)                    # leerer Track, dunkel statt LVGL-Standardgrau
            bar.set_style_bg_color(accent, lv.PART.INDICATOR)   # gefüllter Teil
            bar.set_style_border_width(0, 0)
            bar.set_style_radius(0, 0)
            self.bars.append(bar)

    def update(self, values):
        """values: Liste von 0-100-Werten, ein Eintrag je Balken (siehe
        MicReading.bands aus sensors/microphone.py)."""
        for bar, value in zip(self.bars, values):
            bar.set_value(int(max(0, min(100, value))), lv_const.ANIM_ON)
