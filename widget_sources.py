"""
Direkte API-Anbindungen für die Magic-Mirror-Widgets - ersetzt den
früheren Umweg über einen separaten Flask-Proxy-Server (siehe
api_client.py-Historie/HANDOFF.md: kein solcher Server mehr geplant,
alles läuft möglichst autark direkt auf dem Tab5). 1:1 portiert aus dem
vom Nutzer bereitgestellten Core2-MiniDash-Referenzprojekt (widgets.py) -
dieselben kostenlosen, KEY-LOSEN APIs (Ausnahme: DEFCON braucht einen
selbst betriebenen Endpunkt, optional mit eigenem Key):
  - Wetter: Open-Meteo
  - Luftqualität (Außenluft): Open-Meteo
  - Warnungen: warnung.bund.de (NINA/BBK)
  - News: RSS/Atom-Feeds (eigener, einfacher XML-Tag-Extraktor, da
    MicroPython keinen XML-Parser mitbringt)
  - Krypto: CoinGecko
  - Aktien: Yahoo-Finance-Chart-Endpunkt
  - Zitat des Tages: ZenQuotes
  - EWS: ews.kylemcdonald.net öffentlicher JSON-Snapshot
  - DEFCON: nutzereigener JSON-Endpunkt (z.B. ai-defcon.com)
  - Elbe-Pegel: PEGELONLINE/WSV REST-API
  - Kalender: private iCal-URL (eigener ICS-Parser, keine RRULE-Expansion)

Jede fetch_*(widget_cfg)-Funktion nimmt DIREKT das Widget-Config-Dict aus
config.json ("screens.dashboard.widgets[]") entgegen (anders als im
Core2-Original, das ein verschachteltes cfg["widgets"][...] nutzt) und
gibt ein Dict mit mindestens "ok" (bool) zurück - bei Erfolg die
jeweiligen Datenfelder, bei Fehlschlag "msg" mit einer kurzen
Fehlerbeschreibung. Wird von screens/widget_catalog.py in den jeweiligen
_fetch_<kind>()-Methoden aufgerufen.
"""

try:
    import urequests as requests
except ImportError:
    import requests  # Desktop-Fallback für Tests ohne MicroPython

import sys

from ascii_text import to_ascii

# Deutsche Kurzbeschreibungen statt der englischen Originale aus dem
# Core2-Projekt, damit es zur restlichen (deutschen) Oberfläche passt.
WEATHER_CODES = {
    0: ("Klar", "clear"), 1: ("Meist klar", "clear"),
    2: ("Teilweise bewölkt", "cloudy"), 3: ("Bedeckt", "cloudy"),
    45: ("Nebel", "fog"), 48: ("Reifnebel", "fog"),
    51: ("Leichter Nieselregen", "rain"), 53: ("Nieselregen", "rain"), 55: ("Dichter Nieselregen", "rain"),
    56: ("Gefrierender Nieselregen", "rain"), 57: ("Gefrierender Nieselregen", "rain"),
    61: ("Leichter Regen", "rain"), 63: ("Regen", "rain"), 65: ("Starker Regen", "rain"),
    66: ("Gefrierender Regen", "rain"), 67: ("Gefrierender Regen", "rain"),
    71: ("Leichter Schnee", "snow"), 73: ("Schnee", "snow"), 75: ("Starker Schnee", "snow"),
    77: ("Schneegriesel", "snow"),
    80: ("Regenschauer", "rain"), 81: ("Regenschauer", "rain"), 82: ("Heftige Schauer", "rain"),
    85: ("Schneeschauer", "snow"), 86: ("Schneeschauer", "snow"),
    95: ("Gewitter", "storm"), 96: ("Gewitter mit Hagel", "storm"), 99: ("Schweres Gewitter", "storm"),
}


def _get_json(url, timeout=10, headers=None):
    r = requests.get(url, timeout=timeout, headers=headers) if headers else requests.get(url, timeout=timeout)
    try:
        return r.json()
    finally:
        try:
            r.close()
        except Exception:
            pass


