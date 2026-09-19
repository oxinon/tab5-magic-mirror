"""
Hintergrund-Worker fuer blockierende Netzwerk-Abrufe (widget_sources.py/
api_client.py) - haelt den Haupt-Thread (LVGL/Touch/Uhr) frei von
Netzwerk-I/O. Bestaetigt per diagnose_thread.py: der ESP32-P4 fuehrt
_thread-Threads echt parallel aus (nicht nur kooperativ), der
Hauptthread wird waehrend eines Hintergrund-Threads NICHT blockiert.

SICHERHEITSREGEL (wichtig!): Dieses Modul und die Funktionen, die es im
Hintergrund-Thread aufruft, duerfen NIEMALS LVGL-Objekte anfassen (kein
.set_text(), keine Widget-Erstellung, nichts) - LVGL ist nicht
thread-sicher fuer gleichzeitigen Zugriff aus mehreren Threads. Der
Hintergrund-Thread liefert nur ein reines Daten-Dict ab; das eigentliche
Anzeigen (set_text() etc.) passiert IMMER im Hauptthread, im naechsten
regulaeren refresh()-Zyklus (siehe screens/widget_catalog.py::refresh()).

Bis zu MAX_CONCURRENT Anfragen gleichzeitig im Hintergrund - mehr braucht
es praktisch nie (die meisten Widgets aktualisieren sich ohnehin nur alle
15 Minuten, siehe FETCH_INTERVAL_S), und mehr Threads wuerden nur unnoetig
RAM fuer Thread-Stacks belegen, ohne einen echten Vorteil zu bringen.

EXPONENTIELLES BACKOFF (Optimierungs-Backlog Punkt 5, siehe HANDOFF.md):
Nach dem gleichen Prinzip wie ApiClient.FAIL_BACKOFF_S (api_client.py) -
eine Quelle, die gerade nicht antwortet (z.B. Home Assistant offline),
soll nicht bei JEDEM fälligen Zyklus erneut einen Hintergrund-Thread
"verschwenden", nur um wieder zu scheitern. Anders als bei ApiClient (ein
fester 60s-Backoff für ALLE Aufrufer gemeinsam) wird hier PRO widget_id
gezählt und die Pause nach jedem weiteren Fehlschlag verdoppelt (30s, 60s,
120s, ... bis FAIL_BACKOFF_MAX_S) - ein dauerhaft nicht erreichbares
Gerät bekommt so mit der Zeit immer größere Pausen, statt den ESP32-P4
unbegrenzt oft mit demselben aussichtslosen Versuch zu belasten. Nach
einem einzigen Erfolg wird der Zähler sofort wieder auf 0 zurückgesetzt -
kein "Nachhinken" einer einmal instabilen Quelle, die sich erholt hat.
"""

import _thread

try:
    import time
except ImportError:
    time = None

_lock = _thread.allocate_lock()
_in_flight = {}        # widget_id -> Start-Zeitpunkt (ms), GERADE von einem Hintergrund-Thread bearbeitet
_backoff_override = {}  # widget_id -> (base_s, max_s) - eigene Backoff-Werte pro Widget (siehe submit())
_results = {}          # widget_id -> Ergebnis-Dict (vom Worker geschrieben, vom Hauptthread abgeholt)
_fail_count = {}       # widget_id -> Anzahl aufeinanderfolgender Fehlschläge (siehe Docstring oben)
_retry_after = {}      # widget_id -> Unix-Timestamp, vor dem submit() ablehnt (Backoff aktiv)
_bg = {}               # key -> [wert, zeitpunkt_ms oder None (=veraltet), laufende_nr, letzter_fehler]

# Insgesamt hoechstens so viele Hintergrund-Threads gleichzeitig. Jeder Thread braucht
# einen 64-KB-Stack; ein vierter gleichzeitiger Thread schlug auf dem Tab5 mit
# OSError("can't create thread") fehl (vermutlich knapper interner RAM, nicht der
# grosse Heap). Deshalb wieder 3 (der urspruengliche, bewaehrte Wert).
# Solange das Netzwerk beim Boot noch nicht steht (WLAN wird asynchron aufgebaut,
# siehe main.py::boot_network_task), werden WIDGET-Abrufe (News, Kalender, Wetter,
# PC-Status ...) nicht gestartet - sie wuerden nur an einem Timeout scheitern und
# danach ins Backoff laufen. submit() lehnt sie ab (ohne Fehlschlag zu zaehlen);
# der Aufrufer versucht es im naechsten Zyklus erneut. Hintergrund-Cache-Aufrufe
# ("bg:", z.B. SD-Verlauf) sind davon nicht betroffen.
_network_ready = True


