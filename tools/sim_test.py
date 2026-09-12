"""
Desktop-Testharness - läuft mit normalem CPython, KEIN MicroPython/LVGL
nötig. Damit lässt sich die komplette Sensor-/Historie-/SD-Logging-Logik
schon jetzt durchspielen, bevor die Tab5-Hardware da ist.

Aufruf:
    python3 tools/sim_test.py
"""

import sys
import os
import random
import shutil

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sensors.history import SensorHistory
from sensors.air_quality import LocalBME688Source
from sensors.accelerometer import LocalAccelSource
from sensors.microphone import LocalMicSource
from sensors.sd_logger import SDLogger
from widgets.air_quality_light import score_to_level
from i18n import STRINGS

LEVEL_LABEL = {
    "good": STRINGS["level.good"], "moderate": STRINGS["level.moderate_air"],
    "bad": STRINGS["level.bad"], "unknown": STRINGS["level.unknown"],
}


SIM_SD_PATH = os.path.join(os.path.dirname(__file__), "sim_sd_logs")


class FakeAirSource(LocalBME688Source):
    """Simuliert einen BME688 mit langsam sinkendem Gaswiderstand (= sich
    verschlechternde Luft), ohne echte I2C-Hardware."""

    def __init__(self):
        super().__init__(i2c=None)
        self._t = 0

    def is_available(self):
        return True

    def _read_raw(self):
        self._t += 1
        temp = 21.5 + random.uniform(-0.2, 0.2)
        hum = 45 + 10 * (0.5 - random.random())
        press = 1013 + random.uniform(-2, 2)
        base_gas = 50000 if self._t < 30 else 50000 - (self._t - 30) * 300
        gas = max(5000, base_gas + random.uniform(-1000, 1000))
        return temp, press, hum, gas


class FakeAccelSource(LocalAccelSource):
    """Simuliert ruhige Beschleunigung mit einer künstlichen Erschütterung
    um Messung #50 herum, um den STA/LTA-Trigger durchzuspielen.

    Wichtig: StaLtaTrigger rechnet mit echter Zeit (time.time()). Da diese
    Simulation aber alle 60 "Messungen" in Millisekunden statt über reale
    Sekunden durchläuft, würden die Zeitkonstanten (0.5s/30s) nie greifen -
    deshalb wird hier ein simulierter, um 1s pro Messung fortschreitender
    Zeitstempel an trigger.update() übergeben (entspricht dem realen
    1-Sekunden-Polling-Takt auf der Tab5)."""

    def __init__(self):
        super().__init__(i2c=None)
        self._t = 0
        self._sim_time = 1_700_000_000.0  # beliebiger fester Startzeitpunkt

    def is_available(self):
        return True

    def _read_raw(self):
        self._t += 1
        shake = 0.0
        if 48 <= self._t <= 52:
            shake = random.uniform(0.5, 1.5)  # kurzer heftiger Ausschlag
        x = random.uniform(-0.02, 0.02) + shake
        y = random.uniform(-0.02, 0.02)
        z = 1.0 + random.uniform(-0.02, 0.02)
        return x, y, z

    def read(self):
        import math
        try:
            x, y, z = self._read_raw()
        except Exception as e:
            from sensors.accelerometer import AccelReading
            return AccelReading(ok=False, msg=str(e))

        self._sim_time += 1.0  # 1 simulierte Sekunde pro Messung
        magnitude = math.sqrt(x * x + y * y + z * z) - 1.0
        quake_state = self.trigger.update(abs(magnitude), ts=self._sim_time)

        from sensors.accelerometer import AccelReading
        return AccelReading(x=x, y=y, z=z, magnitude=magnitude,
                             quake=quake_state, ok=True)


class FakeMicSource(LocalMicSource):
    """Simuliert Hintergrundrauschen mit einer lauteren Phase dazwischen."""

    def __init__(self):
        super().__init__(i2s=None)
        self._t = 0

    def is_available(self):
        return True

    def _read_samples(self):
        self._t += 1
        loud = 30 <= self._t <= 40
        amplitude = 4000 if loud else 300
        return [random.uniform(-amplitude, amplitude) for _ in range(64)]


def main():
    if os.path.isdir(SIM_SD_PATH):
        shutil.rmtree(SIM_SD_PATH)

    history = SensorHistory(max_len=200)
    air = FakeAirSource()
    accel = FakeAccelSource()
    mic = FakeMicSource()
    sd_logger = SDLogger(base_path=SIM_SD_PATH, flush_every=5)

    print("Simuliere 60 Messungen (Luftverschlechterung + kurze Erschütterung"
          " + laute Phase)...\n")
    for i in range(60):
        air_reading = air.read()
        accel_reading = accel.read()
        mic_reading = mic.read()

        combined = {}
        combined.update(air_reading.as_dict())
        combined.update(accel_reading.as_dict())
        combined.update(mic_reading.as_dict())
        history.add(combined)
        sd_logger.log(combined)

        level = score_to_level(air_reading.iaq_score)
        score_str = "%.1f" % air_reading.iaq_score if air_reading.iaq_score is not None else "—"
        quake_flag = "  <<< ERSCHÜTTERUNG" if accel_reading.quake.get("triggered") else ""
        print("#%2d  T=%.1f°C  Luft=%s(%s)  |Accel|=%.2fg  Sound=%.0f%s" % (
            i, air_reading.temp_c, LEVEL_LABEL[level], score_str,
            accel_reading.magnitude, mic_reading.db, quake_flag,
        ))

    sd_logger.close()

    print("\nDownsampled Temperaturverlauf (20 Punkte):")
    print([round(v, 1) for v in history.downsample("temp_c", points=20)])

    print("\nDownsampled Schallpegel-Verlauf (20 Punkte):")
    print([round(v, 1) for v in history.downsample("sound_db", points=20)])

    print("\nSD-Log-Dateien in %s:" % SIM_SD_PATH)
    for fname in sorted(os.listdir(SIM_SD_PATH)):
        path = os.path.join(SIM_SD_PATH, fname)
        with open(path) as f:
            lines = f.readlines()
        print("  %s: %d Zeilen (inkl. Header)" % (fname, len(lines)))
        print("  Header:", lines[0].strip())
        print("  Beispielzeile:", lines[-1].strip())


if __name__ == "__main__":
    main()
