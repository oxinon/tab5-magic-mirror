"""
Air-Quality Sensor-Abstraktion für den Room-Monitor-Screen.

Zwei mögliche Quellen, eine gemeinsame Schnittstelle:
  - RemoteSensorSource:  fragt den bestehenden Magic-Mirror-Proxy
                         (/api/env-sensor) ab (T-Display-S3-Umweltstation).
  - LocalBME688Source:   liest ein direkt am Tab5 per I2C angeschlossenes
                         BME688 aus (Grove-Port), über denselben Treiber
                         und dieselbe IAQ-Logik wie das T-Display-S3-
                         Projekt (siehe sensors/bme680_driver.py,
                         sensors/iaq_tracker.py) - beide Projekte
                         verhalten sich damit identisch.

SensorManager entscheidet je nach `mode` (siehe unten), welche Quelle(n)
genutzt werden. Screens sprechen nur mit dem Manager, nie direkt mit den
Sources.
"""

import time

# time.ticks_ms()/ticks_diff() sind MicroPython-spezifisch - siehe
# sensors/accelerometer.py für die ausführliche Begründung dieses Fallbacks.
try:
    _ticks_ms = time.ticks_ms
    _ticks_diff = time.ticks_diff
except AttributeError:
    def _ticks_ms():
        return int(time.time() * 1000)

    def _ticks_diff(a, b):
        return a - b

try:
    from sensors.bme680_driver import BME680_I2C
except ImportError:
    # bme680_driver.py nutzt `micropython`/`ubinascii` - auf dem Desktop
    # (z.B. tools/sim_test.py) nicht vorhanden. LocalBME688Source bleibt
    # trotzdem importierbar; is_available() liefert dann ehrlich False,
    # außer ein Test-Double überschreibt is_available()/_read_raw() selbst
    # (siehe tools/sim_test.py::FakeAirSource).
    BME680_I2C = None

from sensors.iaq_tracker import IAQTracker

BME688_I2C_ADDR = 0x77  # oder 0x76, je nach SDO-Pin - beim Anschluss prüfen


class SensorReading:
    def __init__(self, temp_c=None, humidity=None, pressure_hpa=None,
                 gas_ohm=None, iaq_score=None, iaq_confidence=None,
                 source="unknown", ok=True, msg=None):
        self.temp_c = temp_c
        self.humidity = humidity
        self.pressure_hpa = pressure_hpa
        self.gas_ohm = gas_ohm
        self.iaq_score = iaq_score
        self.iaq_confidence = iaq_confidence
        self.source = source  # "local" | "remote"
        self.ok = ok
        self.msg = msg

    def as_dict(self):
        return {
            "temp_c": self.temp_c,
            "humidity": self.humidity,
            "pressure_hpa": self.pressure_hpa,
            "gas_ohm": self.gas_ohm,
            "iaq_score": self.iaq_score,
            "source": self.source,
        }

    def to_json(self):
        """Für die Web-UI (/api/status) - anders als as_dict() auch mit
        ok/msg/confidence, damit der Browser Fehler-/Kalibrierungszustände anzeigen kann."""
        d = self.as_dict()
        d["ok"] = self.ok
        d["msg"] = self.msg
        d["iaq_confidence"] = self.iaq_confidence
        return d


class RemoteSensorSource:
    """Nutzt den bestehenden Magic-Mirror-Proxy /api/env-sensor."""

    def __init__(self, api_client):
        self.api_client = api_client  # bestehender api_client.py-Wrapper

    def is_available(self):
        return True  # Verfügbarkeit ergibt sich erst beim read() (Server erreichbar?)

    def read(self):
        try:
            data = self.api_client.get_json("/api/env-sensor")
        except Exception as e:
            return SensorReading(source="remote", ok=False, msg=str(e))

        if not data or not data.get("ok", False):
            msg = data.get("msg", "unbekannter Fehler") if data else "keine Antwort"
            return SensorReading(source="remote", ok=False, msg=msg)

        # Feldnamen entsprechen dem /api/status-Schema des T-Display-S3-Projekts
        return SensorReading(
            temp_c=data.get("temp_sts35", data.get("temperature")),
            humidity=data.get("humidity"),
            pressure_hpa=data.get("pressure"),
            gas_ohm=data.get("gas"),
            iaq_score=data.get("iaq_score"),
            iaq_confidence=data.get("iaq_confidence"),
            source="remote",
            ok=True,
        )


