"""
Domänen-Logik für die Luftqualitäts-Ampel: Schwellwerte für den Score aus
sensors/air_quality.py. Das Zeichnen (Kreis + Label) übernimmt die
generische widgets/traffic_light.TrafficLight, von der diese Klasse erbt.

Farbe UND Text werden bei jedem set_score()-Aufruf frisch aus COLORS/
STRINGS nachgeschlagen (nicht beim Import einmalig eingefroren) - so
übernehmen Theme- und Sprachwechsel automatisch die neuen Werte beim
nächsten refresh()-Zyklus, ganz ohne Screen-Neuaufbau.
"""

from theme import COLORS
from i18n import STRINGS
from widgets.traffic_light import TrafficLight

# Schwellwerte für den Score aus sensors/air_quality.py (0-100, höher = besser)
THRESHOLDS = {
    "good": 70,      # >= 70  -> grün
    "moderate": 40,  # 40-69  -> amber
    # < 40             -> rot
}


def score_to_level(score):
    """Reine Funktion, kein LVGL - beliebig testbar."""
    if score is None:
        return "unknown"
    if score >= THRESHOLDS["good"]:
        return "good"
    if score >= THRESHOLDS["moderate"]:
        return "moderate"
    return "bad"


# Zeigen auf COLORS/STRINGS-SCHLÜSSEL, nicht auf die Werte selbst - die
# eigentlichen Werte werden bei jedem Aufruf live nachgeschlagen (siehe oben)
LEVEL_COLOR_KEY = {"good": "up", "moderate": "amber", "bad": "red", "unknown": "fg_faint"}
LEVEL_TEXT_KEY = {"good": "level.good", "moderate": "level.moderate_air",
                   "bad": "level.bad", "unknown": "level.unknown"}


class AirQualityLight(TrafficLight):
    def set_score(self, score):
        level = score_to_level(score)
        self.set(COLORS[LEVEL_COLOR_KEY[level]], STRINGS[LEVEL_TEXT_KEY[level]])
