"""
Eigenstaendiger Akku-Diagnose-Test fuer die Tab5 - laeuft UNABHAENGIG vom
restlichen Tab5-MagicMirror-Projekt (gleiches Prinzip wie diagnose_mic.py).

Hintergrund: main.py::status_bar_task() zeigt aktuell fest "100%" (TODO,
nie an echte Werte angebunden). Laut M5Stack-Doku ("Tab5 Power"):
  M5.Power.isCharging()
  M5.Power.getBatteryLevel()     -> 0-100
  M5.Power.getBatteryVoltage()   -> mV

WICHTIG: Auf dieser Firmware haben sich bereits ZWEI aehnliche APIs
(M5.Imu.getAccel(), M5.Mic.record()) als "meldet Erfolg, liefert aber
keine echten Werte" herausgestellt - deshalb hier erst einmal isoliert
und wiederholt testen, BEVOR es ins Hauptprogramm eingebaut wird.

Benutzung:
    mpremote connect <PORT> run diagnose_battery.py

Waehrend des Laufs einmal das USB-Ladekabel ab- und wieder anstecken,
um zu sehen, ob sich isCharging()/getBatteryLevel() dabei aendern.
"""

import time
import M5

M5.begin()

print("dir(M5.Power):")
print(" ", dir(M5.Power))
print()

for i in range(10):
    line = []
    try:
        line.append("isCharging=%r" % M5.Power.isCharging())
    except Exception as e:
        line.append("isCharging() raised: %s" % e)
    try:
        line.append("getBatteryLevel=%r" % M5.Power.getBatteryLevel())
    except Exception as e:
        line.append("getBatteryLevel() raised: %s" % e)
    try:
        line.append("getBatteryVoltage=%r" % M5.Power.getBatteryVoltage())
    except Exception as e:
        line.append("getBatteryVoltage() raised: %s" % e)
    try:
        line.append("getBatteryCurrent=%r" % M5.Power.getBatteryCurrent())
    except Exception as e:
        line.append("getBatteryCurrent() raised: %s" % e)

    print("Durchlauf %d: %s" % (i + 1, ", ".join(line)))
    time.sleep(2)

print()
print("Fertig. Bitte den KOMPLETTEN Log-Ausschnitt oben zurueckmelden, "
      "am besten inkl. einem Durchlauf mit angestecktem und einem mit "
      "abgezogenem Ladekabel, falls sich die Werte dabei aendern.")
