"""
Eigenständiger Diagnose-Test für die Akku-SPANNUNG (nicht den bereits
bestätigten Prozentwert, siehe main.py::M5.Power.getBatteryLevel()) -
läuft UNABHÄNGIG vom restlichen Projekt.

Hintergrund: main.py liest bereits erfolgreich M5.Power.getBatteryLevel()
(Prozent) - laut Kommentar dort per früherem diagnose_battery.py
bestätigt. Die Spannung selbst (typischerweise M5.Power.getBatteryVoltage(),
liefert Millivolt) wurde bisher nicht getestet. Nach den zwei Abstürzen
in dieser Session bei anderen, neuen M5-Aufrufen (Mic, SDCard) hier erst
isoliert prüfen, BEVOR das in main.py/status_bar.py eingebaut wird.

Benutzung:
    mpremote connect <PORT> run diagnose_battery_voltage.py
"""

import time
import M5

M5.begin()

print("Firmware/Board-Info:")
try:
    import os
    print(" ", os.uname())
except Exception as e:
    print("  os.uname() fehlgeschlagen:", e)
print()

print("dir(M5.Power):")
print(" ", dir(M5.Power))
print()

# Bereits bestätigt funktionsfähig (siehe main.py) - als Referenzwert
# in derselben Ausgabe, damit man beide Werte nebeneinander sieht.
try:
    print("M5.Power.getBatteryLevel() =", M5.Power.getBatteryLevel(), "%  (bereits bestätigt, siehe main.py)")
except Exception as e:
    print("M5.Power.getBatteryLevel() raised:", e)
print()

for probe in ("getBatteryVoltage", "isCharging", "getBatteryCurrent", "getType"):
    if hasattr(M5.Power, probe):
        try:
            val = getattr(M5.Power, probe)()
            print("M5.Power.%s() = %r" % (probe, val))
        except Exception as e:
            print("M5.Power.%s() raised: %s" % (probe, e))
    else:
        print("M5.Power hat kein '%s'" % probe)

print()
print("Mehrfach hintereinander lesen (Spannung sollte sich unter Last/")
print("beim Laden leicht bewegen, ein FESTER Wert wäre verdächtig -")
print("gleiches Muster wie beim M5.Imu-Firmware-Bug):")
for i in range(5):
    try:
        v = M5.Power.getBatteryVoltage()
        print("  Messung %d: %r" % (i + 1, v))
    except Exception as e:
        print("  Messung %d raised: %s" % (i + 1, e))
    time.sleep_ms(500)

print()
print("Fertig. Bitte den kompletten Log zurückmelden.")