# ---------------------------------------------------------------------
# Orts-Suche fürs Web-UI (Standort für Wetter/Luftqualität, Regional-
# schlüssel für Warnungen) - kein API-Key nötig.
# ---------------------------------------------------------------------
def geocode_search(query):
    """Standortsuche für Wetter/Luftqualität (Open-Meteo-Geocoding)."""
    url = "https://geocoding-api.open-meteo.com/v1/search?name=%s&count=5&language=de&format=json" % query
    data = _get_json(url)
    results = []
    for res in data.get("results", []) or []:
        parts = [res.get("name")]
        if res.get("admin1"):
            parts.append(res["admin1"])
        if res.get("country"):
            parts.append(res["country"])
        results.append({
            "label": ", ".join(p for p in parts if p),
            "latitude": res.get("latitude"),
            "longitude": res.get("longitude"),
        })
    return results


def ags_search(query):
    """Ortssuche für NINA-Warnungen (Amtlicher Regionalschlüssel). Stadt-
    staaten (Hamburg/Berlin/Bremen) haben keinen eigenen Kreis-Eintrag in
    der API-Antwort - fällt in dem Fall auf den Gemeindeschlüssel zurück."""
    is_plz = query.isdigit() and len(query) == 5
    param = "postalCode" if is_plz else "name"
    url = "https://openplzapi.org/de/Localities?%s=%s" % (param, query)
    data = _get_json(url)
    results = []
    seen = set()
    for loc in data if isinstance(data, list) else []:
        district = loc.get("district") or {}
        municipality = loc.get("municipality") or {}
        federal_state = loc.get("federalState") or {}
        key_source = district.get("key") or municipality.get("key")
        if not key_source:
            continue
        ars = key_source + "0" * (12 - len(key_source))
        if ars in seen:
            continue
        seen.add(ars)
        area_name = district.get("name") or municipality.get("name")
        parts = [loc.get("name"), area_name, federal_state.get("name")]
        results.append({"label": ", ".join(p for p in parts if p), "ars": ars})
        if len(results) >= 8:
            break
    return results


# ---------------------------------------------------------------------
# Wetter (Open-Meteo, kein API-Key nötig)
# ---------------------------------------------------------------------
def fetch_weather(widget_cfg):
    """widget_cfg (aus config.json) braucht: "latitude"/"longitude"
    (float), optional "units" ("celsius"|"fahrenheit", Default celsius)
    und "location" (nur als Anzeigename, nicht für die Abfrage nötig)."""
    lat, lon = widget_cfg.get("latitude"), widget_cfg.get("longitude")
    if lat is None or lon is None:
        return {"ok": False, "msg": "Kein Standort konfiguriert (Breiten-/Längengrad fehlt)"}
    units = widget_cfg.get("units", "celsius")
    temp_unit = "fahrenheit" if units == "fahrenheit" else "celsius"
    url = (
        "https://api.open-meteo.com/v1/forecast?latitude={}&longitude={}"
        "&current=temperature_2m,apparent_temperature,relative_humidity_2m,"
        "wind_speed_10m,wind_gusts_10m,weather_code"
        "&temperature_unit={}&wind_speed_unit=kmh&timezone=auto"
    ).format(lat, lon, temp_unit)
    try:
        data = _get_json(url)
        cur = data.get("current", {})
        code = cur.get("weather_code", 0)
        desc, group = WEATHER_CODES.get(int(code), ("Unbekannt", "cloudy"))
        return {
            "ok": True,
            "location": to_ascii(widget_cfg.get("location", "")),
            "group": group,
            "description": to_ascii(desc),
            "temperature": cur.get("temperature_2m"),
            "feels_like": cur.get("apparent_temperature"),
            "humidity": cur.get("relative_humidity_2m"),
            "wind": cur.get("wind_speed_10m"),
            "gusts": cur.get("wind_gusts_10m"),
            "unit": "F" if units == "fahrenheit" else "C",
        }
    except Exception as e:
        return {"ok": False, "msg": "Wetter-Fehler: %s" % e}


# ---------------------------------------------------------------------
# News (RSS/Atom, kein API-Key nötig) - eigener, sehr einfacher XML-Tag-
# Extraktor statt eines echten XML-Parsers (den MicroPython nicht
# mitbringt), reicht für die flache Struktur von RSS 2.0.
# ---------------------------------------------------------------------
# Pro Widget-id ein eigener Rotations-Zähler, damit mehrere News-Kacheln
# mit unterschiedlichen Quellen unabhängig voneinander durchrotieren.
_news_source_idx = {}


