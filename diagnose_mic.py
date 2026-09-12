"""
Eigenständiger Mikrofon-Diagnose-Test für die Tab5 - läuft UNABHÄNGIG vom
restlichen Tab5-MagicMirror-Projekt, nach dem Vorbild von mic_test.py aus
deinem Core2-Projekt.

Hintergrund: sensors/microphone.py meldet "M5.Mic.record() liefert
durchgehend Stille" - der Aufruf wirft KEINE Exception, liefert aber immer
(0,0,0,...). Genau dasselbe Muster wie beim bekannten M5.Imu.getAccel()-
Firmware-Bug (siehe sensors/accelerometer.py-Docstring). Dieses Skript
prüft das isoliert und ausführlicher:
  1. Welche Methoden/Attribute M5.Mic auf dieser Firmware tatsächlich hat
     (dir()) - falls sich seit dem letzten Firmware-Build etwas geändert
     hat oder Namen abweichen.
  2. Den Enabled-/Recording-Status VOR und NACH begin()/record().
  3. Die rohen Bytes aus dem Aufnahme-Puffer direkt (nicht erst als
     interpretierte Samples) - falls dort NICHT alles \\x00 ist, aber die
     Interpretation trotzdem 0 ergibt, wäre das ein Fehler in UNSEREM
     Code statt ein Firmware-Bug.
  4. Mehrere Sample-Raten/Puffer-Größen zur Sicherheit.

Benutzung:
    mpremote connect <PORT> run diagnose_mic.py

Rede/klatsche/pfeife während der Durchläufe in Richtung des Geräts.
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
print("dir(M5.Mic):")
print(" ", dir(M5.Mic))
print()

for probe in ("isEnabled", "isRecording", "config"):
    if hasattr(M5.Mic, probe):
        try:
            val = getattr(M5.Mic, probe)() if probe != "config" else M5.Mic.config()
            print("M5.Mic.%s() = %r" % (probe, val))
        except Exception as e:
            print("M5.Mic.%s() raised: %s" % (probe, e))
    else:
        print("M5.Mic hat kein '%s'" % probe)
print()

# Lautsprecher aus, bevor das Mikrofon startet (Mic/Speaker teilen sich
# laut M5Stack-Doku dieselbe Audio-Hardware auf der Tab5).
try:
    M5.Speaker.end()
except Exception as e:
    print("M5.Speaker.end() fehlgeschlagen (meist harmlos):", e)

for sample_rate in (16000, 44100):
    print("-" * 60)
    print("Teste mit sample_rate=%d" % sample_rate)
    num_samples = 512
    buf = bytearray(num_samples * 2)

    try:
        begin_ok = M5.Mic.begin()
        print("  M5.Mic.begin() =", begin_ok)
    except Exception as e:
        print("  M5.Mic.begin() raised:", e)
        continue

    try:
        print("  isEnabled() nach begin():", M5.Mic.isEnabled())
    except Exception:
        pass

    time.sleep_ms(150)  # etwas Anlaufzeit, falls der Dezimationsfilter das braucht

    try:
        result = M5.Mic.record(buf, sample_rate, True)
        waited = 0
        while M5.Mic.isRecording() and waited < 1000:
            time.sleep_ms(5)
            waited += 5
        print("  M5.Mic.record() =", result, " (gewartet: %dms)" % waited)
    except Exception as e:
        print("  M5.Mic.record() raised:", e)
        result = None

    try:
        M5.Mic.end()
    except Exception:
        pass

    # Rohe Bytes direkt ausgeben - falls hier NICHT alles 0 ist, obwohl
    # die interpretierten Samples 0 zeigen, liegt der Fehler bei uns,
    # nicht in der Firmware.
    raw_bytes = list(buf[0:32])
    all_zero_bytes = all(b == 0 for b in buf)
    print("  Erste 32 rohe Bytes:", raw_bytes)
    print("  KOMPLETTER Puffer nur Nullbytes?", all_zero_bytes)

    samples = []
    for j in range(num_samples):
        lo = buf[2 * j]
        hi = buf[2 * j + 1]
        v = (hi << 8) | lo
        if v >= 32768:
            v -= 65536
        samples.append(v)
    mn, mx = min(samples), max(samples)
    print("  Interpretierte Samples: min=%d max=%d" % (mn, mx))

    time.sleep_ms(300)

print("-" * 60)
print("Fertig. Bitte den KOMPLETTEN Log-Ausschnitt oben zurückmelden.")
