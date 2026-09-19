"""
Lokaler Beschleunigungssensor (eingebaute BMI270-IMU) als zusätzliche
Sensorquelle für den Environment-/Conference-Room-Screen.

Nutzt M5.Imu.getAccel() - die offizielle UIFlow2-API für die eingebaute
IMU (läuft komplett über M5.begin(), KEIN manuell erzeugtes I2C-Objekt
auf geratenen internen Pins nötig - genau das hat beim Display-Backlight
(GPIO22) schon einmal das ganze Gerät lahmgelegt, siehe HANDOFF.md).

BEKANNTER FIRMWARE-BUG (Stand: recherchiert für UIFlow2 MicroPython
v1.27.0-dirty, dieselbe Version, die auf diesem Tab5 läuft): Auf
mindestens einem anderen M5-Gerät (StickS3) liefert M5.Imu.getAccel()
durchgehend (0.0, 0.0, 0.0), OBWOHL M5.Imu.isEnabled() korrekt True und
M5.Imu.getType() korrekt BMI270 (6) melden - die Sensor-ERKENNUNG
funktioniert, nur die eigentlichen Werte kommen nicht durch. Community-
Workaround dort: direkter I2C-Zugriff auf die BMI270-Register unter
Umgehung von M5.Imu. Falls X/Y/Z hier ebenfalls bei 0.00/0.00/1.00 hängen
bleiben, ist das exakt dieser bekannte Bug - dann bräuchte es denselben
Workaround (eigenes I2C-Objekt auf den ECHTEN internen IMU-Pins, die
zuerst sicher ermittelt werden müssten, z.B. per eigenständigem
Diagnose-Skript wie mic_test.py, NICHT direkt im laufenden main.py
ausprobiert - siehe HANDOFF.md zur Vorsicht bei internen I2C-Bus-Zugriffen).

Nutzt dieselbe STA/LTA-Technik wie die T-Display-S3-Umweltstation (siehe
quake_trigger.py), damit ein Klopfen/eine Erschütterung im Konferenzraum
genauso erkannt wird wie dort.
"""

import math
import time

from sensors.quake_trigger import StaLtaTrigger

try:
    import M5
except ImportError:
    M5 = None  # Desktop-Test (tools/sim_test.py) - kein M5-Modul vorhanden

# time.ticks_ms()/ticks_diff() sind MicroPython-spezifisch (monotone,
# überlaufsichere Millisekunden-Zähler) - auf dem Desktop (tools/sim_test.py)
# gibt es sie nicht. Fallback auf time.time()*1000 dort; auf der Tab5 wird
# immer die echte, überlaufsichere Variante genutzt.
try:
    _ticks_ms = time.ticks_ms
    _ticks_diff = time.ticks_diff
except AttributeError:
    def _ticks_ms():
        return int(time.time() * 1000)

    def _ticks_diff(a, b):
        return a - b


class AccelReading:
    def __init__(self, x=None, y=None, z=None, magnitude=None,
                 quake=None, ok=True, msg=None):
        self.x = x
        self.y = y
        self.z = z
        self.magnitude = magnitude
        self.quake = quake or {}
        self.ok = ok
        self.msg = msg

    def as_dict(self):
        d = {
            "accel_x": self.x,
            "accel_y": self.y,
            "accel_z": self.z,
            "accel_magnitude": self.magnitude,
            "quake_triggered": self.quake.get("triggered"),
            "quake_ratio": self.quake.get("ratio"),
        }
        return d

    def to_json(self):
        """Für die Web-UI (/api/status)."""
        return {
            "ok": self.ok, "msg": self.msg,
            "x": self.x, "y": self.y, "z": self.z,
            "magnitude": self.magnitude, "quake": self.quake,
        }


class LocalAccelSource:
    """i2c/addr-Parameter bewusst noch angenommen (auch wenn M5.Imu sie
    nicht braucht) - falls sich der oben beschriebene Firmware-Bug
    bestätigt, greifen wir hier später auf einen direkten I2C-Fallback auf
    denselben (dann extern übergebenen) Bus zurück, ohne die Aufrufstelle
    in main.py nochmal ändern zu müssen."""

    def __init__(self, i2c=None, addr=0x68, sta_tau_s=0.5, lta_tau_s=30.0, trigger_ratio=3.0,
                 min_amplitude_g=0.02):
        self.i2c = i2c
        self.addr = addr
        self.trigger = StaLtaTrigger(sta_tau_s=sta_tau_s, lta_tau_s=lta_tau_s,
                                      trigger_ratio=trigger_ratio, min_sta=min_amplitude_g)
        self._warned_zero = False
        self._zero_streak = 0

    # So viele aufeinanderfolgende (0,0,0)-Reads (bei ~20Hz = 2s), bis der
    # Sensor als "liefert keine Daten" gilt. Ein echter Sensor misst immer
    # mindestens die Erdanziehung (~1g) - exakt (0,0,0) ist nie plausibel.
    ZERO_STREAK_LIMIT = 40

    def is_available(self):
        if M5 is None:
            return False
        try:
            return bool(M5.Imu.isEnabled())
        except Exception:
            return False

    def _read_raw(self):
        """Erwartet (x, y, z) in g. Siehe Modul-Docstring für den bekannten
        Firmware-Bug, falls hier dauerhaft (0.0, 0.0, 0.0) herauskommt."""
        if M5 is None:
            raise NotImplementedError("M5-Modul nicht verfügbar (Desktop-Test?)")
        x, y, z = M5.Imu.getAccel()
        if x == 0.0 and y == 0.0 and z == 0.0 and not self._warned_zero:
            # Einmaliger Hinweis statt Log-Spam bei jedem einzelnen Read
            # (accel_task in main.py läuft mit ~20Hz) - siehe Modul-
            # Docstring: das ist der bekannte M5.Imu-Firmware-Bug, kein
            # Verkabelungsproblem.
            self._warned_zero = True
            print("LocalAccelSource: M5.Imu.getAccel() liefert (0,0,0) - "
                  "vermutlich der bekannte Firmware-Bug, siehe Modul-Docstring "
                  "in sensors/accelerometer.py.")
        return x, y, z

    def read(self):
        try:
            x, y, z = self._read_raw()
        except Exception as e:
            return AccelReading(ok=False, msg=str(e))

        # Bekannter Firmware-Bug (siehe Modul-Docstring): dauerhaft (0,0,0).
        # Frueher wurde daraus magnitude = -1.0 mit ok=True - "ruhig", aber
        # erfundene Werte in Widget, Web-Grafik und SD-Log. Jetzt ehrlich
        # "nicht verfuegbar", und der STA/LTA-Trigger bekommt keine Fake-Daten.
        if x == 0.0 and y == 0.0 and z == 0.0:
            self._zero_streak += 1
            if self._zero_streak >= self.ZERO_STREAK_LIMIT:
                return AccelReading(ok=False,
                                     msg="IMU liefert nur Nullwerte (bekannter Firmware-Bug)")
        else:
            self._zero_streak = 0

        # 1g (Erdanziehung) abziehen, damit im Ruhezustand ~0 herauskommt
        magnitude = math.sqrt(x * x + y * y + z * z) - 1.0
        quake_state = self.trigger.update(abs(magnitude))

        return AccelReading(x=x, y=y, z=z, magnitude=magnitude,
                             quake=quake_state, ok=True)
