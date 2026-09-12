"""
Sensor-Dashboard fuer den internen BME688 - eigener, vollwertiger ZWEITER
BILDSCHIRM (m5ui.M5Page), kein Overlay mehr. Umschalten zwischen diesem
und dem Haupt-Dashboard passiert ueber zwei Buttons im Burger-Menue
(siehe burger_menu.py) - genau wie vor der Konsolidierung auf ein
einzelnes Dashboard, nur diesmal bewusst als zweiter Screen für dieses
eine Zusatz-Feature statt wieder vier Screens.

Zeigt Temperatur (linke Achse, eigene Skala) sowie Feuchte und IAQ
(rechte Achse, beide natürlich 0-100, teilen sich die Skala) gleichzeitig
in einer Grafik. Luftdruck (~1013) passt auf keine der beiden Skalen und
steht nur in der Live-Werte-Zeile, nicht im Diagramm.

Datenquelle: bevorzugt die SD-Karte (sensors/sd_reader.py::SDReader),
sobald die gemountet werden kann (aktuell ENODEV, siehe HANDOFF.md) -
faellt bis dahin automatisch auf den In-RAM-Ringpuffer zurueck
(sensors/history.py::SensorHistory, dieselbe Instanz, die screens/
dashboard.py bei jedem Refresh-Zyklus fuellt). Sobald die SD-Karte
funktioniert, braucht dieser Screen KEINE Aenderung - _get_rows() prueft
das automatisch bei jedem Aufruf neu.

WICHTIG: lv.chart wird hier zum ersten Mal in diesem Projekt genutzt.
Erster Testlauf zeigte einen komplett schwarzen Grafikbereich (Rest -
Titel/Legende/Dropdown - funktionierte einwandfrei), OHNE dass der
Chart-Aufbau selbst eine Exception geworfen hat. Diese Version setzt
zusätzlich eine explizite Linienbreite und ruft invalidate() auf; falls
es beim naechsten Test IMMER NOCH schwarz bleibt, steht dank der
dir(chart)-Diagnoseausgabe im Log sofort da, welche Methoden es auf
dieser Firmware tatsächlich gibt.
"""

import time

try:
    import lvgl as lv
    _HAS_LVGL = True
except ImportError:
    _HAS_LVGL = False

from theme import COLORS
from widgets.status_bar import StatusBar
import ntp_clock


def _hex(color_hex):
    return lv.color_hex(int(color_hex.lstrip("#"), 16))


# (key, Anzeigename, Einheit, Formatstring, Farbe, Achse)
# Temperatur bekommt die linke Achse mit ihrer eigenen echten Skala (statt
# normalisiert) - Feuchte und IAQ liegen beide schon natürlich zwischen
# 0-100 und teilen sich deshalb sinnvoll die rechte Achse. Luftdruck (~1013)
# passt auf keine der beiden Skalen und bleibt nur in der Live-Werte-Zeile,
# nicht im Diagramm.
METRICS = [
    ("temp_c", "Temperatur", "\u00b0C", "%.1f", COLORS["accent"], "left"),
    ("humidity", "Feuchte", "%", "%.0f", COLORS["up"], "right"),
    ("iaq_score", "IAQ", "", "%.0f", COLORS["red"], "right"),
]
# Luftdruck nur in der Live-Werte-Zeile, siehe METRICS-Kommentar oben.
EXTRA_LIVE_METRICS = [("pressure_hpa", "Druck", " hPa", "%.0f")]

# (Sekunden oder None fuer "alles", Anzeigename)
TIME_RANGES = [
    (600, "Letzte 10 Min"),
    (1800, "Letzte 30 Min"),
    (3600, "Letzte Stunde"),
    (3 * 3600, "Letzte 3 Stunden"),
    (None, "Gesamter Verlauf"),
]

