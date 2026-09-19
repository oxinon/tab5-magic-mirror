"""
Eigenstaendiger SD-Karten-Diagnose-Test fuer die Tab5 (ESP32-P4) - laeuft
UNABHAENGIG vom Hauptprogramm, kein Risiko fuer Display/restliche App.

Hintergrund: SD-Karten-Unterstuetzung fuer den ESP32-P4 (SDIO 3.0, braucht
eine Spannungsumschaltung 3.3V/1.8V ueber einen internen LDO-Regler) ist
ein AKTUELL OFFENER, noch nicht vollstaendig geloester Bug direkt in
MicroPython selbst (siehe micropython/micropython Issue #18984,
"Unable to use SD card on some ESP32-P4 boards") - das betrifft alle
ESP32-P4-Boards, nicht nur die Tab5 oder diese Firmware. Es kann also
sein, dass hier gar keine Kombination funktioniert, unabhaengig davon,
was wir versuchen.

Probiert trotzdem systematisch mehrere plausible Varianten:
  1. machine.SDCard() ganz ohne Argumente - falls das Tab5-Board-Profil
     Slot/Pins/LDO automatisch waehlt (wie bei M5.Mic/M5.Power/M5.Imu).
  2. slot=0 (SDIO 3.0, feste Pins lt. ESP32-P4-Architektur) mit und ohne
     expliziten ldo-Wert (1-4, siehe aktuelle MicroPython-Doku zu
     machine.SDCard - "ldo" ist neu und speziell fuer den P4-Fall).
  3. slot=1 (SDIO 2.0, langsamer, aber flexibler verdrahtbar).

Benutzung:
    mpremote connect <PORT> run diagnose_sdcard.py

WICHTIG: Vorher eine (leere oder testweise beschreibbare) SD-Karte
einlegen, sonst schlagen alle Varianten mit "no card" statt einem
echten Ergebnis fehl.
"""

import machine
import os
import time

print("Firmware/Board-Info:")
try:
    print(" ", os.uname())
except Exception as e:
    print("  os.uname() fehlgeschlagen:", e)
print()


def _try_mount(label, make_card):
    print("-" * 60)
    print("Teste:", label)
    try:
        card = make_card()
    except Exception as e:
        print("  SDCard(...) fehlgeschlagen:", repr(e))
        return False

    try:
        os.mount(card, "/sd_test")
    except Exception as e:
        print("  os.mount() fehlgeschlagen:", repr(e))
        return False

    try:
        files = os.listdir("/sd_test")
        print("  ERFOLG! Inhalt von /sd_test:", files)
        test_path = "/sd_test/tab5_diagnose_test.txt"
        with open(test_path, "w") as f:
            f.write("Tab5 SD-Test OK\n")
        with open(test_path) as f:
            print("  Testdatei geschrieben+gelesen:", f.read().strip())
        os.remove(test_path)
        return True
    except Exception as e:
        print("  Zugriff nach dem Mounten fehlgeschlagen:", repr(e))
        return False
    finally:
        try:
            os.umount("/sd_test")
        except Exception:
            pass


results = {}

results["SDCard() ohne Argumente"] = _try_mount(
    "machine.SDCard() ohne Argumente (Board-Profil-Autodetect?)",
    lambda: machine.SDCard())

results["slot=0"] = _try_mount(
    "machine.SDCard(slot=0) - SDIO 3.0, feste Pins",
    lambda: machine.SDCard(slot=0))

for ldo in (1, 2, 3, 4):
    results["slot=0, ldo=%d" % ldo] = _try_mount(
        "machine.SDCard(slot=0, ldo=%d)" % ldo,
        lambda ldo=ldo: machine.SDCard(slot=0, ldo=ldo))

results["slot=1"] = _try_mount(
    "machine.SDCard(slot=1) - SDIO 2.0",
    lambda: machine.SDCard(slot=1))

print("-" * 60)
print("Zusammenfassung:")
for label, ok in results.items():
    print("  %s -> %s" % (label, "ERFOLG" if ok else "fehlgeschlagen"))
print()
print("Fertig. Bitte den KOMPLETTEN Log zurückmelden.")