def set_network_ready(ready):
    global _network_ready
    _network_ready = bool(ready)


MAX_CONCURRENT = 3
# Davon duerfen Widget-Abrufe (News, Kalender, PC-Status ...) hoechstens so viele
# belegen - der Rest ist fuer interaktive Hintergrund-Aufrufe reserviert (Web-UI,
# Touch-Schalter, SD-Verlauf), die sonst hinter den Widgets warten muessten.
MAX_WIDGET_CONCURRENT = 2

# Erster Backoff nach dem ERSTEN Fehlschlag einer Quelle, danach jeweils
# verdoppelt (30s, 60s, 120s, 240s, ...) bis zum Deckel FAIL_BACKOFF_MAX_S.
# Bewusst kürzer als der kürzeste normale FETCH_INTERVAL_S (30s bei
# server_status) angesetzt, damit ein EINZELNER Ausfall nicht gleich eine
# ganze Runde überspringt - erst bei WIEDERHOLTEN Fehlschlägen wächst die
# Pause spürbar.
FAIL_BACKOFF_BASE_S = 30
# 30 Minuten Deckel - länger als der langsamste normale FETCH_INTERVAL_S
# (15 Minuten), aber nicht so lang, dass eine inzwischen wieder erreichbare
# Quelle stundenlang stumm bliebe.
FAIL_BACKOFF_MAX_S = 1800

# _thread.start_new_thread() startet standardmäßig mit einem SEHR kleinen
# Stack (oft nur ein paar KB) - für unsere Fetch-Funktionen (Socket-I/O,
# JSON-Parsing, String-Verarbeitung, teils mehrere Aufrufebenen tief) hat
# das beim ersten echten Test zu einem sofortigen Absturz geführt ("Guru
# Meditation Error: Core 1 panic'ed (Stack protection fault)" in Task
# "mp_thread"). Bei über 23MB freiem RAM (siehe Speicher-Check) ist ein
# großzügiger Stack völlig unproblematisch - lieber zu viel als nochmal
# ein Stack-Überlauf. _thread.stack_size() muss laut MicroPython-Doku vor
# JEDEM start_new_thread()-Aufruf gesetzt werden (gilt jeweils nur für
# den NÄCHSTEN gestarteten Thread, kein globaler Default).
THREAD_STACK_SIZE = 65536  # 64 KB pro Hintergrund-Thread

# Ein Abruf, der länger als so viele Sekunden "in Bearbeitung" ist, gilt als
# hängengeblieben (Thread ist von außen nicht abbrechbar) - submit() gibt das
# Widget dann wieder frei, statt es für immer zu blockieren. Deutlich länger
# als der längste normale Abruf (Socket-Timeouts liegen bei 5-10s).
STALE_IN_FLIGHT_S = 45


def _now_ms():
    """Monotone Millisekunden (ticks_ms) - im Gegensatz zu time.time()
    unempfindlich gegen den NTP-Sprung nach dem Boot."""
    if time is not None and hasattr(time, "ticks_ms"):
        return time.ticks_ms()
    return int(time.time() * 1000) if time is not None else 0


def _age_s(start_ms):
    if time is not None and hasattr(time, "ticks_diff"):
        return time.ticks_diff(_now_ms(), start_ms) / 1000
    return (_now_ms() - start_ms) / 1000


# ---------------------------------------------------------------------------
# THREAD-POOL: Die Worker-Threads werden EINMAL beim Start angelegt und laufen dauerhaft.
# Vorher wurde fuer JEDEN Abruf ein neuer Thread mit 64-KB-Stack gestartet (PC-Status alle
# 3 s = >1000 pro Stunde, dazu Widgets/HA/Atom). Ueber Nacht (Log): nach etwa 15-90 min
# schlug das Anlegen ploetzlich dauerhaft fehl ("can't create thread", auch bei 0 laufenden
# Threads - Speicher/Task-Ressourcen fragmentiert bzw. erschoepft), Widgets froren ein, und
# das viele gc.collect() im Fehlerpfad blockierte die Oberflaeche ("schedule queue full").
# Mit dem Pool entsteht nach dem Boot KEIN neuer Thread mehr.
# Jeder Worker wartet auf einer eigenen Sperre ("wake") - submit() gibt sie frei, sobald
# ein Auftrag bereitliegt (die Sperre wird als Semaphor benutzt; auf dem ESP32 darf sie von
# einem anderen Thread freigegeben werden als von dem, der sie belegt hat).
# ---------------------------------------------------------------------------
POOL_SIZE = MAX_CONCURRENT


