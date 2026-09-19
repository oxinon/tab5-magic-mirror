"""
Sensor-Dashboard fuer den internen BME688 UND bis zu 2 externe
Core2-MiniDash-BME688-Sensoren (Phase A, siehe HANDOFF.md) - eigener,
vollwertiger ZWEITER BILDSCHIRM (m5ui.M5Page), kein Overlay. Umschalten
zwischen diesem und dem Haupt-Dashboard passiert ueber zwei Buttons im
Burger-Menue (siehe burger_menu.py) - genau wie vor der Konsolidierung
auf ein einzelnes Dashboard, nur diesmal bewusst als zweiter Screen fuer
dieses eine Zusatz-Feature statt wieder vier Screens.

Zeigt Temperatur (linke Achse, eigene Skala, 0.1°C-Aufloesung) sowie
Feuchte und IAQ (rechte Achse, beide natuerlich 0-100, teilen sich die
Skala) gleichzeitig in einer Grafik - UND ZWAR FUER JEDE aktiv geschaltete
Quelle (Lokal + konfigurierte externe Sensoren) gleichzeitig als eigene
Kurve, unterscheidbar per Legenden-Namenssuffix ("Temperatur (Lokal)",
"Temperatur (Extern 1)", ...) und per Farbabstufung (siehe _blend_white()).
Luftdruck (~1013) passt auf keine der beiden Skalen und steht nur in der
Live-Werte-Zeile, nicht im Diagramm.

Datenquellen:
- Lokal: bevorzugt die SD-Karte (sensors/sd_reader.py::SDReader), sobald
  die gemountet werden kann (aktuell ENODEV, siehe README) - faellt bis
  dahin automatisch auf den In-RAM-Ringpuffer zurueck (sensors/
  history.py::SensorHistory, dieselbe Instanz, die main.py::
  local_sensor_log_task() bei jedem Zyklus fuellt).
- Extern 1/2: NUR In-RAM-Ringpuffer (kein SD-Logging dafuer geplant,
  siehe HANDOFF.md Phase A) - eigene SensorHistory-Instanzen, gefuellt
  von main.py::external_sensor_log_task() ueber den Hintergrund-
  Thread-Pool (fetch_worker.py), damit ein langsam antwortender externer
  Sensor NICHT den Hauptthread blockiert.

WICHTIG zur Quellenauswahl: Es gibt bewusst KEIN lv.dropdown und KEIN
lv.checkbox hier (siehe HANDOFF.md-Wunsch nach "Dropdown"/"Checkbox") -
stattdessen Toggle-Buttons wie bei der bereits bewaehrten
Zeitraum-Auswahl. Grund: lv.dropdown hat sich in GENAU diesem Screen
schon einmal mit einem kontextabhaengigen "function takes 3 positional
arguments but 2 were given" verabschiedet (siehe weiter unten), und
lv.checkbox wurde im gesamten Projekt bisher nirgends getestet. Die
"Dropdown"-Anforderung ("welche Quellen ueberhaupt konfiguriert/aktiv
sind") wird stattdessen einfach dadurch geloest, dass nur Quellen mit
gesetzter base_url ueberhaupt als Button auftauchen (siehe __init__,
Aufbau von self._sources) - eine unkonfigurierte Quelle nimmt gar nicht
erst am Auswahl-UI teil.

WICHTIG: lv.chart wird hier zum ersten Mal in diesem Projekt genutzt.
Erster Testlauf zeigte einen komplett schwarzen Grafikbereich (Rest -
Titel/Legende/Dropdown - funktionierte einwandfrei), OHNE dass der
Chart-Aufbau selbst eine Exception geworfen hat. Diese Version setzt
zusätzlich eine explizite Linienbreite und ruft invalidate() auf; falls
es beim naechsten Test IMMER NOCH schwarz bleibt, steht dank der
dir(chart)-Diagnoseausgabe im Log sofort da, welche Methoden es auf
dieser Firmware tatsächlich gibt.

LAYOUT-HINWEIS (Phase A): Die zusaetzliche Quellen-Auswahl-Zeile und die
zusaetzliche Live-Werte-Zeile fuer externe Quellen werden NUR gebaut,
wenn tatsaechlich mindestens eine externe Quelle konfiguriert ist
(base_url gesetzt) - ohne externe Quellen ist Layout/Verhalten dieses
Screens dadurch 1:1 identisch zu vorher (kein Regressionsrisiko fuer den
aktuellen Alleinbetrieb mit nur dem lokalen Sensor). Mit externen Quellen
wird der Chart-Bereich etwas niedriger (siehe CHART_ROW_HEIGHT_*), damit
die zusaetzlichen Zeilen ins 1200x640-Panel passen - das ist am echten
Geraet noch nicht visuell geprueft (kein Zugriff auf tools/sim_test.py
bzw. die Tab5 aus dieser Chat-Session heraus) - bitte nach dem naechsten
Kopieren aufs Geraet kurz gegenpruefen, ob alles sichtbar bleibt, und bei
Bedarf CHART_ROW_HEIGHT_MULTI_SOURCE weiter reduzieren.
"""

import time
import gc

try:
    import lvgl as lv
    _HAS_LVGL = True
except ImportError:
    _HAS_LVGL = False

from theme import COLORS
from widgets.status_bar import StatusBar
from lvgl_safety import lvgl_safe_callback
import ntp_clock
import fetch_worker


