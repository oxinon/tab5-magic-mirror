"""
Zentrale Texte für alle Screens/Widgets. Gleiche Architektur wie theme.py:
STRINGS wird als EIN Dict-Objekt exportiert und bei einem Sprachwechsel nur
in-place verändert (`.clear()` + `.update()`), nicht neu zugewiesen - damit
bestehende `from i18n import STRINGS`-Importe automatisch aktuell bleiben.

WICHTIG (gleiche Einschränkung wie beim Theme): Texte, die nur EINMAL beim
Bau eines Screens gesetzt werden (z.B. Karten-Überschriften wie "KLIMA"),
ändern sich erst nach einem Neuaufbau des Screens. Texte, die bei jedem
refresh()-Zyklus neu gesetzt werden (z.B. Ampel-Status, Schalter-Zustand),
übernehmen die neue Sprache dagegen automatisch beim nächsten Refresh -
siehe die Kommentare in environment.py/room_dashboard.py, wo das jeweils
zutrifft.
"""

DE = {
    # Karten-Überschriften (environment.py) - werden nur beim Bau gesetzt,
    # brauchen für einen Sprachwechsel also einen Screen-Neuaufbau
    "card.climate": "RAUMKLIMA",
    "mm.climate_ext.default_title": "RAUMKLIMA EXT.",
    "mm.climate_ext.unavailable": "nicht erreichbar",
    "card.air_quality": "LUFTQUALITÄT",
    "card.acoustic": "AKUSTIK",
    "card.equalizer": "MIKROFON",
    "card.acceleration": "BESCHLEUNIGUNG",

    # Klima-Karte
    "source.local_bme688": "lokal (BME688)",
    "source.remote_tdisplay": "remote (T-Display-S3)",
    "source.unavailable": "nicht verfügbar: %s",

    # Luftqualität
    "iaq.calibrating": "kalibriere...",
    "iaq.value_line": "IAQ %.0f",
    "air_quality.value_line": "Score %.0f",
    "level.good": "GUT",
    "level.moderate_air": "MÄSSIG",
    "level.bad": "SCHLECHT",
    "level.unknown": "N/A",

    # Akustik
    "acoustic.value_line": "Pegel %.0f",
    "level.quiet": "RUHIG",
    "level.moderate_sound": "NORMAL",
    "level.loud": "LAUT",

    # Beschleunigung
    "accel.triggered": "ERSCHÜTTERUNG!",
    "accel.calm": "ruhig",
    "accel.magnitude": "|a| %.2fg",
    "accel.no_value": "--",
    "accel.ratio_caption": "Peak %.1fx",
    "accel.last_event": "Letztes Ereignis: vor %ds",
    "accel.no_events": "Noch kein Ereignis",

    # Room Dashboard (ha_switch)
    "switch.on": "AN",
    "switch.off": "AUS",
    "switch.na": "n/a",

    # Settings-Screen
    "settings.appearance": "DARSTELLUNG",
    "settings.brightness": "HELLIGKEIT",
    "settings.language": "SPRACHE",
    "settings.wifi": "WLAN",
    "wifi.mode": "Modus",
    "wifi.mode_sta": "Verbunden (Station)",
    "wifi.mode_ap": "Eigener Access Point",
    "wifi.mode_unknown": "Unbekannt",
    "wifi.ssid": "Netzwerk",
    "wifi.ip": "IP-Adresse",
    "wifi.signal": "Signal",
    "wifi.ap_password": "AP-Passwort",
    "wifi.edit_via_web_hint": "SSID/Passwort ändern: über das Web-UI unter /system (im eigenen Access Point \"Tab5-Setup\", falls kein bekanntes WLAN gefunden wurde, sonst unter der IP oben).",
    "menu.close": "Schließen",
    "theme.dark": "Dunkel",
    "theme.light": "Hell",
    "lang.de": "Deutsch",
    "lang.en": "Englisch",

    # Magic-Mirror-Screen - 1:1 aus mirror.js übernommen (I18N.de), damit
    # sich Web-Spiegel und Tab5-Spiegel textlich gleich verhalten
    "mm.cal.default_title": "Kalender",
    "mm.cal.no_events": "Keine anstehenden Termine",
    "mm.cal.unavailable": "Kalender nicht verfügbar",
    "mm.cal.unreachable": "Kalender nicht erreichbar",
    "mm.news.default_title": "Nachrichten",
    "mm.news.no_sources": "Keine Nachrichten verfügbar",
    "mm.news.no_items": "Keine Meldungen",
    "mm.news.unreachable": "Nachrichten nicht erreichbar",
    "mm.crypto.default_title": "Krypto",
    "mm.crypto.no_data": "Keine Kursdaten verfügbar",
    "mm.crypto.unreachable": "Kursdaten nicht erreichbar",
    "mm.weather.default_title": "Wetter",
    "mm.weather.feels_like": "gefühlt",
    "mm.weather.humidity": "Luftfeuchte",
    "mm.weather.wind": "Wind",
    "mm.weather.gusts": "Böen",
    "mm.weather.unavailable": "Wetter nicht verfügbar",
    "mm.weather.unreachable": "Wetter nicht erreichbar",
    "mm.stocks.default_title": "Aktienkurse",
    "mm.stocks.na": "n/a",
    "mm.quote.default_title": "Zitat des Tages",
    "mm.quote.unavailable": "Kein Zitat verfügbar",
    "mm.server.default_title": "Docker Status",
    "mm.pc_status.default_title": "Computer Status",
    "mm.server.summary": "%d von %d laufen",
    "mm.server.unavailable": "Server-Status nicht verfügbar",
    "mm.server.no_containers": "Keine Container gefunden",
    "mm.server.unreachable": "Server-Status nicht erreichbar",
    "mm.warn.default_title": "Warnungen",
    "mm.warn.none": "Keine aktuellen Warnungen",
    "mm.warn.unavailable": "Warnungen nicht verfügbar",
    "mm.warn.unreachable": "Warnungen nicht erreichbar",
    "mm.aqi.default_title": "Luftqualität",
    "mm.aqi.good": "Gut",
    "mm.aqi.ozone": "Ozon",
    "widget.licht.default_title": "LICHT",
    "widget.steckdose.default_title": "STECKDOSE",
    "mm.aqi.moderate": "Mäßig",
    "mm.aqi.poor": "Schlecht",
    "mm.aqi.unavailable": "Luftqualität nicht verfügbar",
    "mm.aqi.unreachable": "Luftqualität nicht erreichbar",
    "mm.pegel.default_title": "Elbe-Pegel",
    "mm.pegel.unavailable": "Pegelstand nicht verfügbar",
    "mm.pegel.unreachable": "Pegelstand nicht erreichbar",
    "mm.ews.default_title": "Apocalypse EWS",
    "mm.ews.stats": "%d Jets aktiv - erwartet %d - z %s",
    "mm.ews.unavailable": "EWS nicht verfügbar",
    "mm.ews.unreachable": "EWS nicht erreichbar",
    "mm.defcon.default_title": "DEFCON",
    "mm.defcon.unavailable": "DEFCON-Daten nicht verfügbar",
    "mm.defcon.no_regions": "Keine Regionen verfügbar",
    "mm.defcon.unreachable": "DEFCON-Daten nicht erreichbar",
    "mm.env.default_title": "Umweltstation",
    "mm.env.unavailable": "Sensordaten nicht verfügbar",
    "mm.env.unreachable": "Sensordaten nicht erreichbar",
    "mm.env.quake_alert": "Erkannt",
    "mm.env.quake_calm": "Ruhig",
    "mm.env.earthquake_label": "Erschütterung",
    "mm.env.temp_label": "Temp.",
    "mm.iaq.excellent": "Ausgezeichnet",
    "mm.iaq.good": "Gut",
    "mm.iaq.moderate": "Mittel",
    "mm.iaq.poor": "Schlecht",
    "mm.iaq.very_poor": "Sehr schlecht",
    "mm.compliments.default_title": "Komplimente",
    "mm.compliments.no_items": "Keine Komplimente hinterlegt",
    "mm.todo.default_title": "Notizen",
    "mm.todo.no_items": "Keine Einträge",
}