class _PoolWorker:
    def __init__(self, idx):
        self.idx = idx
        self.wake = _thread.allocate_lock()
        self.wake.acquire()      # belegt: der Worker blockiert am naechsten acquire() bis submit() freigibt
        self.busy = False
        self.job = None


_pool = []
_pool_started = False


def _pool_loop(w):
    while True:
        w.wake.acquire()         # schlaeft (ohne CPU), bis ein Auftrag da ist
        job = w.job
        if job is not None:
            try:
                _worker(*job)    # faengt Fetcher-Ausnahmen selbst ab und schreibt das Ergebnis
            except Exception as e:
                print("fetch_worker: Pool-Worker %d: unerwarteter Fehler: %r" % (w.idx, e))
        with _lock:
            w.job = None
            w.busy = False


def start_pool():
    """Legt die dauerhaften Worker-Threads an (einmalig). Am besten GANZ am Anfang aufrufen
    (main.py), solange der Speicher noch nicht fragmentiert ist. Gibt die Zahl der laufenden
    Worker zurueck (0 = Fallback auf einen Thread pro Abruf)."""
    global _pool_started
    if _pool_started:
        return len(_pool)
    _pool_started = True
    for i in range(POOL_SIZE):
        w = _PoolWorker(i)
        started = False
        for attempt in range(2):
            try:
                _thread.stack_size(THREAD_STACK_SIZE)
            except Exception:
                pass
            try:
                _thread.start_new_thread(_pool_loop, (w,))
                started = True
                break
            except Exception as e:
                print("fetch_worker: Pool-Thread %d nicht startbar (Versuch %d): %r" % (i, attempt + 1, e))
                try:
                    import gc
                    gc.collect()
                except Exception:
                    pass
        if started:
            _pool.append(w)
    print("fetch_worker: Thread-Pool gestartet (%d von %d Workern)" % (len(_pool), POOL_SIZE))
    return len(_pool)


_fail_log_ms = {}


def _log_start_failure(widget_id, e):
    """Fehlermeldung beim Thread-Start hoechstens einmal je Minute und Widget (Ueber Nacht
    waren es tausende Zeilen)."""
    now = _now_ms()
    last = _fail_log_ms.get(widget_id)
    if last is None or _age_s(last) >= 60:
        _fail_log_ms[widget_id] = now
        with _lock:
            running = len(_in_flight)
        print("fetch_worker: Thread-Start für %r fehlgeschlagen: %r (gleichzeitig laufend: %d)" % (widget_id, e, running))


