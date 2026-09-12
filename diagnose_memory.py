"""
Kurzer Speicher-Check am laufenden Haupt-Programm - NICHT eigenstaendig,
sondern via mpremote exec waehrend main.py bereits laeuft, damit wir den
TATSAECHLICHEN Verbrauch im normalen Betrieb sehen, nicht nur bei einem
frischen Boot ohne Dashboard/Web-UI/Tasks.

Benutzung (main.py muss bereits laufen):
    mpremote connect <PORT> resume exec "exec(open('diagnose_memory.py').read())"
"""
import gc
import esp32

gc.collect()
free = gc.mem_free()
alloc = gc.mem_alloc()
total = free + alloc
print("RAM (MicroPython-Heap):")
print("  belegt: %d KB" % (alloc // 1024))
print("  frei:   %d KB" % (free // 1024))
print("  gesamt: %d KB (%.1f%% frei)" % (total // 1024, 100.0 * free / total))

try:
    print()
    print("PSRAM:")
    print("  ", esp32.idf_heap_info(esp32.HEAP_DATA))
except Exception as e:
    print("  esp32.idf_heap_info() nicht verfügbar:", e)
