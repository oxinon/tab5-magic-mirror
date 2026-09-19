"""
Workaround-Test: internal_spk=False (Optimierungs-Idee aus GitHub-Issue
m5stack/M5Unified#347, siehe Zusammenfassung im Chat) - deaktiviert den
internen Lautsprecher schon BEIM ALLERERSTEN M5.begin() dieser Sitzung,
damit der gemeinsame ES8311-Codec nie in den "für Aufnahme unscharfen"
Zustand kommt (siehe Issue: "cfg.internal_spk = false, speaker never
begun -> works").

WICHTIG - DAMIT DIESER TEST AUSSAGEKRÄFTIG IST, MUSS ER DIE ALLERERSTE
M5.begin()-INITIALISIERUNG NACH EINEM ECHTEN STROMLOS-RESET SEIN:
Laut GitHub-Issue hilft ein nachträgliches Zurücksetzen (auch auf
Register-/Peripherie-Ebene) NICHT mehr, sobald der Lautsprecher einmal
lief - nur "von Anfang an nie starten" funktioniert. Da main.py beim
normalen Boot automatisch M5.begin() mit Standard-Einstellungen
(Lautsprecher AN) aufruft, MUSS main.py vorher kurzzeitig deaktiviert
werden, sonst testen wir nur wieder den bereits "vergifteten" Zustand:

  1. Auf dem PC:
     mpremote connect <PORT> mv :/flash/main.py :/flash/main.py.disabled
  2. Gerät WIRKLICH stromlos machen (Netzteil/Akku-Kabel kurz trennen
     ODER den Power-Knopf lang genug halten für einen echten Aus-Zustand
     - NICHT nur die Reset-Taste, die macht meist nur einen Soft-Reset)
     und wieder einschalten.
  3. mpremote connect <PORT> run diagnose_mic_workaround.py
  4. Egal wie der Test ausgeht, main.py danach wieder zurückbenennen:
     mpremote connect <PORT> mv :/flash/main.py.disabled :/flash/main.py
     und das Gerät neu starten - sonst bleibt main.py deaktiviert!

UNVERIFIZIERT: die genaue MicroPython-Syntax für "internal_spk" beim
globalen M5.begin() - anders als bei Mic.config()/Speaker.config()
(bestätigt: Aufruf mit param=value) konnte ich keine bestätigte
MicroPython-spezifische Fundstelle für den GLOBALEN M5.config() finden,
nur die C++/Arduino-Variante (M5.config() liefert ein cfg-Objekt,
cfg.internal_spk = False, M5.begin(cfg)). Dieses Skript probiert deshalb
mehrere plausible Varianten der Reihe nach und bricht bei der ersten
sauber ab, die tatsächlich existiert (AttributeError/TypeError sind
normale Python-Fehler, KEIN Crash wie bei den Mic-Aufrufen selbst -
sollte hier also nichts abstürzen, nur ggf. "geht nicht" melden).
"""

import M5

print("Firmware/Board-Info:")
try:
    import os
    print(" ", os.uname())
except Exception as e:
    print("  os.uname() fehlgeschlagen:", e)
print()

print("dir(M5):", dir(M5))
print()

cfg = None

# Variante A: M5.config() liefert ein Objekt mit settable-Attributen
# (entspricht der C++/Arduino-API: auto cfg = M5.config(); cfg.internal_spk = false;)
if hasattr(M5, "config"):
    try:
        cfg = M5.config()
        print("Variante A: M5.config() =", cfg, " Typ:", type(cfg))
        if hasattr(cfg, "internal_spk"):
            print("  cfg.internal_spk (vorher) =", cfg.internal_spk)
            cfg.internal_spk = False
            print("  cfg.internal_spk (nachher) =", cfg.internal_spk)
        else:
            print("  cfg-Objekt hat kein 'internal_spk'-Attribut - Variante A funktioniert so nicht.")
            cfg = None
    except Exception as e:
        print("  M5.config() raised:", e)
        cfg = None
else:
    print("M5 hat kein 'config' - Variante A entfällt.")

print()

if cfg is not None:
    print("Rufe M5.begin(cfg) mit deaktiviertem internal_spk auf...")
    try:
        M5.begin(cfg)
        print("  M5.begin(cfg) erfolgreich.")
    except Exception as e:
        print("  M5.begin(cfg) raised:", e)
        cfg = None

if cfg is None:
    # Variante B: direkter Keyword-Parameter an M5.begin() selbst
    print("Versuche stattdessen Variante B: M5.begin(internal_spk=False) direkt...")
    try:
        M5.begin(internal_spk=False)
        print("  M5.begin(internal_spk=False) erfolgreich.")
        cfg = True  # nur als Merker, dass IRGENDEINE Variante geklappt hat
    except Exception as e:
        print("  M5.begin(internal_spk=False) raised:", e)
        print()
        print("KEINE der beiden Varianten hat funktioniert - bitte diesen")
        print("kompletten Log zurückmelden, dann probieren wir eine andere Syntax.")

if cfg is None:
    print("Abbruch - M5 konnte nicht mit deaktiviertem Lautsprecher gestartet werden.")
else:
    print()
    print("M5 erfolgreich mit (hoffentlich) deaktiviertem internal_spk gestartet.")
    print("Teste jetzt das Mikrofon - bitte reden/klatschen/pfeifen:")
    print("-" * 60)

    import time

    NUM_FRAMES = 512
    buf = bytearray(NUM_FRAMES * 4)  # Stereo, korrekt dimensioniert

    try:
        print("  M5.Mic.begin() =", M5.Mic.begin())
        time.sleep_ms(150)
        result = M5.Mic.record(buf, 16000, True)
        waited = 0
        while M5.Mic.isRecording() and waited < 1000:
            time.sleep_ms(5)
            waited += 5
        print("  M5.Mic.record() =", result, " (gewartet: %dms)" % waited)
    except Exception as e:
        print("  raised:", e)
    finally:
        try:
            M5.Mic.end()
        except Exception:
            pass

    left, right = [], []
    for j in range(NUM_FRAMES):
        for channel_list, offset in ((left, 0), (right, 2)):
            lo = buf[4 * j + offset]
            hi = buf[4 * j + offset + 1]
            v = (hi << 8) | lo
            if v >= 32768:
                v -= 65536
            channel_list.append(v)
    print("  Links:  min=%d max=%d" % (min(left), max(left)))
    print("  Rechts: min=%d max=%d" % (min(right), max(right)))
    print("  KOMPLETTER Puffer nur Nullbytes?", all(b == 0 for b in buf))

print("-" * 60)
print("Fertig. Bitte den KOMPLETTEN Log-Ausschnitt zurückmelden - und danach")
print("nicht vergessen, main.py wieder zurückzubenennen (siehe Docstring oben)!")
