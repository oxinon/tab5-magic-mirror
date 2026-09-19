"""
Diagnose (Teil 2): Warum dauert die ERSTE WLAN-Verbindung nach dem Start ~9 s?

Ergebnis von diagnose_wlan.py: Lauf 1 (frisch nach dem Start) brauchte 8,9 s - dabei meldete der
Chip nach 5,2 s kurz "202 WRONG_PASSWORD" und verband dann von selbst; alle weiteren Laeufe
(nach active(False)/active(True)) nur 2,9 s, egal ob DHCP oder feste IP. DHCP ist es also
nicht - der erste Verbindungsversuch nach dem Start scheitert offenbar.

Dieses Skript macht GENAU EINEN Verbindungsversuch direkt nach dem Start, mit waehlbarer
Vorbereitung ("Strategie"), und misst die Zeit:

  sofort   : active(True) und gleich connect()          (so macht es main.py jetzt)
  warten3  : active(True), 3 s warten, dann connect()    (Chip/Link "warmlaufen" lassen)
  scan     : active(True), einmal scannen, dann connect()

Strategie wahlen (Datei auf dem Geraet, bleibt bis zur naechsten Aenderung):
    mpremote connect /dev/ttyACM1 exec "open('/flash/wlan_strategie.txt','w').write('warten3')"
Dann messen (mind. 2x je Strategie, jedes Mal ist das ein frischer Start des Skripts):
    mpremote connect /dev/ttyACM1 run diagnose_wlan_erststart.py
Danach das Geraet neu starten. Es wird nur /flash/wlan_strategie.txt angelegt.
Aufraeumen: mpremote connect /dev/ttyACM1 exec "import os; os.remove('/flash/wlan_strategie.txt')"

Vergleich: Bei welcher Strategie kommt KEIN 202 und die Zeit liegt bei ~3 s?
"""

import time

import network

try:
    import ujson as json
except ImportError:
    import json

NAMEN = {1000: "IDLE", 1001: "CONNECTING", 1010: "GOT_IP", 200: "BEACON_TIMEOUT", 201: "NO_AP_FOUND",
         202: "WRONG_PASSWORD", 203: "ASSOC_FAIL", 204: "HANDSHAKE_TIMEOUT"}

try:
    with open("/flash/wlan_strategie.txt") as f:
        STRATEGIE = f.read().strip()
except OSError:
    STRATEGIE = "sofort"
if STRATEGIE not in ("sofort", "warten3", "scan"):
    raise SystemExit("Unbekannte Strategie: %r" % STRATEGIE)

with open("/flash/config.json") as f:
    w = json.load(f).get("wifi", {})
SSID, PW = w.get("ssid", ""), w.get("password", "")

sta = network.WLAN(network.STA_IF)
try:
    sta.disconnect()          # sauberer Ausgangszustand (Router vergisst die alte Sitzung)
except Exception:
    pass
sta.active(False)
time.sleep_ms(1500)

print("=== Erststart-Test, Strategie: %s ===" % STRATEGIE)
t_start = time.ticks_ms()
sta.active(True)
t_active = time.ticks_ms()
if STRATEGIE == "warten3":
    time.sleep_ms(3000)
elif STRATEGIE == "scan":
    try:
        gefunden = sta.scan()
        print("    Scan: %d Netze, %d ms" % (len(gefunden), time.ticks_diff(time.ticks_ms(), t_active)))
    except Exception as e:
        print("    Scan fehlgeschlagen: %r" % (e,))
t_connect = time.ticks_ms()
sta.connect(SSID, PW)

letzter = None
gesehen_202 = False
while time.ticks_diff(time.ticks_ms(), t_connect) < 30000:
    st = sta.status()
    if st != letzter:
        print("    %5d ms nach connect()  status=%s (%s)" % (time.ticks_diff(time.ticks_ms(), t_connect), st, NAMEN.get(st, "?")))
        letzter = st
        if st == 202:
            gesehen_202 = True
    if sta.isconnected():
        break
    time.sleep_ms(50)

dauer = time.ticks_diff(time.ticks_ms(), t_connect)
print("\nErgebnis: %s | %s nach %d ms (ab connect()) | Gesamt inkl. Vorbereitung %d ms | 202 gesehen: %s" % (
    STRATEGIE, "VERBUNDEN" if sta.isconnected() else "NICHT verbunden", dauer,
    time.ticks_diff(time.ticks_ms(), t_start), gesehen_202))
try:
    print("RSSI: %s dBm, Kanal: %s" % (sta.status("rssi"), sta.config("channel")))
except Exception:
    pass
print("Fertig - Geraet jetzt neu starten (Strg-D oder Kabel).")
