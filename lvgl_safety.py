"""
Decorator für LVGL-Event-Callbacks (Optimierungs-Backlog Punkt 2, siehe
HANDOFF.md) - fasst das bisher in jedem einzelnen Touch-Callback von Hand
wiederholte try/except-Muster zusammen. Grund für das Muster: eine
unbehandelte Exception in einem LVGL-Event-Callback reißt den kompletten
m5ui/LVGL-Scheduler mit ("schedule queue full" als Folgefehler direkt
danach im Log, komplettes Einfrieren des Geräts) - schon mehrfach genau so
beobachtet (siehe z.B. die Historie in config.py::save()-Docstring für den
ersten, andersartigen Fall dieses Musters).

WICHTIG/UNVERIFIZIERT (Warnung aus dem Optimierungs-Backlog selbst):
MicroPython-Funktionsobjekte haben nicht in jedem Kontext zuverlässig ein
__name__-Attribut - und die hier eingesetzten Callbacks sind praktisch
IMMER Closures (von einer _make_*_handler()-Fabrikfunktion zurückgegebene
innere "_handler"-Funktionen), nicht einfache Top-Level-Funktionen, bei
denen __name__ typischerweise zuverlässiger ist. Bevor dieser Decorator
produktiv verlässlich Namen loggt, bitte auf echter Hardware kurz
verifizieren (z.B. per einfachem diagnose_callback_name.py: eine Closure
bauen, decorieren, auslösen, Log-Ausgabe prüfen). Deshalb hier defensiv
gebaut:
- Ein explizites "label" mitgeben, wo möglich (siehe Aufrufer in
  screens/dashboard.py, screens/sensor_history_screen.py, screens/
  widget_catalog.py) - jede _make_*_handler()-Fabrik kennt ohnehin schon
  einen aussagekräftigen Bezeichner wie eine entity_id/relay_id/einen
  Board-Index, der informativer ist als jeder Funktionsname.
- Nur falls kein label übergeben wurde, versuchsweise func.__name__ lesen
  (in einem eigenen try/except - eine Closure liefert dabei ohnehin oft
  nur die wenig hilfreiche Zeichenkette "_handler").
- Als letzten Rückfall "?" verwenden. NIE selbst einen Fehler werfen - ein
  Fehler HIER, im Sicherheitsnetz selbst, wäre besonders bitter (genau die
  Situation, vor der dieser Decorator eigentlich schützen soll).
"""


try:
    import sys
except ImportError:
    sys = None


def lvgl_safe_callback(default_return=None, label=None):
    """Decorator-Fabrik für LVGL-Event-Callbacks. Verwendung direkt an
    einer Handler-Funktion:

        @lvgl_safe_callback(label="Atom-Schalter Relais 1")
        def _handler(e):
            ...

    oder - der häufigere Fall in diesem Projekt - innerhalb einer
    _make_*_handler()-Fabrikfunktion, die eine Closure zurückgibt und
    dabei bereits über die relevanten Bezeichner (z.B. relay_id) verfügt:

        def _make_atom_toggle_handler(self, board_index, switch_entry):
            @lvgl_safe_callback(
                label="Atom-Schalter Board %d Relais %s" % (board_index, switch_entry["relay_id"]))
            def _handler(e):
                ...
            return _handler

    default_return: was der Callback im Fehlerfall zurückgeben soll -
    für LVGL-Event-Callbacks praktisch immer None (LVGL erwartet ohnehin
    keinen Rückgabewert), aber konfigurierbar für den Fall, dass dieser
    Decorator auch anderswo (nicht-LVGL) eingesetzt wird.

    Loggt im Fehlerfall den VOLLSTÄNDIGEN Traceback (sys.print_exception())
    statt nur der Fehlermeldung, sonst sieht man im Log oft nur etwas wie
    "function takes 3 positional arguments but 2 were given" OHNE zu
    wissen, welcher der vielen Callbacks im Projekt das war (siehe Vorbild
    burger_menu.py::_switch(), von dort übernommen). sys.print_exception()
    ist MicroPython-spezifisch (nicht Teil von CPythons sys-Modul) - beim
    lokalen Testen unter Desktop-Python (siehe tools/sim_test.py) daher
    ein einfacher Fallback auf repr(e)."""
    def decorator(func):
        name = label
        if name is None:
            try:
                name = func.__name__
            except Exception:
                name = "?"

        def wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                # KRITISCH: absichtlich IMMER abfangen und nur loggen,
                # egal was schiefgeht - siehe Modul-Docstring oben.
                print("LVGL-Callback-Fehler in %s:" % name)
                try:
                    sys.print_exception(e)
                except AttributeError:
                    print(repr(e))  # Desktop-Python-Fallback
                return default_return
        return wrapper
    return decorator
