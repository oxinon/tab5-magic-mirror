"""
Lokales Mikrofon (ES7210, Dual-Mic-Array am Tab5) als Sensorquelle für den
Konferenzraum-Screen: keine Aufnahme/Diktierfunktion, sondern
  - ein grober Gesamt-Schallpegel ("wie laut ist es hier gerade") und
  - eine grobe Frequenzband-Zerlegung fürs Equalizer-Widget ("was für ein
    Geräusch ist das gerade").

Beides NICHT kalibriert (kein echtes SPL-Messgerät/Spektrumanalysator) -
reicht aber locker für eine Ampel/einen Verlauf + einen visuellen
Equalizer im Meeting-Raum.

Die Bänder werden per Goertzel-Algorithmus berechnet statt per vollem FFT:
Goertzel liefert die Magnitude für EINE bestimmte Frequenz sehr günstig
(kein externes DSP-Paket wie ulab nötig) - für 6-8 feste Bänder reicht das
locker und ist auf einem ESP32-P4 ohne Zusatzbibliothek machbar.
"""

import math
import time

try:
    import M5
except ImportError:
    M5 = None  # Desktop-Test (tools/sim_test.py) - kein M5-Modul vorhanden


try:
    _ticks_ms = time.ticks_ms
    _ticks_diff = time.ticks_diff
except AttributeError:  # Desktop-Test
    def _ticks_ms():
        return int(time.time() * 1000)

    def _ticks_diff(a, b):
        return a - b


class MicReading:
    def __init__(self, rms=None, db=None, peak=None, bands=None, ok=True, msg=None):
        self.rms = rms
        self.db = db
        self.peak = peak
        self.bands = bands or []  # Liste von 0-100-Werten, ein Eintrag je Frequenzband
        self.ok = ok
        self.msg = msg

    def as_dict(self):
        # bands wird bewusst NICHT für die SD-Karte geloggt (siehe sd_logger.py
        # FIELDS) - das wären 8 zusätzliche Spalten nur für eine Live-Anzeige.
        # Nur der Gesamtpegel (sound_rms/sound_db) wird dauerhaft aufgezeichnet.
        return {"sound_rms": self.rms, "sound_db": self.db, "sound_peak": self.peak}

    def to_json(self):
        """Für die Web-UI (/api/status) - hier DÜRFEN die bands mit rein,
        das geht direkt an den Equalizer im Browser, nicht auf die SD-Karte."""
        return {
            "ok": self.ok, "msg": self.msg,
            "rms": self.rms, "db": self.db, "peak": self.peak, "bands": self.bands,
        }


