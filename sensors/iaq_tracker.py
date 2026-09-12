"""
IAQ-Berechnung (Luftqualitäts-Score aus Gaswiderstand + Luftfeuchte),
1:1 übernommen aus main.py der T-Display-S3-Umweltstation (dort:
`IAQTracker`) - damit beide Projekte sich identisch verhalten und die
gleichen config.json-Begriffe ("baseline_mode", "burn_in_readings",
"tau_up_h", "tau_down_h") verwenden.

Reines Python ohne Hardware-Abhängigkeit - deshalb komplett ohne Tab5
testbar (siehe tools/sim_test.py).

Zwei Baseline-Modi:
  "fixed"   - klassisch: sammelt einmalig burn_in_readings Gas-Messungen
              nach dem Boot, mittelt die obere Hälfte davon, und nutzt das
              danach dauerhaft als "gute Luft"-Referenz.
  "rolling" - die Baseline passt sich weiter an: steigt schnell, wenn
              sauberere Luft gemessen wird (tau_up_h), und "vergisst"
              alte gute Luft langsam wieder (tau_down_h) - ähnlich wie
              Boschs eigenes BSEC seine Baseline laufend neu lernt, und
              verzeiht eine unglückliche erste Kalibrierung.
"""


class IAQTracker:
    def __init__(self, mode="fixed", hum_baseline=40.0, hum_weight=0.25,
                 burn_in_readings=10, tau_up_h=1.0, tau_down_h=24.0,
                 sample_interval_s=30.0):
        self.mode = mode
        self.hum_baseline = hum_baseline
        self.hum_weight = hum_weight
        self.burn_in_readings = burn_in_readings
        self.sample_interval_s = sample_interval_s

        tau_up_s = tau_up_h * 3600.0
        tau_down_s = tau_down_h * 3600.0
        self.alpha_up = sample_interval_s / (tau_up_s + sample_interval_s)
        self.alpha_down = sample_interval_s / (tau_down_s + sample_interval_s)
        # Anzahl Messungen, die "ein volles Gedächtnisfenster" darstellen -
        # für die Kalibrierungs-Konfidenz in Prozent.
        self.confidence_target = max(1, int(tau_down_s / sample_interval_s))

        self.gas_readings = []
        self.gas_baseline = None
        self.readings_count = 0

    def reset(self):
        """Kalibrierung manuell neu starten (z.B. nach dem Lüften des Raums)."""
        self.gas_readings = []
        self.gas_baseline = None
        self.readings_count = 0

    def update(self, gas_ohm, humidity):
        self.readings_count += 1

        if self.mode == "rolling":
            if self.gas_baseline is None:
                self.gas_baseline = gas_ohm  # Baseline mit erster Messung starten
            elif gas_ohm > self.gas_baseline:
                self.gas_baseline += self.alpha_up * (gas_ohm - self.gas_baseline)
            else:
                self.gas_baseline += self.alpha_down * (gas_ohm - self.gas_baseline)
            if self.readings_count < self.burn_in_readings:
                return None  # kurze Aufwärmphase, bevor der erste Score kommt
        else:  # "fixed"
            if self.gas_baseline is None:
                self.gas_readings.append(gas_ohm)
                if len(self.gas_readings) >= self.burn_in_readings:
                    sorted_vals = sorted(self.gas_readings)
                    top_half = sorted_vals[len(sorted_vals) // 2:]
                    self.gas_baseline = sum(top_half) / len(top_half)
                return None

        hum_offset = humidity - self.hum_baseline
        hum_weight_pct = self.hum_weight * 100
        if hum_offset > 0:
            hum_score = (100 - self.hum_baseline - hum_offset) / (100 - self.hum_baseline) * hum_weight_pct
        else:
            hum_score = (self.hum_baseline + hum_offset) / self.hum_baseline * hum_weight_pct
        hum_score = max(0.0, min(hum_score, hum_weight_pct))

        gas_weight_pct = 100 - hum_weight_pct
        if gas_ohm >= self.gas_baseline:
            gas_score = gas_weight_pct
        else:
            gas_score = (gas_ohm / self.gas_baseline) * gas_weight_pct
        gas_score = max(0.0, min(gas_score, gas_weight_pct))

        return round(hum_score + gas_score, 1)

    def confidence(self):
        """Grobe 0-100%-Angabe, wie vertrauenswürdig die aktuelle Baseline ist."""
        if self.mode == "fixed":
            if self.gas_baseline is not None:
                return 100.0
            return round(100.0 * len(self.gas_readings) / self.burn_in_readings, 1)
        else:
            return round(100.0 * min(self.readings_count, self.confidence_target) / self.confidence_target, 1)
