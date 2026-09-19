"""
Der EINE Dashboard-Screen - ersetzt die früheren drei Screens Environment,
Room Dashboard und Magic Mirror (siehe HANDOFF.md/config.py für die
Vorgeschichte). Optisch/technisch eine Erweiterung von WidgetCatalogScreen
(gleiche Karten-Optik, gleicher Kachel-Mechanismus) um die Widget-Typen
aus den beiden anderen früheren Screens:

  - climate, air_quality, acoustic, equalizer, acceleration
    (früher screens/environment.py - lokale Tab5-Sensoren: BME688/
    Mikrofon/BMI270. BME688 selbst ist noch nicht an echte I2C-Pins
    angeschlossen (siehe HANDOFF.md Abschnitt 2/8.3) - die Anzeige ist
    aber fertig verdrahtet und zeigt "nicht verfügbar", bis das geklärt
    ist, statt zu crashen.)
  - ha_entity, ha_switch
    (früher screens/room_dashboard.py - Home-Assistant-Sensoren und die
    beiden Schalter Licht/Steckdose)

Da nicht alle ~25 Widgets gleichzeitig in ein 4x3-Raster (12 Zellen)
passen, sind standardmäßig nur 12 aktiv - genau das gleiche Prinzip wie
vorher schon bei WidgetCatalogScreen (siehe screens/widget_catalog.py,
elbe_pegel/ews/defcon/compliments waren dort z.B. auch nur vorbereitet,
aber deaktiviert). Welche Widgets sichtbar sind und wo, wird über
config.json ("screens.dashboard.widgets[].enabled"/"position") gesteuert
und über das Web-UI (/dashboard) bearbeitbar - OHNE Scrollen: das
Dashboard soll auf einen Blick vollständig sichtbar sein.

Erbt von WidgetCatalogScreen (screens/widget_catalog.py), um dessen 16
Widget-Baumeister (_build_clock, _build_calendar, ...) UND den generischen
_build_widget()-Dispatch (getattr(self, "_build_" + widget_type)) sowie
die periodische API-Fetch-Logik in refresh() unverändert weiterzunutzen -
hier kommen nur die zusätzlichen Widget-Typen und ihr jeweiliger
Aktualisierungsweg (lokale Sensoren bzw. Home-Assistant-Polling statt
Magic-Mirror-Server-API) dazu.
"""

try:
    import lvgl as lv
    _HAS_LVGL = True
except ImportError:
    _HAS_LVGL = False

import fetch_worker
from theme import COLORS
from i18n import STRINGS
from lvgl_safety import lvgl_safe_callback
from widgets.card import Card
from widgets.air_quality_light import AirQualityLight, score_to_level, LEVEL_COLOR_KEY
from widgets.sound_light import SoundLight
from widgets.equalizer import Equalizer
from widgets.acceleration_widget import AccelerationWidget
from sensors.microphone import LocalMicSource
from screens.widget_catalog import WidgetCatalogScreen

CARD_TITLE_KEYS = {
    "climate": "card.climate",
    "air_quality": "card.air_quality",
    "acoustic": "card.acoustic",
    "equalizer": "card.equalizer",
    "acceleration": "card.acceleration",
}


def _hex(color_hex):
    return lv.color_hex(int(color_hex.lstrip("#"), 16))


def _style_switch(switch):
    """Einheitliches Erscheinungsbild für ALLE Schalter-Widgets (Home
    Assistant UND Atom-Relais) - Nutzerwunsch: grauer Hintergrund im
    AUS-Zustand soll zum Grauton der Datums-Beschriftung passen (statt des
    helleren LVGL-Standardgraus), und im AN-Zustand grün statt des LVGL-
    Standard-Blaus. ANNAHME (nicht durch widgets/clock_widget.py verifiziert,
    da mir diese Datei nicht vorliegt): "fg_dim" ist der Grauton der
    Datums-Beschriftung - falls das beim Testen nicht genau passt, bitte
    Bescheid geben, dann exakt auf den richtigen COLORS-Wert ändern.
    "up" ist die schon vorhandene Grün-/Erfolgsfarbe aus theme.py, dieselbe,
    die z.B. auch für positive Zustände anderswo verwendet wird."""
    switch.set_style_bg_color(_hex(COLORS["fg_dim"]), lv.PART.MAIN)
    switch.set_style_bg_color(_hex(COLORS["up"]), lv.PART.INDICATOR | lv.STATE.CHECKED)


