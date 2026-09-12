"""
Liest die von sensors/sd_logger.py geschriebenen Tages-CSV-Dateien
zurück - für die Web-UI-Grafiken (siehe web_server.py), die einen
einstellbaren Zeitverlauf über die auf der SD-Karte gespeicherten Daten
zeigen sollen. Anders als im T-Display-S3-Projekt (dessen Grafiken nur
akkumulieren, während die Browser-Seite offen ist) lädt die Tab5-Seite die
Historie beim Öffnen direkt von hier, weil wir ja dauerhaft loggen.
"""

import time

try:
    import uos as os
except ImportError:
    import os


class SDReader:
    def __init__(self, base_path="/sd/logs"):
        self.base_path = base_path

    def _day_files_for_range(self, hours):
        """Pfade der Tages-Dateien, die für die letzten `hours` Stunden
        relevant sein könnten (heute + so viele Tage zurück wie nötig)."""
        now = time.time()
        days_needed = int(hours // 24) + 2  # Rand-Tage inklusive
        paths = []
        for i in range(days_needed):
            t = time.localtime(now - i * 86400)
            date_str = "%04d-%02d-%02d" % (t[0], t[1], t[2])
            paths.append("%s/%s.csv" % (self.base_path, date_str))
        return paths

    def read_range(self, hours, max_points=300):
        """
        Liest alle Zeilen der letzten `hours` Stunden über ggf. mehrere
        Tages-Dateien, filtert nach Zeitstempel und downsampled auf
        max_points Zeilen (Mittelwert je Bucket für Zahlenfelder) - analog
        zu sensors/history.py, nur von der SD-Karte statt aus dem
        RAM-Ringpuffer gelesen. Gibt eine Liste von dicts zurück, älteste
        Zeile zuerst (passend für Chart.js-Achsen).
        """
        cutoff = time.time() - hours * 3600
        rows = []

        for path in reversed(self._day_files_for_range(hours)):
            try:
                with open(path) as f:
                    header = f.readline().strip().split(",")
                    for line in f:
                        parts = line.strip().split(",")
                        if len(parts) != len(header):
                            continue
                        row = dict(zip(header, parts))
                        try:
                            ts = float(row["timestamp"])
                        except (KeyError, ValueError):
                            continue
                        if ts >= cutoff:
                            rows.append((ts, row))
            except OSError:
                continue  # Datei für den Tag existiert nicht - überspringen

        rows.sort(key=lambda r: r[0])
        return self._downsample(rows, max_points)

    def _downsample(self, rows, max_points):
        if not rows:
            return []
        if len(rows) <= max_points:
            return [self._to_typed(r[1], r[0]) for r in rows]

        bucket_size = len(rows) / max_points
        out = []
        for i in range(max_points):
            start = int(i * bucket_size)
            end = int((i + 1) * bucket_size) or (start + 1)
            chunk = rows[start:end] or [rows[start]]
            out.append(self._merge_bucket(chunk))
        return out

    def _merge_bucket(self, chunk):
        """Zahlenfelder: Mittelwert über den Bucket. Alles andere (z.B.
        "source", "quake_triggered"): letzter Wert im Bucket."""
        keys = chunk[0][1].keys()
        merged = {}
        for key in keys:
            if key == "timestamp":
                continue
            values, last = [], None
            for _, row in chunk:
                raw = row.get(key, "")
                last = raw
                try:
                    values.append(float(raw))
                except ValueError:
                    pass
            merged[key] = round(sum(values) / len(values), 3) if values else (last or None)
        merged["timestamp"] = chunk[-1][0]
        return merged

    def _to_typed(self, row, ts):
        """Wandelt die rohen String-Werte einer einzelnen CSV-Zeile in
        Zahlen um, wo möglich (für JSON/Chart.js praktischer als Strings)."""
        out = {"timestamp": ts}
        for key, raw in row.items():
            if key == "timestamp":
                continue
            if raw == "":
                out[key] = None
                continue
            try:
                out[key] = float(raw)
            except ValueError:
                out[key] = raw
        return out
