"""
Langzeit-Logging aller Sensordaten auf die microSD-Karte des Tab5.

Bewusst getrennt von sensors/history.py: history.py ist der RAM-Ringpuffer
für die Live-Sparklines (kurzes Fenster, geht beim Neustart verloren) -
der SD-Logger schreibt zusätzlich dauerhaft mit, damit Daten einen
Reboot/Stromausfall überstehen und sich auch über Tage/Wochen auswerten
lassen.

CSV statt JSON, weil CSV-Anhängen (nur eine neue Zeile schreiben) viel
günstiger ist als bei jedem Datenpunkt eine komplette JSON-Datei neu zu
schreiben - relevant für die Schreib-Lebensdauer einer SD-Karte.

Eine Datei pro Tag (`YYYY-MM-DD.csv`), damit einzelne Dateien nicht
unbegrenzt wachsen und sich alte Tage einfach archivieren/löschen lassen.
"""

import time

try:
    import uos as os
except ImportError:
    import os

# Feste Spaltenreihenfolge über alle Sensorquellen hinweg - fehlende Werte
# werden als leere Zelle geschrieben, damit die CSV trotzdem valide bleibt,
# auch wenn z.B. das Mikrofon noch nicht angeschlossen ist.
FIELDS = [
    "timestamp", "source",
    "temp_c", "humidity", "pressure_hpa", "gas_ohm", "iaq_score",
    "accel_x", "accel_y", "accel_z", "accel_magnitude",
    "quake_triggered", "quake_ratio",
    "sound_rms", "sound_db",
]


# Zeitstempel unterhalb dieser Grenze (1.1.2024) = Uhr noch nicht per NTP
# gestellt (interne RTC steht dann im Jahr 2000): nichts loggen, sonst
# landen falsche Zeitstempel in "2000-01-01.csv" und die Historie ist
# unbrauchbar.
MIN_VALID_TS = 1704067200

# Fehlermeldungen hoechstens so oft (Sekunden) ausgeben - sonst alle 20s
# dieselbe Zeile, solange die SD-Karte fehlt.
ERROR_PRINT_INTERVAL_S = 300


class SDLogger:
    def __init__(self, base_path="/sd/logs", flush_every=10):
        self.base_path = base_path
        self.flush_every = flush_every
        self._pending = 0
        self._current_date = None
        self._file = None
        self._last_err_print = None
        self._warned_no_time = False
        self._ensure_dir()

    def _print_error(self, text):
        now = time.time()
        if self._last_err_print is None or abs(now - self._last_err_print) > ERROR_PRINT_INTERVAL_S:
            self._last_err_print = now
            print(text)

    def _close_broken(self):
        """Datei-Handle nach einem Fehler verwerfen, damit der naechste
        log()-Aufruf sie neu oeffnet. Frueher blieb das kaputte Handle fuer
        den Rest des Tages bestehen (z.B. SD kurz gezogen): jeder weitere
        Schreibversuch schlug fehl, das Logging war bis zum Neustart tot."""
        f, self._file = self._file, None
        self._current_date = None
        self._pending = 0
        if f is not None:
            try:
                f.close()
            except Exception:
                pass

    def _header_matches(self, path):
        """True, wenn die erste Zeile der vorhandenen Datei zum aktuellen
        FIELDS-Schema passt."""
        try:
            with open(path) as f:
                return f.readline().strip() == ",".join(FIELDS)
        except Exception:
            return False

    def _ensure_dir(self):
        try:
            os.stat(self.base_path)
        except OSError:
            try:
                os.mkdir(self.base_path)
            except Exception as e:
                print("SDLogger: Verzeichnis konnte nicht angelegt werden (SD gesteckt?):", e)

    def _date_str(self, ts):
        t = time.localtime(ts)
        return "%04d-%02d-%02d" % (t[0], t[1], t[2])

    def _path_for(self, ts):
        return "%s/%s.csv" % (self.base_path, self._date_str(ts))

    def _open_if_needed(self, ts):
        date_str = self._date_str(ts)
        if date_str == self._current_date and self._file is not None:
            return

        if self._file is not None:
            self._flush()
            self._file.close()

        path = self._path_for(ts)
        is_new = True
        try:
            os.stat(path)
            is_new = False
        except OSError:
            pass

        if not is_new and not self._header_matches(path):
            # Schema hat sich geaendert (FIELDS erweitert): alte Datei zur
            # Seite legen und neu beginnen - sonst haengen neue Zeilen mit
            # anderer Spaltenzahl an alte, und sd_reader ueberspringt sie still.
            old = path[:-4] + ".old.csv"
            try:
                try:
                    os.remove(old)
                except OSError:
                    pass
                os.rename(path, old)
                print("SDLogger: Spaltenschema geaendert - alte Datei nach", old)
            except Exception as e:
                print("SDLogger: alte Datei konnte nicht beiseitegelegt werden:", e)
            is_new = True

        self._file = open(path, "a")
        if is_new:
            self._file.write(",".join(FIELDS) + "\n")

        self._current_date = date_str

    def log(self, values: dict, ts=None):
        """values: flaches Dict, z.B. aus mehreren reading.as_dict()-Aufrufen
        zusammengeführt. Fehlende FIELDS-Einträge werden leer geschrieben."""
        ts = ts if ts is not None else time.time()

        if ts < MIN_VALID_TS:
            if not self._warned_no_time:
                self._warned_no_time = True
                print("SDLogger: Uhr noch nicht per NTP gestellt - logge erst danach.")
            return False

        try:
            self._open_if_needed(ts)
        except Exception as e:
            self._close_broken()
            self._print_error("SDLogger: Datei konnte nicht geöffnet werden (SD gesteckt?): %s" % (e,))
            return False

        row = [str(ts)] + [self._fmt(values.get(k)) for k in FIELDS[1:]]
        try:
            self._file.write(",".join(row) + "\n")
        except Exception as e:
            self._close_broken()
            self._print_error("SDLogger: Schreibfehler (Datei wird beim naechsten Mal neu geoeffnet): %s" % (e,))
            return False

        self._pending += 1
        if self._pending >= self.flush_every:
            self._flush()
        return True

    def _fmt(self, v):
        if v is None:
            return ""
        if isinstance(v, bool):
            return "1" if v else "0"
        if isinstance(v, float):
            return "%.3f" % v
        return str(v)

    def _flush(self):
        try:
            self._file.flush()
        except Exception:
            pass
        self._pending = 0

    def close(self):
        if self._file is not None:
            self._flush()
            self._file.close()
            self._file = None
