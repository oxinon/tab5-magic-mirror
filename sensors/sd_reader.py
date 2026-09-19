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


def _now_ms():
    if hasattr(time, "ticks_ms"):
        return time.ticks_ms()
    return int(time.time() * 1000)


def _ticks_diff_ms(a, b):
    if hasattr(time, "ticks_diff"):
        return time.ticks_diff(a, b)
    return a - b


class SDReader:
    def __init__(self, base_path="/sd/logs"):
        self.base_path = base_path
        self._cache = None  # ((hours, max_points), zeitpunkt_ms, ergebnis)

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

    # Groesste erlaubte Zeitspanne (ein Jahr) - ohne Grenze baute
    # _day_files_for_range() bei z.B. hours=1e9 Millionen Pfad-Strings (Hang/MemoryError).
    STRING_COLS = ("source",)   # nicht-numerische Spalten (siehe sd_logger.FIELDS)
    MAX_HOURS = 24 * 366
    CACHE_TTL_MS = 20000

    def read_range(self, hours, max_points=300):
        """
        Liefert die letzten `hours` Stunden als Liste von dicts (aelteste zuerst),
        auf hoechstens max_points Zeit-Buckets verdichtet (Mittelwert je Bucket fuer
        Zahlenfelder, sonst letzter Wert).

        STREAMING: Die CSV-Zeilen werden beim Lesen direkt in die Buckets
        einsummiert - frueher wurde JEDE Zeile als eigenes dict im RAM gehalten
        und erst danach verdichtet (ein Tag = tausende dicts, eine Woche
        zehntausende: MemoryError/mehrere Sekunden Stillstand). Jetzt ist der
        Speicherbedarf konstant (max_points Buckets). Ergebnis wird 20s
        zwischengespeichert (Web-UI und Sensor-Screen fragen oft gleichzeitig).
        """
        try:
            hours = float(hours)
        except (TypeError, ValueError):
            hours = 6.0
        if hours != hours:  # NaN
            hours = 6.0
        hours = max(0.05, min(self.MAX_HOURS, hours))
        max_points = max(10, min(1000, int(max_points)))

        now_ms = _now_ms()
        cache = self._cache
        if cache is not None and cache[0] == (round(hours, 3), max_points) \
                and _ticks_diff_ms(now_ms, cache[1]) < self.CACHE_TTL_MS:
            return cache[2]

        now = time.time()
        span = hours * 3600.0
        cutoff = now - span
        bucket_s = span / max_points
        buckets = {}
        header_keys = []

        for path in reversed(self._day_files_for_range(hours)):
            try:
                f = open(path)
            except OSError:
                continue  # Datei fuer den Tag existiert nicht - ueberspringen
            try:
                header = f.readline().strip().split(",")
                if "timestamp" not in header:
                    continue
                ts_idx = header.index("timestamp")
                ncol = len(header)
                header_keys = [k for k in header if k != "timestamp"]
                for line in f:
                    parts = line.strip().split(",")
                    if len(parts) != ncol:
                        continue
                    try:
                        ts = float(parts[ts_idx])
                    except ValueError:
                        continue
                    if ts < cutoff or ts > now + 300:
                        continue
                    idx = int((ts - cutoff) / bucket_s)
                    if idx >= max_points:
                        idx = max_points - 1
                    b = buckets.get(idx)
                    if b is None:
                        b = [{}, {}, {}, ts]
                        buckets[idx] = b
                    sums, counts, last = b[0], b[1], b[2]
                    for i in range(ncol):
                        raw = parts[i]
                        if not raw or i == ts_idx:
                            continue  # leere Zelle (z.B. Sensor ohne Wert): keine Exception-Kosten
                        key = header[i]
                        if key in self.STRING_COLS:
                            last[key] = raw
                            continue
                        try:
                            v = float(raw)
                        except ValueError:
                            continue
                        sums[key] = sums.get(key, 0.0) + v
                        counts[key] = counts.get(key, 0) + 1
                    if ts > b[3]:
                        b[3] = ts
            except OSError:
                pass  # Lesefehler mitten in der Datei (SD gezogen) - bisherige Buckets behalten
            finally:
                try:
                    f.close()
                except Exception:
                    pass

        out = []
        for idx in sorted(buckets.keys()):
            sums, counts, last, last_ts = buckets[idx]
            row = {}
            for key in header_keys:
                if counts.get(key):
                    row[key] = round(sums[key] / counts[key], 3)
                elif key in self.STRING_COLS:
                    row[key] = last.get(key) or None
                else:
                    row[key] = None
            row["timestamp"] = last_ts
            out.append(row)

        self._cache = ((round(hours, 3), max_points), now_ms, out)
        return out

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
