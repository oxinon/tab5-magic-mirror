"""
Screen 1: Magic Mirror - 1:1-Optik-Nachbau des bestehenden Web-Spiegels
(oxinon/magic-mirror-3000). Alle 16 Widget-Typen aus mirror.js sind hier
nachgebaut: Uhr, Kalender, News, Krypto, Wetter, Aktien, Zitat,
Server-Status, Warnungen, Luftqualität, Elbe-Pegel, EWS, DEFCON,
Komplimente, Notizen, Umweltstation.

Datenquelle: Wetter und News holen ihre Daten DIREKT von öffentlichen,
key-losen APIs (Open-Meteo, RSS-Feeds - siehe widget_sources.py, portiert
aus dem vom Nutzer bereitgestellten Core2-MiniDash-Referenzprojekt). Die
übrigen Server-Widgets (Kalender/Krypto/Aktien/Zitat/Server-Status/
Warnungen/Luftqualität/Elbe-Pegel/EWS/DEFCON) nutzen noch den alten
api_client.py-Pfad zu einem separaten Magic-Mirror-Server, der aktuell
bewusst deaktiviert ist (siehe config.py "server.enabled") - werden nach
und nach auf denselben direkten API-Ansatz wie Wetter/News umgestellt.
Notizen/Komplimente sind wie im Original reine Konfigurationsdaten
(wcfg.items), kein API-Call. Die Umweltstation nutzt denselben
air_sensor_manager/accel_source wie die climate/air_quality-Widgets in
screens/dashboard.py - keine doppelte Datenquelle.

Zwei bewusste Vereinfachungen gegenüber dem Original:
  1. Kein CSS-Scroll-Ticker für News - stattdessen rotiert jede Schlagzeile
     einzeln durch (LVGL kann keine CSS-Keyframe-Animationen). Genau das
     Muster, das das Original bei Server-Status/DEFCON/Komplimenten
     ohnehin schon für "mehr Einträge als Platz" nutzt.
  2. Datenabruf (selten, z.B. alle 5-60 Minuten) und Rotation innerhalb
     bereits geladener Daten (häufig, alle paar Sekunden) sind getrennt:
     refresh() holt neue Daten nur, wenn das jeweilige Intervall
     abgelaufen ist; ein eigener LVGL-Timer pro Rotations-Widget blättert
     unabhängig davon durch die zuletzt geladenen Einträge.
"""

import time

import widget_sources
from theme import COLORS
from i18n import STRINGS, get_lang
from widgets.grid_layout import GridLayout, parse_position
from widgets.card import Card
from widgets.status_bar import StatusBar
from widgets.clock_widget import ClockWidget

try:
    import lvgl as lv
    _HAS_LVGL = True
except ImportError:
    _HAS_LVGL = False

# Wie oft neu vom Server geholt wird (Sekunden) - 1:1 aus mirror.js REFRESH_MS
FETCH_INTERVAL_S = {
    "calendar": 300, "news": 600, "crypto": 60, "weather": 600,
    "stocks": 300, "quote": 3600, "server_status": 30, "warnings": 300,
    "air_quality_mirror": 1800, "elbe_pegel": 900, "ews": 1200, "defcon": 300,
}
ROWS_PER_PAGE = 4  # wie SERVER_PAGE_SIZE/DEFCON_PAGE_SIZE im Original

# Maximale Spaltenbreite (col_span) je Widget-Typ - nur diese vier profitieren
# optisch von mehr Breite (längere Schlagzeilen/Zitate/Sprüche bzw. eine
# größere Uhr); alle anderen bleiben bei 1 (auch wenn config.json von Hand
# etwas anderes einträgt - siehe Klemmung in _build_widget unten). Für die
# Web-UI-Positions-Auswahl gilt dasselbe Limit (siehe web_server.py).
SPAN_LIMITS = {"news": 4, "compliments": 4, "quote": 4, "clock": 2}


def _hex(color_hex):
    return lv.color_hex(int(color_hex.lstrip("#"), 16))


def _sign(x):
    if x is None:
        return 0
    return 1 if x > 0 else (-1 if x < 0 else 0)