class LocalBME688Source:
    """
    Liest ein direkt am Tab5-Grove-Port (I2C) angeschlossenes BME688 über
    denselben Treiber (bme680_driver.BME680_I2C) und dieselbe IAQ-Logik
    (iaq_tracker.IAQTracker) wie das T-Display-S3-Projekt.

    sample_interval_s MUSS zum tatsächlichen Poll-Takt passen (config.json
    "room_sensor.poll_interval_s"), sonst stimmen die Zeitkonstanten der
    IAQTracker-Baseline nicht.
    """

    RECHECK_INTERVAL_MS = 30000  # siehe accelerometer.py::LocalAccelSource für Begründung

    def __init__(self, i2c=None, addr=BME688_I2C_ADDR, iaq_cfg=None,
                 sample_interval_s=30.0, offsets=None):
        self.i2c = i2c
        self.addr = addr
        self._driver = None
        self._init_error = None
        self._last_scan_ms = None
        self._available_cached = False
        # Korrekturfaktoren wie im Core2-Referenzprojekt (dort
        # config.py "offsets": {"temp_bme680", "humidity", "pressure"}) -
        # additiv auf den Rohwert angewendet, siehe read() unten. Bewusst
        # ein MUTABLES Dict statt Kopie: web_server.py aktualisiert es bei
        # jedem Speichern direkt in-place (siehe main.py-Wiring), damit
        # eine neue Kalibrierung sofort beim nächsten read() wirkt, ohne
        # dass main.py diesen Sensor-Quellen-Objekt neu bauen müsste.
        self.offsets = offsets if offsets is not None else {}

        iaq_cfg = iaq_cfg or {}
        self.iaq_tracker = IAQTracker(
            mode=iaq_cfg.get("baseline_mode", "fixed"),
            burn_in_readings=iaq_cfg.get("burn_in_readings", 10),
            tau_up_h=iaq_cfg.get("tau_up_h", 1.0),
            tau_down_h=iaq_cfg.get("tau_down_h", 24.0),
            sample_interval_s=sample_interval_s,
        )

    def is_available(self):
        # Scan-Ergebnis zwischenspeichern statt bei jedem Poll neu zu
        # scannen - siehe accelerometer.py::LocalAccelSource.is_available()
        # für die ausführliche Begründung (verhindert Timeout-Spam +
        # blockierte asyncio-Schleife).
        if self.i2c is None or BME680_I2C is None:
            self._available_cached = False
            return False

        now = _ticks_ms()
        if self._last_scan_ms is not None and \
                _ticks_diff(now, self._last_scan_ms) < self.RECHECK_INTERVAL_MS:
            return self._available_cached

        self._last_scan_ms = now
        try:
            # writeto() mit leerem Payload statt scan() - siehe
            # accelerometer.py::LocalAccelSource.is_available() für die
            # ausführliche Begründung (nur 1 Adresse statt ~112).
            self.i2c.writeto(self.addr, b"")
        except OSError:
            self._available_cached = False
            return False
        except Exception:
            self._available_cached = False
            return False

        if self._driver is None and self._init_error is None:
            try:
                self._driver = BME680_I2C(self.i2c, address=self.addr)
            except Exception as e:
                self._init_error = str(e)
                self._available_cached = False
                return False
        self._available_cached = self._driver is not None
        return self._available_cached

    def _read_raw(self):
        t = self._driver.temperature
        p = self._driver.pressure
        h = self._driver.humidity
        gas = self._driver.gas
        return t, p, h, gas

    def read(self):
        if not self.is_available():
            return SensorReading(source="local", ok=False,
                                  msg=self._init_error or "BME688 nicht am I2C-Bus gefunden")
        try:
            t, p, h, gas = self._read_raw()
        except Exception as e:
            return SensorReading(source="local", ok=False, msg=str(e))

        # Korrekturfaktoren additiv anwenden (siehe __init__-Docstring) -
        # NACH dem Rohwert-Lesen, aber VOR der IAQ-Berechnung, damit ein
        # korrigierter Feuchte-Wert auch in die IAQ-Baseline einfließt.
        t += self.offsets.get("temp_c", 0.0)
        h += self.offsets.get("humidity", 0.0)
        p += self.offsets.get("pressure_hpa", 0.0)

        iaq = self.iaq_tracker.update(gas, h)
        return SensorReading(
            temp_c=t, humidity=h, pressure_hpa=p, gas_ohm=gas,
            iaq_score=iaq, iaq_confidence=self.iaq_tracker.confidence(),
            source="local", ok=True,
            msg="Baseline wird noch kalibriert..." if iaq is None else None,
        )


