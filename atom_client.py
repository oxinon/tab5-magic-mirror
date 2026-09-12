"""
Client für das gepairte M5Stack-Atom-2-Relais-Board (Licht/Steckdose) -
siehe vom Nutzer bereitgestelltes Referenzprojekt (main.py/webserver.py/
sync_client.py/wifi_manager.py). Nutzt dieselbe schlanke HTTP-API, die der
Atom für sich selbst und fürs Pairing zwischen zwei Atoms schon mitbringt:

  GET  /api/status         -> {"relay1_state": bool, "relay2_state": bool, ...}
  POST /api/toggle/<1|2>   -> schaltet das jeweilige Relais um, gibt den
                              neuen Status zurück

WICHTIG - das Tab5 ist selbst der "Partner" im Sinne der Atom-Programm-
logik (siehe sync_client.py::notify_partner): Der Atom schickt bei JEDER
lokalen Zustandsänderung (Taster-Klick ODER eigenes Web-UI) automatisch
`GET /api/sync?relay=<1|2>&state=<0|1>` an die in SEINER EIGENEN
config.json hinterlegte "partner_ip" - dafür muss dort die IP-Adresse
DES TAB5 eingetragen werden (nicht umgekehrt!). Damit dieser Aufruf beim
Tab5 tatsächlich ankommt, implementiert web_server.py eine passende
`/api/sync`-Route (siehe dort), die den Zustand nur ANZEIGT, statt
selbst wieder toggle() aufzurufen - sonst würden sich beide Geräte
gegenseitig endlos hin- und herschalten.

Gleiches Backoff-Prinzip wie api_client.py/ha_client.py: nach einem
Fehlschlag 60s lang sofort fehlschlagen statt jedes Mal erneut auf einen
Timeout zu warten.
"""

try:
    import urequests as requests
except ImportError:
    import requests  # Desktop-Fallback für Tests ohne MicroPython

try:
    import time
except ImportError:
    time = None


class AtomClient:
    FAIL_BACKOFF_S = 60

    def __init__(self, base_url):
        self.base_url = base_url.rstrip("/")
        # Ob überhaupt ein Atom-Board konfiguriert ist (siehe config.py
        # "atom.enabled", von main.py gesetzt) - genau dasselbe Prinzip
        # wie ha_client.py::HomeAssistantClient.enabled.
        self.enabled = True
        # Siehe api_client.py::ApiClient.network_available - im AP-/Setup-
        # Modus ohne Heimnetz gibt es ohnehin keinen Pfad zum Atom.
        self.network_available = True
        self._retry_after = 0  # time.time()-Wert; davor sofort fehlschlagen

    def _check_backoff(self):
        if not self.enabled:
            return "Atom-Relais-Board nicht konfiguriert/aktiviert (siehe config.json)"
        if not self.network_available:
            return "Kein Netzwerk verfügbar (Access-Point-/Setup-Modus)"
        if time is not None and time.time() < self._retry_after:
            return "Atom-Board kürzlich nicht erreichbar (Backoff aktiv)"
        return None

    def _mark_result(self, ok):
        if time is None:
            return
        self._retry_after = 0 if ok else (time.time() + self.FAIL_BACKOFF_S)

    def get_status(self):
        """Liefert u.a. {"relay1_state": bool, "relay2_state": bool, "ok": True}
        oder {"ok": False, "msg": ...} bei Fehlschlag."""
        skip_reason = self._check_backoff()
        if skip_reason:
            return {"ok": False, "msg": skip_reason}
        r = None
        try:
            r = requests.get(self.base_url + "/api/status", timeout=5)
            data = r.json()
            data["ok"] = True
            self._mark_result(True)
            return data
        except Exception as e:
            self._mark_result(False)
            return {"ok": False, "msg": str(e)}
        finally:
            if r is not None:
                try:
                    r.close()
                except Exception:
                    pass

    def toggle(self, relay_id):
        """relay_id: 1 oder 2. Schaltet um (nicht: setzt auf einen
        bestimmten Zustand - genau wie der physische Taster am Atom selbst,
        siehe main.py::RelayController.toggle() im Referenzprojekt)."""
        skip_reason = self._check_backoff()
        if skip_reason:
            return {"ok": False, "msg": skip_reason}
        r = None
        try:
            r = requests.post("%s/api/toggle/%d" % (self.base_url, relay_id), timeout=5)
            data = r.json()
            data["ok"] = True
            self._mark_result(True)
            return data
        except Exception as e:
            self._mark_result(False)
            return {"ok": False, "msg": str(e)}
        finally:
            if r is not None:
                try:
                    r.close()
                except Exception:
                    pass