def submit(widget_id, fetcher_fn, screen, parts, backoff_base_s=None, backoff_max_s=None):
    """Reicht einen Abruf ein, FALLS gerade Kapazität frei ist, dieses
    Widget nicht bereits in Bearbeitung ist UND kein Backoff aktiv ist
    (siehe Modul-Docstring). fetcher_fn wird als fetcher_fn(screen, parts)
    im Hintergrund-Thread aufgerufen und muss ein reines Daten-Dict
    zurückgeben (siehe Modul-Docstring - KEIN LVGL-Zugriff darin!). Gibt
    True zurück, wenn angenommen, sonst False (einfach beim nächsten
    refresh()-Zyklus erneut versuchen - last_fetch dann nicht
    aktualisieren, siehe Aufrufer - BESSER NOCH: vorher in_backoff()
    prüfen und das Widget dann gar nicht erst als "fällig" behandeln,
    siehe screens/widget_catalog.py::refresh()).

    backoff_base_s/backoff_max_s (optional): eigene Backoff-Werte für dieses
    Widget statt der globalen 30s..30min - nötig für schnell getaktete
    Widgets (Computer-Status, 3s-Takt), sonst würde eine kurze Störung
    (z.B. PC über Nacht aus) die Anzeige danach bis zu 30 Minuten
    einfrieren lassen."""
    if not _pool_started:
        start_pool()   # Notfall: falls main.py den Pool nicht schon frueh gestartet hat
    worker = None
    with _lock:
        if not _network_ready and not widget_id.startswith("bg:"):
            return False
        if backoff_base_s is not None and backoff_max_s is not None:
            _backoff_override[widget_id] = (backoff_base_s, backoff_max_s)
        started = _in_flight.get(widget_id)
        if started is not None:
            if _age_s(started) < STALE_IN_FLIGHT_S:
                return False
            # Hängengebliebener Abruf: Widget wieder freigeben (siehe
            # STALE_IN_FLIGHT_S). Ein evtl. später doch noch fertig werdender
            # alter Thread schreibt höchstens ein veraltetes Ergebnis.
            print("fetch_worker: Abruf %r hängt seit >%ds - gebe frei" % (widget_id, STALE_IN_FLIGHT_S))
            _in_flight.pop(widget_id, None)
        if len(_in_flight) >= MAX_CONCURRENT:
            return False
        if not widget_id.startswith("bg:"):
            widget_jobs = 0
            for k in _in_flight:
                if not k.startswith("bg:"):
                    widget_jobs += 1
            if widget_jobs >= MAX_WIDGET_CONCURRENT:
                return False
        if time is not None and time.time() < _retry_after.get(widget_id, 0):
            return False  # Backoff aktiv, siehe _update_backoff_locked()
        if _pool:
            for w in _pool:
                if not w.busy:
                    worker = w
                    break
            if worker is None:
                return False   # alle Worker beschaeftigt - naechster Zyklus
            worker.busy = True
            worker.job = (widget_id, fetcher_fn, screen, parts)
        _in_flight[widget_id] = _now_ms()
    if worker is not None:
        try:
            worker.wake.release()   # Worker aufwecken
        except Exception as e:
            # Diese Firmware erlaubt das Freigeben der Sperre aus einem anderen Thread nicht:
            # Worker aus dem Pool nehmen (bei leerem Pool greift der Ein-Thread-pro-Abruf-Fallback).
            print("fetch_worker: Worker %d nicht aufweckbar (%r) - Pool wird abgeschaltet" % (worker.idx, e))
            with _lock:
                del _pool[:]
                worker.busy = False
                worker.job = None
                _in_flight.pop(widget_id, None)
            return False
        return True

    # ---- Fallback ohne Pool (Pool-Threads liessen sich beim Start nicht anlegen): ein Thread pro Abruf ----
    try:
        _thread.stack_size(THREAD_STACK_SIZE)
    except Exception:
        pass  # falls diese Firmware stack_size() nicht unterstützt - dann eben mit Default-Größe versuchen
    try:
        _thread.start_new_thread(_worker, (widget_id, fetcher_fn, screen, parts))
    except Exception as e:
        # Thread konnte nicht gestartet werden (z.B. zu wenig zusammenhängender
        # RAM für den 64-KB-Stack nach längerer Laufzeit). Vorher blieb die
        # widget_id dadurch DAUERHAFT in _in_flight hängen -> submit() lehnte
        # dieses Widget für immer stillschweigend ab (Anzeige fror ein).
        with _lock:
            _in_flight.pop(widget_id, None)
        _log_start_failure(widget_id, e)
        return False
    return True


def _update_backoff_locked(widget_id, ok):
    """NUR innerhalb eines bereits gehaltenen _lock aufrufen (siehe
    _worker() unten) - kein eigenes Locking, um das (nicht wiedereintritts-
    fähige) _lock nicht doppelt zu belegen."""
    if ok:
        _fail_count.pop(widget_id, None)
        _retry_after.pop(widget_id, None)
        return
    count = _fail_count.get(widget_id, 0) + 1
    _fail_count[widget_id] = count
    if time is not None:
        base_s, max_s = _backoff_override.get(widget_id, (FAIL_BACKOFF_BASE_S, FAIL_BACKOFF_MAX_S))
        backoff_s = min(max_s, base_s * (2 ** min(count - 1, 20)))
        _retry_after[widget_id] = time.time() + backoff_s


def in_backoff(widget_id):
    """True, wenn widget_id gerade eine Fehlschlags-Backoff-Pause hat.
    Vom Aufrufer (screens/widget_catalog.py::refresh()) genutzt, um
    solche Widgets schon VOR submit() aus der "fällig"-Liste
    auszuschließen - sonst würden sie (nie erfolgreich, aber auch nie
    "last_fetch"-aktualisiert) mit wachsender Dringlichkeit immer wieder
    ganz oben in der Prioritätsliste landen und den wenigen
    MAX_FETCHES_PER_CYCLE-Plätzen pro Zyklus tatsächlich sendebereite
    andere Widgets wegnehmen."""
    if time is None:
        return False
    with _lock:
        return time.time() < _retry_after.get(widget_id, 0)