class SensorManager:
    """
    mode bestimmt, welche Quelle(n) genutzt werden:
      - "auto"        : lokal bevorzugt, Fallback remote (bisheriges Verhalten)
      - "local_only"  : immer lokal, kein Fallback (zeigt ehrlich "nicht
                        verfügbar", statt heimlich auf remote umzuschalten)
      - "remote_only" : immer remote, lokaler BME688 wird ignoriert
      - "both"        : read() liefert weiterhin eine "primäre" Quelle
                        (wie "auto"), read_all() liefert IMMER beide - für
                        die Web-UI, die dann z.B. lokal und remote
                        nebeneinander anzeigen kann (siehe web_server.py)
    """

    MODES = ("auto", "local_only", "remote_only", "both")

    def __init__(self, remote_source, local_source, history=None,
                 recheck_interval_s=30, mode="auto"):
        self.remote = remote_source
        self.local = local_source
        self.history = history
        self.recheck_interval_s = recheck_interval_s
        self.mode = mode if mode in self.MODES else "auto"
        self._last_check = 0
        self._use_local = False

    def set_mode(self, mode):
        """Ändert den Modus sofort, ohne Neustart - z.B. aus web_server.py
        beim Speichern der Einstellungen aufgerufen."""
        if mode in self.MODES:
            self.mode = mode

    def _refresh_source_choice(self):
        now = time.time()
        if now - self._last_check < self.recheck_interval_s:
            return
        self._last_check = now
        self._use_local = self.local.is_available()

    def read(self):
        """Liefert die 'primäre' Quelle für den aktuellen Modus - das ist
        es, was Screens wie EnvironmentScreen als eine SensorReading
        anzeigen. Für "both" siehe zusätzlich read_all()."""
        if self.mode == "local_only":
            reading = self.local.read()
        elif self.mode == "remote_only":
            reading = self.remote.read()
        else:  # "auto" oder "both" - gleiches Auswahlverhalten für die Primärquelle
            self._refresh_source_choice()
            reading = self.local.read() if self._use_local else self.remote.read()
            if self._use_local and not reading.ok:
                reading = self.remote.read()

        if reading.ok and self.history is not None:
            self.history.add(reading.as_dict())
        return reading

    def read_all(self):
        """Liefert IMMER beide Quellen als Dict {"local": ..., "remote": ...},
        unabhängig vom Modus - für die Web-UI im "both"-Modus, aber auch
        allgemein nützlich zum Vergleichen/Debuggen der beiden Sensoren."""
        return {"local": self.local.read(), "remote": self.remote.read()}
