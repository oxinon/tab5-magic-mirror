"""
Widget-Katalog (Basisklasse WidgetCatalogScreen) - 1:1-Optik-Nachbau des
früheren Web-Spiegels (oxinon/magic-mirror-3000), ursprünglich als
eigener "Magic Mirror"-Screen gebaut, jetzt aber die gemeinsame Basis für
das EINE konsolidierte Tab5-Dashboard (siehe screens/dashboard.py::
DashboardScreen, das von dieser Klasse ERBT statt sie zu ersetzen - daher
liegen die "klassischen" 16 Widget-Typen hier: Uhr, Kalender, News,
Krypto, Wetter, Aktien, Zitat, Server-Status, Warnungen, Luftqualität,
Elbe-Pegel, EWS, DEFCON, Komplimente, Notizen, Umweltstation) sowie das
komplette Karten-/Raster-System (GridLayout/Card) und den generischen
Widget-Dispatch (_build_widget() findet automatisch _build_<typ>() per
getattr()). DashboardScreen ergänzt nur noch die zusätzlichen Typen, die
früher auf separaten Environment-/Room-Dashboard-Screens lebten (Klima,
Luftqualität lokal, Akustik, Equalizer, Beschleunigung, Home-Assistant).

Datenquelle: Bis auf Server-Status holen inzwischen ALLE Widgets ihre
Daten DIREKT von öffentlichen, key-losen APIs (Open-Meteo, RSS-Feeds,
CoinGecko, Yahoo Finance, ZenQuotes, warnung.bund.de, PEGELONLINE, iCal -
siehe widget_sources.py, portiert aus dem vom Nutzer bereitgestellten
Core2-MiniDash-Referenzprojekt) - kein separater Magic-Mirror-Server mehr
nötig. Server-Status hat dafür (noch) keine sinnvolle direkte Entsprechung
und bleibt bewusst inaktiv (siehe config.py "server.enabled"). Notizen/
Komplimente sind reine Konfigurationsdaten (wcfg.items), kein API-Call.
Die Umweltstation nutzt denselben air_sensor_manager/accel_source wie die
climate/air_quality-Widgets in screens/dashboard.py - keine doppelte
Datenquelle.

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
import fetch_worker
import ntp_clock
import config
from ascii_text import to_ascii
from theme import COLORS
from i18n import STRINGS, get_lang
from lvgl_safety import lvgl_safe_callback
from widgets.grid_layout import GridLayout, parse_position
from widgets.card import Card, HEADER_HEIGHT, HEADER_GAP
from widgets import lv_const
from widgets.status_bar import StatusBar
from widgets.clock_widget import ClockWidget
from widgets.air_quality_light import score_to_level, LEVEL_COLOR_KEY

try:
    import lvgl as lv
    _HAS_LVGL = True
except ImportError:
    _HAS_LVGL = False

# Wie oft neu vom Server geholt wird (Sekunden) - 1:1 aus mirror.js REFRESH_MS
FETCH_INTERVAL_S = {
    # Externe Online-Dienste (Wetter/News/Krypto/etc.) - 15 Minuten reicht
    # für alle, das sind keine zeitkritischen Daten und schont unnötige
    # Anfragen an fremde Server.
    "calendar": 900, "news": 900, "crypto": 900, "weather": 900,
    "stocks": 900, "quote": 900, "warnings": 900,
    "air_quality_mirror": 900, "elbe_pegel": 900, "ews": 900, "defcon": 900,
    # Lokale Dienste im eigenen Netz - dürfen deutlich häufiger, da die
    # Daten wichtiger/zeitkritischer sind und kein fremder Server damit
    # belastet wird.
    "server_status": 30,
    # pc_status NICHT hier - läuft über einen eigenen, von diesem
    # 20s-Grundtakt entkoppelten 3s-Task (siehe main.py::pc_status_task()
    # und WidgetCatalogScreen.update_pc_status()), da 3 Sekunden mit dem
    # normalen FETCH_INTERVAL_S/refresh()-Mechanismus nicht erreichbar
    # wären (der läuft ja selbst nur alle poll_interval_s/20s).
}
ROWS_PER_PAGE = 4  # wie SERVER_PAGE_SIZE/DEFCON_PAGE_SIZE im Original

# kind -> Funktion(screen, parts), die die BLOCKIERENDE Netzwerk-Anfrage
# macht und ein reines Daten-Dict zurückgibt - OHNE jeglichen LVGL-
# Zugriff (wird im Hintergrund-Thread aufgerufen, siehe fetch_worker.py).
# Nicht jeder kind-Name entspricht 1:1 einem widget_sources.fetch_X()-
# Namen (z.B. "air_quality_mirror" -> fetch_air_quality), und
# "server_status" nutzt einen anderen Mechanismus (api_client.py statt
# widget_sources.py) - daher eine explizite Zuordnung statt Namens-
# Ableitung. Kinds, die hier NICHT auftauchen (z.B. "env_sensor_mirror" -
# liest nur lokal per I2C, kein Netzwerk), laufen weiterhin ganz normal
# synchron im Hauptthread, da dafür kein Hintergrund-Thread nötig ist.
_BACKGROUND_FETCHERS = {
    "calendar": lambda screen, parts: widget_sources.fetch_calendar(parts["cfg"]),
    "climate_ext": lambda screen, parts: widget_sources.fetch_climate_ext(parts["cfg"]),
    "pc_status": lambda screen, parts: widget_sources.fetch_pc_status(parts["cfg"]),
    "news": lambda screen, parts: widget_sources.fetch_news(parts["cfg"]),
    "crypto": lambda screen, parts: widget_sources.fetch_crypto(parts["cfg"]),
    "weather": lambda screen, parts: widget_sources.fetch_weather(parts["cfg"]),
    "stocks": lambda screen, parts: widget_sources.fetch_stocks(parts["cfg"]),
    "quote": lambda screen, parts: widget_sources.fetch_quote({}),
    "server_status": lambda screen, parts: screen._safe_get("/api/server-status"),
    "warnings": lambda screen, parts: widget_sources.fetch_warnings(parts["cfg"]),
    "air_quality_mirror": lambda screen, parts: widget_sources.fetch_air_quality(parts["cfg"]),
    "elbe_pegel": lambda screen, parts: widget_sources.fetch_elbe_pegel(parts["cfg"]),
    "ews": lambda screen, parts: widget_sources.fetch_ews({}),
    "defcon": lambda screen, parts: widget_sources.fetch_defcon(parts["cfg"]),
}

# Maximale Spaltenbreite (col_span) je Widget-Typ - nur diese vier profitieren
# optisch von mehr Breite (längere Schlagzeilen/Zitate/Sprüche bzw. eine
# größere Uhr); alle anderen bleiben bei 1 (auch wenn config.json von Hand
# etwas anderes einträgt - siehe Klemmung in _build_widget unten). Für die
# Web-UI-Positions-Auswahl gilt dasselbe Limit (siehe web_server.py).
SPAN_LIMITS = {"news": 4, "compliments": 4, "quote": 4, "clock": 2, "todo": 2, "logo": 2}
# Analoge Begrenzung für row_span (Nutzerwunsch: Notiz-Widget soll auch
# nach unten 2 Kacheln nutzen dürfen) - eigenständig von SPAN_LIMITS
# (Spalten), da beide Richtungen unabhängig sinnvoll sein können. Nur
# "todo" bisher, da nur dafür angefragt - andere Typen bleiben bei der
# Standardhöhe von 1 Zeile (kein Eintrag hier = Limit 1, siehe
# _build_widget()).
ROW_SPAN_LIMITS = {"todo": 2}

# Sichtbare Einträge des todo-Widgets je nach (tatsächlich angewandtem,
# also bereits geklemmtem) row_span: 1 Kachel -> 4, 2 Kacheln -> 9. Bei 2
# Kacheln geht relativ weniger Platz für Titel/Ränder verloren, daher mehr
# als das Doppelte. Unbekannte Werte fallen auf den nächstkleineren
# bekannten zurück (siehe _todo_rows_for_span()).
TODO_ROWS_BY_SPAN = {1: 4, 2: 9}


TODO_ROW_H = 40               # Hoehe einer Notiz-Zeile in Pixeln (siehe _build_todo)
TODO_ROW_TOLERANCE_PX = 30    # so viel duerfen die Zeilen den Innenabstand unten "anknabbern"


def _todo_rows_for_card(card, row_span=1):
    """Anzahl Notiz-Zeilen aus der tatsaechlichen Kartenhoehe (statt fester Tabelle):
    passt sich Raster/Zeilenhoehe an. Beim Standardraster (3 Zeilen) ergibt das 4
    Zeilen bei 1 Kachel und 9 bei 2 Kacheln - wie die Tabelle TODO_ROWS_BY_SPAN.
    Faellt auf die Tabelle zurueck, falls die Karte keine Masse liefert."""
    try:
        usable = card.h - HEADER_HEIGHT - HEADER_GAP - 2 * card.padding
        rows = int((usable + TODO_ROW_TOLERANCE_PX) // TODO_ROW_H)
    except Exception:
        return _todo_rows_for_span(row_span)
    return max(1, min(12, rows))


def _todo_rows_for_span(row_span):
    """Zeilenanzahl für das todo-Widget aus row_span (mind. 1 Zeile)."""
    best = TODO_ROWS_BY_SPAN[1]
    for span in sorted(TODO_ROWS_BY_SPAN):
        if span <= row_span:
            best = TODO_ROWS_BY_SPAN[span]
    return max(1, best)


def _hex(color_hex):
    return lv.color_hex(int(color_hex.lstrip("#"), 16))


def _format_price(value):
    """Ab 1000 keine Nachkommastellen mehr (z.B. "1111$" statt "1111.23$") -
    bei größeren Kursen sind zwei Nachkommastellen nur unnütze Ziffern und
    machen die eh schon enge Zeile (Ticker + Kurs + Änderung) unnötig lang."""
    value = value or 0
    if abs(value) >= 1000:
        return "%.0f" % value
    return "%.2f" % value


def _sign(x):
    if x is None:
        return 0
    return 1 if x > 0 else (-1 if x < 0 else 0)


class WidgetCatalogScreen:
    def __init__(self, screen_config, api_client, air_sensor_manager=None,
                 accel_source=None, parent=None, on_menu_pressed=None):
        if not _HAS_LVGL:
            raise RuntimeError("WidgetCatalogScreen benötigt lvgl/m5ui (nur auf der Tab5 verfügbar)")

        self.api = api_client
        self.air = air_sensor_manager
        self.accel = accel_source

        self.screen = parent or lv.obj()
        self.screen.set_style_bg_color(_hex(COLORS["bg"]), 0)

        self.status_bar = StatusBar(self.screen, on_menu_pressed=on_menu_pressed)

        self.grid = GridLayout.from_config(screen_config)
        self._parts = {}       # widget id -> {"kind":..., <lvgl elemente>}
        self._last_fetch = {}  # widget id -> Unix-Timestamp der letzten Aktualisierung
        # Für den Web-UI-Live-Spiegel (Phase B, siehe web_server.py/
        # main.py::_get_widget_snapshot()) - widget id -> das zuletzt
        # empfangene Roh-Daten-Dict jedes Hintergrund-abgerufenen Widgets
        # (siehe _BACKGROUND_FETCHERS), zusätzlich zur direkten Anwendung
        # aufs LVGL-Widget (siehe refresh() Schritt 1 unten). Rein additiv -
        # ändert nichts am bisherigen Anzeigeverhalten, wird nur beim
        # Anwenden der Daten mitgeschrieben. Überlebt bewusst destroy()
        # (siehe dort), damit der Web-Spiegel auch dann noch die zuletzt
        # bekannten Werte zeigen kann, wenn der Nutzer gerade auf dem
        # Sensor-Dashboard-Screen ist (wo dieses Objekt nicht existiert).
        self._last_data = {}
        # Beim allerersten refresh()-Aufruf werden ALLE fälligen Widgets
        # auf einmal geladen (kein MAX_FETCHES_PER_CYCLE-Limit), damit das
        # Dashboard gleich nach dem Boot vollständig befüllt ist, statt
        # sich über mehrere Minuten (5+ Zyklen à 20s) langsam zu füllen -
        # siehe refresh()-Docstring für die Abwägung.
        self._first_refresh_done = False

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
        max_fit_col_span = self.grid.cols - col
        col_span = min(cfg.get("col_span", 1), max_type_span, max_fit_col_span)
        col_span = max(col_span, 1)

        # row_span analog geklemmt (Nutzerwunsch: Notiz-Widget soll auch
        # nach UNTEN 2 Kacheln nutzen dürfen, nicht nur nach rechts) -
        # erweitert wird immer nach unten (row bleibt die obere Kante),
        # also darf row + row_span - 1 nicht über die letzte Zeile
        # hinausgehen. ROW_SPAN_LIMITS ist bewusst eine EIGENE, separate
        # Begrenzung von SPAN_LIMITS (col) - ein Widget-Typ kann in eine
        # Richtung breiter, in die andere aber trotzdem auf 1 begrenzt
        # sein müssen (z.B. wenn sein Inhalt nicht sinnvoll höher werden
        # kann/soll).
        max_type_row_span = ROW_SPAN_LIMITS.get(widget_type, 1)
        max_fit_row_span = self.grid.rows - row
        row_span = min(cfg.get("row_span", 1), max_type_row_span, max_fit_row_span)
        row_span = max(row_span, 1)

        x, y, w, h = self.grid.cell(row, col, col_span, row_span)
        align = self.grid.h_align(col, col_span)
        card = Card(self.screen, x, y, w, h, align=align)

        builder = getattr(self, "_build_" + widget_type, None)
        if builder is None:
            print("WidgetCatalogScreen: unbekannter Widget-Typ %r (id=%r) - übersprungen" % (
                widget_type, widget_id))
            return
        if widget_type in ROW_SPAN_LIMITS:
            # Geklemmten row_span an den Builder weiterreichen (Flache Kopie,
            # damit das gespeicherte config-Dict nicht verändert wird).
            cfg = dict(cfg)
            cfg["_row_span"] = row_span
        builder(card, cfg)

    def _make_rows(self, card, count, font=None):
        """N leere Zeilen-Labels vorab anlegen (für Listen-Widgets) - bei
        weniger Einträgen bleiben die überzähligen Zeilen einfach leer."""
        font = font or lv.font_montserrat_24
        return [card.add_label("", font=font, color=COLORS["fg"]) for _ in range(count)]

    def _start_rotation_timer(self, widget_id, period_s, repaint_fn):
        """Eigener LVGL-Timer fürs Durchblättern bereits geladener Daten,
        unabhängig vom (viel selteneren) Datenabruf in refresh()."""
        # Alten Timer des Widgets zuerst loeschen (sonst tickt er nach einem
        # Ueberschreiben der Referenz unsichtbar weiter = Leck) und den Callback
        # absichern (siehe lvgl_safety.py) - eine Exception in einem Timer darf den
        # LVGL-Scheduler nicht mitreissen.
        old = self._parts[widget_id].get("_timer")
        if old is not None:
            try:
                old.delete()
            except Exception:
                pass
        safe = lvgl_safe_callback(label="Rotation %s" % widget_id)(lambda t: repaint_fn())
        timer = lv.timer_create(safe, int(period_s * 1000), None)
        self._parts[widget_id]["_timer"] = timer

    def get_snapshot(self):
        """Für den Web-UI-Live-Spiegel (Phase B, siehe web_server.py::
        /api/widget-snapshot, main.py::_get_widget_snapshot()) - liefert
        das zuletzt empfangene Roh-Daten-Dict jedes Hintergrund-
        abgerufenen Widgets (siehe _BACKGROUND_FETCHERS), OHNE selbst
        etwas frisch abzufragen - identisch zu dem, was gerade (oder beim
        letzten Aufenthalt auf dem Haupt-Dashboard) auf dem Tab5-Display
        zu sehen war."""
        return dict(self._last_data)

    def destroy(self):
        """Für Live-Reload (siehe main.py::_do_reload()) - VOR einem
        Neuaufbau aufräumen: alle Rotations-Timer stoppen UND alle Widgets
        mit einem EIGENEN, selbst-tickenden Timer (aktuell nur die Uhr,
        siehe widgets/clock_widget.py::deinit()) explizit deinitialisieren
        (sonst laufen diese Timer nach dem Neuaufbau gegen längst gelöschte
        Objekte weiter - LvReferenceError, beobachtet: Uhr blieb stehen,
        Absturz im nächsten status_bar_task()-Zyklus). Danach alle Kind-
        Objekte des Screens entfernen (lv.obj.clean() - der Screen/die
        M5Page selbst bleibt bestehen und wird für den Neuaufbau
        wiederverwendet, NICHT neu erzeugt)."""
        for parts in self._parts.values():
            timer = parts.get("_timer")
            if timer is not None:
                try:
                    timer.delete()
                except Exception as e:
                    print("Timer-Cleanup fehlgeschlagen (weiter aufräumen):", e)
            widget = parts.get("widget")
            deinit = getattr(widget, "deinit", None)
            if deinit is not None:
                try:
                    deinit()
                except Exception as e:
                    print("Widget-Cleanup fehlgeschlagen (weiter aufräumen):", e)
        try:
            self.screen.clean()
        except Exception as e:
            print("Screen-Cleanup fehlgeschlagen:", e)
            raise
        self._parts = {}

    # ---- Uhr ----
    def _build_clock(self, card, cfg):
        clock = ClockWidget(card.content_parent(),
                             format24h=cfg.get("format24h", True),
                             show_seconds=cfg.get("show_seconds", True),
                             show_date=cfg.get("show_date", True),
                             align=card.align,
                             # Tatsächlich verfügbare Breite (wie card.add_label()
                             # sie berechnet) statt einer fest codierten Zahl -
                             # vorher war die Uhr-Box (290px) breiter als der
                             # wirklich nutzbare Platz in einer 1-spaltigen
                             # Kachel (~268px bei Standard-Padding), wodurch sie
                             # bei Rechtsbündigkeit ein paar Pixel über den
                             # rechten Rand hinausragte.
                             width=max(card.w - 2 * card.padding, 10))
        card.place(clock)
        # "widget"-Referenz MUSS gespeichert werden (nicht nur "kind") -
        # sonst kann destroy() unten den eigenen 1-Sekunden-Timer der Uhr
        # nicht finden und abräumen (siehe ClockWidget.deinit()). Genau das
        # hat beim Live-Reload gefehlt: der alte Timer lief nach dem
        # Neuaufbau gegen bereits gelöschte LVGL-Objekte weiter ->
        # LvReferenceError, Uhr blieb stehen.
        self._parts[cfg["id"]] = {"kind": "clock", "widget": clock}

    # ---- Kalender ----
    def _build_calendar(self, card, cfg):
        card.add_title(cfg.get("title") or STRINGS["mm.cal.default_title"], font=lv.font_montserrat_24, color=COLORS["accent"])
        # Zwei Zeilen pro Termin (Titel, dann Datum/Zeit) statt einer
        # kombinierten Zeile - deshalb nur halb so viele Termine wie
        # Zeilen verfügbar sind, sonst würde die Kachel überlaufen.
        num_events = max(1, ROWS_PER_PAGE // 2)
        event_rows = []
        for _ in range(num_events):
            title_label = card.add_label("", font=lv.font_montserrat_24, color=COLORS["fg"])
            # Gleiche Farbe wie das Datum im Uhr-Widget (fg_dim) - siehe
            # widgets/clock_widget.py::date_label.
            datetime_label = card.add_label("", font=lv.font_montserrat_24, color=COLORS["fg_dim"])
            event_rows.append((title_label, datetime_label))
        self._parts[cfg["id"]] = {"kind": "calendar", "event_rows": event_rows, "cfg": cfg}

    def _fetch_calendar(self, parts, data=None):
        # Direkter iCal-Abruf statt Magic-Mirror-Server-Proxy (siehe
        # widget_sources.py) - braucht eine private iCal-URL in der
        # Widget-Config ("ical_url").
        if data is None:
            data = widget_sources.fetch_calendar(parts["cfg"])
        event_rows = parts["event_rows"]

        def _paint_single_message(message):
            event_rows[0][0].set_text(message)
            event_rows[0][1].set_text("")
            for title_label, dt_label in event_rows[1:]:
                title_label.set_text("")
                dt_label.set_text("")

        if not data or not data.get("ok"):
            _paint_single_message((data or {}).get("msg") or STRINGS["mm.cal.unavailable"])
            return
        events = data.get("events", [])
        if not events:
            _paint_single_message(STRINGS["mm.cal.no_events"])
            return
        for i, (title_label, dt_label) in enumerate(event_rows):
            if i < len(events):
                title, when = self._format_event(events[i])
                title_label.set_text(title)
                dt_label.set_text(when)
            else:
                title_label.set_text("")
                dt_label.set_text("")

    def _format_event(self, ev):
        # "start" ist ein UTC-Epoch-Zeitstempel (siehe widget_sources.py::
        # fetch_calendar) - ntp_clock.from_epoch() rechnet den
        # konfigurierten utc_offset + automatische EU-Sommerzeit dazu
        # (dieselbe Logik wie das Uhr-Widget), statt roh in UTC anzuzeigen.
        t = ntp_clock.from_epoch(ev["start"])
        if ev.get("all_day"):
            when = "%04d-%02d-%02d" % (t[0], t[1], t[2])
        else:
            when = "%04d-%02d-%02d %02d:%02d" % (t[0], t[1], t[2], t[3], t[4])
        return ev.get("title", ""), when

    # ---- Externes Raumklima (z.B. Core2-MiniDash mit eigenem BME688) -
    # 1:1 dasselbe Layout wie das lokale Raumklima-Widget (screens/
    # dashboard.py::_build_climate), nur über die Web-API eines anderen
    # Geräts statt des lokalen Sensors, siehe widget_sources.py::
    # fetch_climate_ext() ----
    def _build_climate_ext(self, card, cfg):
        card.add_title(cfg.get("title") or STRINGS["mm.climate_ext.default_title"],
                        font=lv.font_montserrat_24, color=COLORS["accent"])
        self._parts[cfg["id"]] = {
            "kind": "climate_ext", "cfg": cfg,
            "temp": card.add_label("--", font=lv.font_montserrat_48, color=COLORS["fg"]),
            "humidity": card.add_label("--", font=lv.font_montserrat_24, color=COLORS["fg_dim"]),
            "pressure": card.add_label("--", font=lv.font_montserrat_24, color=COLORS["fg_dim"]),
            "iaq": card.add_label("--", font=lv.font_montserrat_24, color=COLORS["fg_faint"]),
        }

    def _fetch_climate_ext(self, parts, data=None):
        if data is None:
            data = widget_sources.fetch_climate_ext(parts["cfg"])
        if not data or not data.get("ok"):
            parts["temp"].set_text("--")
            parts["humidity"].set_text("--")
            parts["pressure"].set_text("--")
            parts["iaq"].set_text((data or {}).get("msg") or STRINGS["mm.climate_ext.unavailable"])
            parts["iaq"].set_style_text_color(_hex(COLORS["fg_faint"]), 0)
            return
        temp_c = data.get("temp_c")
        parts["temp"].set_text("%.1f°C" % temp_c if temp_c is not None else "--")
        humidity = data.get("humidity")
        parts["humidity"].set_text("%.0f%% rH" % humidity if humidity is not None else "--")
        pressure_hpa = data.get("pressure_hpa")
        parts["pressure"].set_text("%.0f hPa" % pressure_hpa if pressure_hpa is not None else "--")
        iaq_score = data.get("iaq_score")
        if iaq_score is not None:
            parts["iaq"].set_text(STRINGS["iaq.value_line"] % iaq_score)
            level = score_to_level(iaq_score)
            parts["iaq"].set_style_text_color(_hex(COLORS[LEVEL_COLOR_KEY[level]]), 0)
        else:
            parts["iaq"].set_text(STRINGS["iaq.calibrating"])
            parts["iaq"].set_style_text_color(_hex(COLORS["fg_faint"]), 0)

    # ---- PC-Systemstatus (CPU/GPU, z.B. per Windows-PC oder Linux mit
    # github.com/oxinon/knob-esp32s3-aida-sse-server-linux) - fragt
    # denselben "/sse"-Endpunkt ab wie der echte Waveshare-Knob, siehe
    # widget_sources.py::fetch_pc_status() ----
    def _build_pc_status(self, card, cfg):
        card.add_title(cfg.get("title") or STRINGS["mm.pc_status.default_title"], font=lv.font_montserrat_24, color=COLORS["accent"])
        self._parts[cfg["id"]] = {
            "kind": "pc_status", "cfg": cfg,
            # CPU-Auslastung als "Hero-Zahl" (48pt, wie Uhr/Wetter/
            # Luftqualität), Rest kompakt darunter.
            "cpu_usage": card.add_label("--", font=lv.font_montserrat_48, color=COLORS["fg"]),
            "cpu_detail": card.add_label("", font=lv.font_montserrat_24, color=COLORS["fg_dim"]),
            "gpu_usage": card.add_label("", font=lv.font_montserrat_24, color=COLORS["fg"]),
            "gpu_detail": card.add_label("", font=lv.font_montserrat_24, color=COLORS["fg_dim"]),
        }

    def _fetch_pc_status(self, parts, data=None):
        if data is None:
            data = widget_sources.fetch_pc_status(parts["cfg"])
        if not data or not data.get("ok"):
            parts["cpu_usage"].set_text("--")
            parts["cpu_detail"].set_text((data or {}).get("msg") or "nicht erreichbar")
            parts["cpu_detail"].set_style_text_color(_hex(COLORS["fg_faint"]), 0)
            parts["gpu_usage"].set_text("")
            parts["gpu_detail"].set_text("")
            return

        def _usage_color(usage):
            # Gemeinsame Ampel-Farblogik für CPU UND GPU - hohe Auslastung
            # rot statt neutral weiß, gleiche Schwellen für beide.
            if usage is None:
                return COLORS["fg"]
            if usage >= 85:
                return COLORS["red"]
            if usage >= 60:
                return COLORS["amber"]
            return COLORS["up"]

        cpu_usage = data.get("cpu_usage")
        parts["cpu_usage"].set_text("%.0f%%" % cpu_usage if cpu_usage is not None else "--")
        parts["cpu_usage"].set_style_text_color(_hex(_usage_color(cpu_usage)), 0)

        # Lüfterdrehzahl auf Wunsch entfernt - nur noch Takt + Temperatur.
        cpu_freq = data.get("cpu_freq")
        cpu_temp = data.get("cpu_temp")
        parts["cpu_detail"].set_style_text_color(_hex(COLORS["fg_dim"]), 0)
        parts["cpu_detail"].set_text("CPU %s MHz - %s°C" % (
            "%.0f" % cpu_freq if cpu_freq is not None else "--",
            "%.0f" % cpu_temp if cpu_temp is not None else "--",
        ))

        gpu_usage = data.get("gpu_usage")
        parts["gpu_usage"].set_text("GPU %s%%" % ("%.0f" % gpu_usage if gpu_usage is not None else "--"))
        parts["gpu_usage"].set_style_text_color(_hex(_usage_color(gpu_usage)), 0)

        gpu_freq = data.get("gpu_freq")
        gpu_temp = data.get("gpu_temp")
        parts["gpu_detail"].set_text("%s MHz - %s°C" % (
            "%.0f" % gpu_freq if gpu_freq is not None else "--",
            "%.0f" % gpu_temp if gpu_temp is not None else "--",
        ))

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

    def _fetch_news(self, parts, data=None):
        # Direkter RSS-Abruf statt Magic-Mirror-Server-Proxy (siehe
        # widget_sources.py) - holt bei jedem Aufruf EINE Quelle (reihum
        # wechselnd), deshalb hier immer genau ein "Source"-Eintrag statt
        # der früheren Liste mehrerer gleichzeitig geladener Quellen.
        if data is None:
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
        # Zwei Labels pro Zeile (Ticker gedimmt, Wert farbig nach Kursrichtung)
        # statt einem kombinierten, zu langen Text - gleiches Prinzip wie
        # beim DEFCON-Widget. Passt so auf eine Zeile, z.B. "AAPL  325.59 $  +0.19%".
        rows = []
        row_w = card.w - 2 * card.padding
        for _ in range(ROWS_PER_PAGE):
            row = lv.obj(card.content_parent())
            row.set_size(row_w, 34)
            row.set_style_bg_opa(0, 0)
            row.set_style_border_width(0, 0)
            row.set_style_pad_all(0, 0)
            row.remove_flag(lv.obj.FLAG.SCROLLABLE)
            row.set_flex_flow(lv.FLEX_FLOW.ROW)
            row.set_flex_align(lv.FLEX_ALIGN.SPACE_BETWEEN, lv.FLEX_ALIGN.CENTER, lv.FLEX_ALIGN.CENTER)
            name_label = lv.label(row)
            name_label.set_style_text_font(lv.font_montserrat_24, 0)
            # Gleiche Farbe wie das Datum im Uhr-Widget (fg_dim) - siehe
            # widgets/clock_widget.py::date_label.
            name_label.set_style_text_color(_hex(COLORS["fg_dim"]), 0)
            value_label = lv.label(row)
            value_label.set_style_text_font(lv.font_montserrat_24, 0)
            rows.append((name_label, value_label))
        self._parts[cfg["id"]] = {"kind": "crypto", "rows": rows, "cfg": cfg}

    def _fetch_crypto(self, parts, data=None):
        # Direkter CoinGecko-Abruf statt Magic-Mirror-Server-Proxy (siehe
        # widget_sources.py) - kein API-Key nötig.
        if data is None:
            data = widget_sources.fetch_crypto(parts["cfg"])
        rows = parts["rows"]
        if not data or not data.get("ok") or not data.get("prices"):
            self._paint_message([n for n, v in rows], (data or {}).get("msg") or STRINGS["mm.crypto.no_data"])
            for _, v in rows:
                v.set_text("")
            return
        for i, (name_label, value_label) in enumerate(rows):
            if i < len(data["prices"]):
                p = data["prices"][i]
                change = p.get("change24h") or 0
                sign = "+" if change >= 0 else ""
                currency_symbol = widget_sources.CURRENCY_SYMBOLS.get(
                    (p.get("currency") or "").upper(), (p.get("currency") or "").upper())
                name_label.set_text(p.get("ticker", p.get("id", "?")))
                value_label.set_text("%s %s %s%.2f%%" % (
                    _format_price(p.get("price")), currency_symbol, sign, change))
                value_label.set_style_text_color(_hex(COLORS["up"] if change >= 0 else COLORS["down"]), 0)
            else:
                name_label.set_text("")
                value_label.set_text("")

    # ---- Wetter ----
    def _build_weather(self, card, cfg):
        # Titel zeigt den konfigurierten Ort statt der generischen
        # Beschriftung "WETTER" - fällt nur zurück, wenn (noch) kein
        # Standort gesetzt ist. cfg["title"] überschreibt das weiterhin,
        # falls mal ein anderer Titel gewünscht ist.
        title = cfg.get("title") or cfg.get("location") or STRINGS["mm.weather.default_title"]
        card.add_title(title, font=lv.font_montserrat_24, color=COLORS["accent"])

        # Icon + Temperatur nebeneinander in einer eigenen Zeile (wie beim
        # Core2-Projekt/Original-Magic-Mirror-3000) statt die Temperatur
        # allein zu zentrieren. Das Icon ist ein simples Vektor-Symbol aus
        # LVGL-Kreisen (siehe _draw_weather_icon()) - keine Bild-Datei,
        # kein Unicode/Emoji-Symbol, da sich Sonderzeichen auf diesem
        # Display schon früh als unzuverlässig herausgestellt haben
        # (siehe ascii_text.py).
        row = lv.obj(card.content_parent())
        row.set_size(card.w - 2 * card.padding, 90)
        row.set_style_bg_opa(0, 0)
        row.set_style_border_width(0, 0)
        row.set_style_pad_all(0, 0)
        row.remove_flag(lv.obj.FLAG.SCROLLABLE)
        row.set_flex_flow(lv.FLEX_FLOW.ROW)
        row.set_flex_align(lv.FLEX_ALIGN.CENTER, lv.FLEX_ALIGN.CENTER, lv.FLEX_ALIGN.CENTER)
        row.set_style_pad_column(16, 0)

        icon = lv.obj(row)
        icon.set_size(72, 72)
        icon.set_style_bg_opa(0, 0)
        icon.set_style_border_width(0, 0)
        icon.set_style_pad_all(0, 0)
        icon.remove_flag(lv.obj.FLAG.SCROLLABLE)

        # Temperatur ("Hero-Zahl", 48pt) - direkt als lv.label statt über
        # card.add_label(), da sie hier Teil der eigenen Icon-Zeile ist,
        # nicht der normalen Kachel-Spalte.
        temp = lv.label(row)
        temp.set_text("--")
        temp.set_style_text_font(lv.font_montserrat_48, 0)
        temp.set_style_text_color(_hex(COLORS["fg"]), 0)

        desc = card.add_label("", font=lv.font_montserrat_24, color=COLORS["fg_dim"])
        stats = card.add_label("", font=lv.font_montserrat_24, color=COLORS["fg_faint"])
        self._parts[cfg["id"]] = {
            "kind": "weather", "icon": icon, "icon_group": None,
            "temp": temp, "desc": desc, "stats": stats, "cfg": cfg,
        }

    # Regen-Blau ist bewusst NICHT Teil von theme.py/COLORS - rein
    # dekorativ fürs Wetter-Icon, keine Status-Bedeutung wie die anderen
    # Farben dort. Gleicher Blauton wie die "Extern"-Kurven im Web-UI.
    _WEATHER_ICON_RAIN_BLUE = "#3d7fff"
    # Silbrig-helles Mond-Grau, ebenfalls rein dekorativ (siehe oben) -
    # bewusst NICHT reines Weiß/COLORS["fg"], damit sich der Mond optisch
    # vom hellen "fg" der Sterne daneben abhebt (siehe _draw_weather_icon()
    # "clear_night"-Zweig).
    _WEATHER_ICON_MOON_SILVER = "#d8d8e0"

    def _draw_weather_icon(self, icon, group):
        """Zeichnet ein einfaches Wetter-Symbol aus LVGL-Kreisen (die
        einzige Grundform, die sich in diesem Projekt bisher überall
        zuverlässig verhalten hat, siehe traffic_light.py/sound_light.py)
        - kein Bild, kein Unicode/Emoji. icon.clean() entfernt zuerst alle
        vorherigen Formen, bevor neu gezeichnet wird (einfacher und
        robuster als einzelne Formen wiederzuverwenden/umzufärben)."""
        icon.clean()

        def dot(cx, cy, d, color):
            o = lv.obj(icon)
            o.set_size(d, d)
            o.remove_flag(lv.obj.FLAG.SCROLLABLE)
            o.set_style_radius(lv_const.RADIUS_CIRCLE, 0)
            o.set_style_border_width(0, 0)
            o.set_style_bg_color(_hex(color), 0)
            o.set_pos(cx - d // 2, cy - d // 2)

        gray = COLORS["fg_faint"]
        dark_gray = COLORS["fg_dim"]
        amber = COLORS["amber"]

        if group == "clear":
            dot(36, 36, 48, amber)
        elif group == "clear_night":
            # Mond (heller Kreis, leicht kleiner/versetzt als die Sonne
            # bei "clear") + drei kleine "Sterne" drumherum - dieselbe
            # Kreis-Bauweise wie alle anderen Icons hier. BEWUSST kein
            # ausgeschnittener Halbmond (bräuchte eine zum Karten-
            # Hintergrund exakt passende "Ausstanz"-Farbe, die je nach
            # Theme hell/dunkel unterschiedlich wäre, siehe theme.py) -
            # ein voller heller Kreis liest sich in diesem winzigen
            # 72x72-Format ohnehin kaum von einem Halbmond unterscheidbar.
            dot(40, 38, 38, self._WEATHER_ICON_MOON_SILVER)
            dot(14, 16, 7, COLORS["fg"])
            dot(60, 16, 6, COLORS["fg"])
            dot(58, 56, 6, COLORS["fg"])
        elif group == "cloudy":
            dot(24, 40, 34, gray)
            dot(44, 32, 40, gray)
            dot(54, 44, 30, gray)
        elif group == "fog":
            # Waagerechte Reihen statt einer Wolken-Silhouette - optisch
            # klar von "cloudy" unterscheidbar, gleiche Kreis-Bauweise.
            for cy in (22, 36, 50):
                for cx in (16, 36, 56):
                    dot(cx, cy, 16, gray)
        elif group in ("rain", "snow", "storm"):
            cloud_color = dark_gray if group == "storm" else gray
            dot(22, 26, 30, cloud_color)
            dot(40, 20, 36, cloud_color)
            dot(54, 30, 26, cloud_color)
            if group == "rain":
                drop_color = self._WEATHER_ICON_RAIN_BLUE
            elif group == "snow":
                drop_color = COLORS["fg"]
            else:
                drop_color = amber
            dot(22, 58, 11, drop_color)
            dot(38, 63, 11, drop_color)
            dot(54, 58, 11, drop_color)
        # unbekannte/fehlende Gruppe -> Icon bleibt leer, kein Absturz

    def _fetch_weather(self, parts, data=None):
        # Direkter Open-Meteo-Abruf statt Magic-Mirror-Server-Proxy (siehe
        # widget_sources.py) - kein API-Key nötig.
        if data is None:
            data = widget_sources.fetch_weather(parts["cfg"])
        if not data or not data.get("ok"):
            parts["temp"].set_text("--")
            parts["desc"].set_text((data or {}).get("msg") or STRINGS["mm.weather.unavailable"])
            parts["stats"].set_text("")
            return
        group = data.get("group")
        if group != parts.get("icon_group"):
            # Nur neu zeichnen, wenn sich die Kategorie tatsächlich
            # geändert hat (z.B. "clear" -> "rain") - spart unnötige
            # LVGL-Objekt-Erstellung bei jedem Refresh, wenn sich am
            # Wetter gerade nichts ändert.
            try:
                self._draw_weather_icon(parts["icon"], group)
                parts["icon_group"] = group
            except Exception as e:
                print("Wetter-Icon konnte nicht gezeichnet werden:", e)
        unit = data.get("unit", "C")
        # Eine Nachkommastelle statt gerundet auf ganze Grad (z.B. "21.4°C"
        # statt "21°C") - genauer, und macht die größere Schrift (48pt,
        # siehe _build_weather) sichtbar lebendiger, da sich die Anzeige
        # zwischen Aktualisierungen auch bei kleinen Änderungen bewegt.
        temp_val = data.get("temperature")
        parts["temp"].set_text("%.1f°%s" % (temp_val, unit) if temp_val is not None else "--")
        parts["desc"].set_text(data.get("description", ""))
        # Feuchte und Wind jeweils auf eigener Zeile (echter Zeilenumbruch
        # statt eines Trennzeichens) - bei einem Trennzeichen in einer
        # Zeile ist der Text zu lang für die Kachelbreite und bricht an
        # einer zufälligen/ungünstigen Stelle mitten im Wort um.
        lines = []
        if data.get("humidity") is not None:
            lines.append("%s %d%%" % (STRINGS["mm.weather.humidity"], round(data["humidity"])))
        if data.get("wind") is not None:
            lines.append("%s %d km/h" % (STRINGS["mm.weather.wind"], round(data["wind"])))
        parts["stats"].set_text("\n".join(lines))

    # ---- Aktien ----
    def _build_stocks(self, card, cfg):
        card.add_title(cfg.get("title") or STRINGS["mm.stocks.default_title"], font=lv.font_montserrat_24, color=COLORS["accent"])
        # Zwei Labels pro Zeile (Ticker gedimmt, Wert farbig) - siehe
        # _build_crypto oben für die ausführliche Begründung.
        rows = []
        row_w = card.w - 2 * card.padding
        for _ in range(ROWS_PER_PAGE):
            row = lv.obj(card.content_parent())
            row.set_size(row_w, 34)
            row.set_style_bg_opa(0, 0)
            row.set_style_border_width(0, 0)
            row.set_style_pad_all(0, 0)
            row.remove_flag(lv.obj.FLAG.SCROLLABLE)
            row.set_flex_flow(lv.FLEX_FLOW.ROW)
            row.set_flex_align(lv.FLEX_ALIGN.SPACE_BETWEEN, lv.FLEX_ALIGN.CENTER, lv.FLEX_ALIGN.CENTER)
            name_label = lv.label(row)
            name_label.set_style_text_font(lv.font_montserrat_24, 0)
            name_label.set_style_text_color(_hex(COLORS["fg_dim"]), 0)
            value_label = lv.label(row)
            value_label.set_style_text_font(lv.font_montserrat_24, 0)
            rows.append((name_label, value_label))
        self._parts[cfg["id"]] = {"kind": "stocks", "rows": rows, "cfg": cfg}

    def _fetch_stocks(self, parts, data=None):
        # Direkter Yahoo-Finance-Abruf statt Magic-Mirror-Server-Proxy
        # (siehe widget_sources.py) - kein API-Key nötig.
        if data is None:
            data = widget_sources.fetch_stocks(parts["cfg"])
        rows = parts["rows"]
        if not data or not data.get("ok") or not data.get("prices"):
            self._paint_message([n for n, v in rows], (data or {}).get("msg") or STRINGS["mm.crypto.no_data"])
            for _, v in rows:
                v.set_text("")
            return
        for i, (name_label, value_label) in enumerate(rows):
            if i >= len(data["prices"]):
                name_label.set_text("")
                value_label.set_text("")
                continue
            q = data["prices"][i]
            name_label.set_text(q.get("ticker", "?"))
            if q.get("price") is None:
                value_label.set_text(q.get("msg") or STRINGS["mm.stocks.na"])
                value_label.set_style_text_color(_hex(COLORS["fg_faint"]), 0)
                continue
            change = q.get("change24h") or 0
            sign = "+" if change >= 0 else ""
            value_label.set_text("%s%s %s%.2f%%" % (
                _format_price(q.get("price")), (" " + q["currency"]) if q.get("currency") else "", sign, change))
            value_label.set_style_text_color(_hex(COLORS["up"] if change >= 0 else COLORS["down"]), 0)

    # ---- Zitat ----
    def _build_quote(self, card, cfg):
        text = card.add_label("", font=lv.font_montserrat_24, color=COLORS["fg"])
        author = card.add_label("", font=lv.font_montserrat_24, color=COLORS["fg_dim"])
        self._parts[cfg["id"]] = {"kind": "quote", "text": text, "author": author}

    def _fetch_quote(self, parts, data=None):
        # Direkter ZenQuotes-Abruf statt Magic-Mirror-Server-Proxy (siehe
        # widget_sources.py) - kein API-Key nötig.
        if data is None:
            data = widget_sources.fetch_quote({})
        if not data or not data.get("ok"):
            parts["text"].set_text((data or {}).get("msg") or STRINGS["mm.quote.unavailable"])
            parts["author"].set_text("")
            return
        parts["text"].set_text(data.get("text", ""))
        parts["author"].set_text(data.get("author", ""))

    # ---- Server-Status (Docker-Container, wie im ursprünglichen Magic-
    # Mirror-3000-Projekt) - echte farbige Kreise statt Text-Symbolen,
    # maximal 4 gleichzeitig sichtbar (ROWS_PER_PAGE), bei mehr Containern
    # rotiert die Anzeige seitenweise durch (wie News/Warnungen) ----
    def _build_server_status(self, card, cfg):
        card.add_title(cfg.get("title") or STRINGS["mm.server.default_title"],
                        font=lv.font_montserrat_24, color=COLORS["accent"])
        summary = card.add_label("", font=lv.font_montserrat_24, color=COLORS["accent"])
        rows = []
        row_w = card.w - 2 * card.padding
        for _ in range(ROWS_PER_PAGE):
            row = lv.obj(card.content_parent())
            row.set_size(row_w, 32)
            row.set_style_bg_opa(0, 0)
            row.set_style_border_width(0, 0)
            row.set_style_pad_all(0, 0)
            row.remove_flag(lv.obj.FLAG.SCROLLABLE)
            row.set_flex_flow(lv.FLEX_FLOW.ROW)
            row.set_flex_align(lv.FLEX_ALIGN.START, lv.FLEX_ALIGN.CENTER, lv.FLEX_ALIGN.CENTER)
            row.set_style_pad_column(10, 0)
            dot = lv.obj(row)
            dot.set_size(16, 16)
            dot.set_style_radius(lv_const.RADIUS_CIRCLE, 0)
            dot.set_style_border_width(0, 0)
            dot.remove_flag(lv.obj.FLAG.SCROLLABLE)
            name_label = lv.label(row)
            name_label.set_style_text_font(lv.font_montserrat_24, 0)
            name_label.set_style_text_color(_hex(COLORS["fg"]), 0)
            rows.append((dot, name_label))
        self._parts[cfg["id"]] = {
            "kind": "server_status", "summary": summary, "rows": rows,
            "containers": [], "page": 0,
        }
        self._start_rotation_timer(cfg["id"], cfg.get("rotation_s", 15),
                                    lambda: self._repaint_pages(cfg["id"], "containers",
                                                                 self._paint_server_page))

    def _fetch_server_status(self, parts, data=None):
        # Über den bestehenden Magic-Mirror-Server-Mechanismus (siehe
        # config.py "server.enabled"/"server.base_url", api_client.py) -
        # dafür muss ein Server erreichbar sein, der unter /api/server-status
        # ein JSON wie {"ok": true, "running": 3, "total": 5, "containers":
        # [{"name": "...", "status": "running"|"..."}]} liefert (siehe
        # ursprüngliches Magic-Mirror-3000-Projekt).
        if data is None:
            data = self._safe_get("/api/server-status")
        if not data or not data.get("ok"):
            parts["summary"].set_text((data or {}).get("msg") or STRINGS["mm.server.unavailable"])
            self._paint_message([n for d, n in parts["rows"]], "")
            for d, n in parts["rows"]:
                d.set_style_bg_color(_hex(COLORS["fg_faint"]), 0)
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
            self._paint_message([n for d, n in rows], STRINGS["mm.server.no_containers"])
            for d, n in rows:
                d.set_style_bg_color(_hex(COLORS["fg_faint"]), 0)
            return
        chunk = containers[parts["page"] * ROWS_PER_PAGE: (parts["page"] + 1) * ROWS_PER_PAGE]
        for i, (dot, name_label) in enumerate(rows):
            if i < len(chunk):
                c = chunk[i]
                running = c.get("status") == "running"
                dot.set_style_bg_color(_hex(COLORS["up"] if running else COLORS["red"]), 0)
                name_label.set_text(c.get("name", "?"))
            else:
                dot.set_style_bg_color(_hex(COLORS["bg"]), 0)
                name_label.set_text("")

    # ---- Warnungen ----
    def _build_warnings(self, card, cfg):
        card.add_title(cfg.get("title") or STRINGS["mm.warn.default_title"], font=lv.font_montserrat_24, color=COLORS["accent"])
        # Einzelne Meldung statt Liste - bei mehreren Warnungen wird im
        # Wechsel durchrotiert (gleiches Prinzip wie bei News, siehe
        # _repaint_news), statt mehrere Zeilen gleichzeitig zu zeigen.
        # Farbe wird dynamisch gesetzt (siehe _paint_warning_text unten) -
        # Ampel-Prinzip: grün ohne Meldungen, amber bei mittlerer,
        # rot bei höchster Warnstufe. Start-Farbe grün, bis der erste
        # Abruf durch ist.
        text = card.add_label("", font=lv.font_montserrat_24, color=COLORS["up"])
        self._parts[cfg["id"]] = {"kind": "warnings", "text": text, "items": [], "idx": 0, "cfg": cfg}
        self._start_rotation_timer(cfg["id"], cfg.get("rotation_s", 8),
                                    lambda: self._repaint_warning(cfg["id"]))

    def _fetch_warnings(self, parts, data=None):
        # Direkter Abruf von warnung.bund.de statt Magic-Mirror-Server-
        # Proxy (siehe widget_sources.py) - braucht einen Amtlichen
        # Regionalschlüssel (ARS) in der Widget-Config.
        if data is None:
            data = widget_sources.fetch_warnings(parts["cfg"])
        if not data or not data.get("ok"):
            parts["items"] = []
            parts["text"].set_text((data or {}).get("msg") or STRINGS["mm.warn.unavailable"])
            return
        warnings = data.get("warnings", [])
        parts["items"] = warnings
        parts["idx"] = 0
        if not warnings:
            parts["text"].set_style_text_color(_hex(COLORS["up"]), 0)
            parts["text"].set_text(STRINGS["mm.warn.none"])
        else:
            self._paint_warning_text(parts["text"], warnings[0])

    def _repaint_warning(self, widget_id):
        parts = self._parts[widget_id]
        items = parts["items"]
        if not items:
            return
        parts["idx"] = (parts["idx"] + 1) % len(items)
        self._paint_warning_text(parts["text"], items[parts["idx"]])

    def _paint_warning_text(self, label, warning):
        # Ampel-Prinzip wie bei der Luftqualität: grün/amber/rot statt
        # immer rot - "severity" kommt schon fertig klassifiziert von
        # widget_sources.py::fetch_warnings() mit ("critical"/"moderate").
        severity_color = {"critical": COLORS["red"], "moderate": COLORS["amber"]}
        label.set_style_text_color(_hex(severity_color.get(warning.get("severity"), COLORS["up"])), 0)
        label.set_text(warning.get("title", ""))

    # ---- Luftqualität (Magic-Mirror-Variante, unabhängig vom eigenen BME688-Widget) ----
    def _build_air_quality_mirror(self, card, cfg):
        card.add_title(cfg.get("title") or STRINGS["mm.aqi.default_title"], font=lv.font_montserrat_24, color=COLORS["accent"])
        # 48pt statt 24pt - wie die Temperatur im Wetter-Widget/der Score
        # im lokalen Luftqualität-Widget (screens/dashboard.py), damit alle
        # "Hero-Zahl"-Kacheln optisch einheitlich wirken.
        value = card.add_label("--", font=lv.font_montserrat_48, color=COLORS["fg"])
        label = card.add_label("", font=lv.font_montserrat_24, color=COLORS["fg_dim"])
        # Zusätzliche Messwerte (PM2.5/PM10/Ozon) wie im Core2-Referenz-
        # widget (dort als eigene Zeilen rechts neben der AQI-Ampel) - hier
        # als eine zusätzliche Zeile, gleiche Farbe wie die Windgeschwindigkeit
        # im Wetter-Widget (fg_faint).
        details = card.add_label("", font=lv.font_montserrat_24, color=COLORS["fg_faint"])
        self._parts[cfg["id"]] = {"kind": "air_quality_mirror", "value": value, "label": label,
                                   "details": details, "cfg": cfg}

    def _fetch_air_quality_mirror(self, parts, data=None):
        # Direkter Open-Meteo-Abruf statt Magic-Mirror-Server-Proxy (siehe
        # widget_sources.py) - Außenluft, unabhängig vom lokalen BME688.
        if data is None:
            data = widget_sources.fetch_air_quality(parts["cfg"])
        if not data or not data.get("ok"):
            parts["value"].set_text("--")
            parts["label"].set_text((data or {}).get("msg") or STRINGS["mm.aqi.unavailable"])
            parts["details"].set_text("")
            return
        aqi = data.get("aqi")
        parts["value"].set_text(str(round(aqi)) if aqi is not None else "--")
        band = ("good" if aqi is not None and aqi <= 40 else
                "moderate" if aqi is not None and aqi <= 80 else "poor")
        band_key = {"good": "mm.aqi.good", "moderate": "mm.aqi.moderate", "poor": "mm.aqi.poor"}[band]
        parts["value"].set_style_text_color(_hex(
            COLORS["up"] if band == "good" else COLORS["amber"] if band == "moderate" else COLORS["red"]), 0)
        parts["label"].set_text("%s%s" % (STRINGS[band_key], (" - " + data["location"]) if data.get("location") else ""))

        # PM2.5/PM10 zusammen auf einer Zeile (kurz genug), Ozon auf einer
        # eigenen zweiten Zeile darunter (echter Zeilenumbruch statt
        # Trennzeichen, gleicher Grund wie beim Wetter-Widget oben).
        pm_bits = []
        if data.get("pm25") is not None:
            pm_bits.append("PM2.5 %.1f" % data["pm25"])
        if data.get("pm10") is not None:
            pm_bits.append("PM10 %.1f" % data["pm10"])
        lines = []
        if pm_bits:
            lines.append(" - ".join(pm_bits))
        if data.get("ozone") is not None:
            lines.append("%s %.1f" % (STRINGS["mm.aqi.ozone"], data["ozone"]))
        parts["details"].set_text("\n".join(lines))

    # ---- Elbe-Pegel ----
    def _build_elbe_pegel(self, card, cfg):
        card.add_title(cfg.get("title") or STRINGS["mm.pegel.default_title"], font=lv.font_montserrat_24, color=COLORS["accent"])
        value = card.add_label("--", font=lv.font_montserrat_24, color=COLORS["fg"])
        station = card.add_label("", font=lv.font_montserrat_24, color=COLORS["fg_faint"])
        self._parts[cfg["id"]] = {"kind": "elbe_pegel", "value": value, "station": station, "cfg": cfg}

    def _fetch_elbe_pegel(self, parts, data=None):
        # Direkter PEGELONLINE/WSV-Abruf statt Magic-Mirror-Server-Proxy
        # (siehe widget_sources.py) - kein API-Key nötig.
        if data is None:
            data = widget_sources.fetch_elbe_pegel(parts["cfg"])
        if not data or not data.get("ok"):
            parts["value"].set_text("--")
            parts["station"].set_text((data or {}).get("msg") or STRINGS["mm.pegel.unavailable"])
            return
        trend = {1: " (+)", -1: " (-)"}.get(_sign(data.get("trend")), "")
        parts["value"].set_text("%s %s%s" % (data.get("value_cm", "--"), data.get("unit", "cm"), trend))
        parts["station"].set_text(data.get("station_name", ""))

    # ---- Apocalypse EWS ----
    def _build_ews(self, card, cfg):
        card.add_title(cfg.get("title") or STRINGS["mm.ews.default_title"], font=lv.font_montserrat_24, color=COLORS["accent"])
        # 48pt statt 24pt - wie Uhr/Wetter/Luftqualität/Raumklima, damit
        # alle "Hero-Zahl"-Kacheln optisch einheitlich wirken.
        level = card.add_label("--", font=lv.font_montserrat_48, color=COLORS["fg"])
        stats = card.add_label("", font=lv.font_montserrat_24, color=COLORS["fg_dim"])
        self._parts[cfg["id"]] = {"kind": "ews", "level": level, "stats": stats}

    def _fetch_ews(self, parts, data=None):
        # Direkter Abruf des öffentlichen JSON-Snapshots statt Magic-
        # Mirror-Server-Proxy (siehe widget_sources.py) - kein API-Key nötig.
        if data is None:
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
        # Zwei Labels pro Zeile (Name gedimmt, Wert farbig nach
        # Schweregrad) statt einem kombinierten Text - ein einzelnes LVGL-
        # Label kann nur EINE Farbe auf einmal haben, siehe Core2-Vorbild
        # (dort zwei getrennte d.text()-Aufrufe für Name/Wert).
        rows = []
        row_w = card.w - 2 * card.padding
        for _ in range(ROWS_PER_PAGE):
            row = lv.obj(card.content_parent())
            row.set_size(row_w, 34)
            row.set_style_bg_opa(0, 0)
            row.set_style_border_width(0, 0)
            row.set_style_pad_all(0, 0)
            row.remove_flag(lv.obj.FLAG.SCROLLABLE)
            row.set_flex_flow(lv.FLEX_FLOW.ROW)
            row.set_flex_align(lv.FLEX_ALIGN.SPACE_BETWEEN, lv.FLEX_ALIGN.CENTER, lv.FLEX_ALIGN.CENTER)
            name_label = lv.label(row)
            name_label.set_style_text_font(lv.font_montserrat_24, 0)
            name_label.set_style_text_color(_hex(COLORS["fg_dim"]), 0)
            value_label = lv.label(row)
            value_label.set_style_text_font(lv.font_montserrat_24, 0)
            value_label.set_style_text_color(_hex(COLORS["fg"]), 0)
            rows.append((name_label, value_label))
        self._parts[cfg["id"]] = {"kind": "defcon", "rows": rows, "regions": [], "page": 0, "cfg": cfg}
        self._start_rotation_timer(cfg["id"], cfg.get("rotation_s", 15),
                                    lambda: self._repaint_pages(cfg["id"], "regions",
                                                                 self._paint_defcon_page))

    def _fetch_defcon(self, parts, data=None):
        # Direkter Abruf des nutzereigenen DEFCON-Endpunkts statt Magic-
        # Mirror-Server-Proxy (siehe widget_sources.py) - URL/API-Key
        # kommen aus der Widget-Config ("url"/"api_key").
        if data is None:
            data = widget_sources.fetch_defcon(parts["cfg"])
        if not data or not data.get("ok"):
            self._paint_message([n for n, v in parts["rows"]], (data or {}).get("msg") or STRINGS["mm.defcon.unavailable"])
            for _, v in parts["rows"]:
                v.set_text("")
            parts["regions"] = []
            return
        parts["regions"] = data.get("regions", [])
        parts["page"] = 0
        if not parts["regions"]:
            self._paint_message([n for n, v in parts["rows"]], STRINGS["mm.defcon.no_regions"])
            for _, v in parts["rows"]:
                v.set_text("")
        else:
            self._paint_defcon_page(parts)

    def _paint_defcon_page(self, parts):
        rows = parts["rows"]
        chunk = parts["regions"][parts["page"] * ROWS_PER_PAGE:(parts["page"] + 1) * ROWS_PER_PAGE]
        # Farbe nach Schweregrad, wie im Core2-Referenzwidget (rot/amber/
        # grün) - severity kommt schon fertig klassifiziert von
        # widget_sources.py::_defcon_severity() mit.
        severity_color = {"critical": COLORS["red"], "warning": COLORS["amber"], "normal": COLORS["up"]}
        for i, (name_label, value_label) in enumerate(rows):
            if i < len(chunk):
                r = chunk[i]
                name_label.set_text(r.get("name", "?"))
                value_label.set_text("%.1f" % r.get("value", 0))
                value_label.set_style_text_color(_hex(severity_color.get(r.get("severity"), COLORS["fg"])), 0)
            else:
                name_label.set_text("")
                value_label.set_text("")

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
        parts["text"].set_text(to_ascii(items[parts["idx"]]))

    # ---- Notizen (lokale Konfigurationsdaten, kein API-Call) ----
    # ---- Logo (eigenes PNG als Bild, bis zu 2 Kacheln breit) - siehe
    # tools/png_to_lvgl.py für die Umwandlung. Diese Firmware hat KEINEN
    # PNG-Decoder eingebaut (bestätigt per diagnose_image.py), deshalb
    # rohe, bereits entpackte RGB565A8-Pixeldaten statt einer echten PNG-
    # Datei (bestätigt funktionierend per diagnose_image_raw.py) - erster
    # Einsatz von lv.image in diesem Projekt, daher komplett in try/except
    # abgesichert.
    def _build_logo(self, card, cfg):
        filename = cfg.get("file")
        err_msg = None
        dsc = None
        raw = None
        if not filename:
            err_msg = "Keine Datei konfiguriert"
        else:
            try:
                with open(filename, "rb") as f:
                    header = f.read(4)
                    w = header[0] | (header[1] << 8)
                    h = header[2] | (header[3] << 8)
                    pixel_bytes = w * h * 2
                    rgb565_data = f.read(pixel_bytes)
                    alpha_data = f.read(w * h)
                if len(rgb565_data) != pixel_bytes or len(alpha_data) != w * h:
                    raise ValueError("Datei unvollständig/beschädigt")
                raw = rgb565_data + alpha_data
                dsc = lv.image_dsc_t({
                    "header": {"w": w, "h": h, "cf": lv.COLOR_FORMAT.RGB565A8},
                    "data_size": len(raw),
                    "data": raw,
                })
                img = lv.image(card.content_parent())
                img.set_src(dsc)
                img.center()
            except Exception as e:
                err_msg = "Logo konnte nicht geladen werden: %s" % e
                print("_build_logo:", err_msg)

        if err_msg:
            card.add_label(err_msg, font=lv.font_montserrat_24, color=COLORS["fg_faint"])
        # dsc/raw hier im eigenen, garantiert lebendigen Dict festhalten
        # (NICHT als Attribut am lv.image-Objekt selbst - das erlauben
        # diese nativen LVGL-Objekte nicht, siehe Fehlermeldung beim
        # ersten Testlauf: "'image' object has no attribute '_logo_dsc'")
        # - sonst könnte der Garbage Collector die Rohdaten einsammeln,
        # während lv.image sie noch zur Anzeige braucht.
        self._parts[cfg["id"]] = {"kind": "logo", "_dsc": dsc, "_raw": raw}

    def _build_todo(self, card, cfg):
        card.add_title(cfg.get("title") or STRINGS["mm.todo.default_title"], font=lv.font_montserrat_24, color=COLORS["accent"])
        row_w = card.w - 2 * card.padding
        rows_per_page = _todo_rows_for_card(card, cfg.get("_row_span", 1))
        rows = []
        for _ in range(rows_per_page):
            row = lv.obj(card.content_parent())
            row.set_size(row_w, 40)
            row.set_style_bg_opa(0, 0)
            row.set_style_border_width(0, 0)
            row.set_style_pad_all(0, 0)
            row.remove_flag(lv.obj.FLAG.SCROLLABLE)
            # Antippbar machen (fürs Abhaken direkt auf dem Display, siehe
            # _toggle_todo_item()) - erweiterter Treffbereich wie bei den
            # Licht/Steckdose-Schaltern, da eine ganze Zeile ein kleineres
            # Ziel als ein Schalter ist.
            row.add_flag(lv.obj.FLAG.CLICKABLE)
            row.set_ext_click_area(10)
            row.set_flex_flow(lv.FLEX_FLOW.ROW)
            row.set_flex_align(lv.FLEX_ALIGN.START, lv.FLEX_ALIGN.CENTER, lv.FLEX_ALIGN.CENTER)
            row.set_style_pad_column(12, 0)

            box = lv.obj(row)
            box.set_size(26, 26)
            box.set_style_radius(4, 0)
            box.set_style_border_width(2, 0)
            box.set_style_border_color(_hex(COLORS["fg_faint"]), 0)
            box.set_style_bg_opa(0, 0)
            box.remove_flag(lv.obj.FLAG.SCROLLABLE)

            label = lv.label(row)
            label.set_style_text_font(lv.font_montserrat_24, 0)
            label.set_style_text_color(_hex(COLORS["fg"]), 0)

            rows.append({"row": row, "box": box, "label": label})

        widget_id = cfg["id"]
        self._parts[widget_id] = {
            "kind": "todo", "rows": rows, "items": list(cfg.get("items", [])),
            "page": 0, "rows_per_page": rows_per_page,
        }
        for i, r in enumerate(rows):
            r["row"].add_event_cb(self._make_todo_toggle_handler(widget_id, i), lv.EVENT.CLICKED, None)

        self._paint_todo_page(widget_id)
        # Nur rotieren lassen, wenn tatsächlich mehr Einträge da sind, als
        # auf eine Seite passen - sonst unnötig alle paar Sekunden dieselbe
        # Seite neu zeichnen.
        if len(cfg.get("items", [])) > rows_per_page:
            self._start_rotation_timer(widget_id, cfg.get("rotation_s", 8),
                                        lambda: self._advance_todo_page(widget_id))

    def _make_todo_toggle_handler(self, widget_id, row_index_in_page):
        # Eigene Methode statt Lambda direkt in der Schleife oben, damit
        # jede Zeile ihren EIGENEN, zum Aufbauzeitpunkt festen Index
        # bekommt (das übliche "späte Bindung in Schleifen"-Problem, siehe
        # gleiches Muster bei den Atom-Schalter-Handlern). Absicherung
        # läuft über @lvgl_safe_callback (Optimierungs-Backlog Punkt 2,
        # siehe HANDOFF.md) statt über ein eigenes try/except.
        @lvgl_safe_callback(label="Notiz-Umschaltung %s[%d]" % (widget_id, row_index_in_page))
        def _handler(e):
            self._toggle_todo_item(widget_id, row_index_in_page)
        return _handler

    def _toggle_todo_item(self, widget_id, row_index_in_page):
        parts = self._parts.get(widget_id)
        if parts is None:
            return
        items = parts["items"]
        abs_index = parts["page"] * parts["rows_per_page"] + row_index_in_page
        if abs_index >= len(items):
            return  # leere Zeile angetippt (weniger Einträge als Zeilen) - nichts zu tun
        items[abs_index]["done"] = not items[abs_index].get("done")
        self._paint_todo_page(widget_id)

        # Sofort dauerhaft speichern - frisch von der Platte laden, NICHT
        # ein evtl. veraltetes cfg-Objekt weiterverwenden (gleiches Prinzip
        # wie bei den Speichern-Endpunkten in web_server.py), damit ein
        # gleichzeitig im Web-UI geöffnetes /notes nach einem Neuladen den
        # aktuellen Stand sieht statt etwas zu überschreiben.
        try:
            fresh_cfg = config.load()
            # Bugfix (Multi-Dashboard-Feature, siehe HANDOFF.md): hier
            # stand noch fresh_cfg["screens"]["dashboard"]["widgets"] - die
            # alte Einzel-Struktur, die es seit "screens.dashboards"
            # (Liste aus 2 Profilen) nicht mehr gibt. todo_widget war
            # dadurch IMMER None, das Speichern lief still ins Leere -
            # ein auf dem Display abgehaktes Notiz-Item sah zwar sofort
            # richtig aus (_paint_todo_page() lief ja), verschwand aber
            # beim nächsten Neustart/Live-Reload wieder, ohne dass
            # irgendwo eine sichtbare Fehlermeldung aufgetaucht wäre.
            # Das Widget gehört zwangsläufig zum gerade AKTIVEN Profil
            # (nur dessen DashboardScreen ist überhaupt gebaut/sichtbar,
            # wenn diese Methode durch einen Antipp-Callback läuft).
            profiles = fresh_cfg.get("screens", {}).get("dashboards", [])
            active_id = fresh_cfg.get("screens", {}).get("active_dashboard_id")
            active_profile = next((p for p in profiles if p.get("id") == active_id),
                                   profiles[0] if profiles else {})
            widgets = active_profile.get("widgets", [])
            todo_widget = next((w for w in widgets if w.get("id") == widget_id), None)
            if todo_widget is not None:
                todo_widget["items"] = items
                config.save(fresh_cfg)
        except Exception as e:
            print("Notiz-Speichern fehlgeschlagen:", e)

    def _advance_todo_page(self, widget_id):
        parts = self._parts.get(widget_id)
        if parts is None:
            return
        items = parts["items"]
        rpp = parts["rows_per_page"]
        page_count = max(1, (len(items) + rpp - 1) // rpp)
        parts["page"] = (parts["page"] + 1) % page_count
        self._paint_todo_page(widget_id)

    def _paint_todo_page(self, widget_id):
        parts = self._parts.get(widget_id)
        if parts is None:
            return
        items = parts["items"]
        rpp = parts["rows_per_page"]
        start = parts["page"] * rpp
        chunk = items[start:start + rpp]
        for i, r in enumerate(parts["rows"]):
            if i < len(chunk):
                it = chunk[i]
                done = bool(it.get("done"))
                r["label"].set_text(to_ascii(it.get("text", "")))
                r["label"].set_style_text_color(_hex(COLORS["fg_faint"] if done else COLORS["fg"]), 0)
                # Umrandung bewusst IMMER grau (Nutzerwunsch), auch wenn erledigt:
                # erledigt = grün gefüllt, offen = grau umrandet und leer.
                r["box"].remove_flag(lv.obj.FLAG.HIDDEN)
                r["box"].set_style_bg_color(_hex(COLORS["up"]), 0)
                r["box"].set_style_bg_opa(255 if done else 0, 0)
                r["box"].set_style_border_color(_hex(COLORS["fg_faint"]), 0)
            else:
                r["label"].set_text("" if items else STRINGS["mm.todo.no_items"])
                # Leere Zeile (weniger Einträge als Zeilen): Box ausblenden.
                # Vorher blieb hier die Farbe der vorherigen Seite stehen
                # (grüne Umrandung an Zeilen ohne Eintrag nach dem Umblättern).
                r["box"].set_style_bg_opa(0, 0)
                r["box"].set_style_border_color(_hex(COLORS["fg_faint"]), 0)
                r["box"].add_flag(lv.obj.FLAG.HIDDEN)

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
        parts["stats"].set_text("%.1f° - %.0f%%" % (
            reading.temp_c or 0, reading.humidity or 0))
        if reading.iaq_score is not None:
            band = ("excellent" if reading.iaq_score >= 80 else "good" if reading.iaq_score >= 60 else
                    "moderate" if reading.iaq_score >= 40 else "poor" if reading.iaq_score >= 20 else "very_poor")
            parts["iaq"].set_text("IAQ %.0f - %s" % (reading.iaq_score, STRINGS["mm.iaq." + band]))
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
    #
    # Netzwerk-Widgets (siehe _BACKGROUND_FETCHERS) laufen jetzt über
    # einen Hintergrund-Thread (fetch_worker.py, per diagnose_thread.py
    # bestätigt echt parallel auf dem ESP32-P4) - der Hauptthread (LVGL/
    # Touch/Uhr) blockiert dadurch NICHT mehr während einer laufenden
    # Anfrage. Vorher lief das synchron und hat bei jedem Abruf die
    # GESAMTE Oberfläche für die Dauer der Anfrage eingefroren (auch die
    # Uhr, die einen eigenen 1s-Timer hat - "hängt gelegentlich 1-2s").
    #
    # Ablauf pro Zyklus:
    #   1. Alle seit dem letzten Zyklus im Hintergrund fertig gewordenen
    #      Ergebnisse abholen und anwenden (set_text() etc. - das darf
    #      NUR hier im Hauptthread passieren, nie im Hintergrund-Thread
    #      selbst, siehe fetch_worker.py-Docstring).
    #   2. Fällige Widgets ermitteln, nach Dringlichkeit relativ zum
    #      eigenen Intervall sortieren (kurze Intervalle bevorzugt).
    #   3. Für Netzwerk-Widgets: Hintergrund-Abruf ANSTOSSEN (nicht
    #      abwarten!) - läuft parallel weiter, Ergebnis kommt im
    #      nächsten oder übernächsten Zyklus über Schritt 1 zurück.
    #      Für rein lokale Widgets (z.B. env_sensor_mirror): weiterhin
    #      ganz normal synchron, kein Hintergrund-Thread nötig/sinnvoll.
    #
    # AUSNAHME - Boot-Burst: Der ALLERERSTE refresh()-Aufruf ignoriert
    # das MAX_FETCHES_PER_CYCLE-Limit komplett und stößt ALLE fälligen
    # Hintergrund-Abrufe auf einmal an (die Hintergrund-Kapazität selbst
    # bleibt trotzdem durch fetch_worker.MAX_CONCURRENT begrenzt) - so
    # ist das Dashboard gleich nach dem Boot zügig komplett befüllt.
    # ------------------------------------------------------------------
    MAX_FETCHES_PER_CYCLE = 3

    def refresh(self):
        now = time.time()

        # Schritt 1: fertige Hintergrund-Ergebnisse anwenden.
        for widget_id, data in fetch_worker.collect_results().items():
            self._last_data[widget_id] = data  # Web-UI-Live-Spiegel, siehe __init__-Docstring
            parts = self._parts.get(widget_id)
            if parts is None:
                continue
            fetch_fn = getattr(self, "_fetch_" + parts["kind"], None)
            if fetch_fn:
                try:
                    fetch_fn(parts, data=data)
                except Exception as e:
                    print("Fehler beim Anwenden von Hintergrund-Daten (%s):" % widget_id, e)

        # Schritt 2: fällige Widgets sammeln + priorisieren.
        due = []
        for widget_id, parts in self._parts.items():
            kind = parts["kind"]
            if kind in ("clock", "todo", "pc_status", "logo"):
                # clock/todo/logo: ticken selbst bzw. sind rein statisch.
                # pc_status: läuft über einen eigenen, entkoppelten 3s-Task
                # (siehe update_pc_status() unten/main.py::pc_status_task()),
                # nicht über diesen 20s-Grundtakt.
                continue
            if fetch_worker.in_backoff(widget_id):
                # Kürzlich wiederholt fehlgeschlagen (siehe fetch_worker.py-
                # Docstring, Optimierungs-Backlog Punkt 5) - erst gar nicht
                # als "fällig" behandeln, sonst würde last_fetch nie
                # aktualisiert und die Dringlichkeit wüchse ungebremst
                # weiter, wodurch dieses eine aussichtslose Widget bei
                # jedem Zyklus die knappen MAX_FETCHES_PER_CYCLE-Plätze für
                # tatsächlich sendebereite andere Widgets wegnehmen würde.
                continue
            interval = FETCH_INTERVAL_S.get(kind, 300)
            last = self._last_fetch.get(widget_id, 0)
            elapsed = now - last
            if elapsed < interval:
                continue
            urgency = elapsed / interval  # >= 1.0, größer = dringender
            due.append((urgency, widget_id, kind, parts))
        due.sort(key=lambda t: t[0], reverse=True)

        # Schritt 3: abarbeiten - Netzwerk-Widgets in den Hintergrund,
        # rein lokale Widgets weiterhin synchron.
        limit = len(due) if not self._first_refresh_done else self.MAX_FETCHES_PER_CYCLE
        for urgency, widget_id, kind, parts in due[:limit]:
            bg_fetcher = _BACKGROUND_FETCHERS.get(kind)
            if bg_fetcher is not None:
                if fetch_worker.submit(widget_id, bg_fetcher, self, parts):
                    self._last_fetch[widget_id] = now
                # Sonst: Hintergrund-Kapazität gerade voll - last_fetch
                # NICHT aktualisieren, bleibt fällig fürs nächste Mal.
            else:
                self._last_fetch[widget_id] = now
                fetch_fn = getattr(self, "_fetch_" + kind, None)
                if fetch_fn:
                    fetch_fn(parts)
        self._first_refresh_done = True

    def update_pc_status(self):
        """Eigener, schneller (3s) Update-Pfad für Computer-Status-Widgets,
        unabhängig vom trägen 20s-Grundtakt der übrigen Online-Widgets -
        aufgerufen von main.py::pc_status_task(). Docker-Status bleibt
        bewusst am normalen, gedrosselten Weg (FETCH_INTERVAL_S) - eine
        lokale, aber nicht ganz so zeitkritische Info wie die CPU/GPU-
        Auslastung.

        WICHTIG: Läuft jetzt auch über den Hintergrund-Thread (siehe
        fetch_worker.py) statt blockierend im Hauptthread - das lief
        bisher versehentlich synchron, obwohl gerade DIESES Widget am
        häufigsten (alle 3s statt alle 15 Minuten) einen Netzwerk-Zugriff
        auslöst und damit der wahrscheinlichste verbleibende Grund für
        das gemeldete gelegentliche Hängen der Uhr war."""
        pc_status_ids = [wid for wid, parts in self._parts.items() if parts.get("kind") == "pc_status"]
        if not pc_status_ids:
            return

        # 1. Ergebnisse von einem vorherigen Aufruf anwenden, falls
        # inzwischen fertig geworden (siehe collect_results()-Docstring -
        # NUR unsere eigenen IDs, nicht die des normalen 20s-Zyklus).
        for widget_id, data in fetch_worker.collect_results(widget_ids=pc_status_ids).items():
            self._last_data[widget_id] = data  # Web-UI-Live-Spiegel, siehe __init__-Docstring
            parts = self._parts.get(widget_id)
            if parts is not None:
                try:
                    self._fetch_pc_status(parts, data=data)
                except Exception as e:
                    print("Fehler beim Anwenden von PC-Status-Daten:", e)

        # 2. Neuen Abruf im Hintergrund anstoßen (nicht abwarten!).
        for widget_id in pc_status_ids:
            # Eigener, kurzer Backoff (3s..15s): der globale (30s..30min) würde
            # die Anzeige nach kurzen Ausfällen (PC aus, WLAN-Aussetzer)
            # bis zu 30 Minuten einfrieren lassen.
            fetch_worker.submit(widget_id, _BACKGROUND_FETCHERS["pc_status"], self,
                                self._parts[widget_id], backoff_base_s=3, backoff_max_s=15)
