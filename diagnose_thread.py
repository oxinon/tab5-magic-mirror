"""
Eigenstaendiger Multithreading-Diagnose-Test - laeuft UNABHAENGIG vom
Hauptprogramm, kein Risiko fuer Display/restliche App.

Hintergrund: die Uhr (und alles andere) haengt gelegentlich 1-2s, weil
Netzwerk-Anfragen (Wetter, News, ...) BLOCKIEREND im selben Thread wie
LVGL/Touch laufen - eine einzelne langsame Anfrage friert die ganze
Oberflaeche fuer ihre Dauer ein. Die eigentliche Loesung: Netzwerk-I/O in
einen Hintergrund-Thread auslagern (_thread-Modul), der Haupt-Thread
bleibt die ganze Zeit frei fuer Touch/LVGL-Rendering.

WICHTIG: Das ist der erste Einsatz von Multithreading in diesem Projekt -
bisher unbekannt, ob/wie gut _thread auf dieser Firmware funktioniert.
Dieses Skript testet das isoliert:
  1. Existiert _thread ueberhaupt, kann ein Thread gestartet werden?
  2. Funktioniert die Kommunikation zurueck zum Hauptthread (simples,
     gemeinsam genutztes Dict) zuverlaessig?
  3. Der eigentliche Test: WAEHREND der Hintergrund-Thread eine
     blockierende Netzwerk-Anfrage macht (mit absichtlich langsamer
     Verzoegerung simuliert, siehe time.sleep() im Worker), zaehlt der
     Hauptthread parallel einen Zaehler hoch - bleibt der Zaehler
     wirklich UNGEBREMST weiterlaufen, ist der Beweis erbracht, dass der
     Hauptthread tatsaechlich nicht blockiert wird.

Benutzung:
    mpremote connect <PORT> run diagnose_thread.py
"""

import time

try:
    import _thread
    _HAS_THREAD = True
except ImportError:
    _HAS_THREAD = False

print("_thread verfügbar:", _HAS_THREAD)
if not _HAS_THREAD:
    print("Fertig - _thread existiert nicht auf dieser Firmware, Ende.")
else:
    print("dir(_thread):", dir(_thread))
    print()

    # --- Test 1: einfacher Start + Rueckgabe ueber ein gemeinsames Dict ---
    result = {"done": False, "value": None}

    def _worker_simple():
        result["value"] = 41 + 1
        result["done"] = True

    try:
        _thread.start_new_thread(_worker_simple, ())
        waited_ms = 0
        while not result["done"] and waited_ms < 2000:
            time.sleep_ms(10)
            waited_ms += 10
        print("Test 1 (einfacher Thread + Rückgabewert):",
              "OK, Ergebnis =", result["value"], "nach %dms" % waited_ms
              if result["done"] else "FEHLGESCHLAGEN (Timeout)")
    except Exception as e:
        print("Test 1 fehlgeschlagen:", repr(e))

    print()

    # --- Test 2: blockiert ein "langsamer" Hintergrund-Thread wirklich
    # NICHT den Hauptthread? ---
    slow_result = {"done": False}

    def _worker_slow():
        # Simuliert eine langsame Netzwerk-Anfrage (2 Sekunden).
        time.sleep(2)
        slow_result["done"] = True

    try:
        _thread.start_new_thread(_worker_slow, ())
        counter = 0
        start = time.ticks_ms()
        while not slow_result["done"]:
            counter += 1
            time.sleep_ms(50)
            if time.ticks_diff(time.ticks_ms(), start) > 3000:
                break  # Sicherheitsnetz, falls Test 2 haengen bleibt
        elapsed = time.ticks_diff(time.ticks_ms(), start)
        print("Test 2 (Hauptthread waehrend 2s-Hintergrundarbeit):")
        print("  Hintergrund-Thread fertig:", slow_result["done"])
        print("  Hauptthread-Zaehler in der Zwischenzeit hochgezaehlt auf:", counter)
        print("  Vergangene Zeit im Hauptthread: %dms" % elapsed)
        print("  -> Wenn 'counter' deutlich > 0 UND done=True nach ca. 2000ms:")
        print("     Hauptthread lief WIRKLICH parallel weiter, kein Blockieren!")
    except Exception as e:
        print("Test 2 fehlgeschlagen:", repr(e))

    print()
    print("Fertig. Bitte den kompletten Log zurückmelden.")