CHART_POINTS = 60
# 15s statt 5s - der zugrunde liegende Sensor liefert ohnehin nur alle
# poll_interval_s (Standard 20s) einen neuen Wert (siehe main.py::
# local_sensor_log_task()) - alle 5s neu zu rechnen hat den Großteil der
# Zyklen für UNVERÄNDERTE Daten verschwendet, spürbar bei einem großen
# 24h-Puffer.
REFRESH_MS = 15000


class SensorHistoryScreen:
    """page: eine EIGENE, bereits erzeugte m5ui.M5Page (siehe main.py) -
    dieser Screen baut seinen Inhalt direkt darauf auf (wie
    screens/dashboard.py::DashboardScreen es mit der Haupt-Page macht),
    statt ein Overlay darüber zu legen. Aktivierung/Wechsel passiert von
    außen über page.screen_load() (siehe burger_menu.py)."""

    def __init__(self, page, history, sd_reader=None, on_menu_pressed=None):
        if not _HAS_LVGL:
            raise RuntimeError("SensorHistoryScreen benötigt lvgl/m5ui (nur auf der Tab5 verfügbar)")
        self.page = page
        self.history = history
        self.sd_reader = sd_reader
        self._range_idx = 2  # "Letzte Stunde" als sinnvoller Standard
        self._series_by_key = {}
        self._chart = None
        self._refresh_timer = None

        self.status_bar = StatusBar(self.page, on_menu_pressed=on_menu_pressed)

        panel = lv.obj(self.page)
        panel.set_size(1200, 640)
        panel.set_pos(40, 60)
        panel.set_style_bg_opa(0, 0)
        panel.set_style_border_width(0, 0)
        panel.set_style_pad_all(0, 0)
        panel.remove_flag(lv.obj.FLAG.SCROLLABLE)
        panel.set_flex_flow(lv.FLEX_FLOW.COLUMN)
        panel.set_style_pad_row(14, 0)

        header_row = lv.obj(panel)
        header_row.set_size(1160, 34)
        header_row.set_style_bg_opa(0, 0)
        header_row.set_style_border_width(0, 0)
        header_row.set_style_pad_all(0, 0)
        header_row.remove_flag(lv.obj.FLAG.SCROLLABLE)
        header_row.set_flex_flow(lv.FLEX_FLOW.ROW)
        header_row.set_flex_align(lv.FLEX_ALIGN.SPACE_BETWEEN, lv.FLEX_ALIGN.CENTER, lv.FLEX_ALIGN.CENTER)

        title = lv.label(header_row)
        title.set_text("SENSOR-DASHBOARD (INTERNER BME688)")
        title.set_style_text_font(lv.font_montserrat_24, 0)
        title.set_style_text_color(_hex(COLORS["accent"]), 0)
        title.set_style_text_letter_space(2, 0)

        # Oben rechts statt einer eigenen Zeile weiter unten - spart eine
        # komplette Zeile Höhe, wodurch der Chart weiter oben Platz hat.
        self._source_label = lv.label(header_row)
        self._source_label.set_style_text_font(lv.font_montserrat_24, 0)
        self._source_label.set_style_text_color(_hex(COLORS["fg_faint"]), 0)

        self._live_label = lv.label(panel)
        self._live_label.set_style_text_font(lv.font_montserrat_24, 0)
        self._live_label.set_style_text_color(_hex(COLORS["fg"]), 0)

        # Legende: Name jeder Kurve in ihrer eigenen Linienfarbe, damit
        # trotz normalisierter 0-100-Skala klar bleibt, welche Linie
        # welchen Messwert zeigt.
        legend = lv.obj(panel)
        legend.set_size(1160, 34)
        legend.set_style_bg_opa(0, 0)
        legend.set_style_border_width(0, 0)
        legend.set_style_pad_all(0, 0)
        legend.remove_flag(lv.obj.FLAG.SCROLLABLE)
        legend.set_flex_flow(lv.FLEX_FLOW.ROW)
        legend.set_flex_align(lv.FLEX_ALIGN.START, lv.FLEX_ALIGN.CENTER, lv.FLEX_ALIGN.CENTER)
        legend.set_style_pad_column(24, 0)
        for key, label, unit, fmt, color, axis in METRICS:
            item = lv.label(legend)
            item.set_text(label + (" (links)" if axis == "left" else " (rechts)"))
            item.set_style_text_font(lv.font_montserrat_24, 0)
            item.set_style_text_color(_hex(color), 0)

        controls = lv.obj(panel)
        controls.set_size(1160, 60)
        controls.set_style_bg_opa(0, 0)
        controls.set_style_border_width(0, 0)
        controls.set_style_pad_all(0, 0)
        controls.remove_flag(lv.obj.FLAG.SCROLLABLE)
        controls.set_flex_flow(lv.FLEX_FLOW.ROW)
        controls.set_flex_align(lv.FLEX_ALIGN.START, lv.FLEX_ALIGN.CENTER, lv.FLEX_ALIGN.CENTER)
        controls.set_style_pad_column(12, 0)

        # Buttons statt lv.dropdown für die Zeitraum-Auswahl - dropdown
        # hat sich beim Bildschirmwechsel (nach page.clean()) mit einem
        # bisher ungeklärten "function takes 3 positional arguments but 2
        # were given" verabschiedet, obwohl derselbe add_event_cb-Aufruf
        # isoliert getestet einwandfrei lief - offenbar ein kontext-
        # abhängiges Problem. lv.dropdown wurde bislang nirgendwo sonst in
        # der aktiven App genutzt; Buttons dagegen hunderte Male
        # zuverlässig, daher hier die robustere Wahl statt der Ursache
        # weiter hinterherzujagen.
        self._range_buttons = []
        for i, (seconds, label) in enumerate(TIME_RANGES):
            btn = lv.button(controls)
            btn.set_size(lv.SIZE_CONTENT, 44)
            btn_label = lv.label(btn)
            btn_label.set_text(label)
            btn_label.set_style_text_font(lv.font_montserrat_24, 0)
            btn_label.center()
            btn.add_event_cb(self._make_range_handler(i), lv.EVENT.CLICKED, None)
            self._range_buttons.append(btn)
        self._update_range_button_styles()

        def _try(fn, label):
            # Jede Chart-Setup-Methode einzeln absichern - eine einzelne
            # abweichende Methode (wie set_range() gerade eben) soll nicht
            # den kompletten restlichen Aufbau (v.a. add_series() weiter
            # unten, ohne das gar keine Kurve angezeigt werden kann)
            # verhindern.
            try:
                fn()
            except Exception as e:
                print("SensorHistoryScreen: %s übersprungen (%s)" % (label, e))

        # Chart-Zeile: [Skala links (Temperatur)] [Chart] [Skala rechts
        # (Feuchte/IAQ)] - lv.chart hat auf dieser Firmware kein
        # set_axis_tick() (keine native Achsenbeschriftung, siehe
        # dir(chart) im Log), deshalb hier zwei schmale Spalten mit
        # Min/Max-Text-Labels von Hand daneben, statt den Graphen über die
        # volle Breite zu ziehen.
        chart_row = lv.obj(panel)
        chart_row.set_size(1160, 400)
        chart_row.set_style_bg_opa(0, 0)
        chart_row.set_style_border_width(0, 0)
        chart_row.set_style_pad_all(0, 0)
        chart_row.remove_flag(lv.obj.FLAG.SCROLLABLE)
        chart_row.set_flex_flow(lv.FLEX_FLOW.ROW)
        chart_row.set_flex_align(lv.FLEX_ALIGN.CENTER, lv.FLEX_ALIGN.CENTER, lv.FLEX_ALIGN.CENTER)
        chart_row.set_style_pad_column(10, 0)

        def _axis_column(color, top_text, bottom_text):
            col = lv.obj(chart_row)
            col.set_size(80, 400)
            col.set_style_bg_opa(0, 0)
            col.set_style_border_width(0, 0)
            col.set_style_pad_all(0, 0)
            col.remove_flag(lv.obj.FLAG.SCROLLABLE)
            col.set_flex_flow(lv.FLEX_FLOW.COLUMN)
            col.set_flex_align(lv.FLEX_ALIGN.SPACE_BETWEEN, lv.FLEX_ALIGN.CENTER, lv.FLEX_ALIGN.CENTER)
            top = lv.label(col)
            top.set_text(top_text)
            top.set_style_text_font(lv.font_montserrat_24, 0)
            top.set_style_text_color(_hex(color), 0)
            bottom = lv.label(col)
            bottom.set_text(bottom_text)
            bottom.set_style_text_font(lv.font_montserrat_24, 0)
            bottom.set_style_text_color(_hex(color), 0)
            return top, bottom

        self._left_axis_top, self._left_axis_bottom = _axis_column(COLORS["accent"], "--", "--")
        # Rechte Achse ist fest 0-100 (Feuchte/IAQ, siehe METRICS-Kommentar
        # oben) - deren Beschriftung ändert sich nie, anders als links
        # (Temperatur), wo Min/Max bei jedem Refresh neu berechnet werden.

        try:
            self._chart = lv.chart(chart_row)
            self._chart.set_size(1000, 400)
            _try(lambda: self._chart.set_type(lv.chart.TYPE.LINE), "set_type")
            _try(lambda: self._chart.set_style_bg_color(_hex(COLORS["bg"]), 0), "set_style_bg_color")
            _try(lambda: self._chart.set_style_border_color(_hex(COLORS["fg_faint"]), 0), "set_style_border_color")
            # Feineres Raster (mehr Unterteilungen als das Default) in der
            # gleichen dunkelgrauen Farbe wie die Achsenbeschriftung
            # (fg_faint) - Gitterlinien laufen auf lv.PART.MAIN, nicht auf
            # lv.PART.ITEMS (das ist für die Datenlinien selbst reserviert,
            # siehe set_style_line_width weiter unten).
            _try(lambda: self._chart.set_div_line_count(7, 9), "set_div_line_count")
            _try(lambda: self._chart.set_style_line_color(_hex(COLORS["fg_faint"]), lv.PART.MAIN), "set_style_line_color (Raster)")
            _try(lambda: self._chart.set_style_line_width(1, lv.PART.MAIN), "set_style_line_width (Raster)")
            # Sichtbare Linienbreite/Punktgröße erzwingen, statt uns auf
            # ein evtl. zu dünnes/unsichtbares Theme-Default zu verlassen -
            # das war der wahrscheinlichste Grund für den komplett
            # schwarzen Grafikbereich beim ersten Test.
            _try(lambda: self._chart.set_style_line_width(3, lv.PART.ITEMS), "set_style_line_width")
            _try(lambda: self._chart.set_style_size(0, 0, lv.PART.INDICATOR), "set_style_size")
            # Linke Achse: Temperatur mit ihrer eigenen, echten Skala.
            # Rechte Achse: Feuchte + IAQ, beide natürlich 0-100 - siehe
            # METRICS-Kommentar oben.
            _try(lambda: self._chart.set_axis_range(lv.chart.AXIS.SECONDARY_Y, 0, 100), "set_axis_range")
            self._chart.set_point_count(CHART_POINTS)
            for key, label, unit, fmt, color, axis in METRICS:
                chart_axis = lv.chart.AXIS.PRIMARY_Y if axis == "left" else lv.chart.AXIS.SECONDARY_Y
                self._series_by_key[key] = self._chart.add_series(_hex(color), chart_axis)
            # Einmalige Diagnose-Ausgabe - falls die Grafik immer noch
            # schwarz bleibt, verrät das im Log sofort, welche Methoden
            # dieses Chart-Objekt auf dieser Firmware tatsächlich hat.
            print("SensorHistoryScreen: dir(chart) =", dir(self._chart))
        except Exception as e:
            print("SensorHistoryScreen: Chart-Aufbau fehlgeschlagen:", e)
            self._chart = None
            self._series_by_key = {}
            err_label = lv.label(panel)
            err_label.set_text("Grafik nicht verfügbar (%s)" % e)
            err_label.set_style_text_color(_hex(COLORS["red"]), 0)

        # Rechte Achse ist fest 0-100 (Feuchte/IAQ) - Beschriftung ändert
        # sich nie, deshalb erst hier (fixer Text) statt bei jedem Refresh
        # neu gesetzt wie bei der linken (Temperatur-)Achse.
        _axis_column(COLORS["up"], "100", "0")

        # Zeit-Achse unten - an der Breite des Charts selbst ausgerichtet
        # (1000px), nicht an den beiden 80px-Achsenspalten links/rechts,
        # daher zwei gleich breite "Platzhalter" links/rechts statt die
        # Zeile über die volle chart_row-Breite zu ziehen.
        time_row = lv.obj(panel)
        time_row.set_size(1160, 30)
        time_row.set_style_bg_opa(0, 0)
        time_row.set_style_border_width(0, 0)
        time_row.set_style_pad_all(0, 0)
        time_row.remove_flag(lv.obj.FLAG.SCROLLABLE)
        time_row.set_flex_flow(lv.FLEX_FLOW.ROW)
        time_row.set_flex_align(lv.FLEX_ALIGN.CENTER, lv.FLEX_ALIGN.CENTER, lv.FLEX_ALIGN.CENTER)

        spacer_left = lv.obj(time_row)
        spacer_left.set_size(80, 1)
        spacer_left.set_style_bg_opa(0, 0)
        spacer_left.set_style_border_width(0, 0)

        time_labels_box = lv.obj(time_row)
        time_labels_box.set_size(1000, 30)
        time_labels_box.set_style_bg_opa(0, 0)
        time_labels_box.set_style_border_width(0, 0)
        time_labels_box.set_style_pad_all(0, 0)
        time_labels_box.remove_flag(lv.obj.FLAG.SCROLLABLE)
        time_labels_box.set_flex_flow(lv.FLEX_FLOW.ROW)
        time_labels_box.set_flex_align(lv.FLEX_ALIGN.SPACE_BETWEEN, lv.FLEX_ALIGN.CENTER, lv.FLEX_ALIGN.CENTER)
        self._time_label_left = lv.label(time_labels_box)
        self._time_label_mid = lv.label(time_labels_box)
        self._time_label_right = lv.label(time_labels_box)
        for lbl in (self._time_label_left, self._time_label_mid, self._time_label_right):
            lbl.set_text("--:--")
            lbl.set_style_text_font(lv.font_montserrat_24, 0)
            lbl.set_style_text_color(_hex(COLORS["fg_faint"]), 0)

        spacer_right = lv.obj(time_row)
        spacer_right.set_size(80, 1)
        spacer_right.set_style_bg_opa(0, 0)
        spacer_right.set_style_border_width(0, 0)

        self._refresh()
        self._refresh_timer = lv.timer_create(lambda t: self._refresh(), REFRESH_MS, None)

    def _make_range_handler(self, idx):
        def _handler(e):
            try:
                self._range_idx = idx
                self._update_range_button_styles()
                self._refresh()
            except Exception as ex:
                print("SensorHistoryScreen: Zeitraum-Wechsel fehlgeschlagen:", ex)
        return _handler

    def _update_range_button_styles(self):
        for i, btn in enumerate(self._range_buttons):
            selected = i == self._range_idx
            btn.set_style_bg_color(_hex(COLORS["accent"] if selected else COLORS["fg_faint"]), 0)

    def _get_rows(self):
        """Liste von (ts, dict) - bevorzugt SD-Karte, sonst RAM-Puffer.
        Sobald die SD-Karte gemountet werden kann, wandert die Anzeige
        automatisch dorthin um (kein Code-Änderung an diesem Screen
        nötig) - siehe Modul-Docstring."""
        seconds = TIME_RANGES[self._range_idx][0]

        if self.sd_reader is not None:
            try:
                hours = (seconds / 3600.0) if seconds else 24 * 365
                sd_rows = self.sd_reader.read_range(hours=hours)
                if sd_rows:
                    self._source_label.set_text("Quelle: SD-Karte")
                    return [(r.get("timestamp"), r) for r in sd_rows]
            except Exception:
                pass  # SD (noch) nicht verfügbar - stiller Fallback unten

        self._source_label.set_text("Quelle: Arbeitsspeicher (SD nicht verfügbar)")
        buf = self.history._buf if self.history is not None else []
        if seconds is None:
            return list(buf)
        # Von HINTEN nach vorne durchsuchen und abbrechen, sobald ein
        # Eintrag zu alt ist, statt immer den KOMPLETTEN Puffer (bis zu
        # 4320 Einträge bei 24h) zu durchlaufen - bei "letzte 10 Minuten"
        # z.B. reichen die letzten ~30 Einträge, der große Rest wird gar
        # nicht erst angefasst. Funktioniert nur, weil der Puffer
        # garantiert chronologisch sortiert ist (SensorHistory.add() hängt
        # immer nur hinten an).
        cutoff = time.time() - seconds
        result = []
        for row in reversed(buf):
            if row[0] < cutoff:
                break
            result.append(row)
        result.reverse()
        return result

    @staticmethod
    def _bucket_indices(n, points):
        """Teilt die Indizes 0..n-1 in `points` gleich große, LÜCKENLOSE
        Buckets - dieselben Bucket-Grenzen werden für ALLE Messgrößen
        verwendet (siehe _refresh() unten), sonst bekäme z.B. die IAQ-Kurve
        (die anfangs noch kalibriert und deshalb am Anfang lauter None-
        Werte hat) andere, KÜRZERE Buckets als Temperatur - beide Kurven
        hätten zwar am Ende gleich viele Chart-Punkte, aber die würden
        unterschiedliche Zeitspannen abdecken und liefen dadurch sichtbar
        auseinander (genau der gemeldete Versatz)."""
        if n <= points:
            return [[i] for i in range(n)]
        bucket_size = n / points
        buckets = []
        for i in range(points):
            start = int(i * bucket_size)
            end = int((i + 1) * bucket_size) or (start + 1)
            buckets.append(list(range(start, min(end, n))))
        return buckets

    @staticmethod
    def _bucketed_series(rows, buckets, key):
        """Ein Wert pro Bucket (Durchschnitt der vorhandenen, nicht-None
        Werte darin). Fehlt in einem Bucket JEDER Wert (z.B. IAQ während
        der Kalibrierungsphase), wird NICHT einfach die Lücke übersprungen
        (das würde die Kurve wieder gegenüber den anderen verschieben) -
        stattdessen der letzte bekannte Wert fortgeschrieben (vorwärts),
        bzw. für Buckets VOR dem allerersten echten Wert rückwärts mit
        genau diesem ersten Wert aufgefüllt. Damit bleiben alle Kurven auf
        derselben Zeitachse (gleiche Bucket-Anzahl UND -Grenzen wie jede
        andere Messgröße), auch wenn eine erst später "einsetzt"."""
        raw = []
        for bucket in buckets:
            bucket_vals = [rows[i][1].get(key) for i in bucket if rows[i][1].get(key) is not None]
            raw.append(sum(bucket_vals) / len(bucket_vals) if bucket_vals else None)

        # Vorwärts auffüllen
        last_good = None
        filled = []
        for v in raw:
            if v is not None:
                last_good = v
            filled.append(last_good)
        # Rückwärts auffüllen (führende Lücke vor dem allerersten Wert)
        first_good = next((v for v in filled if v is not None), None)
        if first_good is not None:
            filled = [v if v is not None else first_good for v in filled]
        return filled

    def _refresh(self):
        rows = self._get_rows()

        if rows:
            _, last_vals = rows[-1]
            live_bits = []
            for k, lbl, u, f, color, axis in METRICS:
                v = last_vals.get(k)
                live_bits.append("%s: %s%s" % (lbl, (f % v) if v is not None else "--", u))
            for k, lbl, u, f in EXTRA_LIVE_METRICS:
                v = last_vals.get(k)
                live_bits.append("%s: %s%s" % (lbl, (f % v) if v is not None else "--", u))
            self._live_label.set_text("   ".join(live_bits))
        else:
            self._live_label.set_text("Noch keine Daten aufgezeichnet")

        if self._chart is None:
            return  # Chart-Aufbau war schon beim Öffnen fehlgeschlagen

        if rows:
            # Zeitstempel links/mittig/rechts unter dem Graphen - über
            # ntp_clock.from_epoch() in Ortszeit (inkl. Sommerzeit) statt
            # roh in UTC, gleiches Prinzip wie bei Uhr/Kalender.
            def _fmt_time(ts):
                t = ntp_clock.from_epoch(ts)
                return "%02d:%02d" % (t[3], t[4])
            self._time_label_left.set_text(_fmt_time(rows[0][0]))
            self._time_label_mid.set_text(_fmt_time(rows[len(rows) // 2][0]))
            self._time_label_right.set_text(_fmt_time(rows[-1][0]))
        else:
            for lbl in (self._time_label_left, self._time_label_mid, self._time_label_right):
                lbl.set_text("--:--")

        try:
            point_count = None
            temp_vals_for_range = None
            if rows:
                buckets = self._bucket_indices(len(rows), CHART_POINTS)
                point_count = len(buckets)
                self._chart.set_point_count(point_count)
            for key, label, unit, fmt, color, axis in METRICS:
                series = self._series_by_key.get(key)
                if series is None or not rows:
                    continue
                # Gleiche Bucket-Grenzen für JEDE Messgröße (siehe
                # _bucket_indices()-Docstring) - das ist der eigentliche
                # Fix gegen den gemeldeten zeitlichen Versatz.
                vals = self._bucketed_series(rows, buckets, key)
                if all(v is None for v in vals):
                    continue  # diese Messgröße hat noch nie einen Wert geliefert
                if axis == "left":
                    temp_vals_for_range = [v for v in vals if v is not None]
                for i, v in enumerate(vals):
                    if v is not None:
                        self._chart.set_series_value_by_id(series, i, int(round(v)))

            if temp_vals_for_range:
                lo, hi = min(temp_vals_for_range), max(temp_vals_for_range)
                if hi == lo:
                    lo, hi = lo - 1, hi + 1
                axis_lo, axis_hi = int(lo) - 1, int(hi) + 1
                try:
                    self._chart.set_axis_range(lv.chart.AXIS.PRIMARY_Y, axis_lo, axis_hi)
                except Exception:
                    pass
                self._left_axis_top.set_text("%d°C" % axis_hi)
                self._left_axis_bottom.set_text("%d°C" % axis_lo)

            # Manche LVGL-Bindings zeichnen ein Chart nach set_series_value_by_id()
            # nicht automatisch neu - invalidate() falls vorhanden, sonst
            # harmlos ignorieren.
            if hasattr(self._chart, "invalidate"):
                self._chart.invalidate()
        except Exception as e:
            print("SensorHistoryScreen: Chart-Update fehlgeschlagen:", e)

    def destroy(self):
        """Für den Bildschirmwechsel zurück zum Haupt-Dashboard (siehe
        burger_menu.py/main.py) - VOR dem Neuaufbau des anderen Screens
        auf DERSELBEN Page aufgerufen. Nur den eigenen Timer stoppen -
        page.clean() (entfernt alle Kind-Objekte) macht der Aufrufer
        selbst, direkt bevor er den neuen Screen aufbaut."""
        if self._refresh_timer is not None:
            self._refresh_timer.delete()
            self._refresh_timer = None