def _hex(color_hex):
    return lv.color_hex(int(color_hex.lstrip("#"), 16))


def _blend_white(color_hex, factor):
    """Mischt color_hex mit Weiß (factor 0.0 = Originalfarbe, 1.0 = Weiß).
    Genutzt, um pro Quelle (Lokal/Extern 1/Extern 2) eine unterscheidbare,
    aber erkennbar verwandte Farbvariante je Messgröße zu erzeugen (siehe
    HANDOFF.md Phase A: "Linienstil oder Namens-Suffix in der Legende") -
    Lokal bleibt in der vollen, bekannten Farbe, externe Quellen werden
    zunehmend heller."""
    v = int(color_hex.lstrip("#"), 16)
    r, g, b = (v >> 16) & 0xFF, (v >> 8) & 0xFF, v & 0xFF
    r = int(r + (255 - r) * factor)
    g = int(g + (255 - g) * factor)
    b = int(b + (255 - b) * factor)
    return "#%02x%02x%02x" % (r, g, b)


# Pro Quellen-Rang (0=Lokal, 1=Extern 1, 2=Extern 2) ein Weiß-Mischfaktor,
# siehe _blend_white() oben.
SOURCE_BLEND_FACTORS = [0.0, 0.4, 0.7]

# (key, Anzeigename, Einheit, Formatstring, Farbe, Achse, Chart-Skalierung)
# Temperatur bekommt die linke Achse mit ihrer eigenen echten Skala (statt
# normalisiert) - Feuchte und IAQ liegen beide schon natürlich zwischen
# 0-100 und teilen sich deshalb sinnvoll die rechte Achse. Luftdruck (~1013)
# passt auf keine der beiden Skalen und bleibt nur in der Live-Werte-Zeile,
# nicht im Diagramm.
# Die Skalierung (letztes Element) ist noetig, weil lv.chart intern mit
# `int`-Werten arbeitet: Temperatur wird vor dem Setzen mit ×10
# multipliziert (0.1°C-Aufloesung statt 1°C), Feuchte/IAQ bleiben bei ×1,
# da eine ganzzahlige Prozent-/Score-Anzeige hier ausreicht.
METRICS = [
    ("temp_c", "Temperatur", "\u00b0C", "%.1f", COLORS["accent"], "left", 10),
    ("humidity", "Feuchte", "%", "%.0f", COLORS["up"], "right", 1),
    ("iaq_score", "IAQ", "", "%.0f", COLORS["red"], "right", 1),
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

# ---------------------------------------------------------------------------
# Bildschirm-/Layout-Maße (Optimierungs-Backlog Punkt 6, siehe HANDOFF.md)
# - EIN Satz Basis-Konstanten statt der vorher zwanzig verstreuten
# Zahlen-Literale (z.B. panel.set_size(1200, 640), Zeilenbreiten von 1160) -
# falls M5Stack irgendwann eine Tab5-Variante mit anderer Auflösung
# bringt, reicht es, SCREEN_WIDTH/SCREEN_HEIGHT unten anzupassen, der Rest
# wird automatisch neu berechnet, statt jeden einzelnen set_size()-Aufruf
# von Hand durchgehen zu müssen (genau das hat bei den letzten beiden
# Layout-Korrekturen an dieser Datei am meisten Zeit gekostet).
#
# UNVERIFIZIERT/BEWUSST SO ENTSCHIEDEN: Dieser Screen fragt die
# tatsächliche Display-Auflösung NICHT zur Laufzeit von LVGL ab (z.B.
# über eine "lv.display_get_horizontal_resolution()"-artige Methode) -
# das würde eine LVGL-API voraussetzen, die sich ohne Zugriff auf echte
# Hardware nicht verlässlich prüfen lässt (siehe generelle "erst per
# Diagnose-Skript prüfen"-Regel für neue APIs in diesem Projekt, siehe
# HANDOFF.md Abschnitt 2). SCREEN_WIDTH/SCREEN_HEIGHT bleiben daher
# weiterhin FESTE Werte - nur eben an GENAU EINER Stelle statt an zwanzig
# verstreuten set_size()-Aufrufen. Falls eine künftige Tab5-Variante eine
# andere Auflösung hat, UND sich herausstellt, dass eine Laufzeit-Abfrage
# zuverlässig funktioniert, wäre das Ersetzen dieser beiden Konstanten
# durch einen entsprechenden LVGL-Aufruf der nächste, isoliert testbare
# Schritt - bewusst nicht Teil dieser Änderung.
SCREEN_WIDTH = 1280
SCREEN_HEIGHT = 720

# Panel-Rand: links/rechts symmetrisch, oben Platz für die StatusBar
# (siehe widgets/status_bar.py::BAR_HEIGHT), unten ein bewusster Puffer
# (siehe frühere Layout-Korrektur - das Panel darf den Bildschirmrand
# nicht ganz ausreizen, siehe PANEL_HEIGHT unten).
PANEL_MARGIN_X = 40
PANEL_MARGIN_TOP = 60
PANEL_MARGIN_BOTTOM = 20

PANEL_WIDTH = SCREEN_WIDTH - 2 * PANEL_MARGIN_X            # bisher hartcodiert: 1200
PANEL_HEIGHT = SCREEN_HEIGHT - PANEL_MARGIN_TOP - PANEL_MARGIN_BOTTOM  # bisher hartcodiert: 640

# Sichtbarer Leerraum rechts im Panel (Design-Entscheidung, keine
# LVGL-Vorgabe) - alle Zeilen im Panel sind etwas schmaler als das Panel
# selbst, siehe ROW_WIDTH.
CONTENT_MARGIN_RIGHT = 40
ROW_WIDTH = PANEL_WIDTH - CONTENT_MARGIN_RIGHT             # bisher hartcodiert: 1160

# Chart-Zeilen-Hoehe: etwas niedriger, sobald externe Quellen konfiguriert
# sind (siehe Layout-Hinweis im Modul-Docstring), damit die zusaetzliche
# Quellen-Auswahl-Zeile + externe Live-Werte-Zeile noch ins Panel passen.
# Ohne externe Quellen bleibt exakt der bisherige Wert (390) - also keine
# Layout-Aenderung im heutigen Alleinbetrieb mit nur dem lokalen Sensor.
CHART_ROW_HEIGHT_SINGLE_SOURCE = 390
CHART_ROW_HEIGHT_MULTI_SOURCE = 300

# Spaltenbreiten in der Chart-Zeile: MUESSEN sich exakt zu ROW_WIDTH (der
# Breite aller anderen Zeilen im Panel) aufsummieren, da chart_row KEIN
# set_style_pad_column() verwendet (siehe _build init unten) - eine
# frühere Version hatte hier 20px zu viel, was per CENTER-Ausrichtung zu
# einem Versatz nach links UND zu abgeschnittenem Text an der linken
# Achse führte (gemeldet: erste Ziffer von z.B. "25.1°C" halb
# abgeschnitten). AXIS_COL_WIDTH ist eine reine Design-Entscheidung
# (breit genug für "25.1°C"), nicht von der Auflösung abhängig -
# CHART_WIDTH ergibt sich daraus als Rest von ROW_WIDTH, bleibt also auch
# bei einer künftigen anderen Auflösung automatisch konsistent.
AXIS_COL_WIDTH = 90
CHART_WIDTH = ROW_WIDTH - 2 * AXIS_COL_WIDTH               # bisher hartcodiert: 980





def _safe_call(fn, label):
    """Jede Chart-Setup-Methode einzeln absichern - eine einzelne
    abweichende Methode soll nicht den kompletten restlichen Aufbau (v.a.
    add_series(), ohne das gar keine Kurve angezeigt werden kann)
    verhindern. War vorher eine lokale Closure in __init__, jetzt
    modulweit, weil sowohl der Erstaufbau als auch _rebuild_chart() (neu
    in Phase A, beim Umschalten der Quellen-Toggle-Buttons) sie
    brauchen."""
    try:
        fn()
    except Exception as e:
        print("SensorHistoryScreen: %s übersprungen (%s)" % (label, e))


class SensorHistoryScreen:
    """page: eine EIGENE, bereits erzeugte m5ui.M5Page (siehe main.py) -
    dieser Screen baut seinen Inhalt direkt darauf auf (wie
    screens/dashboard.py::DashboardScreen es mit der Haupt-Page macht),
    statt ein Overlay darüber zu legen. Aktivierung/Wechsel passiert von
    außen über page.screen_load() (siehe burger_menu.py).

    external_sources: Liste von (config_dict, SensorHistory-Instanz)-
    Paaren, siehe main.py (external_sources_cfg/external_histories,
    Phase A) - config_dict braucht mindestens "base_url" und "name".
    Quellen ohne gesetzte base_url werden ignoriert."""

    def __init__(self, page, history, sd_reader=None, external_sources=None, on_menu_pressed=None,
                 history_long=None):
        if not _HAS_LVGL:
            raise RuntimeError("SensorHistoryScreen benötigt lvgl/m5ui (nur auf der Tab5 verfügbar)")
        self.page = page
        self.history = history
        self.sd_reader = sd_reader
        # Grober 24h-RAM-Verlauf (siehe main.py::history_long) - Rueckfall fuer
        # Zeitraeume, die der feine RAM-Puffer nicht abdeckt und fuer die keine
        # SD-Daten vorliegen.
        self.history_long = history_long
        self._sd_pending = False  # SD-Daten wurden angefordert, sind aber noch nicht da
        self._poll_timer = None
        self._range_idx = 2  # "Letzte Stunde" als sinnvoller Standard
        self._series_by_key = {}  # (source_key, metric_key) -> lv.chart-Series
        self._chart = None
        self._refresh_timer = None

        # ---- Quellenliste aufbauen (Phase A) ----
        # (source_key, Anzeigename, SensorHistory-Instanz, sd_reader-oder-None)
        # Lokal ist immer dabei. Externe Quellen nur, wenn tatsächlich eine
        # base_url konfiguriert ist - siehe Modul-Docstring ("Dropdown"-
        # Anforderung wird dadurch implizit erfüllt).
        self._sources = [("local", "Lokal", self.history, self.sd_reader)]
        for i, (src_cfg, src_history) in enumerate(external_sources or []):
            if src_cfg.get("base_url"):
                name = src_cfg.get("name") or ("Extern %d" % (i + 1))
                self._sources.append(("ext%d" % (i + 1), name, src_history, None))
        self._has_external = len(self._sources) > 1
        # Alle Quellen starten aktiv (gleichzeitig im Chart sichtbar).
        self._active = {key: True for key, _, _, _ in self._sources}
        self._source_buttons = {}

        self.status_bar = StatusBar(self.page, on_menu_pressed=on_menu_pressed)

        panel = lv.obj(self.page)
        panel.set_size(PANEL_WIDTH, PANEL_HEIGHT)
        panel.set_pos(PANEL_MARGIN_X, PANEL_MARGIN_TOP)
        panel.set_style_bg_opa(0, 0)
        panel.set_style_border_width(0, 0)
        panel.set_style_pad_all(0, 0)
        panel.remove_flag(lv.obj.FLAG.SCROLLABLE)
        panel.set_flex_flow(lv.FLEX_FLOW.COLUMN)
        panel.set_style_pad_row(6, 0)
        self._panel = panel

        header_row = lv.obj(panel)
        header_row.set_size(ROW_WIDTH, 34)
        header_row.set_style_bg_opa(0, 0)
        header_row.set_style_border_width(0, 0)
        header_row.set_style_pad_all(0, 0)
        header_row.remove_flag(lv.obj.FLAG.SCROLLABLE)
        header_row.set_flex_flow(lv.FLEX_FLOW.ROW)
        header_row.set_flex_align(lv.FLEX_ALIGN.SPACE_BETWEEN, lv.FLEX_ALIGN.CENTER, lv.FLEX_ALIGN.CENTER)

        title = lv.label(header_row)
        title.set_text("SENSOR-DASHBOARD" if self._has_external else "SENSOR-DASHBOARD (INTERNER BME688)")
        title.set_style_text_font(lv.font_montserrat_24, 0)
        title.set_style_text_color(_hex(COLORS["accent"]), 0)
        title.set_style_text_letter_space(2, 0)

        # Oben rechts statt einer eigenen Zeile weiter unten - spart eine
        # komplette Zeile Höhe, wodurch der Chart weiter oben Platz hat.
        self._source_label = lv.label(header_row)
        self._source_label.set_style_text_font(lv.font_montserrat_24, 0)
        self._source_label.set_style_text_color(_hex(COLORS["fg_faint"]), 0)

        self._live_label = lv.label(panel)
        self._live_label.set_size(ROW_WIDTH, 34)
        self._live_label.set_style_text_font(lv.font_montserrat_24, 0)
        self._live_label.set_style_text_color(_hex(COLORS["fg"]), 0)

        # Eine zusätzliche kompakte Live-Werte-Zeile für externe Quellen
        # (nur gebaut, falls welche konfiguriert sind - siehe
        # Layout-Hinweis im Modul-Docstring).
        self._live_label_ext = None
        if self._has_external:
            self._live_label_ext = lv.label(panel)
            self._live_label_ext.set_size(ROW_WIDTH, 34)
            self._live_label_ext.set_style_text_font(lv.font_montserrat_24, 0)
            self._live_label_ext.set_style_text_color(_hex(COLORS["fg_dim"]), 0)

        # Legende: Name jeder Kurve in ihrer eigenen Linienfarbe, damit
        # klar bleibt, welche Linie welchen Messwert (und, bei mehreren
        # Quellen, welche Quelle) zeigt. Wird bei jeder Änderung der
        # aktiven Quellen neu aufgebaut (siehe _build_legend()).
        self._legend = lv.obj(panel)
        self._legend.set_size(ROW_WIDTH, 34)
        self._legend.set_style_bg_opa(0, 0)
        self._legend.set_style_border_width(0, 0)
        self._legend.set_style_pad_all(0, 0)
        self._legend.remove_flag(lv.obj.FLAG.SCROLLABLE)
        self._legend.set_flex_flow(lv.FLEX_FLOW.ROW)
        self._legend.set_flex_align(lv.FLEX_ALIGN.START, lv.FLEX_ALIGN.CENTER, lv.FLEX_ALIGN.CENTER)
        self._legend.set_style_pad_column(20, 0)
        self._build_legend()

        # ---- Quellen-Auswahl-Zeile (Phase A) - nur mit externen Quellen ----
        # Toggle-Buttons statt Checkbox/Dropdown, siehe Modul-Docstring.
        if self._has_external:
            source_controls = lv.obj(panel)
            source_controls.set_size(ROW_WIDTH, 46)
            source_controls.set_style_bg_opa(0, 0)
            source_controls.set_style_border_width(0, 0)
            source_controls.set_style_pad_all(0, 0)
            source_controls.remove_flag(lv.obj.FLAG.SCROLLABLE)
            source_controls.set_flex_flow(lv.FLEX_FLOW.ROW)
            source_controls.set_flex_align(lv.FLEX_ALIGN.START, lv.FLEX_ALIGN.CENTER, lv.FLEX_ALIGN.CENTER)
            source_controls.set_style_pad_column(12, 0)

            hint = lv.label(source_controls)
            hint.set_text("Im Chart anzeigen:")
            hint.set_style_text_font(lv.font_montserrat_24, 0)
            hint.set_style_text_color(_hex(COLORS["fg_faint"]), 0)

            for key, name, _hist, _sd in self._sources:
                btn = lv.button(source_controls)
                btn.set_size(lv.SIZE_CONTENT, 40)
                btn_label = lv.label(btn)
                btn_label.set_text(name)
                btn_label.set_style_text_font(lv.font_montserrat_24, 0)
                btn_label.center()
                btn.add_event_cb(self._make_source_toggle_handler(key), lv.EVENT.CLICKED, None)
                self._source_buttons[key] = btn
            self._update_source_button_styles()

        controls = lv.obj(panel)
        controls.set_size(ROW_WIDTH, 54)
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

        # Chart-Zeile: [Skala links (Temperatur)] [Chart] [Skala rechts
        # (Feuchte/IAQ)] - lv.chart hat auf dieser Firmware kein
        # set_axis_tick() (keine native Achsenbeschriftung, siehe
        # dir(chart) im Log), deshalb hier zwei schmale Spalten mit
        # Min/Max-Text-Labels von Hand daneben, statt den Graphen über die
        # volle Breite zu ziehen.
        chart_row_height = CHART_ROW_HEIGHT_MULTI_SOURCE if self._has_external else CHART_ROW_HEIGHT_SINGLE_SOURCE
        self._chart_row_height = chart_row_height
        chart_row = lv.obj(panel)
        chart_row.set_size(ROW_WIDTH, chart_row_height)
        chart_row.set_style_bg_opa(0, 0)
        chart_row.set_style_border_width(0, 0)
        chart_row.set_style_pad_all(0, 0)
        chart_row.remove_flag(lv.obj.FLAG.SCROLLABLE)
        chart_row.set_flex_flow(lv.FLEX_FLOW.ROW)
        chart_row.set_flex_align(lv.FLEX_ALIGN.CENTER, lv.FLEX_ALIGN.CENTER, lv.FLEX_ALIGN.CENTER)
        # KEIN set_style_pad_column() mehr hier - AXIS_COL_WIDTH/CHART_WIDTH
        # summieren sich jetzt exakt zu 1160, jeder zusätzliche Abstand
        # würde denselben Überhang-Bug wieder einführen (siehe Kommentar
        # bei AXIS_COL_WIDTH oben).
        self._chart_row = chart_row

        def _axis_column(color, top_text, bottom_text):
            col = lv.obj(chart_row)
            col.set_size(AXIS_COL_WIDTH, chart_row_height)
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

        self._chart_container = lv.obj(chart_row)
        self._chart_container.set_size(CHART_WIDTH, chart_row_height)
        self._chart_container.set_style_bg_opa(0, 0)
        self._chart_container.set_style_border_width(0, 0)
        self._chart_container.set_style_pad_all(0, 0)
        self._chart_container.remove_flag(lv.obj.FLAG.SCROLLABLE)
        self._build_chart()

        # Rechte Achse ist fest 0-100 (Feuchte/IAQ) - Beschriftung ändert
        # sich nie, deshalb erst hier (fixer Text) statt bei jedem Refresh
        # neu gesetzt wie bei der linken (Temperatur-)Achse.
        _axis_column(COLORS["up"], "100", "0")

        # Zeit-Achse unten - an der Breite des Charts selbst ausgerichtet
        # (1000px), nicht an den beiden 80px-Achsenspalten links/rechts,
        # daher zwei gleich breite "Platzhalter" links/rechts statt die
        # Zeile über die volle chart_row-Breite zu ziehen.
        time_row = lv.obj(panel)
        time_row.set_size(ROW_WIDTH, 30)
        time_row.set_style_bg_opa(0, 0)
        time_row.set_style_border_width(0, 0)
        time_row.set_style_pad_all(0, 0)
        time_row.remove_flag(lv.obj.FLAG.SCROLLABLE)
        time_row.set_flex_flow(lv.FLEX_FLOW.ROW)
        time_row.set_flex_align(lv.FLEX_ALIGN.CENTER, lv.FLEX_ALIGN.CENTER, lv.FLEX_ALIGN.CENTER)

        spacer_left = lv.obj(time_row)
        spacer_left.set_size(AXIS_COL_WIDTH, 1)
        spacer_left.set_style_bg_opa(0, 0)
        spacer_left.set_style_border_width(0, 0)

        time_labels_box = lv.obj(time_row)
        time_labels_box.set_size(CHART_WIDTH, 30)
        time_labels_box.set_style_bg_opa(0, 0)
        time_labels_box.set_style_border_width(0, 0)
        time_labels_box.set_style_pad_all(0, 0)
        # Kleiner Sicherheitsabstand links/rechts (6px) - die äußeren
        # beiden Zeitlabels sitzen sonst exakt bündig an den Rändern
        # dieser Box, was gemeldet wurde als "nur die Hälfte zu sehen"
        # (Glyphen können je nach Font leicht über ihre eigentliche
        # Breite hinausragen, wenn sie exakt an einer Clip-Kante liegen).
        time_labels_box.set_style_pad_left(6, 0)
        time_labels_box.set_style_pad_right(6, 0)
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
        spacer_right.set_size(AXIS_COL_WIDTH, 1)
        spacer_right.set_style_bg_opa(0, 0)
        spacer_right.set_style_border_width(0, 0)

        self._refresh()
        # Timer-Callbacks mit demselben Sicherheitsnetz wie Touch-Callbacks
        # (siehe lvgl_safety.py) - eine Exception darf den Scheduler nicht mitreissen.
        self._refresh_timer = lv.timer_create(
            lvgl_safe_callback(label="Sensor-Screen-Refresh")(lambda t: self._refresh()), REFRESH_MS, None)
        # Kurzer Hilfs-Timer: sobald angeforderte SD-Daten im Hintergrund fertig
        # sind, sofort anzeigen (statt bis zu 15s auf den naechsten Refresh zu warten).
        self._poll_timer = lv.timer_create(
            lvgl_safe_callback(label="Sensor-Screen-SD-Poll")(lambda t: self._poll_sd()), 3000, None)

    # ------------------------------------------------------------------
    # Legende (Phase A: pro aktiver Quelle + Messgröße ein Eintrag)
    # ------------------------------------------------------------------
    def _build_legend(self):
        self._legend.clean()
        for key, label, unit, fmt, base_color, axis, scale in METRICS:
            for source_idx, (source_key, source_name, _hist, _sd) in enumerate(self._sources):
                if not self._active.get(source_key, True):
                    continue
                color = _blend_white(base_color, SOURCE_BLEND_FACTORS[min(source_idx, len(SOURCE_BLEND_FACTORS) - 1)])
                item = lv.label(self._legend)
                text = label + (" (links)" if axis == "left" else " (rechts)")
                if self._has_external:
                    text = "%s – %s" % (label, source_name)
                item.set_text(text)
                item.set_style_text_font(lv.font_montserrat_24, 0)
                item.set_style_text_color(_hex(color), 0)

    # ------------------------------------------------------------------
    # Chart-Aufbau (Phase A: als eigene Methode, damit _rebuild_chart()
    # beim Umschalten der Quellen-Toggle-Buttons dieselbe Logik nutzen
    # kann statt sie zu duplizieren)
    # ------------------------------------------------------------------
    def _build_chart(self):
        self._series_by_key = {}
        try:
            self._chart = lv.chart(self._chart_container)
            self._chart.set_size(CHART_WIDTH, self._chart_row_height)
            _safe_call(lambda: self._chart.set_type(lv.chart.TYPE.LINE), "set_type")
            _safe_call(lambda: self._chart.set_style_bg_color(_hex(COLORS["bg"]), 0), "set_style_bg_color")
            _safe_call(lambda: self._chart.set_style_border_color(_hex(COLORS["fg_faint"]), 0), "set_style_border_color")
            # Feineres Raster (mehr Unterteilungen als das Default) in der
            # gleichen dunkelgrauen Farbe wie die Achsenbeschriftung
            # (fg_faint) - Gitterlinien laufen auf lv.PART.MAIN, nicht auf
            # lv.PART.ITEMS (das ist für die Datenlinien selbst reserviert,
            # siehe set_style_line_width weiter unten).
            _safe_call(lambda: self._chart.set_div_line_count(7, 9), "set_div_line_count")
            _safe_call(lambda: self._chart.set_style_line_color(_hex(COLORS["fg_faint"]), lv.PART.MAIN), "set_style_line_color (Raster)")
            _safe_call(lambda: self._chart.set_style_line_width(1, lv.PART.MAIN), "set_style_line_width (Raster)")
            # Sichtbare Linienbreite/Punktgröße erzwingen, statt uns auf
            # ein evtl. zu dünnes/unsichtbares Theme-Default zu verlassen -
            # das war der wahrscheinlichste Grund für den komplett
            # schwarzen Grafikbereich beim ersten Test.
            _safe_call(lambda: self._chart.set_style_line_width(3, lv.PART.ITEMS), "set_style_line_width")
            _safe_call(lambda: self._chart.set_style_size(0, 0, lv.PART.INDICATOR), "set_style_size")
            # Linke Achse: Temperatur (0.1°C-Aufloesung, siehe METRICS-
            # Kommentar oben) mit ihrer eigenen, echten (×10-skalierten)
            # Skala. Rechte Achse: Feuchte + IAQ, beide natürlich 0-100 -
            # siehe METRICS-Kommentar oben.
            _safe_call(lambda: self._chart.set_axis_range(lv.chart.AXIS.SECONDARY_Y, 0, 100), "set_axis_range")
            self._chart.set_point_count(CHART_POINTS)
            for key, label, unit, fmt, base_color, axis, scale in METRICS:
                chart_axis = lv.chart.AXIS.PRIMARY_Y if axis == "left" else lv.chart.AXIS.SECONDARY_Y
                for source_idx, (source_key, source_name, _hist, _sd) in enumerate(self._sources):
                    if not self._active.get(source_key, True):
                        continue
                    color = _blend_white(base_color, SOURCE_BLEND_FACTORS[min(source_idx, len(SOURCE_BLEND_FACTORS) - 1)])
                    self._series_by_key[(source_key, key)] = self._chart.add_series(_hex(color), chart_axis)
            # (Frueher: dir(chart)-Diagnoseausgabe bei JEDEM Aufbau - ~500 Namen ins Log
            # und eine grosse temporaere Liste. Das Chart funktioniert, nicht mehr noetig.)
        except Exception as e:
            print("SensorHistoryScreen: Chart-Aufbau fehlgeschlagen:", e)
            self._chart = None
            self._series_by_key = {}
            err_label = lv.label(self._chart_container)
            err_label.set_text("Grafik nicht verfügbar (%s)" % e)
            err_label.set_style_text_color(_hex(COLORS["red"]), 0)

    def _rebuild_chart(self):
        """Wird aufgerufen, wenn sich die aktiven Quellen ändern (Toggle-
        Button) - die Anzahl/Farbe der Kurven eines lv.chart lässt sich
        nicht nachträglich ändern (kein remove_series() auf dieser
        Firmware bestätigt), daher komplett neu aufbauen statt zu
        versuchen, einzelne Serien zu verstecken. Passiert nur bei dieser
        seltenen Nutzerinteraktion, nicht bei jedem 15s-Refresh."""
        if self._chart is not None:
            try:
                self._chart.delete()
            except Exception as e:
                print("SensorHistoryScreen: alten Chart löschen fehlgeschlagen:", e)
            self._chart = None
        self._chart_container.clean()
        # Optimierungs-Backlog Punkt 4 (siehe HANDOFF.md) - der alte Chart
        # (potenziell mehrere Serien mit hunderten Datenpunkten) ist
        # gerade freigegeben worden, JETZT aufräumen, bevor der neue
        # Chart wieder Speicher belegt.
        gc.collect()
        self._build_chart()
        self._build_legend()

    def _make_source_toggle_handler(self, key):
        @lvgl_safe_callback(label="Quellen-Umschaltung %s" % key)
        def _handler(e):
            # Mindestens eine Quelle muss aktiv bleiben, sonst gibt es
            # nichts anzuzeigen (Achsen-Range-Berechnung in _refresh()
            # setzt das voraus).
            active_count = sum(1 for v in self._active.values() if v)
            if self._active.get(key) and active_count <= 1:
                return
            self._active[key] = not self._active.get(key, True)
            self._update_source_button_styles()
            self._rebuild_chart()
            self._refresh()
        return _handler

    def _update_source_button_styles(self):
        for key, btn in self._source_buttons.items():
            active = self._active.get(key, True)
            btn.set_style_bg_color(_hex(COLORS["accent"] if active else COLORS["fg_faint"]), 0)

    def _make_range_handler(self, idx):
        @lvgl_safe_callback(label="Zeitraum-Wechsel %d" % idx)
        def _handler(e):
            self._range_idx = idx
            self._update_range_button_styles()
            self._refresh()
        return _handler

    def _poll_sd(self):
        if not self._sd_pending:
            return
        entry, _seq = fetch_worker.bg_get("sd_rows_screen")
        if entry is not None and entry.get("range") == self._range_idx and entry.get("rows"):
            self._refresh()

    def _update_range_button_styles(self):
        for i, btn in enumerate(self._range_buttons):
            selected = i == self._range_idx
            btn.set_style_bg_color(_hex(COLORS["accent"] if selected else COLORS["fg_faint"]), 0)

    def _get_rows_for(self, hist, sd_reader):
        """Wie die frühere _get_rows(), aber für eine beliebige
        (history, sd_reader)-Kombination statt fest für den lokalen
        Sensor - Rückgabe: (rows, quelle_str) statt direkt den
        Anzeige-Text zu setzen, damit der Aufrufer (Phase A: pro Quelle
        einzeln) entscheidet, was mit dem Ergebnis passiert."""
        seconds = TIME_RANGES[self._range_idx][0]

        if sd_reader is not None:
            # SD-Zugriff im HINTERGRUND-Thread (frueher direkt hier im LVGL-Timer:
            # alle Tages-CSVs lesen und parsen = spuerbares Ruckeln alle 15s).
            # Bis die Daten da sind, zeigt dieser Zyklus den RAM-Verlauf.
            idx = self._range_idx
            hours = (seconds / 3600.0) if seconds else 24 * 365
            key = "sd_rows_screen"
            entry, _seq = fetch_worker.bg_get(key)
            if entry is not None and entry.get("range") != idx:
                fetch_worker.bg_expire(key)  # anderer Zeitraum gewaehlt -> neu laden
            entry, _seq = fetch_worker.submit_cached(
                key, lambda: {"range": idx, "rows": sd_reader.read_range(hours=hours)}, 30)
            if entry is not None and entry.get("range") == idx and entry.get("rows"):
                self._sd_pending = False
                return [(r.get("timestamp"), r) for r in entry["rows"]], "sd"
            self._sd_pending = True

        buf = hist._buf if hist is not None else []
        if hist is self.history and self.history_long is not None:
            long_buf = self.history_long._buf
            fine_cover_s = (buf[-1][0] - buf[0][0]) if len(buf) > 1 else 0
            if long_buf and (seconds is None or seconds > fine_cover_s):
                buf = long_buf  # groeberer, aber laengerer Verlauf
        if seconds is None:
            return list(buf), "ram"
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
        return result, "ram"

    @staticmethod
    def _bucket_indices(n, points):
        """Teilt die Indizes 0..n-1 in `points` gleich große, LÜCKENLOSE
        Buckets. Für Phase A (mehrere Quellen) WICHTIG: `points` wird für
        ALLE Quellen von außen auf denselben Wert festgelegt (siehe
        _refresh() - point_count kommt von der Quelle mit den meisten
        Daten, i.d.R. Lokal), auch wenn `n` (die Zeilenanzahl) je Quelle
        unterschiedlich ist - nur so bleiben die Kurven verschiedener
        Quellen auf derselben Zeitachse (siehe HANDOFF.md Phase A:
        "mit denselben Bucket-Grenzen"). Innerhalb EINER Quelle löst das
        außerdem weiterhin den ursprünglichen Versatz zwischen z.B.
        Temperatur und IAQ (das anfangs noch kalibriert und deshalb am
        Anfang lauter None-Werte hat)."""
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

    def _format_live_line(self, last_vals, include_pressure):
        bits = []
        for k, lbl, u, f, color, axis, scale in METRICS:
            v = last_vals.get(k)
            bits.append("%s: %s%s" % (lbl, (f % v) if v is not None else "--", u))
        if include_pressure:
            for k, lbl, u, f in EXTRA_LIVE_METRICS:
                v = last_vals.get(k)
                bits.append("%s: %s%s" % (lbl, (f % v) if v is not None else "--", u))
        return "   ".join(bits)

    def _refresh(self):
        # ---- Live-Werte: Lokal (eigene Zeile, inkl. Druck) ----
        local_rows, local_kind = self._get_rows_for(self.history, self.sd_reader)
        self._source_label.set_text("Quelle: SD-Karte" if local_kind == "sd" else "Quelle: Arbeitsspeicher (SD nicht verfügbar)")
        if local_rows:
            self._live_label.set_text(self._format_live_line(local_rows[-1][1], include_pressure=True))
        else:
            self._live_label.set_text("Noch keine Daten aufgezeichnet")

        # ---- Live-Werte: externe Quellen (eine kompakte Zeile) ----
        rows_by_source = {"local": local_rows}
        if self._live_label_ext is not None:
            ext_bits = []
            for source_key, source_name, hist, _sd in self._sources:
                if source_key == "local":
                    continue
                ext_rows, _kind = self._get_rows_for(hist, None)
                rows_by_source[source_key] = ext_rows
                if ext_rows:
                    ext_bits.append("%s: %s" % (source_name, self._format_live_line(ext_rows[-1][1], include_pressure=False)))
                else:
                    ext_bits.append("%s: keine Daten" % source_name)
            self._live_label_ext.set_text("   |   ".join(ext_bits))

        if self._chart is None:
            return  # Chart-Aufbau war schon beim Öffnen fehlgeschlagen

        # ---- Zeitachse unten (anhand der ersten aktiven Quelle mit
        # Daten - bevorzugt Lokal, siehe Docstring von _bucket_indices()) ----
        reference_rows = None
        for source_key, _name, _hist, _sd in self._sources:
            if self._active.get(source_key, True) and rows_by_source.get(source_key):
                reference_rows = rows_by_source[source_key]
                break
        if reference_rows:
            def _fmt_time(ts):
                t = ntp_clock.from_epoch(ts)
                return "%02d:%02d" % (t[3], t[4])
            self._time_label_left.set_text(_fmt_time(reference_rows[0][0]))
            self._time_label_mid.set_text(_fmt_time(reference_rows[len(reference_rows) // 2][0]))
            self._time_label_right.set_text(_fmt_time(reference_rows[-1][0]))
        else:
            for lbl in (self._time_label_left, self._time_label_mid, self._time_label_right):
                lbl.set_text("--:--")

        try:
            point_count = min(CHART_POINTS, len(reference_rows)) if reference_rows else CHART_POINTS
            self._chart.set_point_count(point_count)

            temp_scaled_all = []  # für die gemeinsame linke Achse (alle aktiven Quellen)
            for source_key, _name, _hist, _sd in self._sources:
                if not self._active.get(source_key, True):
                    continue
                rows = rows_by_source.get(source_key)
                if not rows:
                    continue
                # Gleiche Bucket-ANZAHL (point_count) für JEDE Quelle und
                # JEDE Messgröße (siehe _bucket_indices()-Docstring) - das
                # ist der eigentliche Fix gegen den gemeldeten zeitlichen
                # Versatz, jetzt auch quellenübergreifend.
                buckets = self._bucket_indices(len(rows), point_count)
                for key, label, unit, fmt, base_color, axis, scale in METRICS:
                    series = self._series_by_key.get((source_key, key))
                    if series is None:
                        continue
                    vals = self._bucketed_series(rows, buckets, key)
                    if all(v is None for v in vals):
                        continue  # diese Messgröße hat für diese Quelle noch nie einen Wert geliefert
                    if axis == "left":
                        temp_scaled_all.extend(int(round(v * scale)) for v in vals if v is not None)
                    for i, v in enumerate(vals):
                        if v is not None:
                            self._chart.set_series_value_by_id(series, i, int(round(v * scale)))

            if temp_scaled_all:
                lo, hi = min(temp_scaled_all), max(temp_scaled_all)
                if hi == lo:
                    lo, hi = lo - 10, hi + 10  # mind. 1°C sichtbare Spanne (×10-skaliert)
                temp_scale = 10  # siehe METRICS - Temperatur ist die einzige "left"-Messgröße
                axis_lo, axis_hi = lo - temp_scale, hi + temp_scale
                try:
                    self._chart.set_axis_range(lv.chart.AXIS.PRIMARY_Y, axis_lo, axis_hi)
                except Exception:
                    pass
                self._left_axis_top.set_text("%.1f°C" % (axis_hi / temp_scale))
                self._left_axis_bottom.set_text("%.1f°C" % (axis_lo / temp_scale))

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
        if self._poll_timer is not None:
            self._poll_timer.delete()
            self._poll_timer = None
        if self._refresh_timer is not None:
            self._refresh_timer.delete()
            self._refresh_timer = None
