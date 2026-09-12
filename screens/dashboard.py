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

from theme import COLORS
from i18n import STRINGS
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


class DashboardScreen(WidgetCatalogScreen):
    def __init__(self, screen_config, api_client, air_sensor_manager, accel_source,
                 mic_source, history, ha_client, atom_client=None, sd_logger=None,
                 parent=None, on_menu_pressed=None):
        self.mic = mic_source
        self.history = history
        self.sd_logger = sd_logger
        self.ha_client = ha_client
        # Client fürs gepairte M5Stack-Atom-2-Relais-Board (Licht/
        # Steckdose) - siehe atom_client.py. Optional (None), falls (noch)
        # kein Atom-Board konfiguriert ist.
        self.atom_client = atom_client

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

    # ---- Schalter (Licht/Steckdose) - Backend "atom" (M5Stack-Atom-
    # Relais-Board, siehe atom_client.py) oder "ha" (Home Assistant,
    # aktuell deaktiviert, siehe config.py "home_assistant.enabled") ----
    # Bekannte Katalog-Schalter (siehe config.py DEFAULTS) übersetzen sich
    # mit - identifiziert per "id" (nicht per Titel, da der Titel selbst
    # ja gerade erst hier bestimmt wird). Ein Nutzer, der über das Web-UI
    # einen eigenen Titel einträgt, überschreibt das ganz normal weiterhin
    # (siehe cfg.get("title") unten - hat immer Vorrang).
    _DEFAULT_SWITCH_TITLE_KEYS = {"licht": "widget.licht.default_title",
                                   "steckdose": "widget.steckdose.default_title"}

    def _build_ha_switch(self, card, cfg):
        backend = cfg.get("backend", "ha")
        default_title = (STRINGS.get(self._DEFAULT_SWITCH_TITLE_KEYS.get(cfg.get("id"), ""))
                          or cfg.get("entity_id") or ("Relais %s" % cfg.get("relay_id", "?")))
        card.add_title(cfg.get("title") or default_title, font=lv.font_montserrat_24, color=COLORS["accent"])

        switch = lv.switch(card.content_parent())
        # Explizite Größe statt LVGL-Standardgröße (typischerweise nur ca.
        # 45x22px) - auf einem 1280x720-Touchscreen mit ~300px breiten
        # Kacheln braucht das sonst sehr präzises Treffen mit dem Finger.
        # Deutlich größer für bequemes Antippen ohne mehrfach zielen zu müssen.
        switch.set_size(96, 48)
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
        parts = {"kind": "ha_switch", "backend": backend, "switch": switch, "state_label": state_label}

        if backend == "atom":
            relay_id = cfg.get("relay_id")
            parts["relay_id"] = relay_id
            switch.add_event_cb(self._make_atom_toggle_handler(relay_id, switch, state_label),
                                 lv.EVENT.VALUE_CHANGED, None)
        else:
            entity_id = cfg["entity_id"]
            domain = entity_id.split(".")[0]
            parts["entity_id"] = entity_id
            parts["domain"] = domain
            switch.add_event_cb(self._make_ha_toggle_handler(domain, entity_id), lv.EVENT.VALUE_CHANGED, None)

        self._parts[cfg["id"]] = parts

    def _make_ha_toggle_handler(self, domain, entity_id):
        """Geschlossen über domain/entity_id, siehe screens/room_dashboard.py
        (Vorbild) für den TODO-Hinweis zu has_state(lv.STATE.CHECKED)."""
        def _handler(e):
            try:
                sw = e.get_target()
                is_on = sw.has_state(lv.STATE.CHECKED)
                service = "turn_on" if is_on else "turn_off"
                self.ha_client.call_service(domain, service, entity_id)
            except Exception as exc:
                # KRITISCH: eine Exception in einem LVGL-Touch-Callback ist
                # nicht "nur" ein Fehler in diesem einen Handler - sie reißt
                # den kompletten m5ui/LVGL-Scheduler mit runter (beobachtet:
                # "schedule queue full" direkt danach, komplettes Einfrieren
                # des Geräts). Deshalb hier IMMER abfangen und nur loggen,
                # egal was schiefgeht - siehe HANDOFF.md/config.save()-
                # Historie für den ersten (anderen) Fall dieses Musters.
                print("Fehler im HA-Schalter-Callback (%s):" % entity_id, exc)
        return _handler

    def _make_atom_toggle_handler(self, relay_id, switch, state_label):
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
        Antippen zurück, statt einen ungeprüften Zustand stehen zu lassen."""
        def _handler(e):
            try:
                if self.atom_client is None:
                    return
                result = self.atom_client.toggle(relay_id)
                if result.get("ok"):
                    is_on = bool(result.get("relay%s_state" % relay_id))
                else:
                    # Aufruf fehlgeschlagen - zurück auf die Position VOR dem
                    # Antippen (LVGL hat den Schalter beim Antippen schon
                    # umgekippt, also ist "has_state" jetzt die NEUE,
                    # unbestätigte Position - wir wollen das Gegenteil davon).
                    is_on = not switch.has_state(lv.STATE.CHECKED)
                if is_on:
                    switch.add_state(lv.STATE.CHECKED)
                else:
                    switch.remove_state(lv.STATE.CHECKED)
                state_label.set_text(STRINGS["switch.on"] if is_on else STRINGS["switch.off"])
            except Exception as exc:
                # Siehe ausführliche Begründung im _make_ha_toggle_handler-
                # Kommentar oben - dasselbe kritische Muster, hier ist genau
                # das (clear_state() statt remove_state()) auch schon einmal
                # passiert und hat das ganze Gerät eingefroren.
                print("Fehler im Atom-Schalter-Callback (Relais %s):" % relay_id, exc)
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
        # "home_assistant.enabled") und Atom-Relais-Board (siehe
        # atom_client.py) sind zwei unabhängige Backends für denselben
        # Widget-Typ "ha_switch"/"ha_entity" - getrennt behandelt, da
        # jedes Backend einen eigenen, effizienteren Sammel-Aufruf hat
        # (HA: ein get_states() mit allen entity_ids; Atom: EIN
        # get_status() liefert ohnehin IMMER beide Relais auf einmal).
        all_parts = [p for p in self._parts.values() if p["kind"] in ("ha_entity", "ha_switch")]
        ha_parts = [p for p in all_parts if p.get("backend", "ha") == "ha"]
        atom_parts = [p for p in all_parts if p.get("backend") == "atom"]

        if ha_parts and self.ha_client is not None:
            entity_ids = [p["entity_id"] for p in ha_parts]
            states = self.ha_client.get_states(entity_ids)
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

        if atom_parts and self.atom_client is not None:
            status = self.atom_client.get_status()
            for parts in atom_parts:
                if status.get("ok"):
                    key = "relay%s_state" % parts["relay_id"]
                    self._apply_switch_state(parts, bool(status.get(key)))
                else:
                    parts["state_label"].set_text(STRINGS["switch.na"])

    def _apply_switch_state(self, parts, is_on):
        # set_state() statt Klick simulieren - löst KEIN VALUE_CHANGED
        # aus, sonst würde jedes Live-Update versehentlich einen erneuten
        # Service-Call/Toggle auslösen.
        if is_on:
            parts["switch"].add_state(lv.STATE.CHECKED)
        else:
            parts["switch"].remove_state(lv.STATE.CHECKED)
        parts["state_label"].set_text(STRINGS["switch.on"] if is_on else STRINGS["switch.off"])

    def set_relay_state(self, relay_id, is_on):
        """Wird von web_server.py aufgerufen, wenn das Atom-Board von
        SICH AUS eine Zustandsänderung meldet (Taster-Klick oder eigenes
        Web-UI, siehe atom_client.py-Docstring/POST /api/sync) - aktualisiert
        NUR die Anzeige, löst KEINEN erneuten toggle() aus (sonst
        Endlosschleife zwischen Tab5 und Atom)."""
        for parts in self._parts.values():
            if parts.get("kind") == "ha_switch" and parts.get("backend") == "atom" \
                    and parts.get("relay_id") == relay_id:
                self._apply_switch_state(parts, is_on)

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
