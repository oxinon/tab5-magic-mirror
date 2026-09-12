"""
Generische Kreis-Ampel (farbiger Kreis + Text-Label darunter, beides
IMMER zentriert zueinander). Reine LVGL-Darstellung ohne eigene
Schwellwert-Logik - Farbe und Text bestimmt der Aufrufer.

Erzeugt einen eigenen Wrapper (`self.obj`), damit eine Card (siehe
widgets/card.py) die ganze Ampel als ein Element behandeln und je nach
Spaltenposition links/mittig/rechts in der Karte platzieren kann, ohne
dass diese Klasse selbst etwas von Grid/Ausrichtung wissen muss.
"""

from widgets import lv_const

try:
    import lvgl as lv
    _HAS_LVGL = True
except ImportError:
    _HAS_LVGL = False


class TrafficLight:
    def __init__(self, parent, diameter=58):
        if not _HAS_LVGL:
            raise RuntimeError("TrafficLight benötigt lvgl/m5ui (nur auf der Tab5 verfügbar)")

        self.obj = lv.obj(parent)
        self.obj.set_size(diameter, diameter + 8)
        self.obj.set_style_bg_opa(0, 0)
        self.obj.set_style_border_width(0, 0)
        self.obj.set_style_pad_all(0, 0)
        # Ohne das erscheint ein Scrollbalken, sobald Inhalt knapp nicht in
        # die feste Höhe passt - LVGL-Objekte sind standardmäßig scrollbar,
        # alle anderen Widgets in diesem Projekt deaktivieren das schon,
        # hier war es schlicht vergessen worden.
        self.obj.remove_flag(lv.obj.FLAG.SCROLLABLE)

        self.dot = lv.obj(self.obj)
        self.dot.set_size(diameter, diameter)
        self.dot.align(lv.ALIGN.TOP_MID, 0, 0)
        self.dot.set_style_radius(lv_const.RADIUS_CIRCLE, 0)
        self.dot.set_style_border_width(0, 0)

    def set(self, color_hex, text=None):
        # "text" bewusst ignoriert (Parameter bleibt aus Kompatibilität zu
        # AirQualityLight/SoundLight erhalten, die weiterhin einen Text
        # übergeben) - der Status-Text unter dem Kreis (z.B. "GUT"/"n/a")
        # wurde entfernt, die eigentliche Zahl (Score/dB) steht ja schon
        # separat daneben in der Kachel.
        self.dot.set_style_bg_color(lv.color_hex(int(color_hex.lstrip("#"), 16)), 0)
