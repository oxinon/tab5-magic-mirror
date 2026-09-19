"""
Letzter, gezielter SD-Karten-Test: NUR slot=1 (SDIO 2.0) - die einzige
Variante aus diagnose_sdcard.py, die beim letzten Durchlauf nicht mehr
getestet wurde, weil das Skript vorher (bei den ldo=-Versuchen) abgestürzt
ist. slot=1 nutzt KEIN ldo-Kwarg, also denselben unkritischen Code-Pfad
wie die ersten beiden - erfolgreich durchgelaufenen (wenn auch mit ENODEV
gescheiterten) - Tests, kein erhöhtes Absturzrisiko zu erwarten.

Bereits bestätigt (siehe letzter Durchlauf): "ldo" wird von dieser
Firmware (MicroPython v1.27.0-dirty) nicht erkannt (TypeError) - der
micropython/micropython#18984-Fix ist in diesem Build noch nicht
enthalten. Dieser Test hier prüft nur noch die letzte offene Variante.

Benutzung:
    mpremote connect <PORT> run diagnose_sdcard_slot1.py
"""

import machine
import os

print("Firmware/Board-Info:")
try:
    print(" ", os.uname())
except Exception as e:
    print("  os.uname() fehlgeschlagen:", e)
print()

print("Teste: machine.SDCard(slot=1) - SDIO 2.0")
try:
    card = machine.SDCard(slot=1)
except Exception as e:
    print("  SDCard(slot=1) fehlgeschlagen:", repr(e))
    card = None

if card is not None:
    try:
        os.mount(card, "/sd_test")
        files = os.listdir("/sd_test")
        print("  ERFOLG! Inhalt von /sd_test:", files)
        test_path = "/sd_test/tab5_diagnose_test.txt"
        with open(test_path, "w") as f:
            f.write("Tab5 SD-Test OK\n")
        with open(test_path) as f:
            print("  Testdatei geschrieben+gelesen:", f.read().strip())
        os.remove(test_path)
    except Exception as e:
        print("  Mounten/Zugriff fehlgeschlagen:", repr(e))
    finally:
        try:
            os.umount("/sd_test")
        except Exception:
            pass

print()
print("Fertig. Bitte den Log zurückmelden.")
