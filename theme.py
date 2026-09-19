"""
Zentrale Design-Tokens. COLORS wird als EIN Dict-Objekt exportiert und bei
einem Theme-Wechsel nur in-place verändert (COLORS.clear() + .update()),
nicht neu zugewiesen - so bleiben alle bestehenden `from theme import
COLORS`-Importe in den Widget-Dateien automatisch aktuell, ohne dass jede
einzelne Datei stattdessen `theme.COLORS` qualifiziert referenzieren müsste.

WICHTIG (unverifiziert, TODO auf Hardware prüfen): Ein Theme-Wechsel färbt
nur NEU erzeugte Widgets um, weil LVGL Farben beim Erzeugen einmalig setzt
(set_style_bg_color etc.) statt sie laufend aus COLORS nachzuschlagen. Um
einen bereits sichtbaren Screen umzufärben, muss er aktuell neu aufgebaut
werden (siehe screens/settings.py) - eine "live" Umfärbung ohne Rebuild
wäre ein zusätzlicher Ausbauschritt für später.
"""

DARK = {
    "bg": "#000000",
    "fg": "#eef0f2",
    "fg_dim": "#8b8f96",
    "fg_faint": "#52555b",
    "accent": "#c9a15a",
    "accent_dim": "#7d6738",
    "up": "#7fae7a",
    "down": "#b96a5a",
    "amber": "#f5b942",
    "red": "#ff5555",
}

LIGHT = {
    "bg": "#f5f3ef",
    "fg": "#1c1b19",
    "fg_dim": "#5a5852",
    "fg_faint": "#8f8c84",
    "accent": "#9c7a3c",      # gedämpfteres Messing, damit es auf hellem Grund nicht grell wirkt
    "accent_dim": "#c9b183",
    "up": "#4f8a49",
    "down": "#a14a3a",
    "amber": "#c98f1d",
    "red": "#d13f3f",
}

# Startet dunkel (Original-Magic-Mirror-Optik); set_mode() wechselt live
COLORS = dict(DARK)
_current_mode = "dark"

# Verfügbare LVGL/Montserrat-Größen auf der Tab5-Firmware (siehe Handoff-Doku)
FONT_SIZES = [12, 14, 16, 18, 20, 22, 24, 30, 36, 40, 44, 48]


def set_mode(mode):
    """mode: "dark" | "light". Verändert COLORS in-place (siehe Modul-
    Docstring oben), damit bestehende `from theme import COLORS`-Importe
    in den Widget-Dateien den neuen Werten automatisch folgen."""
    global _current_mode
    palette = LIGHT if mode == "light" else DARK
    COLORS.clear()
    COLORS.update(palette)
    _current_mode = "light" if mode == "light" else "dark"


def get_mode():
    return _current_mode