class LocalMicSource:
    """
    Nutzt M5.Mic.begin()/record()/end() - die offizielle UIFlow2-API für
    das eingebaute Mikrofon (siehe m5-docs "Tab5 Mic": "Mic der Tab5 nutzt
    die Mic_Class aus M5Unified", KEIN manuelles I2S-Pin-Setup nötig -
    anders als beim Core2-Referenzprojekt läuft das komplett über
    M5.begin()). Frischer begin()/end()-Zyklus PRO Messung (nicht einmalig
    dauerhaft "an" gelassen) - genau das Muster, das sich im Core2-
    Referenzprojekt (noise_meter.py) als notwendig erwiesen hat, weil die
    Aufnahme-Pipeline nach einem Block sonst nicht sauber weiterlief.

    WICHTIG - Mic/Speaker-Exklusivität: Laut M5Stack-Doku können Mikrofon
    und Lautsprecher auf der Tab5 NICHT gleichzeitig aktiv sein (gemeinsame
    Audio-Hardware). Der Erdbeben-Alarmton (main.py::_play_alarm(), nutzt
    M5.Speaker.tone()) ruft deshalb vor dem Tönen sicherheitshalber
    M5.Mic.end() auf. Umgekehrt hier: vor JEDEM Mic-Read wird zur
    Sicherheit M5.Speaker.end() aufgerufen, falls der Lautsprecher gerade
    noch aktiv war.
    """

    REFERENCE_RMS = 1000.0  # Nenner der dB-Pseudo-Skala - nach dem ersten
                             # Praxistest an einem stillen Raum kalibrieren

    # Grobe, für Sprache/Konferenzraum sinnvolle Frequenzstützstellen (Hz),
    # log-artig verteilt zwischen tiefem Brummen und Zischlauten (12 Bänder
    # für ein etwas feineres Equalizer-Bild als bei 8)
    EQ_BANDS_HZ = [120, 170, 240, 350, 500, 710, 1000, 1450, 2050, 2950, 4200, 6000]

    def __init__(self, i2s=None, sample_window=512, sample_rate=16000):
        # "i2s"-Parameter bleibt aus Abwärtskompatibilität erhalten (main.py
        # übergibt ihn weiterhin), wird von M5.Mic aber nicht gebraucht -
        # M5.begin() konfiguriert die Mikrofon-Hardware bereits passend zur
        # erkannten Tab5.
        self.i2s = i2s
        self.sample_window = sample_window
        self.sample_rate = sample_rate
        self._buf = bytearray(sample_window * 2)  # 16-bit PCM
        self._warned_zero = False
        # Optional (von main.py gesetzt): Funktion ohne Argumente; True = JETZT
        # nicht aufnehmen (z.B. Alarmton laeuft - M5.Speaker.end() beim Lesen
        # wuerde ihn abschneiden).
        self.skip_if = None
        self._silent_streak = 0
        self._last_attempt_ms = None

    def is_available(self):
        return M5 is not None

    def _read_samples(self):
        if M5 is None:
            raise NotImplementedError("M5-Modul nicht verfügbar (Desktop-Test?)")

        # Sicherstellen, dass der Lautsprecher aus ist, BEVOR das Mikrofon
        # startet (siehe Klassen-Docstring - Mic/Speaker-Exklusivität).
        try:
            M5.Speaker.end()
        except Exception:
            pass

        try:
            M5.Mic.begin()
        except Exception as e:
            raise OSError("M5.Mic.begin() fehlgeschlagen: %s" % e)

        try:
            ok = M5.Mic.record(self._buf, self.sample_rate, True)
            if ok is False:
                raise OSError("M5.Mic.record() lieferte False zurück")
            waited_ms = 0
            while M5.Mic.isRecording() and waited_ms < 500:
                time.sleep_ms(5)
                waited_ms += 5
        finally:
            try:
                M5.Mic.end()
            except Exception:
                pass

        n = self.sample_window
        samples = [0] * n
        buf = self._buf
        for i in range(n):
            lo = buf[2 * i]
            hi = buf[2 * i + 1]
            v = (hi << 8) | lo
            if v >= 32768:
                v -= 65536
            samples[i] = v

        if not self._warned_zero and max(samples, default=0) == 0 and min(samples, default=0) == 0:
            # Einmaliger Hinweis statt Log-Spam bei jedem Read - siehe
            # accelerometer.py für dasselbe Muster beim M5.Imu-Firmware-Bug.
            # Falls das dauerhaft (0,0,0...) bleibt, könnte hier ein
            # ähnlicher Firmware-Bug vorliegen wie bei M5.Imu.getAccel().
            self._warned_zero = True
            print("LocalMicSource: M5.Mic.record() liefert durchgehend Stille (0) - "
                  "prüfen, ob das Mikrofon echte Werte liefert (z.B. per eigenem "
                  "Testskript, siehe HANDOFF.md-Vorgehen bei anderen Sensoren).")

        return samples

    def _goertzel_magnitude(self, samples, target_freq):
        n = len(samples)
        k = int(0.5 + (n * target_freq) / self.sample_rate)
        omega = (2 * math.pi * k) / n
        coeff = 2 * math.cos(omega)
        s_prev = 0.0
        s_prev2 = 0.0
        for s in samples:
            s0 = s + coeff * s_prev - s_prev2
            s_prev2 = s_prev
            s_prev = s0
        power = s_prev2 * s_prev2 + s_prev * s_prev - coeff * s_prev * s_prev2
        return math.sqrt(max(power, 0)) / n

    def _db_scaled(self, magnitude):
        db = 20 * math.log10(magnitude / self.REFERENCE_RMS) if magnitude > 0 else -100.0
        return max(0, min(100, db + 60))

    # Nach so vielen stillen Fenstern hintereinander liefert das Mikrofon
    # offenbar nichts (bekannter Firmware-Bug) - dann nur noch alle
    # SILENT_RETRY_S Sekunden neu versuchen statt bei JEDEM Zyklus (jede
    # Aufnahme blockiert bis zu 0,5s den Hauptthread).
    SILENT_LIMIT = 3
    SILENT_RETRY_S = 600

    def read(self):
        if self.skip_if is not None:
            try:
                if self.skip_if():
                    return MicReading(ok=False, msg="Alarmton aktiv")
            except Exception:
                pass
        if self._silent_streak >= self.SILENT_LIMIT and self._last_attempt_ms is not None:
            if _ticks_diff(_ticks_ms(), self._last_attempt_ms) < self.SILENT_RETRY_S * 1000:
                return MicReading(ok=False, msg="Mikrofon liefert nur Stille (bekannter Firmware-Bug)")
        self._last_attempt_ms = _ticks_ms()
        try:
            samples = self._read_samples()
        except Exception as e:
            return MicReading(ok=False, msg=str(e))

        if not samples:
            return MicReading(ok=False, msg="keine Samples gelesen")

        # Komplett stilles Fenster (alle Samples 0) = kein echtes Mikrofonsignal
        # (bekannter Firmware-Bug, siehe _read_samples()). Frueher wurde daraus
        # db=0 mit ok=True ("leise") plus 12 sinnlose Goertzel-Durchlaeufe.
        if not any(samples):
            self._silent_streak += 1
            return MicReading(ok=False, msg="Mikrofon liefert nur Stille (bekannter Firmware-Bug)")
        self._silent_streak = 0

        n = len(samples)
        mean = sum(samples) / n
        rms = math.sqrt(sum((s - mean) ** 2 for s in samples) / n)
        peak = max(abs(s - mean) for s in samples)
        db_scaled = self._db_scaled(rms)

        bands = [
            round(self._db_scaled(self._goertzel_magnitude(samples, f)), 1)
            for f in self.EQ_BANDS_HZ
        ]

        return MicReading(rms=rms, db=db_scaled, peak=peak, bands=bands, ok=True)
