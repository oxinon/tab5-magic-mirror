"""
Zeitreihen-Ringpuffer für Sensormesswerte (RAM- und Flash-schonend).

Gedacht für MicroPython auf dem Tab5, funktioniert aber unverändert auch
unter CPython (siehe tools/sim_test.py) - genau deshalb lässt sich diese
Datei schon jetzt testen, ohne dass die Tab5 angekommen ist.
"""

import time

try:
    import ujson as json
except ImportError:
    import json


class SensorHistory:
    """
    Ringpuffer fester Größe für (timestamp, values-dict) Paare.

    - max_len begrenzt den RAM-Verbrauch (wichtig auf dem ESP32-P4)
    - downsample() reduziert die Historie für schmale Sparkline-Widgets
    - optionale Persistenz auf Flash, damit der Verlauf einen Reboot übersteht
    """

    def __init__(self, max_len=4320, persist_path=None):
        # Default passend zu config.py room_sensor.history_max_points -
        # 4320 Punkte * 20s Takt = 24h Verlauf (siehe dortigen Kommentar).
        self.max_len = max_len
        self.persist_path = persist_path
        self._buf = []  # Liste von (ts, dict)
        if persist_path:
            self._load()

    def add(self, values: dict, ts=None):
        ts = ts if ts is not None else time.time()
        self._buf.append((ts, dict(values)))
        if len(self._buf) > self.max_len:
            self._buf.pop(0)

    def latest(self):
        return self._buf[-1] if self._buf else None

    def series(self, key, n=None):
        """Liefert (timestamps, values) für einen bestimmten Messwert-Key."""
        data = self._buf if n is None else self._buf[-n:]
        ts = [d[0] for d in data]
        vals = [d[1].get(key) for d in data]
        return ts, vals

    def downsample(self, key, points=40):
        """
        Reduziert die Historie auf `points` Werte (Mittelwert je Bucket) -
        für schmale Sparkline-Widgets, bei denen ohnehin nicht alle
        max_len Punkte sichtbar wären.
        """
        _, vals = self.series(key)
        vals = [v for v in vals if v is not None]
        if not vals:
            return []
        if len(vals) <= points:
            return vals
        bucket_size = len(vals) / points
        out = []
        for i in range(points):
            start = int(i * bucket_size)
            end = int((i + 1) * bucket_size) or (start + 1)
            chunk = vals[start:end] or [vals[start]]
            out.append(sum(chunk) / len(chunk))
        return out

    def save(self):
        if not self.persist_path:
            return
        try:
            with open(self.persist_path, "w") as f:
                json.dump(self._buf, f)
        except Exception as e:
            print("SensorHistory: save fehlgeschlagen:", e)

    def _load(self):
        try:
            with open(self.persist_path) as f:
                self._buf = json.load(f)
        except Exception:
            self._buf = []
