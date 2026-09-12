"""
Domänen-Logik für die Akustik-Ampel (Schallpegel): Schwellwerte auf Basis
der 0-100-Pseudo-dB-Skala aus sensors/microphone.py. Gleiche Kreis-
Darstellung wie die Luftqualitäts-Ampel (siehe widgets/traffic_light.py).

Farbe UND Text werden bei jedem set_level()-Aufruf frisch aus COLORS/
STRINGS nachgeschlagen - siehe Docstring in widgets/air_quality_light.py
für die Begründung (Theme-/Sprachwechsel ohne Screen-Neuaufbau).
"""

from theme import COLORS
from i18n import STRINGS
from widgets.traffic_light import TrafficLight

THRESHOLDS = {
    "quiet": 40,     # < 40   -> grün (leise/normal fürs Meeting)
    "moderate": 70,  # 40-69  -> amber
    # >= 70              -> rot (laut)
}


def level_to_level(value):
    if value is None:
        return "unknown"
    if value < THRESHOLDS["quiet"]:
        return "quiet"
    if value < THRESHOLDS["moderate"]:
        return "moderate"
    return "loud"


LEVEL_COLOR_KEY = {"quiet": "up", "moderate": "amber", "loud": "red", "unknown": "fg_faint"}
LEVEL_TEXT_KEY = {"quiet": "level.quiet", "moderate": "level.moderate_sound",
                   "loud": "level.loud", "unknown": "level.unknown"}


class SoundLight(TrafficLight):
    def set_level(self, value):
        level = level_to_level(value)
        self.set(COLORS[LEVEL_COLOR_KEY[level]], STRINGS[LEVEL_TEXT_KEY[level]])
