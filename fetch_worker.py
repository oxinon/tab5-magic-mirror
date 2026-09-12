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
"""

import _thread

_lock = _thread.allocate_lock()
_in_flight = set()    # widget_ids, die GERADE von einem Hintergrund-Thread bearbeitet werden
_results = {}          # widget_id -> Ergebnis-Dict (vom Worker geschrieben, vom Hauptthread abgeholt)

MAX_CONCURRENT = 3

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


def submit(widget_id, fetcher_fn, screen, parts):
    """Reicht einen Abruf ein, FALLS gerade Kapazität frei ist und dieses
    Widget nicht bereits in Bearbeitung ist. fetcher_fn wird als
    fetcher_fn(screen, parts) im Hintergrund-Thread aufgerufen und muss
    ein reines Daten-Dict zurückgeben (siehe Modul-Docstring - KEIN LVGL-
    Zugriff darin!). Gibt True zurück, wenn angenommen, sonst False
    (einfach beim nächsten refresh()-Zyklus erneut versuchen - last_fetch
    dann nicht aktualisieren, siehe Aufrufer)."""
    with _lock:
        if widget_id in _in_flight or len(_in_flight) >= MAX_CONCURRENT:
            return False
        _in_flight.add(widget_id)
    try:
        _thread.stack_size(THREAD_STACK_SIZE)
    except Exception:
        pass  # falls diese Firmware stack_size() nicht unterstützt - dann eben mit Default-Größe versuchen
    _thread.start_new_thread(_worker, (widget_id, fetcher_fn, screen, parts))
    return True


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
        _results[widget_id] = data
        _in_flight.discard(widget_id)


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
