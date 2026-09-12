"""
Minimaler Home-Assistant-REST-Client für den Room-Dashboard-Screen.

Vorbereitung, solange die Tab5 noch nicht da ist: die Struktur steht,
welche Entitäten angezeigt werden sollen wird über config.json
("home_assistant.entities") festgelegt, sodass später ohne Codeänderung
neue Entitäten ergänzt werden können.

Home Assistant benötigt einen Long-Lived Access Token
(Profil -> Sicherheit -> Long-Lived Access Tokens in HA erzeugen) -
NICHT hart codieren, sondern in config.json ablegen (siehe config.py).
"""

try:
    import usocket as _socket
except ImportError:
    try:
        import socket as _socket
    except ImportError:
        _socket = None

if _socket is not None:
    try:
        _socket.setdefaulttimeout(5)  # siehe api_client.py für Begründung
    except Exception:
        pass

try:
    import urequests as requests
except ImportError:
    import requests  # Desktop-Fallback für Tests ohne MicroPython

try:
    import time
except ImportError:
    time = None


class HomeAssistantClient:
    # Siehe api_client.py::ApiClient.FAIL_BACKOFF_S für die ausführliche
    # Begründung - hier besonders wichtig, weil get_states() pro Entität
    # einen EIGENEN blockierenden Request macht (kein Batch-Endpunkt in
    # HA): ohne Backoff würde z.B. bei zwei konfigurierten Schaltern JEDER
    # Refresh-Zyklus erneut 2x5s blockieren, wenn Home Assistant gerade
    # nicht erreichbar ist - mit Backoff bezahlt nur die erste Entität
    # nach einem Fehlschlag den vollen Timeout, alle weiteren im selben
    # und in den nächsten FAIL_BACKOFF_S Sekunden schlagen sofort fehl.
    FAIL_BACKOFF_S = 60

    def __init__(self, base_url, token, timeout=5):
        # base_url z.B. "http://homeassistant.local:8123"
        self.base_url = base_url.rstrip("/")
        self.headers = {
            "Authorization": "Bearer %s" % token,
            "Content-Type": "application/json",
        }
        self.timeout = timeout
        # Ob Home Assistant überhaupt genutzt werden soll (siehe
        # config.py "home_assistant.enabled", von main.py gesetzt) - anders
        # als network_available unten geht es hier nicht um WLAN-Status,
        # sondern darum, ob überhaupt ein Home-Assistant-Server existiert.
        # Ohne das würde jeder Aufruf ins Leere laufen (schlimmstenfalls
        # mit einer sehr langen DNS-Auflösung für ".local"-Namen, die am
        # 5s-Timeout unten komplett vorbeiläuft, da die Namensauflösung
        # selbst auf manchen Ports viel länger dauern kann) - siehe
        # HANDOFF-Historie zu den "alle 5s hängt die Uhr"-Aussetzern.
        self.enabled = True
        # Siehe api_client.py::ApiClient.network_available für die
        # ausführliche Begründung.
        self.network_available = True
        self._retry_after = 0  # time.time()-Wert; davor sofort fehlschlagen

    def _check_backoff(self):
        if not self.enabled:
            return "Home Assistant ist nicht konfiguriert/aktiviert (siehe config.json)"
        if not self.network_available:
            return "Kein Netzwerk verfügbar (Access-Point-/Setup-Modus)"
        if time is not None and time.time() < self._retry_after:
            return "Home Assistant kürzlich nicht erreichbar (Backoff aktiv)"
        return None

    def _mark_result(self, ok):
        if time is None:
            return
        self._retry_after = 0 if ok else (time.time() + self.FAIL_BACKOFF_S)

    def get_state(self, entity_id):
        """Liefert den aktuellen Zustand einer Entität, z.B.
        {"entity_id": "sensor.wohnzimmer_temp", "state": "21.4",
         "attributes": {"unit_of_measurement": "°C", ...}, "ok": True}
        """
        skip_reason = self._check_backoff()
        if skip_reason:
            return {"ok": False, "msg": skip_reason}
        url = "%s/api/states/%s" % (self.base_url, entity_id)
        r = None
        try:
            r = requests.get(url, headers=self.headers)
            if r.status_code != 200:
                self._mark_result(False)
                return {"ok": False, "msg": "HTTP %d" % r.status_code}
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

    def get_states(self, entity_ids):
        """Bequemlichkeitsmethode für mehrere Entitäten auf einmal (macht
        intern mehrere Requests - HA hat keinen Batch-Endpunkt für
        einzelne States; bei vielen Entitäten später ggf. auf die
        Websocket-API oder einen Template-Sensor umsteigen)."""
        return {eid: self.get_state(eid) for eid in entity_ids}

    def call_service(self, domain, service, entity_id, service_data=None):
        """Für spätere Interaktion (z.B. Licht schalten) - vorbereitet,
        aktuell aber in keinem Screen verdrahtet."""
        skip_reason = self._check_backoff()
        if skip_reason:
            return {"ok": False, "msg": skip_reason}
        url = "%s/api/services/%s/%s" % (self.base_url, domain, service)
        payload = {"entity_id": entity_id}
        if service_data:
            payload.update(service_data)
        r = None
        try:
            r = requests.post(url, headers=self.headers, json=payload)
            ok = r.status_code == 200
            self._mark_result(ok)
            return {"ok": ok}
        except Exception as e:
            self._mark_result(False)
            return {"ok": False, "msg": str(e)}
        finally:
            if r is not None:
                try:
                    r.close()
                except Exception:
                    pass