class MagicMirrorScreen:
    def __init__(self, screen_config, api_client, air_sensor_manager=None,
                 accel_source=None, parent=None, on_menu_pressed=None):
        if not _HAS_LVGL:
            raise RuntimeError("MagicMirrorScreen benötigt lvgl/m5ui (nur auf der Tab5 verfügbar)")

        self.api = api_client
        self.air = air_sensor_manager
        self.accel = accel_source

        self.screen = parent or lv.obj()
        self.screen.set_style_bg_color(_hex(COLORS["bg"]), 0)

        self.status_bar = StatusBar(self.screen, on_menu_pressed=on_menu_pressed)

        self.grid = GridLayout.from_config(screen_config)
        self._parts = {}       # widget id -> {"kind":..., <lvgl elemente>}
        self._last_fetch = {}  # widget id -> Unix-Timestamp der letzten Aktualisierung

        for widget_cfg in screen_config.get("widgets", []):
            self._build_widget(widget_cfg)

    # ------------------------------------------------------------------
    # Aufbau
    # ------------------------------------------------------------------
    def _build_widget(self, cfg):
        if not cfg.get("enabled", True):
            return

        row, col = parse_position(cfg["position"])
        widget_type = cfg["type"]
        widget_id = cfg["id"]

        # col_span klemmen: nie mehr als das Typ-Limit (SPAN_LIMITS) UND nie
        # über den rechten Rasterrand hinaus - erweitert wird immer nach
        # rechts (col bleibt die linke Kante), also darf col + col_span - 1
        # nicht über die letzte Spalte hinausgehen.
        max_type_span = SPAN_LIMITS.get(widget_type, 1)
        max_fit_span = self.grid.cols - col
        col_span = min(cfg.get("col_span", 1), max_type_span, max_fit_span)
        col_span = max(col_span, 1)
        row_span = cfg.get("row_span", 1)

        x, y, w, h = self.grid.cell(row, col, col_span, row_span)
        align = self.grid.h_align(col, col_span)
        card = Card(self.screen, x, y, w, h, align=align)

        builder = getattr(self, "_build_" + widget_type, None)
        if builder is None:
            print("MagicMirrorScreen: unbekannter Widget-Typ %r (id=%r) - übersprungen" % (
                widget_type, widget_id))
            return
        builder(card, cfg)

    def _make_rows(self, card, count, font=None):
        """N leere Zeilen-Labels vorab anlegen (für Listen-Widgets) - bei
        weniger Einträgen bleiben die überzähligen Zeilen einfach leer."""
        font = font or lv.font_montserrat_24
        return [card.add_label("", font=font, color=COLORS["fg"]) for _ in range(count)]

    def _start_rotation_timer(self, widget_id, period_s, repaint_fn):
        """Eigener LVGL-Timer fürs Durchblättern bereits geladener Daten,
        unabhängig vom (viel selteneren) Datenabruf in refresh()."""
        timer = lv.timer_create(lambda t: repaint_fn(), int(period_s * 1000), None)
        self._parts[widget_id]["_timer"] = timer

    # ---- Uhr ----
    def _build_clock(self, card, cfg):
        clock = ClockWidget(card.content_parent(),
                             format24h=cfg.get("format24h", True),
                             show_seconds=cfg.get("show_seconds", True),
                             show_date=cfg.get("show_date", True))
        card.place(clock)
        self._parts[cfg["id"]] = {"kind": "clock"}  # tickt selbst, kein refresh() nötig

    # ---- Kalender ----
    def _build_calendar(self, card, cfg):
        card.add_title(cfg.get("title") or STRINGS["mm.cal.default_title"], font=lv.font_montserrat_24, color=COLORS["accent"])
        rows = self._make_rows(card, ROWS_PER_PAGE, font=lv.font_montserrat_24)
        self._parts[cfg["id"]] = {"kind": "calendar", "rows": rows, "cfg": cfg}

    def _fetch_calendar(self, parts):
        # Direkter iCal-Abruf statt Magic-Mirror-Server-Proxy (siehe
        # widget_sources.py) - braucht eine private iCal-URL in der
        # Widget-Config ("ical_url").
        data = widget_sources.fetch_calendar(parts["cfg"])
        rows = parts["rows"]
        if not data or not data.get("ok"):
            self._paint_message(rows, (data or {}).get("msg") or STRINGS["mm.cal.unavailable"])
            return
        events = data.get("events", [])
        if not events:
            self._paint_message(rows, STRINGS["mm.cal.no_events"])
            return
        for i, row in enumerate(rows):
            row.set_text(self._format_event(events[i]) if i < len(events) else "")

    def _format_event(self, ev):
        # "start" ist ein UTC-Epoch-Zeitstempel (siehe widget_sources.py::
        # fetch_calendar) - hier ohne Zeitzonen-Umrechnung als lokale Zeit
        # formatiert (time.localtime() nutzt auf MicroPython i.d.R. UTC,
        # ein Offset wird bewusst nicht angewendet, um keine zweite,
        # abweichende DST-Logik neben ntp_clock.py zu pflegen).
        if ev.get("all_day"):
            t = time.localtime(ev["start"])
            when = "%04d-%02d-%02d" % (t[0], t[1], t[2])
        else:
            t = time.localtime(ev["start"])
            when = "%04d-%02d-%02d %02d:%02d" % (t[0], t[1], t[2], t[3], t[4])
        return "%s  %s" % (when, ev.get("title", ""))

    # ---- News ----
    def _build_news(self, card, cfg):
        source_label = card.add_label("", font=lv.font_montserrat_24, color=COLORS["accent"])
        headline_label = card.add_label("", font=lv.font_montserrat_24, color=COLORS["fg"])
        self._parts[cfg["id"]] = {
            "kind": "news", "source_label": source_label, "headline_label": headline_label,
            "sources": [], "source_idx": 0, "item_idx": 0, "cfg": cfg,
        }
        self._start_rotation_timer(cfg["id"], cfg.get("rotation_s", 8),
                                    lambda: self._repaint_news(cfg["id"]))

    def _fetch_news(self, parts):
        # Direkter RSS-Abruf statt Magic-Mirror-Server-Proxy (siehe
        # widget_sources.py) - holt bei jedem Aufruf EINE Quelle (reihum
        # wechselnd), deshalb hier immer genau ein "Source"-Eintrag statt
        # der früheren Liste mehrerer gleichzeitig geladener Quellen.
        data = widget_sources.fetch_news(parts["cfg"])
        if not data or not data.get("ok") or not data.get("items"):
            parts["sources"] = []
            parts["source_label"].set_text(STRINGS["mm.news.default_title"])
            parts["headline_label"].set_text((data or {}).get("msg") or STRINGS["mm.news.no_sources"])
            return
        parts["sources"] = [{
            "name": data.get("source_name", ""), "ok": True,
            "items": [{"title": t} for t in data["items"]],
        }]
        parts["source_idx"] = 0
        self._repaint_news_now(parts)

    def _repaint_news(self, widget_id):
        parts = self._parts.get(widget_id)
        if not parts or not parts["sources"]:
            return
        parts["item_idx"] += 1
        src = parts["sources"][parts["source_idx"]]
        items = src.get("items", [])
        if items and parts["item_idx"] >= len(items):
            parts["item_idx"] = 0
            parts["source_idx"] = (parts["source_idx"] + 1) % len(parts["sources"])
            src = parts["sources"][parts["source_idx"]]
            items = src.get("items", [])
        self._paint_news_item(parts, src, items)

    def _repaint_news_now(self, parts):
        src = parts["sources"][parts["source_idx"]]
        self._paint_news_item(parts, src, src.get("items", []))

    def _paint_news_item(self, parts, src, items):
        parts["source_label"].set_text(src.get("name") or STRINGS["mm.news.default_title"])
        if not src.get("ok") or not items:
            parts["headline_label"].set_text(src.get("msg") or STRINGS["mm.news.no_items"])
            return
        idx = min(parts["item_idx"], len(items) - 1)
        parts["headline_label"].set_text(items[idx].get("title", ""))

    # ---- Krypto ----
    def _build_crypto(self, card, cfg):
        card.add_title(cfg.get("title") or STRINGS["mm.crypto.default_title"], font=lv.font_montserrat_24, color=COLORS["accent"])
        rows = self._make_rows(card, ROWS_PER_PAGE)
        self._parts[cfg["id"]] = {"kind": "crypto", "rows": rows, "cfg": cfg}

    def _fetch_crypto(self, parts):
        # Direkter CoinGecko-Abruf statt Magic-Mirror-Server-Proxy (siehe
        # widget_sources.py) - kein API-Key nötig.
        data = widget_sources.fetch_crypto(parts["cfg"])
        rows = parts["rows"]
        if not data or not data.get("ok") or not data.get("prices"):
            self._paint_message(rows, (data or {}).get("msg") or STRINGS["mm.crypto.no_data"])
            return
        for i, row in enumerate(rows):
            if i < len(data["prices"]):
                p = data["prices"][i]
                change = p.get("change24h") or 0
                sign = "+" if change >= 0 else ""
                row.set_text("%s  %.2f %s  %s%.2f%%" % (
                    p.get("ticker", p.get("id", "?")), p.get("price") or 0,
                    (p.get("currency") or "").upper(), sign, change))
                row.set_style_text_color(_hex(COLORS["up"] if change >= 0 else COLORS["down"]), 0)
            else:
                row.set_text("")

    # ---- Wetter ----
    def _build_weather(self, card, cfg):
        card.add_title(cfg.get("title") or STRINGS["mm.weather.default_title"], font=lv.font_montserrat_24, color=COLORS["accent"])
        temp = card.add_label("--", font=lv.font_montserrat_24, color=COLORS["fg"])
        desc = card.add_label("", font=lv.font_montserrat_24, color=COLORS["fg_dim"])
        stats = card.add_label("", font=lv.font_montserrat_24, color=COLORS["fg_faint"])
        self._parts[cfg["id"]] = {"kind": "weather", "temp": temp, "desc": desc, "stats": stats, "cfg": cfg}

    def _fetch_weather(self, parts):
        # Direkter Open-Meteo-Abruf statt Magic-Mirror-Server-Proxy (siehe
        # widget_sources.py) - kein API-Key nötig.
        data = widget_sources.fetch_weather(parts["cfg"])
        if not data or not data.get("ok"):
            parts["temp"].set_text("--")
            parts["desc"].set_text((data or {}).get("msg") or STRINGS["mm.weather.unavailable"])
            parts["stats"].set_text("")
            return
        unit = data.get("unit", "C")
        parts["temp"].set_text("%s°%s" % (round(data["temperature"]) if data.get("temperature") is not None else "--", unit))
        parts["desc"].set_text(data.get("description", ""))
        bits = []
        if data.get("humidity") is not None:
            bits.append("%s %d%%" % (STRINGS["mm.weather.humidity"], round(data["humidity"])))
        if data.get("wind") is not None:
            bits.append("%s %d km/h" % (STRINGS["mm.weather.wind"], round(data["wind"])))
        parts["stats"].set_text(" · ".join(bits))

    # ---- Aktien ----
    def _build_stocks(self, card, cfg):
        card.add_title(cfg.get("title") or STRINGS["mm.stocks.default_title"], font=lv.font_montserrat_24, color=COLORS["accent"])
        rows = self._make_rows(card, ROWS_PER_PAGE)
        self._parts[cfg["id"]] = {"kind": "stocks", "rows": rows, "cfg": cfg}

    def _fetch_stocks(self, parts):
        # Direkter Yahoo-Finance-Abruf statt Magic-Mirror-Server-Proxy
        # (siehe widget_sources.py) - kein API-Key nötig.
        data = widget_sources.fetch_stocks(parts["cfg"])
        rows = parts["rows"]
        if not data or not data.get("ok") or not data.get("prices"):
            self._paint_message(rows, (data or {}).get("msg") or STRINGS["mm.crypto.no_data"])
            return
        for i, row in enumerate(rows):
            if i >= len(data["prices"]):
                row.set_text("")
                continue
            q = data["prices"][i]
            if q.get("price") is None:
                row.set_text("%s  %s" % (q.get("ticker", "?"), q.get("msg") or STRINGS["mm.stocks.na"]))
                row.set_style_text_color(_hex(COLORS["fg_faint"]), 0)
                continue
            change = q.get("change24h") or 0
            sign = "+" if change >= 0 else ""
            row.set_text("%s  %.2f%s  %s%.2f%%" % (
                q.get("ticker", "?"), q.get("price") or 0,
                (" " + q["currency"]) if q.get("currency") else "", sign, change))
            row.set_style_text_color(_hex(COLORS["up"] if change >= 0 else COLORS["down"]), 0)

    # ---- Zitat ----
    def _build_quote(self, card, cfg):
        text = card.add_label("", font=lv.font_montserrat_24, color=COLORS["fg"])
        author = card.add_label("", font=lv.font_montserrat_24, color=COLORS["fg_dim"])
        self._parts[cfg["id"]] = {"kind": "quote", "text": text, "author": author}

    def _fetch_quote(self, parts):
        # Direkter ZenQuotes-Abruf statt Magic-Mirror-Server-Proxy (siehe
        # widget_sources.py) - kein API-Key nötig.
        data = widget_sources.fetch_quote({})
        if not data or not data.get("ok"):
            parts["text"].set_text((data or {}).get("msg") or STRINGS["mm.quote.unavailable"])
            parts["author"].set_text("")
            return
        parts["text"].set_text(data.get("text", ""))
        parts["author"].set_text(data.get("author", ""))

    # ---- Server-Status ----
    def _build_server_status(self, card, cfg):
        summary = card.add_label("", font=lv.font_montserrat_24, color=COLORS["accent"])
        rows = self._make_rows(card, ROWS_PER_PAGE, font=lv.font_montserrat_24)
        self._parts[cfg["id"]] = {
            "kind": "server_status", "summary": summary, "rows": rows,
            "containers": [], "page": 0,
        }
        self._start_rotation_timer(cfg["id"], cfg.get("rotation_s", 15),
                                    lambda: self._repaint_pages(cfg["id"], "containers",
                                                                 self._paint_server_page))

    def _fetch_server_status(self, parts):
        # Kein separater Magic-Mirror-Server mehr geplant (siehe
        # config.py "server.enabled"/HANDOFF.md) - dieses Widget hat
        # dadurch aktuell keine sinnvolle direkte API-Entsprechung und
        # bleibt bewusst inaktiv (config.py: enabled=False), bis geklärt
        # ist, ob/wie es (z.B. für einen eigenen Home-Server-Healthcheck)
        # weiterverwendet werden soll.
        data = self._safe_get("/api/server-status")
        if not data or not data.get("ok"):
            parts["summary"].set_text((data or {}).get("msg") or STRINGS["mm.server.unavailable"])
            self._paint_message(parts["rows"], "")
            parts["containers"] = []
            return
        parts["summary"].set_text(STRINGS["mm.server.summary"] % (data.get("running", 0), data.get("total", 0)))
        parts["containers"] = data.get("containers", [])
        parts["page"] = 0
        self._paint_server_page(parts)

    def _paint_server_page(self, parts):
        rows = parts["rows"]
        containers = parts["containers"]
        if not containers:
            self._paint_message(rows, STRINGS["mm.server.no_containers"])
            return
        chunk = containers[parts["page"] * ROWS_PER_PAGE: (parts["page"] + 1) * ROWS_PER_PAGE]
        for i, row in enumerate(rows):
            if i < len(chunk):
                c = chunk[i]
                dot = "●" if c.get("status") == "running" else "○"
                row.set_text("%s %s" % (dot, c.get("name", "?")))
                row.set_style_text_color(_hex(COLORS["up"] if c.get("status") == "running" else COLORS["fg_faint"]), 0)
            else:
                row.set_text("")

    # ---- Warnungen ----
    def _build_warnings(self, card, cfg):
        card.add_title(cfg.get("title") or STRINGS["mm.warn.default_title"], font=lv.font_montserrat_24, color=COLORS["accent"])
        rows = self._make_rows(card, ROWS_PER_PAGE, font=lv.font_montserrat_24)
        self._parts[cfg["id"]] = {"kind": "warnings", "rows": rows, "cfg": cfg}

    def _fetch_warnings(self, parts):
        # Direkter Abruf von warnung.bund.de statt Magic-Mirror-Server-
        # Proxy (siehe widget_sources.py) - braucht einen Amtlichen
        # Regionalschlüssel (ARS) in der Widget-Config.
        data = widget_sources.fetch_warnings(parts["cfg"])
        rows = parts["rows"]
        if not data or not data.get("ok"):
            self._paint_message(rows, (data or {}).get("msg") or STRINGS["mm.warn.unavailable"])
            return
        warnings = data.get("warnings", [])
        if not warnings:
            self._paint_message(rows, STRINGS["mm.warn.none"])
            return
        for i, row in enumerate(rows):
            if i < len(warnings):
                row.set_text(warnings[i].get("title", ""))
                row.set_style_text_color(_hex(COLORS["red"]), 0)
            else:
                row.set_text("")

    # ---- Luftqualität (Magic-Mirror-Variante, unabhängig vom eigenen BME688-Widget) ----
    def _build_air_quality_mirror(self, card, cfg):
        card.add_title(cfg.get("title") or STRINGS["mm.aqi.default_title"], font=lv.font_montserrat_24, color=COLORS["accent"])
        value = card.add_label("--", font=lv.font_montserrat_24, color=COLORS["fg"])
        label = card.add_label("", font=lv.font_montserrat_24, color=COLORS["fg_dim"])
        self._parts[cfg["id"]] = {"kind": "air_quality_mirror", "value": value, "label": label, "cfg": cfg}

    def _fetch_air_quality_mirror(self, parts):
        # Direkter Open-Meteo-Abruf statt Magic-Mirror-Server-Proxy (siehe
        # widget_sources.py) - Außenluft, unabhängig vom lokalen BME688.
        data = widget_sources.fetch_air_quality(parts["cfg"])
        if not data or not data.get("ok"):
            parts["value"].set_text("--")
            parts["label"].set_text((data or {}).get("msg") or STRINGS["mm.aqi.unavailable"])
            return
        aqi = data.get("aqi")
        parts["value"].set_text(str(round(aqi)) if aqi is not None else "--")
        band = ("good" if aqi is not None and aqi <= 40 else
                "moderate" if aqi is not None and aqi <= 80 else "poor")
        band_key = {"good": "mm.aqi.good", "moderate": "mm.aqi.moderate", "poor": "mm.aqi.poor"}[band]
        parts["value"].set_style_text_color(_hex(
            COLORS["up"] if band == "good" else COLORS["amber"] if band == "moderate" else COLORS["red"]), 0)
        parts["label"].set_text("%s%s" % (STRINGS[band_key], (" · " + data["location"]) if data.get("location") else ""))

    # ---- Elbe-Pegel ----
    def _build_elbe_pegel(self, card, cfg):
        card.add_title(cfg.get("title") or STRINGS["mm.pegel.default_title"], font=lv.font_montserrat_24, color=COLORS["accent"])
        value = card.add_label("--", font=lv.font_montserrat_24, color=COLORS["fg"])
        station = card.add_label("", font=lv.font_montserrat_24, color=COLORS["fg_faint"])
        self._parts[cfg["id"]] = {"kind": "elbe_pegel", "value": value, "station": station, "cfg": cfg}

    def _fetch_elbe_pegel(self, parts):
        # Direkter PEGELONLINE/WSV-Abruf statt Magic-Mirror-Server-Proxy
        # (siehe widget_sources.py) - kein API-Key nötig.
        data = widget_sources.fetch_elbe_pegel(parts["cfg"])
        if not data or not data.get("ok"):
            parts["value"].set_text("--")
            parts["station"].set_text((data or {}).get("msg") or STRINGS["mm.pegel.unavailable"])
            return
        trend = {1: " ↑", -1: " ↓"}.get(_sign(data.get("trend")), "")
        parts["value"].set_text("%s %s%s" % (data.get("value_cm", "--"), data.get("unit", "cm"), trend))
        parts["station"].set_text(data.get("station_name", ""))

    # ---- Apocalypse EWS ----
    def _build_ews(self, card, cfg):
        card.add_title(cfg.get("title") or STRINGS["mm.ews.default_title"], font=lv.font_montserrat_24, color=COLORS["accent"])
        level = card.add_label("--", font=lv.font_montserrat_24, color=COLORS["fg"])
        stats = card.add_label("", font=lv.font_montserrat_24, color=COLORS["fg_dim"])
        self._parts[cfg["id"]] = {"kind": "ews", "level": level, "stats": stats}

    def _fetch_ews(self, parts):
        # Direkter Abruf des öffentlichen JSON-Snapshots statt Magic-
        # Mirror-Server-Proxy (siehe widget_sources.py) - kein API-Key nötig.
        data = widget_sources.fetch_ews({})
        if not data or not data.get("ok"):
            parts["level"].set_text("--")
            parts["stats"].set_text((data or {}).get("msg") or STRINGS["mm.ews.unavailable"])
            return
        lvl = data.get("emergency_level")
        parts["level"].set_text(str(lvl) if lvl is not None else "--")
        color_key = "up" if (lvl or 0) <= 2 else "amber" if lvl == 3 else "red"
        parts["level"].set_style_text_color(_hex(COLORS[color_key]), 0)
        z = data.get("z_score")
        parts["stats"].set_text(STRINGS["mm.ews.stats"] % (
            round(data.get("concurrent_count") or 0), round(data.get("baseline_mean") or 0),
            ("%.2f" % z) if z is not None else "--"))

    # ---- DEFCON ----
    def _build_defcon(self, card, cfg):
        card.add_title(cfg.get("title") or STRINGS["mm.defcon.default_title"], font=lv.font_montserrat_24, color=COLORS["accent"])
        rows = self._make_rows(card, ROWS_PER_PAGE, font=lv.font_montserrat_24)
        self._parts[cfg["id"]] = {"kind": "defcon", "rows": rows, "regions": [], "page": 0, "cfg": cfg}
        self._start_rotation_timer(cfg["id"], cfg.get("rotation_s", 15),
                                    lambda: self._repaint_pages(cfg["id"], "regions",
                                                                 self._paint_defcon_page))

    def _fetch_defcon(self, parts):
        # Direkter Abruf des nutzereigenen DEFCON-Endpunkts statt Magic-
        # Mirror-Server-Proxy (siehe widget_sources.py) - URL/API-Key
        # kommen aus der Widget-Config ("url"/"api_key").
        data = widget_sources.fetch_defcon(parts["cfg"])
        if not data or not data.get("ok"):
            self._paint_message(parts["rows"], (data or {}).get("msg") or STRINGS["mm.defcon.unavailable"])
            parts["regions"] = []
            return
        parts["regions"] = data.get("regions", [])
        parts["page"] = 0
        if not parts["regions"]:
            self._paint_message(parts["rows"], STRINGS["mm.defcon.no_regions"])
        else:
            self._paint_defcon_page(parts)

    def _paint_defcon_page(self, parts):
        rows = parts["rows"]
        chunk = parts["regions"][parts["page"] * ROWS_PER_PAGE:(parts["page"] + 1) * ROWS_PER_PAGE]
        for i, row in enumerate(rows):
            if i < len(chunk):
                r = chunk[i]
                row.set_text("%s  %.1f" % (r.get("name", "?"), r.get("value", 0)))
            else:
                row.set_text("")

    # ---- Komplimente (lokale Konfigurationsdaten, kein API-Call) ----
    def _build_compliments(self, card, cfg):
        text = card.add_label("", font=lv.font_montserrat_24, color=COLORS["fg"])
        self._parts[cfg["id"]] = {"kind": "compliments", "text": text,
                                   "items": cfg.get("items", []), "idx": 0}
        self._start_rotation_timer(cfg["id"], cfg.get("rotation_s", 10),
                                    lambda: self._repaint_compliment(cfg["id"]))
        self._repaint_compliment(cfg["id"])

    def _repaint_compliment(self, widget_id):
        parts = self._parts[widget_id]
        items = [i for i in parts["items"] if i]
        if not items:
            parts["text"].set_text(STRINGS["mm.compliments.no_items"])
            return
        parts["idx"] = (parts["idx"] + 1) % len(items)
        parts["text"].set_text(items[parts["idx"]])

    # ---- Notizen (lokale Konfigurationsdaten, kein API-Call) ----
    def _build_todo(self, card, cfg):
        card.add_title(cfg.get("title") or STRINGS["mm.todo.default_title"], font=lv.font_montserrat_24, color=COLORS["accent"])
        rows = self._make_rows(card, ROWS_PER_PAGE, font=lv.font_montserrat_24)
        items = cfg.get("items", [])
        for i, row in enumerate(rows):
            if i < len(items):
                it = items[i]
                prefix = "[x] " if it.get("done") else "[ ] "
                row.set_text(prefix + it.get("text", ""))
                if it.get("done"):
                    row.set_style_text_color(_hex(COLORS["fg_faint"]), 0)
            else:
                row.set_text("" if items else STRINGS["mm.todo.no_items"])
        self._parts[cfg["id"]] = {"kind": "todo"}  # statisch, kein refresh() nötig

    # ---- Umweltstation (nutzt denselben Sensor wie environment.py) ----
    def _build_env_sensor_mirror(self, card, cfg):
        card.add_title(cfg.get("title") or STRINGS["mm.env.default_title"], font=lv.font_montserrat_24, color=COLORS["accent"])
        stats = card.add_label("--", font=lv.font_montserrat_24, color=COLORS["fg"])
        iaq = card.add_label("", font=lv.font_montserrat_24, color=COLORS["fg_dim"])
        quake = card.add_label("", font=lv.font_montserrat_24, color=COLORS["up"])
        self._parts[cfg["id"]] = {"kind": "env_sensor_mirror", "stats": stats, "iaq": iaq, "quake": quake}

    def _fetch_env_sensor_mirror(self, parts):
        if self.air is None:
            parts["stats"].set_text("--")
            parts["iaq"].set_text(STRINGS["mm.env.unavailable"])
            return
        reading = self.air.read()
        if not reading.ok:
            parts["stats"].set_text("--")
            parts["iaq"].set_text(reading.msg or STRINGS["mm.env.unavailable"])
            return
        parts["stats"].set_text("%.1f° · %.0f%%" % (
            reading.temp_c or 0, reading.humidity or 0))
        if reading.iaq_score is not None:
            band = ("excellent" if reading.iaq_score >= 80 else "good" if reading.iaq_score >= 60 else
                    "moderate" if reading.iaq_score >= 40 else "poor" if reading.iaq_score >= 20 else "very_poor")
            parts["iaq"].set_text("IAQ %.0f · %s" % (reading.iaq_score, STRINGS["mm.iaq." + band]))
        else:
            parts["iaq"].set_text(STRINGS["iaq.calibrating"])

        if self.accel is not None:
            accel_reading = self.accel.read()
            if accel_reading.ok:
                triggered = accel_reading.quake.get("triggered", False)
                parts["quake"].set_text("%s: %s" % (
                    STRINGS["mm.env.earthquake_label"],
                    STRINGS["mm.env.quake_alert"] if triggered else STRINGS["mm.env.quake_calm"]))
                parts["quake"].set_style_text_color(_hex(COLORS["red"] if triggered else COLORS["up"]), 0)

    # ------------------------------------------------------------------
    # Gemeinsame Helfer
    # ------------------------------------------------------------------
    def _safe_get(self, path):
        try:
            return self.api.get_json(path)
        except Exception as e:
            return {"ok": False, "msg": str(e)}

    def _paint_message(self, rows, message):
        if rows:
            rows[0].set_text(message)
            for row in rows[1:]:
                row.set_text("")

    def _repaint_pages(self, widget_id, list_key, paint_fn):
        parts = self._parts.get(widget_id)
        if not parts or not parts.get(list_key):
            return
        page_count = max(1, (len(parts[list_key]) + ROWS_PER_PAGE - 1) // ROWS_PER_PAGE)
        parts["page"] = (parts["page"] + 1) % page_count
        paint_fn(parts)

    # ------------------------------------------------------------------
    # refresh() - vom äußeren Poll-Task aufgerufen (siehe main.py). Holt
    # neue Daten nur für Widgets, deren FETCH_INTERVAL_S abgelaufen ist.
    # ------------------------------------------------------------------
    def refresh(self):
        now = time.time()
        for widget_id, parts in self._parts.items():
            kind = parts["kind"]
            if kind in ("clock", "todo"):
                continue  # tickt selbst bzw. ist rein statisch

            interval = FETCH_INTERVAL_S.get(kind, 300)
            last = self._last_fetch.get(widget_id, 0)
            if now - last < interval:
                continue
            self._last_fetch[widget_id] = now

            fetch_fn = getattr(self, "_fetch_" + kind, None)
            if fetch_fn:
                fetch_fn(parts)
