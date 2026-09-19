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
STA_TIMEOUT_S = 15
# Frueher fest "configure123" - oeffentlich im Quelltext, jeder in Funkreichweite
# konnte sich mit dem Setup-Access-Point verbinden (und dort das Web-UI nutzen).
# Jetzt pro Geraet zufaellig, einmalig erzeugt und in config.json gespeichert
# (wifi.ap_password); angezeigt wird es im Burger-Menue auf dem Tab5-Display.
AP_PASSWORD_LENGTH = 12
# ohne leicht verwechselbare Zeichen (0/o, 1/l/i)
_AP_PASSWORD_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"


class WifiManager:
    def __init__(self):
        self.sta = network.WLAN(network.STA_IF)
        self.ap = network.WLAN(network.AP_IF)
        self.mode = None  # "sta" | "ap" | None (noch nicht initialisiert)
        self._ap_password = None
        self._early = None  # (ssid, password, start_ms) - siehe early_connect()

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
        # Passwort bewusst NICHT ins Log (steht im Burger-Menue auf dem Display).
        print("Access Point aktiv: SSID='{}' IP={} (Passwort: siehe Tab5-Display)".format(AP_SSID, ip))
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
    def early_connect(self, ssid, password):
        """Verbindungsaufbau SOFORT anstossen und NICHT darauf warten. main.py ruft das
        ganz am Anfang auf, VOR den ~8s dauernden Modul-Imports: Der WLAN-Chip (ESP32-C6
        ueber esp_hosted) baut die Verbindung selbststaendig auf, waehrend der Prozessor
        noch Python-Dateien uebersetzt. Vorher startete der Aufbau erst NACH den Imports
        (Boot-Log: ~9,5s Wartezeit bis zum Web-UI). connect_sta_async() erkennt einen
        laufenden Fruehstart und wartet nur noch auf dessen Ergebnis."""
        if not ssid:
            return False
        try:
            # Diagnose: in welchem Zustand ist der WLAN-Chip beim Start? (Der ESP32-C6 ist ein
            # eigener Prozessor und kann einen Neustart des Hauptprozessors "ueberleben" -
            # dann waere er evtl. schon mit dem Router verbunden.)
            try:
                vorher = "active=%s verbunden=%s status=%s" % (
                    self.sta.active(), self.sta.isconnected(), self.sta.status())
            except Exception as e:
                vorher = "nicht lesbar (%r)" % (e,)
            t0 = time.ticks_ms()
            self.sta.active(True)
            t1 = time.ticks_ms()
            try:
                nachher = "verbunden=%s status=%s" % (self.sta.isconnected(), self.sta.status())
                bereits_verbunden = self.sta.isconnected() and self.sta.config("essid") == ssid
            except Exception:
                nachher, bereits_verbunden = "nicht lesbar", False
            if bereits_verbunden:
                # Der Chip ist schon mit dem richtigen Netz verbunden: NICHT erneut connect()
                # rufen (das trennt zuerst und wuerde neu beginnen).
                self._early = (ssid, password, t1)
                print("WLAN-Fruehstart: Chip ist bereits verbunden (%s) - kein connect() noetig" % nachher)
                return True
            self.sta.connect(ssid, password)
            t2 = time.ticks_ms()
            self._early = (ssid, password, t2)
            # Zeitmessung fuer die Boot-Analyse: wo bleibt die Zeit (Chip-Start vs. connect)?
            print("WLAN-Fruehstart: vorher [%s] | active(True) %d ms -> [%s] | connect() %d ms" % (
                vorher, time.ticks_diff(t1, t0), nachher, time.ticks_diff(t2, t1)))
            return True
        except Exception as e:
            self._early = None
            print("WLAN-Fruehstart fehlgeschlagen:", e)
            return False

    async def connect_sta_async(self, ssid, password, timeout_s=STA_TIMEOUT_S):
        """Wie connect_sta(), wartet aber über await asyncio.sleep_ms()
        statt blockierendem time.sleep_ms() - friert die UI währenddessen
        nicht ein. Aus einem LVGL-Button-Callback heraus per
        asyncio.create_task(wifi_manager.connect_sta_async(...)) aufrufen."""
        import uasyncio as asyncio

        t_begin = time.ticks_ms()
        early, self._early = self._early, None   # ein Fruehstart gilt nur fuer den ERSTEN Aufruf
        if early is not None and early[0] == ssid and early[1] == password:
            # Verbindung laeuft bereits (siehe early_connect) - NICHT erneut connect() rufen
            # (das trennt zuerst und beginnt von vorn). Das Zeitbudget rechnet ab dem
            # Fruehstart, mindestens aber 8s ab jetzt.
            elapsed_s = time.ticks_diff(t_begin, early[2]) / 1000
            timeout_s = max(8, timeout_s - elapsed_s)
            used_early = True
        else:
            self.ap.active(False)
            self.sta.active(True)
            self.sta.connect(ssid, password)
            used_early = False

        start = time.time()
        t_ref = early[2] if used_early else t_begin
        last_status = None
        while not self.sta.isconnected() and (time.time() - start) < timeout_s:
            # Status-Uebergaenge mitloggen (Diagnose: beim ersten Verbinden nach dem Start
            # meldet der Chip gelegentlich kurz 202 WRONG_PASSWORD und verbindet dann von
            # selbst nach ~9s - siehe diagnose_wlan.py). Nur bei Aenderung ein Print.
            try:
                status = self.sta.status()
            except Exception:
                status = None
            if status != last_status:
                print("WLAN: status=%s nach %d ms" % (status, time.ticks_diff(time.ticks_ms(), t_ref)))
                last_status = status
            await asyncio.sleep_ms(200)

        if self.sta.isconnected():
            self.mode = "sta"
            since_early = (" (%d ms seit Fruehstart)" % time.ticks_diff(time.ticks_ms(), early[2])) if used_early else ""
            print("WLAN: verbunden nach %d ms Wartezeit%s" % (time.ticks_diff(time.ticks_ms(), t_begin), since_early))
            return True
        self.sta.active(False)
        return False

    async def connect_or_ap_async(self, cfg):
        """Wie connect_or_ap(), aber NICHT blockierend (wartet per await): der Aufbau
        dauert 5-10s (esp_hosted/DHCP) - beim Boot war die Oberflaeche in dieser Zeit
        eingefroren (kein Touch, keine Uhr), obwohl das Dashboard schon sichtbar war.
        Gibt (mode, ip) zurueck: ("sta", ip) oder ("ap", ip)."""
        wifi_cfg = cfg.get("wifi", {})
        ssid = wifi_cfg.get("ssid", "")
        password = wifi_cfg.get("password", "")
        if ssid and await self.connect_sta_async(ssid, password):
            ip = self.sta.ifconfig()[0]
            print("WLAN (Station) verbunden, IP:", ip)
            return "sta", ip
        print("WLAN-Verbindung fehlgeschlagen oder keine Zugangsdaten - starte Access Point")
        self.start_ap()
        ip = self.ap.ifconfig()[0]
        print("Access Point aktiv: SSID='{}' IP={} (Passwort: siehe Tab5-Display)".format(AP_SSID, ip))
        return "ap", ip

    async def try_sta_async(self, ssid, password, timeout_s=STA_TIMEOUT_S):
        """Wie connect_sta_async(), stellt bei Misserfolg aber den Access
        Point wieder her. connect_sta_async() schaltet den AP VOR dem
        Verbindungsversuch ab und laesst das Geraet bei einem Fehlschlag
        (Tippfehler, Router weg) ohne JEDE Verbindung zurueck - dann waere es
        weder im Heimnetz noch ueber den AP erreichbar."""
        ok = await self.connect_sta_async(ssid, password, timeout_s)
        if not ok:
            self.start_ap()
        return ok

    async def reconnect_sta_async(self, ssid, password, timeout_s=STA_TIMEOUT_S):
        """Verbindung im STA-Modus erneut aufbauen (z.B. nach einem
        laengeren Router-Ausfall) - OHNE auf den Access Point umzuschalten
        (der hat ein bekanntes Standardpasswort und soll nicht bei jedem
        Router-Neustart auftauchen). self.mode bleibt "sta"."""
        import uasyncio as asyncio
        self.sta.active(True)
        try:
            self.sta.disconnect()
        except Exception:
            pass
        self.sta.connect(ssid, password)
        start = time.time()
        while not self.sta.isconnected() and (time.time() - start) < timeout_s:
            await asyncio.sleep_ms(200)
        return self.sta.isconnected()

    def disconnect_cleanly(self):
        """Vor einem geplanten Neustart die WLAN-Verbindung SAUBER beenden (der Router
        bekommt ein Abmelde-Signal). Ohne das haelt er die alte Sitzung dieser MAC-Adresse
        noch eine Weile fuer gueltig, und der erste Anmeldeversuch nach dem Neustart
        stockt ~5 s, scheitert (Status 202) und klappt erst beim zweiten Versuch
        (Boot-Log: immer ~9 s bis "verbunden" statt ~3 s)."""
        try:
            if self.sta.active() and self.sta.isconnected():
                self.sta.disconnect()
                time.sleep_ms(400)   # dem Chip Zeit geben, das Abmelde-Paket auch zu senden
                return True
        except Exception as e:
            print("WLAN: sauberes Trennen fehlgeschlagen:", e)
        return False

    def sta_connected(self):
        try:
            return bool(self.sta.isconnected())
        except Exception:
            return False

    def ap_has_clients(self):
        """True, wenn gerade ein Geraet am eigenen Access Point haengt (dann
        darf der Retry-Mechanismus den AP nicht abschalten). Auf Firmwares
        ohne status('stations') -> False."""
        if self.mode != "ap":
            return False
        try:
            return bool(self.ap.status("stations"))
        except Exception:
            return False

    def start_ap(self):
        """Sofort, kein Warten nötig - sicher direkt aus einem LVGL-
        Button-Callback heraus aufrufbar."""
        self.sta.active(False)
        self.ap.active(True)
        self.ap.config(essid=AP_SSID, password=self.ap_password(), authmode=network.AUTH_WPA2_PSK)
        self.mode = "ap"

    def ap_password(self):
        """Access-Point-Passwort dieses Geraets (zufaellig, bleibt gleich). Wird
        beim ersten Bedarf erzeugt und in config.json gespeichert."""
        if self._ap_password:
            return self._ap_password
        import config
        cfg = config.load()
        wifi_cfg = cfg.setdefault("wifi", {})
        pw = wifi_cfg.get("ap_password") or ""
        if len(pw) < 8:
            import os
            raw = os.urandom(AP_PASSWORD_LENGTH)
            pw = "".join(_AP_PASSWORD_ALPHABET[b % len(_AP_PASSWORD_ALPHABET)] for b in raw)
            wifi_cfg["ap_password"] = pw
            config.save(cfg)
        self._ap_password = pw
        return pw

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


def disconnect_cleanly():
    return _default_manager.disconnect_cleanly()


def early_connect(ssid, password):
    return _default_manager.early_connect(ssid, password)


async def connect_or_ap_async(cfg):
    return await _default_manager.connect_or_ap_async(cfg)


def ap_password():
    return _default_manager.ap_password()


def mode():
    """Zuletzt eingestellter Modus ("sta" | "ap" | None) - anders als
    status()["mode"] unabhaengig davon, ob die Verbindung GERADE steht."""
    return _default_manager.mode


def sta_connected():
    return _default_manager.sta_connected()


def ap_has_clients():
    return _default_manager.ap_has_clients()


async def try_sta_async(ssid, password, timeout_s=STA_TIMEOUT_S):
    return await _default_manager.try_sta_async(ssid, password, timeout_s)


async def reconnect_sta_async(ssid, password, timeout_s=STA_TIMEOUT_S):
    return await _default_manager.reconnect_sta_async(ssid, password, timeout_s)


def start_ap():
    return _default_manager.start_ap()


async def connect_sta_async(ssid, password, timeout_s=STA_TIMEOUT_S):
    return await _default_manager.connect_sta_async(ssid, password, timeout_s)