def _tag_content(text, tag, start=0):
    open_tag = "<" + tag
    close_tag = "</" + tag + ">"
    i = text.find(open_tag, start)
    if i == -1:
        return None, -1
    i = text.find(">", i)
    if i == -1:
        return None, -1
    i += 1
    j = text.find(close_tag, i)
    if j == -1:
        return None, -1
    return text[i:j], j + len(close_tag)


def _clean_xml_text(s):
    s = s.strip()
    if s.startswith("<![CDATA[") and s.endswith("]]>"):
        s = s[9:-3]
    s = s.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    s = s.replace("&quot;", '"').replace("&#39;", "'").replace("&apos;", "'")
    return to_ascii(s.strip())


def fetch_news(widget_cfg):
    """widget_cfg braucht: "id" (fürs unabhängige Rotieren mehrerer
    Widgets), "sources" (Liste von {"name": .., "feedUrl": ..}), optional
    "max_items" (Default 5). Holt bei jedem Aufruf EINE Quelle (reihum
    wechselnd, wie im Original-Docker-Projekt) statt alle gleichzeitig -
    einfacher und schneller als mehrere Feeds pro Zyklus zu laden."""
    widget_id = widget_cfg.get("id", "news")
    sources = [s for s in (widget_cfg.get("sources") or []) if (s.get("feedUrl") or "").strip()]
    if not sources:
        return {"ok": False, "msg": "Keine Quelle konfiguriert", "items": [], "source_name": ""}

    idx = _news_source_idx.get(widget_id, 0) % len(sources)
    _news_source_idx[widget_id] = idx + 1
    src = sources[idx]
    name = src.get("name") or "News"
    url = src["feedUrl"].strip()
    max_items = int(widget_cfg.get("max_items", 5) or 5)

    try:
        r = requests.get(url, timeout=12)
        text = r.text
        try:
            r.close()
        except Exception:
            pass
        items = []
        pos = 0
        while len(items) < max_items:
            block, next_pos = _tag_content(text, "item", pos)
            if block is None:
                break
            title, _ = _tag_content(block, "title")
            if title:
                items.append(_clean_xml_text(title))
            pos = next_pos
        return {"ok": True, "items": items, "source_name": to_ascii(name)}
    except Exception as e:
        return {"ok": False, "msg": "News-Fehler: %s" % e, "items": [], "source_name": to_ascii(name)}


# ---------------------------------------------------------------------
# Luftqualität (Außenluft, Open-Meteo - unabhängig vom lokalen BME688-
# "air_quality"-Widget, das die Innenraumluft misst)
# ---------------------------------------------------------------------
def fetch_air_quality(widget_cfg):
    lat, lon = widget_cfg.get("latitude"), widget_cfg.get("longitude")
    if lat is None or lon is None:
        return {"ok": False, "msg": "Kein Standort konfiguriert (Breiten-/Längengrad fehlt)"}
    url = (
        "https://air-quality-api.open-meteo.com/v1/air-quality?latitude={}"
        "&longitude={}&current=european_aqi,pm10,pm2_5,ozone&timezone=auto"
    ).format(lat, lon)
    try:
        data = _get_json(url)
        cur = data.get("current", {})
        return {
            "ok": True,
            "location": to_ascii(widget_cfg.get("location", "")),
            "aqi": cur.get("european_aqi"),
            "pm10": cur.get("pm10"),
            "pm25": cur.get("pm2_5"),
            "ozone": cur.get("ozone"),
        }
    except Exception as e:
        return {"ok": False, "msg": "Luftqualität-Fehler: %s" % e}


