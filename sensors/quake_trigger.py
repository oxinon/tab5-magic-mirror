"""
Generischer STA/LTA (Short-Term-Average vs Long-Term-Average) Trigger für
Vibrations-/Erschütterungserkennung.

Hardware-unabhängig: bekommt einfach einen Strom von Beschleunigungs-
Magnituden (z.B. sqrt(x²+y²+z²) - 1g) und meldet, wann kurzfristige
Aktivität deutlich über dem langfristigen Hintergrundrauschen liegt.

Gleiche Grundtechnik wie in der T-Display-S3-Umweltstation (`quake.py`),
hier verallgemeinert, damit sie ohne Codeduplikation auch für den
Tab5-eigenen BMI270 genutzt werden kann (siehe sensors/accelerometer.py).
"""

import math
import time


class StaLtaTrigger:
    def __init__(self, sta_tau_s=0.5, lta_tau_s=30.0, trigger_ratio=3.0,
                 release_delay_s=5.0):
        self.sta_tau_s = sta_tau_s
        self.lta_tau_s = lta_tau_s
        self.trigger_ratio = trigger_ratio
        self.release_delay_s = release_delay_s

        self.sta = 0.0
        self.lta = 1e-6  # kleiner Startwert statt 0, um Division durch 0 zu vermeiden
        self._last_ts = None
        self._triggered_since = None
        self.peak_ratio = 1.0
        # Zeitpunkt (time.time()), an dem zuletzt ein Ereignis AUSGELÖST
        # wurde (steigende Flanke) - fürs "vor Xs"/"noch kein Ereignis"
        # im Widget (siehe widgets/acceleration_widget.py), unabhängig
        # davon, ob der Alarm gerade noch aktiv ist oder schon vorbei.
        self.last_event_ts = None

    def update(self, magnitude, ts=None):
        ts = ts if ts is not None else time.time()

        if self._last_ts is None:
            self._last_ts = ts
            self.sta = magnitude
            self.lta = magnitude
            return self._state(1.0)

        dt = max(ts - self._last_ts, 1e-3)
        self._last_ts = ts

        alpha_sta = 1 - math.exp(-dt / self.sta_tau_s)
        alpha_lta = 1 - math.exp(-dt / self.lta_tau_s)

        self.sta += alpha_sta * (magnitude - self.sta)
        self.lta += alpha_lta * (magnitude - self.lta)

        ratio = self.sta / self.lta if self.lta else 0
        was_triggered = self._triggered_since is not None
        is_active = ratio >= self.trigger_ratio

        if is_active:
            if not was_triggered:
                self.last_event_ts = ts  # steigende Flanke - neues Ereignis
            self._triggered_since = ts
            self.peak_ratio = max(self.peak_ratio, ratio)
        elif self._triggered_since is not None:
            if ts - self._triggered_since < self.release_delay_s:
                is_active = True  # Ausklingphase - Alarm bleibt kurz noch aktiv
            else:
                self._triggered_since = None
                self.peak_ratio = ratio

        return self._state(ratio, is_active)

    def _state(self, ratio, is_active=False):
        return {
            "triggered": is_active,
            "ratio": round(ratio, 2),
            "peak_ratio": round(self.peak_ratio, 2),
            "last_event_ts": self.last_event_ts,
        }
