"""
Container-Karte für eine Rasterzelle. Besteht aus zwei Teilen:

- HEADER: feste Höhe, immer am oberen Rand der Kachel - Überschriften
  landen hier über add_title(). Weil die Höhe/Position unabhängig vom
  restlichen Kachel-Inhalt ist, stehen die Überschriften nebeneinander-
  liegender Kacheln IMMER auf derselben Zeile, egal wie viel Inhalt jede
  Kachel sonst hat.
- BODY: der restliche Platz darunter, nutzt LVGLs Flex-Layout (Spalte,
  Inhalt gleichmäßig über die Höhe verteilt) für den eigentlichen
  Widget-Inhalt - das war vorher der einzige Container (self.obj).

Horizontale Ausrichtung (links/mittig/rechts) kommt von außen (siehe
widgets/grid_layout.GridLayout.h_align) und gilt für Header und Body
gemeinsam - Randspalten richten sich zum Rand aus, alles dazwischen
zentriert sich.
"""

try:
    import lvgl as lv
    _HAS_LVGL = True
except ImportError:
    _HAS_LVGL = False

from widgets import lv_const
from ascii_text import to_ascii


def _hex(color_hex):
    return lv.color_hex(int(color_hex.lstrip("#"), 16))


_CROSS_ALIGN = {}
_TEXT_ALIGN = {}

if _HAS_LVGL:
    _CROSS_ALIGN = {
        "left": lv.FLEX_ALIGN.START,
        "center": lv.FLEX_ALIGN.CENTER,
        "right": lv.FLEX_ALIGN.END,
    }
    _TEXT_ALIGN = {
        "left": lv.TEXT_ALIGN.LEFT,
        "center": lv.TEXT_ALIGN.CENTER,
        "right": lv.TEXT_ALIGN.RIGHT,
    }

HEADER_HEIGHT = 32
HEADER_GAP = 8  # Abstand zwischen Kopfbereich und Rumpf


class Card:
    """
    x, y, w, h: Zellen-Rechteck aus GridLayout.cell().
    align: "left" | "center" | "right" aus GridLayout.h_align().
    padding: Innenabstand zum Kachelrand (die Lücke ZWISCHEN Kacheln
    kommt schon aus GridLayout.cell()/margin - padding ist zusätzlich der
    Abstand vom Karteninhalt zum eigenen Kachelrand, damit nichts klebt).
    """

    def __init__(self, parent, x, y, w, h, align="left", padding=18):
        if not _HAS_LVGL:
            raise RuntimeError("Card benötigt lvgl/m5ui (nur auf der Tab5 verfügbar)")

        self.align = align
        self.padding = padding
        self.w = w
        self.h = h

        self.obj = lv.obj(parent)
        self.obj.set_pos(x, y)
        self.obj.set_size(w, h)
        self.obj.set_style_bg_opa(0, 0)
        self.obj.set_style_border_width(0, 0)
        self.obj.set_style_pad_all(0, 0)
        self.obj.remove_flag(lv.obj.FLAG.SCROLLABLE)

        # Kopfbereich: feste Höhe/Position, unabhängig vom Rumpf-Inhalt.
        self.header = lv.obj(self.obj)
        self.header.set_pos(padding, 2)
        self.header.set_size(max(w - 2 * padding, 10), HEADER_HEIGHT)
        self.header.set_style_bg_opa(0, 0)
        self.header.set_style_border_width(0, 0)
        self.header.set_style_pad_all(0, 0)
        self.header.remove_flag(lv.obj.FLAG.SCROLLABLE)

        # Rumpf: der bisherige einzige Container, jetzt unterhalb des
        # Kopfbereichs statt die volle Kachelhöhe zu nutzen.
        body_y = HEADER_HEIGHT + HEADER_GAP
        self.body = lv.obj(self.obj)
        self.body.set_pos(0, body_y)
        self.body.set_size(w, max(h - body_y, 10))
        self.body.set_style_bg_opa(0, 0)
        self.body.set_style_border_width(0, 0)
        self.body.set_style_pad_all(padding, 0)
        self.body.remove_flag(lv.obj.FLAG.SCROLLABLE)

        self.body.set_flex_flow(lv.FLEX_FLOW.COLUMN)
        self.body.set_flex_align(
            lv.FLEX_ALIGN.SPACE_EVENLY,   # vertikal: Höhe der Kachel gleichmäßig nutzen
            _CROSS_ALIGN[align],          # horizontal: links/mittig/rechts
            lv.FLEX_ALIGN.CENTER,
        )

    def content_parent(self):
        """LVGL-Parent für Komponenten, die ihre eigenen Sub-Elemente
        anlegen (z.B. AirQualityLight, Equalizer). Da sie mit self.body
        als Parent erzeugt werden, reiht das Flex-Layout sie automatisch
        als weiteres Element ein - place() ist daher nur ein bequemer
        Rückgabe-Wrapper für lesbarere Aufrufstellen."""
        return self.body

    def add_title(self, text, font=None, color=None):
        """
        Überschrift im FESTEN Kopfbereich - IMMER in Großbuchstaben, damit
        nebeneinanderliegende Kacheln optisch eine gemeinsame Überschriften-
        Reihe bilden. Landet im Header, nicht im Flex-Rumpf - Position ist
        deshalb unabhängig vom sonstigen Kachel-Inhalt.
        """
        label = lv.label(self.header)
        label.set_text(to_ascii(text).upper())
        label.set_width(max(self.w - 2 * self.padding, 10))
        label.set_long_mode(lv_const.LABEL_LONG_WRAP)
        label.set_style_text_align(_TEXT_ALIGN[self.align], 0)
        if font:
            label.set_style_text_font(font, 0)
        if color:
            label.set_style_text_color(_hex(color), 0)
        return label

    def add_label(self, text="", font=None, color=None, wrap=True):
        """
        wrap=True (Standard): Label bekommt die verfügbare Kachelbreite
        (abzüglich Innenabstand) zugewiesen und bricht bei Bedarf in
        weitere Zeilen um, statt abgeschnitten zu werden oder einen
        Scrollbalken auszulösen - wichtig für dynamischen Text (News-
        Schlagzeilen, Zitate, Komplimente, ...), dessen Länge zur Laufzeit
        wechselt und nicht im Voraus auf eine Schriftgröße hin geplant
        werden kann. wrap=False für kurze, garantiert einzeilige Texte
        (z.B. Icons, feste Labels), wo die Standard-Zentrierung des
        Flex-Layouts ohne feste Breite besser aussieht.
        """
        label = lv.label(self.body)
        label.set_text(to_ascii(text))
        label.set_style_text_align(_TEXT_ALIGN[self.align], 0)
        if wrap:
            label.set_width(max(self.w - 2 * self.padding, 10))
            label.set_long_mode(lv_const.LABEL_LONG_WRAP)
        if font:
            label.set_style_text_font(font, 0)
        if color:
            label.set_style_text_color(_hex(color), 0)
        return label

    def place(self, component):
        return component
