"""
Eigenständiger Mikrofon-Diagnose-Test für die Tab5 - läuft UNABHÄNGIG vom
restlichen Tab5-MagicMirror-Projekt.

VERSION 3 - nach ZWEI harten Abstürzen überarbeitet:
  - V1: M5.Mic.isRecording() VOR begin() -> Absturz.
  - V2: M5.Mic.record(buf, rate, stereo=False) (Mono) -> Absturz, GENAU
    beim record()-Aufruf selbst (begin()/isEnabled() liefen davor noch
    sauber durch). Mono-Aufnahme scheint auf dieser Firmware also
    grundsätzlich instabil zu sein, unabhängig von der Sample-Rate.

Da sensors/microphone.py bereits SEIT MONATEN mit stereo=True läuft, OHNE
je abzustürzen (nur eben mit dem "liefert durchgehend Stille"-Symptom),
ist stereo=True der nachweislich SICHERE Pfad. Dieses Skript testet
deshalb NUR NOCH stereo=True, mit unterschiedlichen Sample-Raten UND -
das ist der eigentliche verbleibende Verdächtige - mit einem KORREKT für
Stereo dimensionierten Puffer (4 Bytes/Sample-Paar), denn
sensors/microphone.py übergibt bislang nur einen für MONO dimensionierten
Puffer (2 Bytes/Sample) an einen Stereo-Aufruf - eine Diskrepanz, die
genau zum "liefert durchgehend Stille"-Symptom passen würde (die Firmware
schreibt evtl. nur in den vorhandenen, zu kleinen Speicherbereich, was
bei Stereo-Interleaving zufällig nur Nullen/Stille ergeben könnte).

Mono wird hier NICHT mehr getestet (siehe oben - reproduzierbar
abgestürzt), auch nicht mit anderen Raten - das Risiko eines dritten
Absturzes für eine Variante, die ohnehin nicht produktiv genutzt wird,
lohnt sich nicht.

Benutzung:
    mpremote connect <PORT> run diagnose_mic.py

Rede/klatsche/pfeife während der Durchläufe in Richtung des Mikrofon-
Arrays auf der Vorderseite. Falls es TROTZDEM nochmal abstürzt: Gerät
startet sich selbst neu (siehe die letzten beiden Male), einfach den Log
wieder zurückschicken.
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

# Lautsprecher aus, bevor das Mikrofon startet (Mic/Speaker teilen sich
# laut M5Stack-Doku dieselbe Audio-Hardware auf der Tab5).
try:
    M5.Speaker.end()
except Exception as e:
    print("M5.Speaker.end() fehlgeschlagen (meist harmlos):", e)

NUM_FRAMES = 512  # 1 Frame = 1 Sample-PAAR (Links+Rechts) bei Stereo

# NUR NOCH Stereo (siehe Docstring - Mono crasht reproduzierbar), mit
# unterschiedlichen Sample-Raten. Puffer ist hier IMMER korrekt für
# Stereo dimensioniert (4 Bytes/Frame) - das ist der Unterschied zu
# sensors/microphone.py, wo der Puffer nur 2 Bytes/Frame groß ist.
SAMPLE_RATES = [8000, 16000, 22050]

for sample_rate in SAMPLE_RATES:
    print("-" * 60)
    print("Teste mit sample_rate=%d, stereo=True, KORREKT dimensioniertem Puffer" % sample_rate)

    buf = bytearray(NUM_FRAMES * 4)  # 4 Bytes/Frame = 2 Kanäle x 2 Bytes

    try:
        begin_ok = M5.Mic.begin()
        print("  M5.Mic.begin() =", begin_ok)
    except Exception as e:
        print("  M5.Mic.begin() raised:", e)
        continue

    try:
        print("  isEnabled() nach begin():", M5.Mic.isEnabled())
    except Exception as e:
        print("  isEnabled() nach begin() raised:", e)

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
    except Exception as e:
        print("  M5.Mic.end() raised:", e)

    raw_bytes = list(buf[0:32])
    all_zero_bytes = all(b == 0 for b in buf)
    print("  Erste 32 rohe Bytes:", raw_bytes)
    print("  KOMPLETTER Puffer nur Nullbytes?", all_zero_bytes)

    # Interleaved L/R auseinandersortieren (LRLRLR...).
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

    time.sleep_ms(300)

print("-" * 60)
print("Zum Vergleich: jetzt EXAKT wie der bisherige Projekt-Code (Puffer")
print("nur 2 Bytes/Frame groß, obwohl stereo=True angefordert wird) -")
print("bei sample_rate=16000, um das reale 'liefert Stille'-Symptom 1:1")
print("nachzustellen und die rohen Bytes dabei direkt zu sehen.")
print("-" * 60)

undersized_buf = bytearray(NUM_FRAMES * 2)  # bewusst zu klein für stereo=True
try:
    print("  M5.Mic.begin() =", M5.Mic.begin())
    time.sleep_ms(150)
    result = M5.Mic.record(undersized_buf, 16000, True)
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

print("  Erste 32 rohe Bytes (zu kleiner Puffer):", list(undersized_buf[0:32]))
print("  KOMPLETTER (zu kleiner) Puffer nur Nullbytes?", all(b == 0 for b in undersized_buf))

print("-" * 60)
print("Fertig. Bitte den KOMPLETTEN Log-Ausschnitt oben zurückmelden.")
