# Startskript (main.py) - erzeugt von tools/build_mpy.sh, oder von Hand kopieren:
#   main.py (bisheriger Inhalt)  ->  umbenennen in app.py      (oder als app.mpy vorkompilieren)
#   diese Datei                  ->  als main.py auf das Geraet
#
# UIFlow2-Startmenue (siehe /flash/boot.py der Firmware): boot.py liest im NVS ("uiflow") die Zahl
# boot_option: 0 = main.py direkt starten, 1 = Startmenue + Netzwerk-Einrichtung, 2 = nur Netzwerk.
# Nach dem Menue setzt boot.py die Variable _uiflow_run_main auf False, wenn man im Menue
# "bleibt" (dann soll main.py NICHT losrennen) - main.py und boot.py teilen sich die Variablen.
#
# Ablauf "UIFlow2 starten" (Web-UI /system):
#   1. Web-UI setzt boot_option = 1 und legt /flash/uiflow_modus an, dann Neustart.
#   2. boot.py zeigt das UIFlow2-Startmenue.
#   3. Dieses Skript: findet den Merker -> loescht ihn und setzt boot_option wieder auf 0
#      (EINMALIG - der naechste Neustart startet unser Programm direkt). Unser Programm
#      startet nur, wenn im Menue nicht "im Menue bleiben" gewaehlt wurde (_uiflow_run_main).
# Dauerhaft im Startmenue bleiben: boot_option = 1 OHNE Merker setzen (siehe Anleitung).
import os


def _exists(path):
    try:
        os.stat(path)
        return True
    except OSError:
        return False


def _set_boot_option(value):
    try:
        import esp32
        nvs = esp32.NVS("uiflow")
        nvs.set_u8("boot_option", value)
        nvs.commit()
        return True
    except Exception:
        return False


if _exists("/flash/uiflow_modus"):
    try:
        os.remove("/flash/uiflow_modus")
    except OSError:
        pass
    _set_boot_option(0)   # ab dem naechsten Start wieder direkt unser Programm

if globals().get("_uiflow_run_main", True):
    import app
else:
    print("UIFlow2-Modus: im Startmenue geblieben - eigenes Programm wird nicht gestartet. "
          "Neustart startet es wieder.")
