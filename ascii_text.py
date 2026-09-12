"""
Wandelt deutsche Umlaute/ß und gängige Akzentzeichen (aus RSS-Feeds,
iCal-Terminen, o.ä.) in ASCII-Näherungen um - NUR für Text, der auf dem
Tab5-Display landet (LVGL/m5ui).

Hintergrund: Die auf dieser Firmware kompilierten LVGL-Fonts
(font_montserrat_16/24/... - siehe HANDOFF.md: font_montserrat_28
existiert in dieser Firmware-Konfiguration z.B. gar nicht erst) enthalten
offenbar nur den ASCII-Bereich (0x20-0x7E), keine Latin-1-Zusatzzeichen
wie Ä/Ö/Ü/ä/ö/ü/ß. LVGL zeigt für ein fehlendes Glyph nichts an (kein
Absturz, kein Platzhalter-Kästchen) - der Buchstabe verschwindet einfach
lautlos, was wie ein Darstellungsfehler aussieht.

Betrifft NUR die Tab5-Anzeige, NICHT das Web-UI (Browser stellen UTF-8
problemlos dar) - web_server.py bleibt daher bewusst unverändert mit
echten Umlauten in seinen HTML-Templates.

1:1 dasselbe Prinzip wie im Core2-MiniDash-Referenzprojekt des Nutzers
(widgets.py::_to_ascii/_TRANSLITERATE) - dort aus demselben Grund für
Newstitel eingesetzt, hier deutlich breiter angewendet (siehe
i18n.py/widget_sources.py/widgets/card.py), da die eingesetzten Fonts
hier noch eingeschränkter sind als M5.Lcd/DejaVu beim Core2.
"""

_TRANSLITERATE = {
    "ä": "ae", "ö": "oe", "ü": "ue",
    "Ä": "Ae", "Ö": "Oe", "Ü": "Ue",
    "ß": "ss",
    "é": "e", "è": "e", "ê": "e", "ë": "e",
    "á": "a", "à": "a", "â": "a",
    "í": "i", "ì": "i", "î": "i",
    "ó": "o", "ò": "o", "ô": "o",
    "ú": "u", "ù": "u", "û": "u",
    "ç": "c", "ñ": "n",
    "É": "E", "È": "E", "Á": "A", "À": "A", "Ó": "O", "Ú": "U",
    "’": "'", "‘": "'", "“": '"', "”": '"',
    "–": "-", "—": "-", "…": "...",
    # Sicherheitsnetz für Zeichen, die zwar nicht aus RSS/Kalender/etc.
    # kommen, aber im Code selbst schon mal versehentlich als Trenner/
    # Symbol verwendet wurden und auf dieser Firmware als leere Kästchen
    # dargestellt werden (siehe HANDOFF.md-Historie: nur ASCII 0x20-0x7E
    # wird unterstützt) - z.B. der Mittelpunkt-Trenner "·", das Ohm-Zeichen
    # "Ω" oder Pfeile/Kreise für Trend-/Status-Anzeigen.
    "·": "-", "Ω": "Ohm", "↑": "+", "↓": "-", "●": "*", "○": "-",
    "€": "EUR", "£": "GBP", "¥": "JPY",
}


def to_ascii(s):
    """Ersetzt bekannte Sonderzeichen, alles andere Nicht-ASCII wird zu
    "?" (statt es einfach unsichtbar verschwinden zu lassen - "?" macht
    wenigstens sichtbar, dass dort ein Zeichen fehlt)."""
    if not s:
        return s
    for k, v in _TRANSLITERATE.items():
        if k in s:
            s = s.replace(k, v)
    out = []
    for ch in s:
        out.append(ch if ord(ch) < 128 else "?")
    return "".join(out)