class DashboardScreen(WidgetCatalogScreen):
    def __init__(self, screen_config, api_client, air_sensor_manager, accel_source,
                 mic_source, history, ha_client, atom_clients=None, sd_logger=None,
                 parent=None, on_menu_pressed=None):
        self.mic = mic_source
        self.history = history
        self.sd_logger = sd_logger
        self.ha_client = ha_client
        # Clients für die gepairten M5Stack-Atom-2-Relais-Boards (Phase C,
        # siehe HANDOFF.md - inzwischen ZWEI unabhängige physische Boards,
        # je 2 Relais) - Liste, Index entspricht config.json
        # "atom_boards"[i] bzw. dem "board_index"-Feld eines
        # relay_pair-Widgets (siehe _build_relay_pair()). Leere Liste,
        # falls (noch) kein Atom-Board konfiguriert ist.
        self.atom_clients = atom_clients or []
        # Hintergrund-Abfragen (Home Assistant/Atom), siehe poll_network_state():
        self._bg_seen = {}        # bg-Key -> zuletzt angewendete laufende Nummer
        self._ha_desired = {}     # entity_id -> gewuenschter Zustand (letzter Tipp gewinnt)
        self._atom_prev = {}      # board_index -> (relay_id, Zustand VOR dem Antippen, Ergebnis-Nr. davor)

        # WidgetCatalogScreen.__init__ setzt self.api/self.air/self.accel,
        # baut StatusBar + Grid und ruft _build_widget() für jeden Eintrag
        # auf - das deckt bereits ALLE Widget-Typen ab, die diese Klasse
        # zusätzlich definiert (_build_climate, _build_ha_switch, ...),
        # da _build_widget() generisch per getattr() dispatcht.
        super().__init__(screen_config, api_client, air_sensor_manager,
                          accel_source, parent, on_menu_pressed)

    # ---- Klima (BME688 Temperatur/Feuchte/Druck) ----
    def _build_climate(self, card, cfg):
        card.add_title(cfg.get("title") or STRINGS[CARD_TITLE_KEYS["climate"]],
                        font=lv.font_montserrat_24, color=COLORS["accent"])
        self._parts[cfg["id"]] = {
            "kind": "climate",
            # 48pt statt 24pt - wie Uhrzeit/Wetter-Temperatur/Luftqualitäts-
            # Score, damit die Temperatur als "Hero-Zahl" der Kachel wirkt.
            "temp": card.add_label("--", font=lv.font_montserrat_48, color=COLORS["fg"]),
            "humidity": card.add_label("--", font=lv.font_montserrat_24, color=COLORS["fg_dim"]),
            "pressure": card.add_label("--", font=lv.font_montserrat_24, color=COLORS["fg_dim"]),
            # Zeigt den IAQ-Wert (farbig nach Schweregrad, wie im Core2-
            # Referenzwidget: grün/amber/rot) statt der früheren reinen
            # Quellenangabe ("Lokal (BME688)") - dieselbe Klassifizierung
            # wie die Luftqualitäts-Ampel (widgets/air_quality_light.py),
            # damit beide Kacheln konsistent einfärben.
            "iaq": card.add_label("--", font=lv.font_montserrat_24, color=COLORS["fg_faint"]),
        }

    # ---- Luftqualität (lokaler BME688-Sensor, unabhängig vom air_quality_mirror-Widget) ----
    def _build_air_quality(self, card, cfg):
        card.add_title(cfg.get("title") or STRINGS[CARD_TITLE_KEYS["air_quality"]],
                        font=lv.font_montserrat_24, color=COLORS["accent"])
        light = AirQualityLight(card.content_parent(), diameter=58)
        card.place(light)
        self._parts[cfg["id"]] = {
            "kind": "air_quality_local",
            "light": light,
            # Größer als vorher (24pt -> 48pt, wie die Uhrzeit im Uhr-
            # Widget) - der IAQ-Score ist die wichtigste Zahl auf dieser
            # Kachel und sollte entsprechend als "Hero-Zahl" wirken.
            "value": card.add_label("--", font=lv.font_montserrat_48, color=COLORS["fg"]),
        }

    # ---- Akustik (Schallpegel-Ampel) ----
    def _build_acoustic(self, card, cfg):
        card.add_title(cfg.get("title") or STRINGS[CARD_TITLE_KEYS["acoustic"]],
                        font=lv.font_montserrat_24, color=COLORS["accent"])
        light = SoundLight(card.content_parent(), diameter=58)
        card.place(light)
        self._parts[cfg["id"]] = {
            "kind": "acoustic",
            "light": light,
            # 48pt statt 24pt - wie die Luftqualitäts-Ampel daneben, damit
            # beide "Ampel"-Widgets optisch einheitlich wirken.
            "value": card.add_label("--", font=lv.font_montserrat_48, color=COLORS["fg"]),
        }

    # ---- Equalizer (Frequenzbänder-Balken) ----
    def _build_equalizer(self, card, cfg):
        card.add_title(cfg.get("title") or STRINGS[CARD_TITLE_KEYS["equalizer"]],
                        font=lv.font_montserrat_24, color=COLORS["accent"])
        content_w = card.w - 2 * card.padding
        content_h = card.h - 2 * card.padding
        eq_h = int(content_h * 0.60)
        eq = Equalizer(card.content_parent(), w=content_w, h=eq_h,
                        num_bands=len(LocalMicSource.EQ_BANDS_HZ))
        card.place(eq)
        self._parts[cfg["id"]] = {"kind": "equalizer", "equalizer": eq}

    # ---- Beschleunigung/Erschütterung (BMI270) ----
    def _build_acceleration(self, card, cfg):
        card.add_title(cfg.get("title") or STRINGS[CARD_TITLE_KEYS["acceleration"]],
                        font=lv.font_montserrat_24, color=COLORS["accent"])
        accel_widget = AccelerationWidget(card.content_parent(), width=card.w - 2 * card.padding,
                                           align=card.align)
        card.place(accel_widget)
        self._parts[cfg["id"]] = {"kind": "acceleration", "widget": accel_widget}

    # ---- Home Assistant: reine Anzeige ----
    def _build_ha_entity(self, card, cfg):
        card.add_title(cfg.get("title", cfg["entity_id"]), font=lv.font_montserrat_24, color=COLORS["accent"])
        value_label = card.add_label("--", font=lv.font_montserrat_24, color=COLORS["fg"])
        entity_label = card.add_label(cfg["entity_id"], font=lv.font_montserrat_24, color=COLORS["fg_faint"])
        self._parts[cfg["id"]] = {
            "kind": "ha_entity", "entity_id": cfg["entity_id"],
            "value_label": value_label, "entity_label": entity_label,
        }

    # ---- Schalter (Home Assistant, aktuell deaktiviert, siehe config.py
    # "home_assistant.enabled") - Atom-Relais-Boards laufen NICHT mehr
    # über diesen Widget-Typ, siehe _build_relay_pair() unten (Phase C,
    # HANDOFF.md: der Nutzer hat inzwischen zwei physische Atom-Boards). ----
    def _build_ha_switch(self, card, cfg):
        backend = cfg.get("backend", "ha")
        if backend != "ha":
            # Sollte nach der Migration in config.py (siehe
            # _migrate_atom_switches_to_relay_pairs()) nicht mehr
            # vorkommen - Sicherheitsnetz für eine unerwartet doch noch
            # nicht migrierte config.json, damit das hier NICHT mit einem
            # KeyError auf "entity_id" abstürzt (Atom-Widgets haben keine
            # entity_id).
            card.add_title("? (unbekanntes Backend)", font=lv.font_montserrat_24, color=COLORS["red"])
            print("_build_ha_switch(): unerwartetes Backend %r bei Widget %r übersprungen "
                  "(Atom-Relais laufen jetzt über relay_pair, siehe HANDOFF.md Phase C)."
                  % (backend, cfg.get("id")))
            self._parts[cfg["id"]] = {"kind": "ha_switch", "backend": backend}
            return

        card.add_title(cfg.get("title", cfg["entity_id"]), font=lv.font_montserrat_24, color=COLORS["accent"])

        switch = lv.switch(card.content_parent())
        # Explizite Größe statt LVGL-Standardgröße (typischerweise nur ca.
        # 45x22px) - auf einem 1280x720-Touchscreen mit ~300px breiten
        # Kacheln braucht das sonst sehr präzises Treffen mit dem Finger.
        # Deutlich größer für bequemes Antippen ohne mehrfach zielen zu müssen.
        switch.set_size(96, 48)
        _style_switch(switch)
        # Zusätzlich der eigentliche TREFFBEREICH (nicht nur die sichtbare
        # Größe) nach allen Seiten erweitert - reagiert jetzt auch, wenn
        # man knapp daneben tippt, ohne dass der Schalter selbst optisch
        # größer wirkt.
        switch.set_ext_click_area(30)
        card.place(switch)

        # Text-Label unter dem Schalter ("AN"/"AUS") auf Wunsch entfernt -
        # der Schalter selbst zeigt den Zustand ja schon. Label bleibt
        # trotzdem bestehen (nur unsichtbar), damit der ganze restliche
        # Aktualisierungscode (set_text() bei jedem Refresh/Toggle) nicht
        # an jeder Stelle einzeln angepasst werden muss.
        state_label = card.add_label("--", font=lv.font_montserrat_24, color=COLORS["fg_dim"])
        state_label.add_flag(lv.obj.FLAG.HIDDEN)

        entity_id = cfg["entity_id"]
        domain = entity_id.split(".")[0]
        parts = {"kind": "ha_switch", "backend": "ha", "switch": switch, "state_label": state_label,
                 "entity_id": entity_id, "domain": domain}
        switch.add_event_cb(self._make_ha_toggle_handler(domain, entity_id), lv.EVENT.VALUE_CHANGED, None)
        self._parts[cfg["id"]] = parts

    def _make_ha_toggle_handler(self, domain, entity_id):
        """Geschlossen über domain/entity_id, siehe screens/room_dashboard.py
        (Vorbild) für den TODO-Hinweis zu has_state(lv.STATE.CHECKED).
        Absicherung gegen eine Exception (siehe lvgl_safety.py-Docstring
        für die ausführliche Begründung) läuft jetzt über den
        @lvgl_safe_callback-Decorator statt über ein eigenes try/except
        (Optimierungs-Backlog Punkt 2, siehe HANDOFF.md)."""
        @lvgl_safe_callback(label="HA-Schalter %s" % entity_id)
        def _handler(e):
            sw = e.get_target()
            is_on = sw.has_state(lv.STATE.CHECKED)
            # Nicht mehr blockierend im LVGL-Callback (frueher bis zu 5s Stillstand
            # bei nicht erreichbarem HA): der Hintergrund-Thread sendet den
            # ZULETZT gewuenschten Zustand (schnelles Hin-und-Her-Tippen: letzter
            # Tipp gewinnt). Danach wird der HA-Status neu geholt (poll_network_state).
            self._ha_desired[entity_id] = is_on
            fetch_worker.bg_submit("ha_call:%s" % entity_id,
                                    lambda: self._ha_send_desired(domain, entity_id))
        return _handler

    def _ha_send_desired(self, domain, entity_id):
        """Laeuft im Hintergrund-Thread (KEIN LVGL-Zugriff!)."""
        result = None
        while True:
            want = self._ha_desired.pop(entity_id, None)
            if want is None:
                break
            result = self.ha_client.call_service(domain, "turn_on" if want else "turn_off", entity_id)
        fetch_worker.bg_expire("ha_states")
        return result

    # ---- Relais-Paar: BEIDE Schalter eines physischen Atom-Boards
    # UNTEREINANDER in EINER Kachel (Phase C, siehe HANDOFF.md - der
    # Nutzer hat zwei unabhängige Atom-Boards mit je 2 Relais, z.B.
    # "Schreibtisch" und "Regal"). "board_index" verweist per Index auf
    # config.json "atom_boards"/self.atom_clients; "labels" sind die vom
    # Nutzer frei vergebenen Beschriftungen der beiden Relais (siehe
    # web_server.py-Dashboard-Editor), NICHT übersetzt (freier Text). ----
    def _build_relay_pair(self, card, cfg):
        board_index = cfg.get("board_index", 0)
        labels = cfg.get("labels") or ["Relais 1", "Relais 2"]
        card.add_title(cfg.get("title") or ("Atom-Switch %d" % (board_index + 1)),
                        font=lv.font_montserrat_24, color=COLORS["accent"])

        switches = []
        for i, relay_id in enumerate((1, 2)):
            row = lv.obj(card.content_parent())
            row.set_size(card.w - 2 * card.padding, 50)
            row.set_style_bg_opa(0, 0)
            row.set_style_border_width(0, 0)
            row.set_style_pad_all(0, 0)
            row.remove_flag(lv.obj.FLAG.SCROLLABLE)
            row.set_flex_flow(lv.FLEX_FLOW.ROW)
            row.set_flex_align(lv.FLEX_ALIGN.SPACE_BETWEEN, lv.FLEX_ALIGN.CENTER, lv.FLEX_ALIGN.CENTER)
            card.place(row)

            name_label = lv.label(row)
            name_label.set_text(labels[i] if i < len(labels) else ("Relais %d" % relay_id))
            name_label.set_style_text_font(lv.font_montserrat_24, 0)
            name_label.set_style_text_color(_hex(COLORS["fg"]), 0)

            switch = lv.switch(row)
            # Etwas kleiner als beim früheren Einzel-Schalter-Widget (96x48),
            # da hier ZWEI Schalter übereinander in dieselbe Kachelhöhe
            # passen müssen - set_ext_click_area gleicht den kleineren
            # sichtbaren Bereich für den Finger wieder aus.
            switch.set_size(80, 40)
            _style_switch(switch)
            switch.set_ext_click_area(20)

            # Verstecktes Text-Label, gleiches Muster wie beim früheren
            # Einzel-Schalter-Widget (siehe _apply_switch_state) - der
            # Schalter selbst zeigt den Zustand ja schon visuell.
            state_label = lv.label(row)
            state_label.set_text("--")
            state_label.add_flag(lv.obj.FLAG.HIDDEN)

            switch_entry = {"relay_id": relay_id, "switch": switch, "state_label": state_label}
            switch.add_event_cb(self._make_atom_toggle_handler(board_index, switch_entry),
                                 lv.EVENT.VALUE_CHANGED, None)
            switches.append(switch_entry)

        self._parts[cfg["id"]] = {"kind": "relay_pair", "board_index": board_index, "switches": switches}

    def _make_atom_toggle_handler(self, board_index, switch_entry):
        """LVGL kippt den Schalter beim Antippen SOFORT von selbst (Standard-
        Widget-Verhalten, noch bevor dieser Callback überhaupt läuft) -
        "sofort reagieren" ist also schon eingebaut, ohne dass wir dafür
        etwas tun müssen. Was hier zusätzlich passiert: toggle() beim Atom
        liefert in der Antwort den TATSÄCHLICHEN, neuen Zustand mit zurück
        (kein bloßes "ok", sondern die komplette, aktuelle Status-Antwort -
        siehe atom_client.py/README.md des Atom-Projekts). Diese echte
        Antwort korrigiert Schalter-Position UND Text-Label - falls der
        Aufruf fehlschlägt (WLAN-Aussetzer, Atom nicht erreichbar, Backoff
        aktiv, ...), springt der Schalter wieder in die Position VOR dem
        Antippen zurück, statt einen ungeprüften Zustand stehen zu lassen.
        Absicherung läuft jetzt über @lvgl_safe_callback statt über ein
        eigenes try/except (Optimierungs-Backlog Punkt 2, siehe
        HANDOFF.md) - hier ist genau dieses Muster (fehlende Absicherung)
        schon einmal aufgetreten und hat das ganze Gerät eingefroren."""
        switch = switch_entry["switch"]
        state_label = switch_entry["state_label"]
        relay_id = switch_entry["relay_id"]

        @lvgl_safe_callback(label="Atom-Schalter Board %d Relais %s" % (board_index, relay_id))
        def _handler(e):
            client = self.atom_clients[board_index] if board_index < len(self.atom_clients) else None
            if client is None:
                return
            # Position VOR dem Antippen merken (LVGL hat den Schalter schon
            # umgekippt) und toggle() im Hintergrund ausfuehren - frueher
            # blockierte das bei nicht erreichbarem Atom bis zu 5s die ganze
            # Oberflaeche. Das Ergebnis (echter Zustand ODER Fehler ->
            # Zurueckspringen) wendet poll_network_state() an.
            was_on = not switch.has_state(lv.STATE.CHECKED)
            _old, seq_before = fetch_worker.bg_get("atom_toggle:%d" % board_index)
            if fetch_worker.bg_submit("atom_toggle:%d" % board_index, lambda: client.toggle(relay_id)):
                self._atom_prev[board_index] = (relay_id, was_on, seq_before)
            else:
                # Vorheriger Schaltvorgang laeuft noch - diesen Tipp verwerfen
                # und Schalter zuruecksetzen (toggle() ist kein "setze auf X").
                if was_on:
                    switch.add_state(lv.STATE.CHECKED)
                else:
                    switch.remove_state(lv.STATE.CHECKED)
                return
            is_on = not was_on
            state_label.set_text(STRINGS["switch.on"] if is_on else STRINGS["switch.off"])
        return _handler


    # ------------------------------------------------------------------
    # refresh() - erweitert WidgetCatalogScreen.refresh() (periodische API-
    # Fetches für die 16 Magic-Mirror-Widget-Typen) um die zwei weiteren
    # Aktualisierungswege der neu hinzugekommenen Widget-Typen:
    #   1. Lokale Sensoren (climate/air_quality_local/acoustic/equalizer/
    #      acceleration) - jeden Zyklus frisch gelesen, kein Throttling
    #      nötig (genau wie früher in screens/environment.py::refresh()).
    #   2. Home-Assistant-Entitäten/Schalter (ha_entity/ha_switch) - EIN
    #      gebündelter get_states()-Aufruf für alle gleichzeitig, statt
    #      pro Widget einzeln (genau wie früher in
    #      screens/room_dashboard.py::refresh()).
    # ------------------------------------------------------------------
    def refresh(self, accel_reading=None, air_reading=None, mic_reading=None):
        super().refresh()  # Magic-Mirror-Widgets (kind-basierter _fetch_*-Dispatch)
        self._refresh_local_sensors(accel_reading, air_reading, mic_reading)
        self._refresh_ha_entities()

    def _refresh_local_sensors(self, accel_reading=None, air_reading=None, mic_reading=None):
        # air_reading/mic_reading kommen jetzt normalerweise als Parameter
        # von main.py::local_sensor_log_task() herein (liest die Sensoren
        # EINMAL pro Zyklus, unabhängig davon, ob gerade das Dashboard
        # oder das Sensor-Dashboard sichtbar ist - siehe dortigen
        # Docstring). Eigene .read()-Aufrufe hier nur noch als Fallback,
        # falls diese Methode mal standalone ohne main.py-Task aufgerufen
        # wird - sonst würde der BME688 doppelt so oft gelesen wie
        # beabsichtigt (siehe frühere "nicht zu oft heizen"-Beratung).
        if air_reading is None and self.air is not None:
            air_reading = self.air.read()
        if accel_reading is None and self.accel is not None:
            accel_reading = self.accel.read()
        if mic_reading is None and self.mic is not None:
            mic_reading = self.mic.read()

        for parts in self._parts.values():
            kind = parts["kind"]

            if kind == "climate" and air_reading is not None:
                if air_reading.ok:
                    parts["temp"].set_text(
                        "%.1f°C" % air_reading.temp_c if air_reading.temp_c is not None else "--")
                    parts["humidity"].set_text(
                        "%.0f%% rH" % air_reading.humidity if air_reading.humidity is not None else "--")
                    parts["pressure"].set_text(
                        "%.0f hPa" % air_reading.pressure_hpa if air_reading.pressure_hpa is not None else "--")
                    iaq_score = air_reading.iaq_score
                    if iaq_score is not None:
                        parts["iaq"].set_text(STRINGS["iaq.value_line"] % iaq_score)
                        level = score_to_level(iaq_score)
                        parts["iaq"].set_style_text_color(_hex(COLORS[LEVEL_COLOR_KEY[level]]), 0)
                    else:
                        parts["iaq"].set_text(STRINGS["iaq.calibrating"])
                        parts["iaq"].set_style_text_color(_hex(COLORS["fg_faint"]), 0)
                else:
                    parts["iaq"].set_text(STRINGS["source.unavailable"] % (air_reading.msg or ""))
                    parts["iaq"].set_style_text_color(_hex(COLORS["fg_faint"]), 0)

            elif kind == "air_quality_local" and air_reading is not None and air_reading.ok:
                parts["light"].set_score(air_reading.iaq_score)
                if air_reading.iaq_score is not None:
                    # Nur der Score, kein Gaswiderstand (kOhm) mehr - passt
                    # so zuverlässig in die Kachelgröße und ist auf einen
                    # Blick lesbar, statt bei größeren Werten umzubrechen.
                    parts["value"].set_text(STRINGS["air_quality.value_line"] % air_reading.iaq_score)
                else:
                    # "--" statt eines langen Kalibrierungstexts - passt
                    # zum minimalistischen Stil der anderen Widgets (z.B.
                    # Wetter/Klima zeigen bei fehlendem Wert auch nur "--").
                    parts["value"].set_text("--")

            elif kind == "acoustic" and mic_reading is not None and mic_reading.ok:
                parts["light"].set_level(mic_reading.db)
                # Nur der aktuelle Pegel, kein Peak-Wert mehr - gleiches
                # Prinzip wie bei der Luftqualitäts-Ampel oben.
                parts["value"].set_text(STRINGS["acoustic.value_line"] % mic_reading.db)

            elif kind == "equalizer" and mic_reading is not None and mic_reading.ok and mic_reading.bands:
                parts["equalizer"].update(mic_reading.bands)

            elif kind == "acceleration" and accel_reading is not None and accel_reading.ok:
                # NICHT mehr hier aktualisiert - hat vorher nur alle
                # poll_interval_s (20s!) einen neuen Wert gezeigt, viel zu
                # träge für ein "live" wirkendes Erschütterungs-Widget.
                # Läuft jetzt über update_acceleration() unten, von main.py
                # aus einem eigenen, schnelleren Takt aufgerufen (ähnlich
                # wie die Uhr ihren eigenen 1-Sekunden-Timer hat).
                pass

        # Zeitreihen-/SD-Logging passiert NICHT mehr hier, sondern in
        # main.py::local_sensor_log_task() - unabhängig davon, ob diese
        # Methode (und damit das Dashboard) gerade überhaupt aktiv ist,
        # siehe dortigen Docstring für die ausführliche Begründung.

    def _refresh_ha_entities(self):
        # Home Assistant (aktuell deaktiviert, siehe config.py
        # "home_assistant.enabled") - ha_entity und ha_switch (nur noch
        # Backend "ha", siehe _build_ha_switch()). Atom-Relais laufen
        # separat über relay_pair-Widgets weiter unten (Phase C, siehe
        # HANDOFF.md) - EIN get_status()-Aufruf PRO BOARD, da jeder davon
        # ohnehin immer BEIDE Relais dieses einen Boards auf einmal liefert.
        # Blockierende Netzwerk-Abrufe (HA/Atom) laufen ab jetzt im
        # Hintergrund (fetch_worker.submit_cached) - frueher direkt hier im
        # Hauptthread: bei nicht erreichbarer Gegenstelle stand die ganze
        # Oberflaeche bis zu 5s still (je Atom-Board bzw. HA-Entity).
        self.poll_network_state()

    # Wie oft HA/Atom im Hintergrund neu abgefragt werden (Sekunden)
    NETWORK_POLL_S = 15

    def poll_network_state(self):
        """Stoesst faellige Hintergrund-Abfragen an und wendet FERTIGE Ergebnisse
        an (set_text() etc. - das darf nur hier im Hauptthread passieren).
        Wird von refresh() UND von main.py::network_state_task() (alle 2s)
        aufgerufen, damit neue Werte zeitnah statt erst im 20s-Takt sichtbar
        werden. Blockiert nie."""
        ha_parts = [p for p in self._parts.values()
                    if p["kind"] in ("ha_entity", "ha_switch") and p.get("backend", "ha") == "ha"]
        if ha_parts and self.ha_client is not None:
            entity_ids = [p["entity_id"] for p in ha_parts]
            states, seq = fetch_worker.submit_cached(
                "ha_states", lambda: self.ha_client.get_states(entity_ids), self.NETWORK_POLL_S)
            if states is not None and self._bg_seen.get("ha_states") != seq:
                self._bg_seen["ha_states"] = seq
                self._apply_ha_states(ha_parts, states)

        for parts in self._parts.values():
            if parts["kind"] != "relay_pair":
                continue
            board_index = parts["board_index"]
            client = self.atom_clients[board_index] if board_index < len(self.atom_clients) else None
            if client is None:
                for switch_entry in parts["switches"]:
                    switch_entry["state_label"].set_text(STRINGS["switch.na"])
                continue
            key = "atom_status:%d" % board_index
            status, seq = fetch_worker.submit_cached(key, client.get_status, self.NETWORK_POLL_S)
            if status is not None and self._bg_seen.get(key) != seq:
                self._bg_seen[key] = seq
                self._apply_atom_status(parts, status)

        # Ergebnisse angetippter Relais (siehe _make_atom_toggle_handler)
        for board_index in list(self._atom_prev.keys()):
            key = "atom_toggle:%d" % board_index
            result, seq = fetch_worker.bg_get(key)
            relay_id, was_on, seq_before = self._atom_prev[board_index]
            if seq <= seq_before:
                continue  # Ergebnis dieses Schaltvorgangs ist noch nicht da
            self._atom_prev.pop(board_index)
            for parts in self._parts.values():
                if parts["kind"] != "relay_pair" or parts.get("board_index") != board_index:
                    continue
                if result and result.get("ok"):
                    self._apply_atom_status(parts, result)
                else:
                    # Fehlgeschlagen: Schalter zurueck auf den Zustand VOR dem Antippen
                    for switch_entry in parts["switches"]:
                        if switch_entry["relay_id"] == relay_id:
                            self._apply_switch_state(switch_entry, was_on)
            fetch_worker.bg_expire("atom_status:%d" % board_index)  # Status bald neu lesen

    def _apply_ha_states(self, ha_parts, states):
        for parts in ha_parts:
            state = states.get(parts["entity_id"], {})
            if parts["kind"] == "ha_entity":
                if state.get("ok"):
                    unit = state.get("attributes", {}).get("unit_of_measurement", "")
                    parts["value_label"].set_text("%s %s" % (state.get("state", "--"), unit))
                    parts["entity_label"].set_style_text_color(_hex(COLORS["fg_faint"]), 0)
                else:
                    parts["value_label"].set_text(STRINGS["switch.na"])
                    parts["entity_label"].set_style_text_color(_hex(COLORS["red"]), 0)
            else:
                if state.get("ok"):
                    self._apply_switch_state(parts, state.get("state") == "on")
                else:
                    parts["state_label"].set_text(STRINGS["switch.na"])

    def _apply_atom_status(self, parts, status):
        for switch_entry in parts["switches"]:
            if status.get("ok"):
                key = "relay%s_state" % switch_entry["relay_id"]
                self._apply_switch_state(switch_entry, bool(status.get(key)))
            else:
                switch_entry["state_label"].set_text(STRINGS["switch.na"])

    def _apply_switch_state(self, parts, is_on):
        # set_state() statt Klick simulieren - löst KEIN VALUE_CHANGED
        # aus, sonst würde jedes Live-Update versehentlich einen erneuten
        # Service-Call/Toggle auslösen.
        if is_on:
            parts["switch"].add_state(lv.STATE.CHECKED)
        else:
            parts["switch"].remove_state(lv.STATE.CHECKED)
        parts["state_label"].set_text(STRINGS["switch.on"] if is_on else STRINGS["switch.off"])

    def set_relay_state(self, board_index, relay_id, is_on):
        """Wird von web_server.py aufgerufen, wenn eines der Atom-Boards
        von SICH AUS eine Zustandsänderung meldet (Taster-Klick oder
        eigenes Web-UI, siehe atom_client.py-Docstring/POST /api/sync) -
        aktualisiert NUR die Anzeige, löst KEINEN erneuten toggle() aus
        (sonst Endlosschleife zwischen Tab5 und Atom). board_index
        identifiziert, WELCHES der (bis zu zwei) Boards gemeldet hat -
        siehe web_server.py::_match_atom_board_by_peer() für die
        Zuordnung anhand der Quell-IP."""
        for parts in self._parts.values():
            if parts.get("kind") == "relay_pair" and parts.get("board_index") == board_index:
                for switch_entry in parts["switches"]:
                    if switch_entry["relay_id"] == relay_id:
                        self._apply_switch_state(switch_entry, is_on)

    def update_acceleration(self, accel_reading):
        """Eigener, schneller Update-Pfad fürs Beschleunigungs-Widget -
        von main.py aus einem eigenen ~1s-Takt aufgerufen (siehe
        accel_display_task()), UNABHÄNGIG vom trägen poll_interval_s-Takt
        (20s) der übrigen Widgets. Der Sensor selbst wird zwar mit 20Hz
        gelesen (siehe main.py::accel_task(), für eine genaue STA/LTA-
        Berechnung), aber eine Textanzeige, die sich 20x pro Sekunde
        ändert, wäre nur noch Flackern statt Lesbarkeit - 1x pro Sekunde
        ist "live genug" für ein Erschütterungs-Widget."""
        if accel_reading is None or not accel_reading.ok:
            return
        for parts in self._parts.values():
            if parts.get("kind") == "acceleration":
                parts["widget"].set_state(
                    triggered=accel_reading.quake.get("triggered", False),
                    ratio=accel_reading.quake.get("ratio"),
                    peak_ratio=accel_reading.quake.get("peak_ratio"),
                    last_event_ts=accel_reading.quake.get("last_event_ts"),
                )
