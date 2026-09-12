"""
Daten-getriebenes Rasterlayout: Zellen-Geometrie UND horizontale
Ausrichtung werden aus der Spaltenposition abgeleitet statt hart im Code
zu stehen.

Folgt bewusst der Konvention des Referenz-Projekts
(oxinon/magic-mirror-3000, 4x4-Raster mit "r{row}-c{col}"-Positionen,
1-indiziert, in config.json gespeichert und per Web-UI editierbar): Wer
später ein Web-UI für den Tab5 baut, kann exakt dasselbe Positions-Format
wiederverwenden.

Ausrichtungsprinzip (identisch zum "Rahmen aus Informationen" im
Original-Magic-Mirror-Layout, hier nur horizontal): linke Randspalte wird
linksbündig ausgerichtet, rechte Randspalte rechtsbündig, alles
dazwischen (und Widgets, die die volle Breite überspannen) zentriert.
"""

try:
    import lvgl as lv
    _HAS_LVGL = True
except ImportError:
    _HAS_LVGL = False

try:
    from widgets.status_bar import BAR_HEIGHT as STATUS_BAR_HEIGHT
except ImportError:
    STATUS_BAR_HEIGHT = 44  # Fallback, falls status_bar.py nicht importierbar ist
SCREEN_W = 1280
SCREEN_H = 720


def parse_position(position):
    """
    Parst "r{row}-c{col}" (1-indiziert, wie im Referenz-Projekt) zu einem
    0-indizierten (row, col)-Tupel, wie es cell()/h_align() erwarten.
    Bewusst ohne `re`-Modul (auf MicroPython nicht überall verfügbar/
    unnötiger Overhead für so ein einfaches Format).
    """
    try:
        r_part, c_part = position.split("-")
        row = int(r_part[1:]) - 1
        col = int(c_part[1:]) - 1
        return row, col
    except (ValueError, IndexError, AttributeError):
        raise ValueError("Ungültige Positions-Angabe: %r (erwartet z.B. 'r1-c1')" % (position,))


class GridLayout:
    def __init__(self, cols, rows, margin=8, top_offset=STATUS_BAR_HEIGHT,
                 screen_w=SCREEN_W, screen_h=SCREEN_H, row_h=None):
        """
        row_h: optionale FESTE Zeilenhöhe in Pixeln. Ohne Angabe (None,
        Standardverhalten) wird die Zeilenhöhe wie bisher so berechnet,
        dass alle `rows` exakt in screen_h passen (kein Scrollen nötig -
        so haben es die früheren Einzel-Screens genutzt).

        MIT Angabe (siehe screens/dashboard.py, das konsolidierte
        Ein-Screen-Dashboard mit ~25 Widgets): die Zeilen behalten eine
        komfortable, konstante Höhe (z.B. dieselbe wie früher bei 3
        Zeilen), auch wenn `rows` dadurch mehr Gesamthöhe braucht als
        screen_h - der Screen muss dann vertikal scrollbar sein (siehe
        DashboardScreen, das dafür bewusst NICHT remove_flag(SCROLLABLE)
        aufruft) statt wie früher alle Zeilen ins sichtbare Fenster zu
        zwängen.
        """
        self.cols = cols
        self.rows = rows
        self.margin = margin
        self.top_offset = top_offset
        self.screen_w = screen_w
        self.screen_h = screen_h
        self.col_w = screen_w // cols
        self.row_h = row_h if row_h is not None else (screen_h - top_offset) // rows

    def total_height(self):
        """Gesamthöhe des Inhalts (Kopfbereich + alle Zeilen) - für
        scrollbare Layouts (row_h fest vorgegeben) größer als screen_h."""
        return self.top_offset + self.rows * self.row_h

    def cell(self, row, col, col_span=1, row_span=1):
        """Pixel-Rechteck (x, y, w, h) für eine Rasterzelle (0-indiziert)."""
        x = col * self.col_w + self.margin
        y = self.top_offset + row * self.row_h + self.margin
        w = self.col_w * col_span - self.margin * 2
        h = self.row_h * row_span - self.margin * 2
        return x, y, w, h

    def h_align(self, col, col_span=1):
        """Liefert "left" | "center" | "right" nach dem Randspalten-Prinzip."""
        touches_left = col == 0
        touches_right = (col + col_span - 1) == (self.cols - 1)
        if touches_left and not touches_right:
            return "left"
        if touches_right and not touches_left:
            return "right"
        return "center"

    @classmethod
    def from_config(cls, screen_config):
        """
        Baut ein GridLayout aus dem "grid"-Teil eines Screen-Eintrags in
        config.json (siehe config.py DEFAULTS["screens"]). Fällt auf 4x3
        zurück, falls der Screen (noch) keine eigene Grid-Angabe hat.
        Optionales "row_h" im Grid-Teil erzwingt eine feste Zeilenhöhe
        (siehe Klassen-Docstring oben) statt automatischer Berechnung.
        """
        grid_cfg = (screen_config or {}).get("grid", {})
        return cls(cols=grid_cfg.get("cols", 4), rows=grid_cfg.get("rows", 3),
                    row_h=grid_cfg.get("row_h"))
