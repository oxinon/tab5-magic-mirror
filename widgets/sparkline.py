"""
Kompaktes Zeitreihen-Widget ("Sparkline") für den Room-Monitor.

Zeigt den Verlauf eines Messwerts (Temperatur, Luftfeuchte, IAQ-Score, ...)
als schmale Linie ohne Achsenbeschriftung - dezent, im Stil der übrigen
mm-*-Widgets. Nutzt lv.chart im LINE-Modus.
"""

try:
    import lvgl as lv
    _HAS_LVGL = True
except ImportError:
    _HAS_LVGL = False

from theme import COLORS


class Sparkline:
    def __init__(self, parent, x, y, w, h, color_hex=None, points=40):
        if not _HAS_LVGL:
            raise RuntimeError("Sparkline benötigt lvgl/m5ui (nur auf der Tab5 verfügbar)")

        self.points = points
        self.chart = lv.chart(parent)
        self.chart.set_pos(x, y)
        self.chart.set_size(w, h)
        self.chart.set_type(lv.chart.TYPE.LINE)
        self.chart.set_point_count(points)
        self.chart.set_div_line_count(0, 0)   # keine Gitterlinien - dezent
        self.chart.set_style_bg_opa(0, 0)      # transparenter Hintergrund
        self.chart.set_style_border_width(0, 0)
        self.chart.set_style_size(0, 0)        # keine Punktmarker, nur Linie

        color = color_hex or COLORS["accent"]
        self.series = self.chart.add_series(
            lv.color_hex(int(color.lstrip("#"), 16)), lv.chart.AXIS.PRIMARY_Y
        )

    def update(self, values):
        """values: Liste von Zahlen, z.B. aus history.downsample(key, points)."""
        self.chart.set_all_value(self.series, lv.CHART_POINT.NONE)
        for v in values[-self.points:]:
            self.chart.set_next_value(self.series, int(v))
        self.chart.refresh()
