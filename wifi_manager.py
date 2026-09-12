"""
WLAN-Verwaltung für den Tab5. Ursprünglich 1:1 nach dem Vorbild von
wifi_manager.py aus dem T-Display-S3-Projekt (AP-Fallback beim Boot),
jetzt erweitert um Laufzeit-Steuerung fürs Settings-Screen:
  - status()          - aktueller Modus/SSID/IP/Signalstärke
  - connect_sta_async()- manuell auf eine andere Station umschalten, OHNE
                         die LVGL-Oberfläche währenddessen einzufrieren
  - start_ap()         - manuell in den Access-Point-Modus wechseln

WICHTIG: connect_or_ap() (Boot-Zeit) und connect_sta() (Sync-Variante)
blockieren bis zu STA_TIMEOUT_S Sekunden - das ist beim Boot unproblematisch
(noch keine UI aktiv), aber NICHT aus einem laufenden LVGL-Event-Callback
heraus verwenden, sonst friert der Touchscreen für bis zu 15s ein!
Für den Settings-Screen deshalb immer connect_sta_async() nutzen (wartet
über `await asyncio.sleep_ms()` statt blockierendem `time.sleep_ms()` -
lässt LVGLs Task-Handler in der Zwischenzeit weiterlaufen).
"""

import network
import time

AP_SSID = "Tab5-Setup"
AP_PASSWORD = "configure123"   # mind. 8 Zeichen für WPA2
STA_TIMEOUT_S = 15


class WifiManager:
    def __init__(self):
        self.sta = network.WLAN(network.STA_IF)
        self.ap = network.WLAN(network.AP_IF)
        self.mode = None  # "sta" | "ap" | None (noch nicht initialisiert)

    # ------------------------------------------------------------------
    # Boot-Zeit (main.py) - blockierend, aber da noch keine UI läuft unproblematisch
    # ------------------------------------------------------------------
    def connect_or_ap(self, cfg):
        """Gibt (mode, ip) zurück: ("sta", ip) oder ("ap", ip)."""
        wifi_cfg = cfg.get("wifi", {})
        ssid = wifi_cfg.get("ssid", "")
        password = wifi_cfg.get("password", "")

        if ssid and self.connect_sta(ssid, password):
            ip = self.sta.ifconfig()[0]
            print("WLAN (Station) verbunden, IP:", ip)
            return "sta", ip

        print("WLAN-Verbindung fehlgeschlagen oder keine Zugangsdaten - starte Access Point")
        self.start_ap()
        ip = self.ap.ifconfig()[0]
        print("Access Point aktiv: SSID='{}' Passwort='{}' IP={}".format(AP_SSID, AP_PASSWORD, ip))
        return "ap", ip

    def connect_sta(self, ssid, password, timeout_s=STA_TIMEOUT_S):
        """Blockierende Variante - NUR beim Boot verwenden (siehe Docstring oben)."""
        self.ap.active(False)
        self.sta.active(True)
        self.sta.connect(ssid, password)

        start = time.time()
        while not self.sta.isconnected() and (time.time() - start) < timeout_s:
            time.sleep_ms(200)

        if self.sta.isconnected():
            self.mode = "sta"
            return True
        self.sta.active(False)
        return False

    # ------------------------------------------------------------------
    # Laufzeit (Settings-Screen) - nicht-blockierend
    # ------------------------------------------------------------------
    async def connect_sta_async(self, ssid, password, timeout_s=STA_TIMEOUT_S):
        """Wie connect_sta(), wartet aber über await asyncio.sleep_ms()
        statt blockierendem time.sleep_ms() - friert die UI währenddessen
        nicht ein. Aus einem LVGL-Button-Callback heraus per
        asyncio.create_task(wifi_manager.connect_sta_async(...)) aufrufen."""
        import uasyncio as asyncio

        self.ap.active(False)
        self.sta.active(True)
        self.sta.connect(ssid, password)

        start = time.time()
        while not self.sta.isconnected() and (time.time() - start) < timeout_s:
            await asyncio.sleep_ms(200)

        if self.sta.isconnected():
            self.mode = "sta"
            return True
        self.sta.active(False)
        return False

    def start_ap(self):
        """Sofort, kein Warten nötig - sicher direkt aus einem LVGL-
        Button-Callback heraus aufrufbar."""
        self.sta.active(False)
        self.ap.active(True)
        self.ap.config(essid=AP_SSID, password=AP_PASSWORD, authmode=network.AUTH_WPA2_PSK)
        self.mode = "ap"

    def status(self):
        """Aktueller Stand für die Settings-UI. rssi ist nur im STA-Modus
        verfügbar und je nach MicroPython-Port/Firmware ggf. nicht
        implementiert - dann bleibt es None."""
        if self.mode == "sta" and self.sta.isconnected():
            rssi = None
            try:
                rssi = self.sta.status("rssi")
            except Exception:
                pass
            ssid = None
            try:
                ssid = self.sta.config("essid")
            except Exception:
                pass
            return {"mode": "sta", "ssid": ssid, "ip": self.sta.ifconfig()[0], "rssi": rssi}

        if self.mode == "ap":
            return {"mode": "ap", "ssid": AP_SSID, "ip": self.ap.ifconfig()[0], "rssi": None}

        return {"mode": "unknown", "ssid": None, "ip": None, "rssi": None}


# ---------------------------------------------------------------------------
# Modul-weites Singleton + Kompatibilitäts-Funktionen, damit main.py
# weiterhin einfach `wifi_manager.connect_or_ap(cfg)` aufrufen kann.
# ---------------------------------------------------------------------------
_default_manager = WifiManager()


def connect_or_ap(cfg):
    return _default_manager.connect_or_ap(cfg)


def status():
    return _default_manager.status()


def start_ap():
    return _default_manager.start_ap()


async def connect_sta_async(ssid, password, timeout_s=STA_TIMEOUT_S):
    return await _default_manager.connect_sta_async(ssid, password, timeout_s)