# ---------------------------------------------------------------------
# Amtliche Warnungen (warnung.bund.de / NINA-BBK, kein API-Key nötig)
# ---------------------------------------------------------------------
def fetch_warnings(widget_cfg):
    """widget_cfg braucht "ars" (Amtlicher Regionalschlüssel, 12-stellig -
    z.B. über die Gemeindesuche auf warnung.bund.de ermitteln)."""
    ars = (widget_cfg.get("ars") or "").strip()
    if not ars:
        return {"ok": False, "msg": "Kein Regionalschlüssel (ARS) konfiguriert", "warnings": []}
    url = "https://warnung.bund.de/api31/dashboard/%s.json" % ars
    try:
        data = _get_json(url)
        warnings = []
        for item in data if isinstance(data, list) else []:
            payload = item.get("payload", {}) or {}
            pdata = payload.get("data", {}) or {}
            title = pdata.get("headline") or "Warnung"
            sev_raw = pdata.get("severity", "")
            severity = "critical" if sev_raw in ("Severe", "Extreme") else "moderate"
            warnings.append({"title": to_ascii(title), "severity": severity})
        return {"ok": True, "warnings": warnings}
    except Exception as e:
        return {"ok": False, "msg": "Warnungen-Fehler: %s" % e, "warnings": []}


# ---------------------------------------------------------------------
# Krypto-Kurse (CoinGecko, kein API-Key nötig)
# ---------------------------------------------------------------------
def fetch_crypto(widget_cfg):
    """widget_cfg braucht "symbols" (Liste von CoinGecko-IDs, z.B.
    ["bitcoin","ethereum"] - NICHT die Börsenkürzel), optional "currency"
    (Default "usd")."""
    symbols = widget_cfg.get("symbols") or []
    currency = (widget_cfg.get("currency") or "usd").lower()
    if not symbols:
        return {"ok": False, "msg": "Keine Coins konfiguriert", "prices": []}

    ids = ",".join(symbols)
    url = ("https://api.coingecko.com/api/v3/simple/price?ids=%s&vs_currencies=%s"
           "&include_24hr_change=true") % (ids, currency)
    try:
        # CoinGecko blockt Anfragen ohne "beschreibenden" User-Agent mit 403.
        r = requests.get(url, timeout=12, headers={"User-Agent": "Tab5MagicMirror/1.0 (MicroPython; ESP32)"})
        data = r.json()
        try:
            r.close()
        except Exception:
            pass
        prices = []
        for sym in symbols:
            entry = data.get(sym, {}) or {}
            prices.append({
                "id": sym,
                "ticker": sym[:4].upper(),
                "price": entry.get(currency),
                "change24h": entry.get(currency + "_24h_change"),
                "currency": currency,
            })
        return {"ok": True, "prices": prices}
    except Exception as e:
        return {"ok": False, "msg": "Krypto-Fehler: %s" % e, "prices": []}


# ---------------------------------------------------------------------
# Aktienkurse (Yahoo-Finance-Chart-Endpunkt, kein API-Key nötig)
# ---------------------------------------------------------------------
CURRENCY_SYMBOLS = {
    # ASCII-Symbole, wo es welche gibt ($ ist schon ASCII) - bei
    # Euro/Pfund/Yen bewusst KEIN Unicode-Symbol (€/£/¥), da die LVGL-
    # Fonts auf dieser Firmware nur ASCII (0x20-0x7E) unterstützen (siehe
    # ascii_text.py) - hier wird aber direkt der Ländercode statt eines
    # Symbols gezeigt, da diese Werte nicht durch ascii_text.to_ascii()
    # laufen (Card.add_label() deckt nur Text ab, der bei Kachel-Aufbau
    # gesetzt wird, nicht bei jedem Refresh direkt gesetzten Text).
    "USD": "$", "EUR": "EUR", "GBP": "GBP", "GBp": "GBP", "CHF": "CHF",
    "JPY": "JPY", "CAD": "C$", "AUD": "A$", "HKD": "HK$",
}


def fetch_stocks(widget_cfg):
    """widget_cfg braucht "symbols" (Yahoo-Finance-Ticker, z.B.
    ["AAPL","SAP.DE"])."""
    symbols = widget_cfg.get("symbols") or []
    if not symbols:
        return {"ok": False, "msg": "Keine Symbole konfiguriert", "prices": []}

    prices = []
    for sym in symbols:
        url = "https://query1.finance.yahoo.com/v8/finance/chart/%s?range=5d&interval=1d" % sym
        try:
            r = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0 (compatible; Tab5MagicMirror/1.0)"})
            data = r.json()
            try:
                r.close()
            except Exception:
                pass
            result_list = ((data.get("chart") or {}).get("result")) or [None]
            result = result_list[0]
            if not result:
                prices.append({"ticker": sym.upper(), "price": None, "change24h": None, "currency": ""})
                continue
            meta = result.get("meta", {}) or {}
            price = meta.get("regularMarketPrice")
            prev_close = meta.get("previousClose") or meta.get("chartPreviousClose")
            change_pct = ((price - prev_close) / prev_close * 100) if (price is not None and prev_close) else None
            currency_code = meta.get("currency") or ""
            prices.append({
                "ticker": (meta.get("symbol") or sym).upper(),
                "price": price,
                "change24h": change_pct,
                "currency": CURRENCY_SYMBOLS.get(currency_code, currency_code),
            })
        except Exception as e:
            prices.append({"ticker": sym.upper(), "price": None, "change24h": None, "currency": "", "msg": str(e)})
    return {"ok": True, "prices": prices}


