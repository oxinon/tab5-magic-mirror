"""
Dünner HTTP-Wrapper für die bestehende Magic-Mirror-Flask-API
(Port 5031, offenes CORS, kein Auth - siehe Handoff-Dokument Abschnitt 5).

WICHTIG: urequests unterstützt keinen timeout-Parameter direkt - ohne
Gegenmaßnahme hängt ein nicht erreichbarer Server den Aufruf UNBEGRENZT,
was auf der Tab5 die komplette asyncio-Schleife blockiert (inkl. LVGL/
Display-Aktualisierung - genau das hat den Bildschirm "ausgehen" lassen,
als kein Magic-Mirror-Server erreichbar war). Deshalb globaler Socket-
Timeout als Sicherheitsnetz: nach REQUEST_TIMEOUT_S schlägt JEDE
Netzwerkoperation mit einer catchbaren Exception fehl, statt ewig zu warten.
"""

try:
    import usocket as _socket
except ImportError:
    try:
        import socket as _socket
    except ImportError:
        _socket = None

REQUEST_TIMEOUT_S = 5

if _socket is not None:
    try:
        _socket.setdefaulttimeout(REQUEST_TIMEOUT_S)
    except Exception:
        pass  # z.B. auf dem Desktop-Fallback nicht in jeder Python-Version identisch

try:
    import urequests as requests
except ImportError:
    import requests  # Desktop-Fallback für Tests ohne MicroPython

try:
    import time
except ImportError:
    time = None


class ApiClient:
    # Nach einem Fehlschlag so lange NICHT erneut versuchen, sondern sofort
    # fehlschlagen - verhindert, dass mehrere Widgets im selben Refresh-
    # Zyklus JEWEILS die vollen REQUEST_TIMEOUT_S abwarten, wenn der Server
    # ohnehin gerade nicht erreichbar ist (z.B. Magic-Mirror-Server aus,
    # falsche IP, oder - wie beobachtet - sogar im ECHTEN Heimnetz einfach
    # nicht erreichbar). Betrifft ALLE Aufrufer dieses Clients gemeinsam
    # (z.B. alle Magic-Mirror-Widgets + der Remote-Fallback des Luft-
    # sensors teilen sich eine ApiClient-Instanz, siehe main.py) - das erste
    # fehlgeschlagene Widget in einem Zyklus "bezahlt" den Timeout, alle
    # anderen im selben Zyklus schlagen danach sofort fehl statt jeweils
    # erneut 5s zu warten.
    FAIL_BACKOFF_S = 60

    def __init__(self, base_url):
        self.base_url = base_url.rstrip("/")
        # Wenn False (siehe main.py - im Access-Point-/Setup-Modus ohne
        # Heimnetz gesetzt): jeder Aufruf schlägt SOFORT fehl statt erst
        # nach REQUEST_TIMEOUT_S. Der 5s-Timeout ist als Sicherheitsnetz
        # gegen einen zeitweise nicht erreichbaren Server gedacht, nicht
        # gegen einen Modus, in dem von vornherein GAR KEIN Netzwerkpfad
        # existiert.
        self.network_available = True
        self._retry_after = 0  # time.time()-Wert; davor sofort fehlschlagen

    def _check_backoff(self):
        if not self.network_available:
            raise OSError("Kein Netzwerk verfügbar (Access-Point-/Setup-Modus)")
        if time is not None and time.time() < self._retry_after:
            raise OSError("Server kürzlich nicht erreichbar (Backoff aktiv)")

    def _mark_result(self, ok):
        if time is None:
            return
        self._retry_after = 0 if ok else (time.time() + self.FAIL_BACKOFF_S)

    def get_json(self, path):
        self._check_backoff()
        r = None
        try:
            r = requests.get(self.base_url + path)
            data = r.json()
            self._mark_result(True)
            return data
        except Exception:
            self._mark_result(False)
            raise
        finally:
            if r is not None:
                try:
                    r.close()
                except Exception:
                    pass

    def post_json(self, path, payload):
        self._check_backoff()
        r = None
        try:
            r = requests.post(self.base_url + path, json=payload)
            data = r.json()
            self._mark_result(True)
            return data
        except Exception:
            self._mark_result(False)
            raise
        finally:
            if r is not None:
                try:
                    r.close()
                except Exception:
                    pass