def _worker(widget_id, fetcher_fn, screen, parts):
    try:
        data = fetcher_fn(screen, parts)
    except Exception as e:
        # Darf hier NICHT eskalieren (unbehandelte Exception in einem
        # _thread-Thread lässt sich vom Hauptthread aus nicht abfangen) -
        # stattdessen als normales "Fehler"-Ergebnis zurückgeben, genau
        # wie es die widget_sources.py-Funktionen bei einem Netzwerk-
        # Fehler selbst auch tun würden.
        data = {"ok": False, "msg": "Hintergrund-Abruf fehlgeschlagen: %s" % e}
    with _lock:
        if widget_id.startswith("bg:"):
            # Hintergrund-Cache (siehe submit_cached()): eigener Speicher, damit
            # collect_results() der Widgets diese Ergebnisse nicht "wegschnappt".
            key = widget_id[3:]
            entry = _bg.get(key)
            if entry is None:
                entry = [None, None, 0, None]
                _bg[key] = entry
            if data.get("ok"):
                entry[0] = data.get("value")
                entry[3] = None
            else:
                entry[3] = data.get("msg") or "Fehler"   # alter Wert bleibt erhalten
            entry[1] = _now_ms()
            entry[2] += 1
        else:
            _results[widget_id] = data
        _in_flight.pop(widget_id, None)
        _update_backoff_locked(widget_id, bool(data) and data.get("ok"))


# ---------------------------------------------------------------------------
# Hintergrund-Cache fuer blockierende Abrufe ausserhalb der Widgets
# (Home Assistant, Atom-Relais, Remote-Sensor, SD-Historie, Geocode ...).
# Frueher liefen diese Aufrufe direkt im Hauptthread bzw. im Web-Handler und
# froren LVGL/Touch/Tasks bis zum Timeout ein (5-15 s bei nicht erreichbarer
# Gegenstelle). Jetzt: fn() laeuft in einem Hintergrund-Thread, der Aufrufer
# bekommt SOFORT den zuletzt gespeicherten Wert (oder None).
#   submit_cached(key, fn, min_interval_s) -> (wert, laufende_nr)
#   bg_get(key)                            -> (wert, laufende_nr)
#   bg_submit(key, fn)                     -> True/False (angenommen?)
#   bg_expire(key)                         -> Wert als veraltet markieren
#   bg_error(key)                          -> letzte Fehlermeldung oder None
# fn() darf NICHT auf LVGL-Objekte zugreifen (siehe Modul-Docstring) und soll
# einen normalen Wert zurueckgeben; eine Exception wird als Fehler notiert.
# ---------------------------------------------------------------------------
def _bg_runner(fn):
    def run(screen, parts):
        try:
            return {"ok": True, "value": fn()}
        except Exception as e:
            return {"ok": False, "msg": str(e)}
    return run


def bg_get(key):
    with _lock:
        entry = _bg.get(key)
        if entry is None:
            return None, 0
        return entry[0], entry[2]


def bg_error(key):
    with _lock:
        entry = _bg.get(key)
        return entry[3] if entry else None


def bg_expire(key):
    with _lock:
        entry = _bg.get(key)
        if entry is not None:
            entry[1] = None


def bg_clear(key):
    """Gespeicherten Wert verwerfen (Speicher freigeben, z.B. nach einem
    einmaligen grossen Ergebnis wie einem konvertierten Logo)."""
    with _lock:
        _bg.pop(key, None)


def bg_submit(key, fn):
    # Kurzes Backoff (2..10s): diese Abrufe sind interaktiv (Web-UI/Touch) und
    # haben meist ein eigenes Backoff im jeweiligen Client (60s).
    return submit("bg:" + key, _bg_runner(fn), None, None, backoff_base_s=2, backoff_max_s=10)


def submit_cached(key, fn, min_interval_s):
    with _lock:
        entry = _bg.get(key)
        stale = entry is None or entry[1] is None or _age_s(entry[1]) >= min_interval_s
    if stale:
        bg_submit(key, fn)
    return bg_get(key)


def collect_results(widget_ids=None):
    """Vom Hauptthread aufgerufen - gibt fertiggewordene Ergebnisse zurück
    und entfernt sie aus der internen Ablage.

    widget_ids=None (Standard): alles abholen (für den normalen 20s-
    refresh()-Zyklus in screens/widget_catalog.py).
    widget_ids=<Liste/Menge>: NUR diese IDs abholen, alle anderen bleiben
    unangetastet in der Ablage liegen - wichtig für separate, schnellere
    Update-Takte wie main.py::pc_status_task() (alle 3s), die sonst dem
    normalen 20s-Zyklus Ergebnisse "wegschnappen" oder umgekehrt selbst
    leer ausgehen könnten, wenn beide gleichzeitig ALLES abholen wollten."""
    global _results
    with _lock:
        if widget_ids is None:
            out = _results
            _results = {}
        else:
            out = {}
            for wid in widget_ids:
                if wid in _results:
                    out[wid] = _results.pop(wid)
    return out