# ---------------------------------------------------------------------
# Zitat des Tages (ZenQuotes, kein API-Key nötig)
# ---------------------------------------------------------------------
def fetch_quote(widget_cfg):
    try:
        data = _get_json("https://zenquotes.io/api/today")
        entry = data[0] if isinstance(data, list) and data else {}
        text = entry.get("q")
        author = entry.get("a")
        if not text:
            return {"ok": False, "msg": "Kein Zitat erhalten"}
        return {"ok": True, "text": to_ascii(text), "author": to_ascii(author or "")}
    except Exception as e:
        return {"ok": False, "msg": "Zitat-Fehler: %s" % e}


# ---------------------------------------------------------------------
# Apocalypse EWS (öffentlicher JSON-Snapshot, kein API-Key nötig)
# ---------------------------------------------------------------------
EWS_DASHBOARD_URL = "https://pub-49bb6a6f314c47be9b481c25e5f6ca9e.r2.dev/dashboard.json"


def fetch_ews(widget_cfg):
    try:
        data = _get_json(EWS_DASHBOARD_URL, timeout=15)
        cur = data.get("current", {}) or {}
        return {
            "ok": True,
            "emergency_level": cur.get("emergencyLevel"),
            "alert_level": cur.get("alertLevel"),
            "concurrent_count": cur.get("concurrentCount"),
            "baseline_mean": cur.get("baselineMean"),
            "z_score": cur.get("zScore"),
        }
    except Exception as e:
        return {"ok": False, "msg": "EWS nicht erreichbar: %s" % e}