EN = {
    "card.climate": "ROOM CLIMATE",
    "mm.climate_ext.default_title": "ROOM CLIMATE EXT.",
    "mm.climate_ext.unavailable": "unavailable",
    "card.air_quality": "AIR QUALITY",
    "card.acoustic": "ACOUSTICS",
    "card.equalizer": "MICROPHONE",
    "card.acceleration": "ACCELERATION",

    "source.local_bme688": "local (BME688)",
    "source.remote_tdisplay": "remote (T-Display-S3)",
    "source.unavailable": "unavailable: %s",

    "iaq.calibrating": "calibrating...",
    "iaq.value_line": "IAQ %.0f",
    "air_quality.value_line": "Score %.0f",
    "level.good": "GOOD",
    "level.moderate_air": "MODERATE",
    "level.bad": "POOR",
    "level.unknown": "N/A",

    "acoustic.value_line": "Level %.0f",
    "level.quiet": "QUIET",
    "level.moderate_sound": "NORMAL",
    "level.loud": "LOUD",

    "accel.triggered": "SHAKING!",
    "accel.calm": "calm",
    "accel.magnitude": "|a| %.2fg",
    "accel.no_value": "--",
    "accel.ratio_caption": "Peak %.1fx",
    "accel.last_event": "Last event: %ds ago",
    "accel.no_events": "No events yet",

    "switch.on": "ON",
    "switch.off": "OFF",
    "switch.na": "n/a",

    "settings.appearance": "APPEARANCE",
    "settings.brightness": "BRIGHTNESS",
    "settings.language": "LANGUAGE",
    "settings.wifi": "WI-FI",
    "wifi.mode": "Mode",
    "wifi.mode_sta": "Connected (Station)",
    "wifi.mode_ap": "Own Access Point",
    "wifi.mode_unknown": "Unknown",
    "wifi.ssid": "Network",
    "wifi.ip": "IP Address",
    "wifi.signal": "Signal",
    "wifi.ap_password": "AP password",
    "wifi.edit_via_web_hint": "Change SSID/password via the web UI at /system (on its own access point \"Tab5-Setup\" if no known Wi-Fi was found, otherwise at the IP above).",
    "menu.close": "Close",
    "theme.dark": "Dark",
    "theme.light": "Light",
    "lang.de": "German",
    "lang.en": "English",

    "mm.cal.default_title": "Calendar",
    "mm.cal.no_events": "No upcoming events",
    "mm.cal.unavailable": "Calendar unavailable",
    "mm.cal.unreachable": "Calendar unreachable",
    "mm.news.default_title": "News",
    "mm.news.no_sources": "No news available",
    "mm.news.no_items": "No headlines",
    "mm.news.unreachable": "News unreachable",
    "mm.crypto.default_title": "Crypto",
    "mm.crypto.no_data": "No price data available",
    "mm.crypto.unreachable": "Price data unreachable",
    "mm.weather.default_title": "Weather",
    "mm.weather.feels_like": "feels like",
    "mm.weather.humidity": "Humidity",
    "mm.weather.wind": "Wind",
    "mm.weather.gusts": "gusts",
    "mm.weather.unavailable": "Weather unavailable",
    "mm.weather.unreachable": "Weather unreachable",
    "mm.stocks.default_title": "Stocks",
    "mm.stocks.na": "n/a",
    "mm.quote.default_title": "Quote of the Day",
    "mm.quote.unavailable": "No quote available",
    "mm.server.default_title": "Docker Status",
    "mm.pc_status.default_title": "Computer Status",
    "mm.server.summary": "%d of %d running",
    "mm.server.unavailable": "Server status unavailable",
    "mm.server.no_containers": "No containers found",
    "mm.server.unreachable": "Server status unreachable",
    "mm.warn.default_title": "Warnings",
    "mm.warn.none": "No current warnings",
    "mm.warn.unavailable": "Warnings unavailable",
    "mm.warn.unreachable": "Warnings unreachable",
    "mm.aqi.default_title": "Air Quality",
    "mm.aqi.good": "Good",
    "mm.aqi.ozone": "Ozone",
    "widget.licht.default_title": "LIGHT",
    "widget.steckdose.default_title": "OUTLET",
    "mm.aqi.moderate": "Moderate",
    "mm.aqi.poor": "Poor",
    "mm.aqi.unavailable": "Air quality unavailable",
    "mm.aqi.unreachable": "Air quality unreachable",
    "mm.pegel.default_title": "Elbe Water Level",
    "mm.pegel.unavailable": "Water level unavailable",
    "mm.pegel.unreachable": "Water level unreachable",
    "mm.ews.default_title": "Apocalypse EWS",
    "mm.ews.stats": "%d jets active - expected %d - z %s",
    "mm.ews.unavailable": "EWS unavailable",
    "mm.ews.unreachable": "EWS unreachable",
    "mm.defcon.default_title": "DEFCON",
    "mm.defcon.unavailable": "DEFCON data unavailable",
    "mm.defcon.no_regions": "No regions available",
    "mm.defcon.unreachable": "DEFCON data unreachable",
    "mm.env.default_title": "Environment Station",
    "mm.env.unavailable": "Sensor data unavailable",
    "mm.env.unreachable": "Sensor data unreachable",
    "mm.env.quake_alert": "Detected",
    "mm.env.quake_calm": "Calm",
    "mm.env.earthquake_label": "Earthquake",
    "mm.env.temp_label": "Temp.",
    "mm.iaq.excellent": "Excellent",
    "mm.iaq.good": "Good",
    "mm.iaq.moderate": "Moderate",
    "mm.iaq.poor": "Poor",
    "mm.iaq.very_poor": "Very poor",
    "mm.compliments.default_title": "Compliments",
    "mm.compliments.no_items": "No compliments configured",
    "mm.todo.default_title": "Notes",
    "mm.todo.no_items": "No entries",
}

# Startet Deutsch; set_lang() wechselt live (siehe Modul-Docstring)
#
# WICHTIG: STRINGS enthält NUR ASCII-transliterierten Text (siehe
# ascii_text.py) - die LVGL-Fonts auf dieser Firmware zeigen Umlaute/ß
# nicht an (Glyph fehlt im kompilierten Font, kein Absturz, der
# Buchstabe verschwindet einfach lautlos). Betrifft NUR die Tab5-Anzeige;
# web_server.py hat eigene, unveränderte Strings mit echten Umlauten fürs
# Web-UI (Browser stellen UTF-8 problemlos dar).
from ascii_text import to_ascii

STRINGS = {k: to_ascii(v) for k, v in DE.items()}
_current_lang = "de"


def set_lang(lang):
    """lang: "de" | "en". Verändert STRINGS in-place, damit bestehende
    `from i18n import STRINGS`-Importe den neuen Werten automatisch folgen."""
    global _current_lang
    table = EN if lang == "en" else DE
    STRINGS.clear()
    STRINGS.update({k: to_ascii(v) for k, v in table.items()})
    _current_lang = "en" if lang == "en" else "de"


def get_lang():
    return _current_lang
