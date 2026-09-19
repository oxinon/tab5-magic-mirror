"""
Diagnose: Warum braucht das WLAN ~9 s bis "verbunden"?  (eigenstaendig, aendert nichts am Projekt)

Misst die Verbindungszeit mehrfach
  A) normal (mit DHCP)
  B) mit FESTER IP (dieselbe Adresse, die DHCP vorher vergeben hat) - ohne DHCP-Verhandlung
und gibt Signalstaerke, Kanal und die Status-Uebergaenge mit Zeitstempeln aus.

Aufruf (vom Rechner, das laufende Programm wird dabei angehalten):
    mpremote connect /dev/ttyACM1 run diagnose_wlan.py

Danach das Geraet neu starten (Kabel raus/rein oder Strg-D in der REPL), damit wieder
main.py laeuft. Es werden KEINE Dateien veraendert.

Deutung:
  * B deutlich schneller als A   -> DHCP ist der Bremsklotz (dann: feste IP oder DHCP-Reservierung
                                    im Router; bei fester IP auf dem Tab5 muss die Adresse frei sein).
  * A und B beide ~9 s            -> Suche/Anmeldung am Router bzw. Chip (dann hilft nur ein anderer
                                    Router-Kanal/-Modus, z.B. WPA2 statt WPA3, oder feste Kanalwahl).
  * sehr schlechtes RSSI (< -75)  -> Signal zu schwach.
"""

import time

import network

try:
    import ujson as json
except ImportError:
    import json

STATUS_NAMEN = {
    1000: "IDLE", 1001: "CONNECTING", 1010: "GOT_IP",
    200: "BEACON_TIMEOUT", 201: "NO_AP_FOUND", 202: "WRONG_PASSWORD",
    203: "ASSOC_FAIL", 204: "HANDSHAKE_TIMEOUT",
}
TIMEOUT_MS = 30000

with open("/flash/config.json") as f:
    wifi_cfg = json.load(f).get("wifi", {})
SSID = wifi_cfg.get("ssid", "")
PASSWORT = wifi_cfg.get("password", "")
if not SSID:
    raise SystemExit("Keine SSID in /flash/config.json")

sta = network.WLAN(network.STA_IF)


def zuruecksetzen():
    try:
        sta.disconnect()
    except Exception:
        pass
    sta.active(False)
    time.sleep_ms(800)
    sta.active(True)
    time.sleep_ms(500)


def messen(label, feste_ip=None):
    zuruecksetzen()
    if feste_ip:
        sta.ifconfig(feste_ip)   # stoppt den DHCP-Client
    t0 = time.ticks_ms()
    sta.connect(SSID, PASSWORT)
    letzter = None
    while time.ticks_diff(time.ticks_ms(), t0) < TIMEOUT_MS:
        st = sta.status()
        if st != letzter:
            print("    %5d ms  status=%s (%s)" % (time.ticks_diff(time.ticks_ms(), t0), st,
                                                   STATUS_NAMEN.get(st, "?")))
            letzter = st
        if sta.isconnected():
            break
        time.sleep_ms(50)
    dauer = time.ticks_diff(time.ticks_ms(), t0)
    ok = sta.isconnected()
    print("%s: %s nach %d ms" % (label, "VERBUNDEN" if ok else "NICHT verbunden", dauer))
    if ok:
        info = sta.ifconfig()
        print("    ifconfig:", info)
        try:
            print("    RSSI: %s dBm, Kanal: %s" % (sta.status("rssi"), sta.config("channel")))
        except Exception as e:
            print("    (RSSI/Kanal nicht lesbar: %r)" % (e,))
        return dauer, info
    return dauer, None


print("=== WLAN-Diagnose fuer '%s' ===" % SSID)
ergebnisse = {"A (DHCP)": [], "B (feste IP)": []}
feste = None
for lauf in range(2):
    print("\n-- Lauf %d --" % (lauf + 1))
    dauer, info = messen("A (DHCP)")
    ergebnisse["A (DHCP)"].append(dauer)
    if info:
        feste = info
    if feste:
        dauer_b, _ = messen("B (feste IP %s)" % feste[0], feste)
        ergebnisse["B (feste IP)"].append(dauer_b)

print("\n=== Ergebnis (ms) ===")
for name, werte in ergebnisse.items():
    print("  %-14s %s" % (name, werte))
print("\nFertig. Geraet jetzt neu starten (Strg-D oder Kabel).")