# ---------------------------------------------------------------------
# DEFCON (nutzereigener JSON-Endpunkt, z.B. ai-defcon.com - optionaler
# API-Key als Header)
# ---------------------------------------------------------------------
def _defcon_severity(value):
    """DEFCON-Prinzip: kleinere Zahl = kritischer."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "normal"
    if v <= 2:
        return "critical"
    if v <= 3.5:
        return "warning"
    return "normal"


def fetch_defcon(widget_cfg):
    url = (widget_cfg.get("url") or "").strip()
    api_key = (widget_cfg.get("api_key") or "").strip()
    if not url:
        return {"ok": False, "msg": "Keine Quell-URL konfiguriert", "regions": []}
    try:
        headers = {"X-API-Key": api_key} if api_key else {}
        data = _get_json(url, headers=headers)
        regions = []
        for entry in data.get("regions", []) or []:
            name = entry.get("name")
            value = entry.get("value")
            if not name or value is None:
                continue
            regions.append({"name": to_ascii(name), "value": value, "severity": _defcon_severity(value)})
        return {"ok": True, "regions": regions}
    except Exception as e:
        return {"ok": False, "msg": "DEFCON-Fehler: %s" % e, "regions": []}


# ---------------------------------------------------------------------
# Elbe-Pegel (PEGELONLINE/WSV REST-API, kein API-Key nötig, Lizenz
# DL-DE->Zero-2.0). Anders als bei den übrigen Quellen wird hier NICHT
# mit einer fest hinterlegten Stations-UUID gearbeitet (die pro Pegel
# unterschiedlich und schwer im Voraus zu verifizieren ist), sondern die
# Stationsliste der Elbe nach "station_name" durchsucht (Groß-/Klein-
# schreibung egal, Teilstring reicht, z.B. "ST. PAULI") - etwas mehr
# Datenverkehr pro Abruf, dafür robust ohne eine möglicherweise falsche
# UUID hart zu codieren.
# ---------------------------------------------------------------------
def fetch_elbe_pegel(widget_cfg):
    station_name = (widget_cfg.get("station_name") or "ST. PAULI").strip().upper()
    url = ("https://www.pegelonline.wsv.de/webservices/rest-api/v2/stations.json"
           "?waters=ELBE&includeTimeseries=true&includeCurrentMeasurement=true")
    try:
        stations = _get_json(url, timeout=15)
        match = None
        for st in stations if isinstance(stations, list) else []:
            name = (st.get("longname") or st.get("shortname") or "").upper()
            if station_name in name:
                match = st
                break
        if match is None:
            return {"ok": False, "msg": "Pegel '%s' nicht gefunden" % station_name}

        water_series = None
        for ts in match.get("timeseries", []) or []:
            if ts.get("shortname") == "W":
                water_series = ts
                break
        current = (water_series or {}).get("currentMeasurement") or {}
        if "value" not in current:
            return {"ok": False, "msg": "Kein aktueller Messwert verfügbar"}

        return {
            "ok": True,
            "station_name": to_ascii(match.get("longname") or match.get("shortname") or station_name),
            "value_cm": current.get("value"),
            "unit": (water_series or {}).get("unit", "cm"),
            "trend": current.get("trend"),
            "timestamp": current.get("timestamp"),
        }
    except Exception as e:
        return {"ok": False, "msg": "Pegel-Fehler: %s" % e}


# ---------------------------------------------------------------------
# Kalender (private iCal-URL, read-only, kein OAuth/API-Key). MicroPython
# hat keine icalendar-Bibliothek - eigener, schlanker ICS-Zeilen-Parser.
# RRULE-Wiederholungen werden NICHT expandiert (wie im Core2-Original).
# ---------------------------------------------------------------------
def _unfold_ics(text):
    lines = text.replace("\r\n", "\n").split("\n")
    out = []
    for line in lines:
        if line and (line[0] == " " or line[0] == "\t") and out:
            out[-1] += line[1:]
        else:
            out.append(line)
    return out


def _ics_unescape(s):
    return (s.replace("\\n", " ").replace("\\N", " ")
             .replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\"))


def _ics_split_line(line):
    if ":" not in line:
        return None, "", None
    left, value = line.split(":", 1)
    parts = left.split(";")
    return parts[0], ";".join(parts[1:]), value


def _parse_ics_dt(value, params):
    import time as _time
    value = value.strip()
    try:
        is_date_only = "VALUE=DATE" in params or (len(value) == 8 and value.isdigit())
        if is_date_only:
            y, mo, dd = int(value[0:4]), int(value[4:6]), int(value[6:8])
            return _time.mktime((y, mo, dd, 0, 0, 0, 0, 0)), True
        is_utc = value.endswith("Z")
        v = value[:-1] if is_utc else value
        y, mo, dd = int(v[0:4]), int(v[4:6]), int(v[6:8])
        hh, mi, ss = int(v[9:11]), int(v[11:13]), int(v[13:15])
        return _time.mktime((y, mo, dd, hh, mi, ss, 0, 0)), False
    except Exception:
        return None


def fetch_calendar(widget_cfg):
    """widget_cfg braucht "ical_url" (private iCal-Adresse, z.B. der
    "geheime" Google-Kalender-Link), optional "max_events" (Default 5),
    "days_ahead" (Default 14)."""
    import time as _time
    url = (widget_cfg.get("ical_url") or "").strip()
    if not url:
        return {"ok": False, "msg": "Keine iCal-URL konfiguriert", "events": []}

    max_events = int(widget_cfg.get("max_events", 5) or 5)
    days_ahead = int(widget_cfg.get("days_ahead", 14) or 14)

    try:
        r = requests.get(url, timeout=15)
        text = r.text
        try:
            r.close()
        except Exception:
            pass
        lines = _unfold_ics(text)

        now = _time.time()
        window_start = now - 20 * 3600
        horizon = now + days_ahead * 86400

        events = []
        in_event = False
        cur = {}
        for line in lines:
            if line.startswith("BEGIN:VEVENT"):
                in_event = True
                cur = {}
                continue
            if line.startswith("END:VEVENT"):
                in_event = False
                dt = cur.get("dtstart")
                if dt is not None:
                    start, all_day = dt
                    if window_start <= start <= horizon:
                        events.append({"title": cur.get("summary", "Ohne Titel"),
                                       "start": start, "all_day": all_day})
                continue
            if not in_event:
                continue
            key, params, value = _ics_split_line(line)
            if key == "SUMMARY":
                cur["summary"] = to_ascii(_ics_unescape(value))
            elif key == "DTSTART":
                parsed = _parse_ics_dt(value, params)
                if parsed is not None:
                    cur["dtstart"] = parsed

        events.sort(key=lambda e: e["start"])
        return {"ok": True, "events": events[:max_events]}
    except Exception as e:
        return {"ok": False, "msg": "Kalender-Fehler: %s" % e, "events": []}


# ---------------------------------------------------------------------
# Externes Raumklima (z.B. Core2-MiniDash mit eigenem BME688) - fragt
# dessen Web-API ab. Feldnamen bestätigt aus dem tatsächlichen Quellcode
# des Referenzprojekts (webserver.py::updateStatus() JS - GET /api/status
# liefert {"bme680": {"ok", "temperature", "humidity", "pressure", "gas"},
# "iaq": {"ok", "score", "confidence", "mode"}, ...}, siehe live_state.py
# für die Grundstruktur).
# ---------------------------------------------------------------------
def fetch_climate_ext(widget_cfg):
    """widget_cfg braucht "base_url" (z.B. "http://192.168.1.30")."""
    base_url = (widget_cfg.get("base_url") or "").rstrip("/")
    if not base_url:
        return {"ok": False, "msg": "Keine IP-Adresse konfiguriert"}
    try:
        data = _get_json(base_url + "/api/status", timeout=8)
        bme = data.get("bme680") or {}
        iaq = data.get("iaq") or {}
        ok = bool(bme.get("ok", False))
        temp_c = bme.get("temperature")
        humidity = bme.get("humidity")
        pressure_hpa = bme.get("pressure")
        iaq_score = iaq.get("score")
        msg = bme.get("msg") or iaq.get("msg")
        if not ok and temp_c is None:
            msg = msg or "Sensor am externen Gerät nicht verfügbar"
        return {"ok": ok, "msg": msg, "temp_c": temp_c, "humidity": humidity,
                "pressure_hpa": pressure_hpa, "iaq_score": iaq_score}
    except Exception as e:
        return {"ok": False, "msg": "Externes Raumklima nicht erreichbar: %s" % e}


# ---------------------------------------------------------------------
# PC-Systemstatus (CPU/GPU) - fragt denselben "/sse"-Endpunkt ab wie der
# echte Waveshare-ESP32-S3-Knob, siehe
# github.com/oxinon/knob-esp32s3-aida-sse-server-linux (PROTOCOL.md).
# WICHTIG: Trotz "text/event-stream"-Header ist das laut Projekt-Doku KEIN
# echtes SSE-Streaming - der Server antwortet einmalig pro Anfrage mit
# einer einzelnen "data: ..."-Zeile, genau wie eine normale HTTP-Antwort.
# Format: "data: Page0|{|}Simple1|CPU usage 17^{|}Simple2|CPU freq 1600^{|}..."
# - Positionen sind FEST (nicht per ID), in exakt dieser Reihenfolge:
# 1=CPU usage, 2=CPU freq, 3=CPU temp, 4=CPU fan, 5=GPU usage, 6=GPU freq,
# 7=GPU temp, 8=GPU fan. Ein Slot kann leer sein (z.B. keine GPU-Auslastung
# gemeldet) oder einen nicht-numerischen Platzhalter enthalten (z.B.
# "GPU temp TRIAL" bei einer AIDA64-Testversion auf dem PC) - beides wird
# hier als "kein Wert" (None) behandelt statt eines Absturzes.
# ---------------------------------------------------------------------
_PC_STATUS_SLOTS = ["cpu_usage", "cpu_freq", "cpu_temp", "cpu_fan",
                    "gpu_usage", "gpu_freq", "gpu_temp", "gpu_fan"]


def fetch_pc_status(widget_cfg):
    """widget_cfg braucht "base_url" (z.B. "http://192.168.1.50:80").

    Nutzt einen EIGENEN, rohen Socket statt urequests: der Server sendet
    laut PROTOCOL.md des Projekts bewusst nur "\\n" statt korrektem
    "\\r\\n" als Zeilenumbruch (damit die echte Knob-Firmware klarkommt,
    die strikt nach "\\n\\n" scannt) - Browser/curl sind damit tolerant,
    urequests dagegen nicht: dessen Status-Zeilen-Parsing (`proto, status,
    msg = line.split(None, 2)`) bricht mit "need more than 1 values to
    unpack", noch bevor unser eigener Code überhaupt drankommt (bestätigt
    per vollständigem Traceback). Ein roher Socket macht keine Annahmen
    über Zeilenumbrüche - wir lesen einfach alle Bytes und trennen den
    Body selbst ab, tolerant gegenüber "\\n\\n" UND "\\r\\n\\r\\n"."""
    base_url = (widget_cfg.get("base_url") or "").strip()
    if not base_url:
        return {"ok": False, "msg": "Keine IP-Adresse konfiguriert"}

    host_port = base_url.split("://", 1)[-1].split("/", 1)[0]
    if ":" in host_port:
        host, port_str = host_port.split(":", 1)
        port = int(port_str)
    else:
        host, port = host_port, 80

    sock = None
    try:
        import socket
        addr = socket.getaddrinfo(host, port)[0][-1]
        sock = socket.socket()
        sock.settimeout(5)
        sock.connect(addr)
        # Unsere EIGENE Anfrage darf ganz normales HTTP/1.1 mit "\r\n"
        # sein - das Problem betrifft nur die ANTWORT des Servers.
        request = "GET /sse HTTP/1.1\r\nHost: %s\r\nConnection: close\r\n\r\n" % host
        sock.send(request.encode())

        chunks = []
        while True:
            chunk = sock.recv(512)
            if not chunk:
                break
            chunks.append(chunk)
        raw = b"".join(chunks).decode("utf-8", "ignore")
    except Exception as e:
        print("widget_sources.fetch_pc_status: Socket-Anfrage fehlgeschlagen:")
        sys.print_exception(e)
        return {"ok": False, "msg": "PC-Status nicht erreichbar: %s" % e}
    finally:
        if sock is not None:
            try:
                sock.close()
            except Exception:
                pass

    # Body nach der Leerzeile abtrennen - tolerant gegenüber "\n\n" (dieser
    # Server) UND "\r\n\r\n" (falls doch mal Standard-konform), siehe
    # Docstring oben.
    text = raw
    for sep in ("\r\n\r\n", "\n\n"):
        if sep in raw:
            text = raw.split(sep, 1)[1]
            break

    if "data:" in text:
        text = text.split("data:", 1)[1]

    values = {}
    try:
        for seg in text.split("{|}"):
            seg = seg.strip()
            if "|" not in seg or not seg.startswith("Simple"):
                continue
            slot, content = seg.split("|", 1)
            try:
                idx = int(slot[len("Simple"):]) - 1
            except ValueError:
                continue
            if not (0 <= idx < len(_PC_STATUS_SLOTS)):
                continue
            content = content.rstrip("^").strip()
            if not content:
                continue  # leerer Slot, siehe Docstring oben
            parts = content.rsplit(" ", 1)
            if len(parts) != 2:
                continue
            try:
                values[_PC_STATUS_SLOTS[idx]] = float(parts[1])
            except ValueError:
                values[_PC_STATUS_SLOTS[idx]] = None  # z.B. "GPU temp TRIAL"
    except Exception as e:
        # Einmaliger Rohtext-Ausdruck ins Log statt eines kryptischen
        # Fehlers im Widget selbst - falls das tatsächliche Antwortformat
        # von der PROTOCOL.md-Doku abweicht (z.B. andere .rslcd-Konfiguration,
        # anderer GPU-Codepfad), sehen wir hier sofort, woran es liegt.
        print("widget_sources.fetch_pc_status: Parsing fehlgeschlagen (%s), Rohtext: %r" % (e, raw))
        return {"ok": False, "msg": "Antwortformat nicht erkannt: %s" % e}

    if not values:
        print("widget_sources.fetch_pc_status: keine Werte gefunden, Rohtext: %r" % raw)
        return {"ok": False, "msg": "Antwort konnte nicht gelesen werden"}
    values["ok"] = True
    return values
