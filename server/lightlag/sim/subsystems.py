"""Meridian-1 ship systems: a deliberately simple, physically-motivated model.

The same model drives the live game AND generates the anomaly model's
training data (ml/anomaly/generate.py), so what the detector learned is
exactly what it sees in play.

Channels (units):
    bus_v      main bus voltage (V)          bus_a     bus current (A)
    batt_soc   battery state of charge (%)   solar_kw  array output (kW)
    loop_a_c   coolant loop A temp (C)       loop_b_c  coolant loop B temp (C)
    cabin_c    cabin air temp (C)            ppco2     CO2 partial pressure (mmHg)
    scrub_dp   scrubber bed pressure drop (kPa)
    link_db    X-band link margin (dB)       dose_rate dose-equivalent rate (uSv/h)

Every number below is an engineering assumption chosen to be plausible and
to give the right *time scales* (minutes for antenna drift, hours for CO2).
None of it is flight data, except the crew's CO2 output (NASA BVAD), which
with four crew in 168 m^3 sets the CO2 build-up rate. See docs/science.md.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

CHANNELS = ["bus_v", "bus_a", "batt_soc", "solar_kw", "loop_a_c", "loop_b_c", "cabin_c",
            "ppco2", "scrub_dp", "link_db", "dose_rate"]

UNITS = {"bus_v": "V", "bus_a": "A", "batt_soc": "%", "solar_kw": "kW", "loop_a_c": "C", "loop_b_c": "C",
         "cabin_c": "C", "ppco2": "mmHg", "scrub_dp": "kPa", "link_db": "dB", "dose_rate": "uSv/h"}

LABELS = {"bus_v": "Main bus voltage", "bus_a": "Main bus current", "batt_soc": "Battery charge",
          "solar_kw": "Solar array output", "loop_a_c": "Coolant loop A", "loop_b_c": "Coolant loop B",
          "cabin_c": "Cabin temperature", "ppco2": "Cabin ppCO2", "scrub_dp": "Scrubber pressure drop",
          "link_db": "Link margin", "dose_rate": "Dose rate"}

# caution / warning limits used by alarms and by the autonomy policy
LIMITS = {
    "ppco2": {"caution": 4.0, "warning": 6.0, "emergency": 8.0},
    "batt_soc": {"caution": 40.0, "warning": 25.0, "emergency": 12.0},
    "loop_a_c": {"caution": 12.0, "warning": 18.0, "emergency": 25.0},
    "loop_b_c": {"caution": 12.0, "warning": 18.0, "emergency": 25.0},
    "link_db": {"caution": 3.0, "warning": 0.0, "emergency": -6.0},
    "dose_rate": {"caution": 300.0, "warning": 1000.0, "emergency": 5000.0},
    "cabin_c": {"caution": 26.0, "warning": 29.0, "emergency": 32.0},
}

def pointing_loss_db(err_deg: float) -> float:
    """Gaussian-beam approximation: 0.6 deg off-axis costs ~12 dB."""
    return 12.0 * (err_deg / 0.6) ** 2


FAULTS = {
    "scrubber_valve_stuck": "CO2 scrubber bed-swap valve stuck; active bed saturates",
    "pump_a_degradation": "Coolant pump A impeller wear; loop A runs hot",
    "bus_short": "Intermittent short on a load branch; voltage sag, current spikes",
    "array_string_loss": "Solar array string failure; ~18% output lost",
    "hga_drift": "High-gain antenna gimbal drift; pointing error grows",
    "heater_stuck_on": "Line heater relay welded closed; extra load and heat",
    "sensor_stuck_ppco2": "ppCO2 sensor frozen at last value",
    "sensor_drift_loop_b": "Loop B temperature sensor bias drifting",
}


@dataclass
class Env:
    r_au: float = 1.0               # heliocentric distance
    link_margin_db: float = 10.0    # from the contact plan (geometry part)
    dose_rate_usv_h: float = 76.7   # radiation model; 1.84 mSv/day of GCR = 76.7 uSv/h
    in_shelter: bool = False


@dataclass
class ShipSystems:
    rng: random.Random = field(default_factory=lambda: random.Random(7))
    # state
    batt_soc: float = 92.0
    loop_a: float = 4.0
    loop_b: float = 4.5
    cabin: float = 21.8
    ppco2: float = 2.4
    bed_sat: float = 0.1            # 0 fresh .. 1 saturated
    bed_timer: float = 0.0
    pump_a_health: float = 1.0
    active_loop: str = "A"
    science_on: bool = True
    heaters_eco: bool = False
    hga_err: float = 0.05           # deg
    array_health: float = 1.0
    faults: dict = field(default_factory=dict)   # name -> {"t": seconds active, "sev": 0..1}
    stuck_values: dict = field(default_factory=dict)
    drift_bias: float = 0.0
    last: dict = field(default_factory=dict)
    # crew and cabin (data/catalog/crew.yaml): CO2 build-up follows from these
    crew: int = 4
    cabin_m3: float = 168.0
    co2_kg_cm_day: float = 1.04         # NASA BVAD
    # power hardware, set from the design at launch (Universe.apply_design)
    batt_kwh: float = 60.0
    array_kw_1au: float = 32.0
    string_loss_frac: float = 0.18      # MODEL: share of output one failed string takes (per array, catalog)

    BED_CYCLE_S = 2 * 3600.0
    CABIN_T_K = 295.0

    def co2_gen_mmhg_s(self) -> float:
        """CO2 partial-pressure rise with no scrubbing: ideal gas, n*R*T/V.
        Four crew at 1.04 kg/day each in 168 m^3 gives ~0.43 mmHg per hour."""
        mol_s = self.crew * self.co2_kg_cm_day / 0.04401 / 86400.0
        return mol_s * 8.314 * self.CABIN_T_K / self.cabin_m3 / 133.322

    # ------------------------------------------------------------- faults
    def inject(self, name: str, severity: float = 1.0) -> None:
        if name not in FAULTS:
            raise KeyError(name)
        self.faults[name] = {"t": 0.0, "sev": max(0.1, min(1.0, severity))}
        if name == "sensor_stuck_ppco2":
            self.stuck_values["ppco2"] = self.last.get("ppco2", self.ppco2)

    def clear(self, name: str) -> None:
        self.faults.pop(name, None)
        self.stuck_values.pop("ppco2", None) if name == "sensor_stuck_ppco2" else None
        if name == "sensor_drift_loop_b":
            self.drift_bias = 0.0

    # ------------------------------------------------------------ actions
    def apply(self, action: str, args: dict | None = None) -> str:
        args = args or {}
        if action == "scrubber_swap_bed":
            if "scrubber_valve_stuck" in self.faults:
                # manual override works: crew cranks the valve by hand
                self.clear("scrubber_valve_stuck")
            self.bed_sat, self.bed_timer = 0.05, 0.0
            return "Scrubber bed swapped; fresh bed online."
        if action == "pump_switch":
            self.active_loop = "B" if self.active_loop == "A" else "A"
            return f"Coolant loop {self.active_loop} now active."
        if action == "science":
            self.science_on = bool(args.get("on", not self.science_on))
            return f"Science payload {'ON' if self.science_on else 'OFF'}."
        if action == "heaters_eco":
            self.heaters_eco = bool(args.get("on", not self.heaters_eco))
            return f"Heater eco mode {'ON' if self.heaters_eco else 'OFF'}."
        if action == "hga_trim":
            dx = float(args.get("dx", 0.0))
            self.hga_err = max(0.02, abs(self.hga_err - dx) if dx else self.hga_err * 0.4)
            if self.hga_err < 0.1 and "hga_drift" in self.faults:
                self.faults["hga_drift"]["t"] = 0.0
            return f"HGA trimmed; pointing error {self.hga_err:.2f} deg."
        if action == "hga_lock":
            self.clear("hga_drift")
            self.hga_err = 0.05
            return "HGA gimbal brake engaged; drift stopped."
        if action == "breaker_reset":
            self.clear("bus_short")
            return "Load branch breaker cycled; short isolated."
        if action == "heater_relay_open":
            self.clear("heater_stuck_on")
            return "Heater relay opened manually."
        if action == "sensor_recal":
            self.clear("sensor_stuck_ppco2")
            self.clear("sensor_drift_loop_b")
            return "Sensors recalibrated."
        return f"Unknown action {action}"

    # --------------------------------------------------------------- step
    def step(self, dt: float, env: Env) -> None:
        for f in self.faults.values():
            f["t"] += dt
        F = self.faults
        # power ---------------------------------------------------------
        array = self.array_health * ((1.0 - self.string_loss_frac) if "array_string_loss" in F else 1.0)
        solar = self.array_kw_1au / max(env.r_au, 0.3) ** 2 * array
        heater = (1.0 + 2.2 * max(0.0, env.r_au - 1.0)) * (0.6 if self.heaters_eco else 1.0)
        load = 6.0 + 0.6 + 0.3 + heater + (3.2 if self.science_on else 0.0) + 0.5
        if "heater_stuck_on" in F:
            load += 1.6
        if "bus_short" in F:
            load += 2.5 * F["bus_short"]["sev"] * (1.0 if self.rng.random() < 0.6 else 0.2)
        net = solar - load
        self.batt_soc = min(100.0, max(0.0, self.batt_soc + net * dt / 3600.0 / self.batt_kwh * 100.0))
        if self.batt_soc >= 99.9 and net > 0:
            solar = load  # shunt regulator sheds excess
        self._solar, self._load = solar, load
        # thermal -------------------------------------------------------
        heat = load * 0.9
        wear = F.get("pump_a_degradation")
        if wear:
            self.pump_a_health = max(0.15, 1.0 - wear["sev"] * wear["t"] / 2400.0)
        h_active = self.pump_a_health if self.active_loop == "A" else 1.0
        target_active = 4.0 + 0.25 * (heat - 12.0) + (1.0 - h_active) * 22.0 + (2.5 if "heater_stuck_on" in F else 0)
        target_idle = 10.0  # a stagnant loop drifts toward equipment-bay temperature
        tau = 900.0
        if self.active_loop == "A":
            self.loop_a += (target_active - self.loop_a) * min(1.0, dt / tau)
            self.loop_b += (target_idle - self.loop_b) * min(1.0, dt / (tau * 4))
        else:
            self.loop_b += (target_active - self.loop_b) * min(1.0, dt / tau)
            self.loop_a += (target_idle - self.loop_a) * min(1.0, dt / (tau * 4))
        active_t = self.loop_a if self.active_loop == "A" else self.loop_b
        cabin_target = 21.5 + 0.35 * (active_t - 4.0)
        self.cabin += (cabin_target - self.cabin) * min(1.0, dt / 2400.0)
        # CO2 -----------------------------------------------------------
        gen = self.co2_gen_mmhg_s()                      # mmHg/s from the crew's breathing
        self.bed_timer += dt
        if self.bed_timer >= self.BED_CYCLE_S and "scrubber_valve_stuck" not in F:
            self.bed_timer, self.bed_sat = 0.0, 0.05     # automatic swap
        self.bed_sat = min(1.0, self.bed_sat + dt / (self.BED_CYCLE_S * (1.6 if "scrubber_valve_stuck" not in F else 0.9)))
        eff = max(0.0, 1.0 - self.bed_sat ** 3)
        removal = (gen / 2.4) * eff * self.ppco2         # scrubbers sized to hold ~2.4 mmHg
        self.ppco2 = max(0.2, self.ppco2 + (gen - removal) * dt)
        # antenna -------------------------------------------------------
        if "hga_drift" in F:
            self.hga_err += 0.0012 * F["hga_drift"]["sev"] * dt
        self.hga_err = min(self.hga_err, 3.0)
        if "sensor_drift_loop_b" in F:
            self.drift_bias += 0.0009 * dt
        self._env = env

    # ------------------------------------------------------------- frame
    def frame(self) -> dict:
        n = self.rng.gauss
        env = getattr(self, "_env", Env())
        solar, load = getattr(self, "_solar", 30.0), getattr(self, "_load", 11.0)
        sag = 6.0 * self.faults["bus_short"]["sev"] if "bus_short" in self.faults and self.rng.random() < 0.6 else 0.0
        bus_v = 112.0 + 8.0 * self.batt_soc / 100.0 - sag + n(0, 0.15)
        bus_a = load * 1000.0 / max(bus_v, 50.0) + n(0, 0.4) + (25.0 if sag else 0.0)
        link = env.link_margin_db + n(0, 0.25)   # pointing loss already included by the contact plan
        dose = env.dose_rate_usv_h * (1.0 + n(0, 0.05))
        bed_dp = 1.2 + 0.9 * self.bed_sat ** 2 + n(0, 0.02)
        f = {
            "bus_v": bus_v, "bus_a": bus_a, "batt_soc": self.batt_soc + n(0, 0.05),
            "solar_kw": solar + n(0, 0.08), "loop_a_c": self.loop_a + n(0, 0.06),
            "loop_b_c": self.loop_b + self.drift_bias + n(0, 0.06), "cabin_c": self.cabin + n(0, 0.04),
            "ppco2": self.ppco2 + n(0, 0.03), "scrub_dp": bed_dp, "link_db": link, "dose_rate": max(0.0, dose),
        }
        if "ppco2" in self.stuck_values:
            f["ppco2"] = self.stuck_values["ppco2"]
        self.last = f
        return {k: round(v, 3) for k, v in f.items()}

    def modes(self) -> dict:
        return {"active_loop": self.active_loop, "science_on": self.science_on, "heaters_eco": self.heaters_eco,
                "bed_sat": round(self.bed_sat, 2), "hga_err_deg": round(self.hga_err, 3),
                "pump_a_health": round(self.pump_a_health, 2)}

    def alarms(self, frame: dict) -> list[dict]:
        out = []
        for ch, lim in LIMITS.items():
            v = frame.get(ch)
            if v is None:
                continue
            low_bad = ch in ("batt_soc", "link_db")
            for level in ("emergency", "warning", "caution"):
                thr = lim[level]
                if (v <= thr) if low_bad else (v >= thr):
                    out.append({"channel": ch, "level": level, "value": v, "limit": thr})
                    break
        return out
