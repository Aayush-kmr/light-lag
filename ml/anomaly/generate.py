"""Synthetic telemetry dataset for the onboard anomaly model.

Uses the exact ship model the game runs (server/lightlag/sim/subsystems.py),
so the detector is trained on the same physics it will watch in play.

Nominal episodes include everything that is NOT a fault: different
heliocentric distances (solar power falls off 1/r^2), operator actions
(science on/off, heater eco, loop swaps, bed swaps), solar proton storms
(dose spikes are environment, not equipment failure), and sensor noise.

Fault types are split into two groups:
  TUNE     - used only to choose the alert persistence on a validation set
  HELD_OUT - never seen in training or tuning; reported separately
The autoencoder itself trains on nominal data only.
"""
from __future__ import annotations

import argparse
import math
import random
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "server"))
from lightlag.sim.subsystems import CHANNELS, Env, ShipSystems, pointing_loss_db  # noqa: E402

TUNE = ["scrubber_valve_stuck", "pump_a_degradation", "bus_short", "hga_drift"]
HELD_OUT = ["array_string_loss", "heater_stuck_on", "sensor_stuck_ppco2", "sensor_drift_loop_b"]
DT = 10.0              # seconds between frames (the game's telemetry cadence)
EP_LEN = 720           # 2 hours per episode


def episode(seed: int, fault: str | None) -> tuple[np.ndarray, np.ndarray, int]:
    rng = random.Random(seed)
    S = ShipSystems(rng=random.Random(seed * 7 + 1))
    r_au = rng.uniform(0.98, 1.55)
    d_au = rng.uniform(0.005, 2.6)
    S.batt_soc = rng.uniform(55, 98)
    S.science_on = rng.random() < 0.8
    S.heaters_eco = rng.random() < 0.2
    S.ppco2 = rng.uniform(2.2, 2.8)
    S.bed_timer = rng.uniform(0, S.BED_CYCLE_S)
    S.bed_sat = rng.uniform(0.05, 0.5)
    if rng.random() < 0.3:
        S.active_loop = "B"
    storm = None
    if fault is None and rng.random() < 0.12:
        t0 = rng.uniform(0.1, 0.6) * EP_LEN * DT
        storm = (t0, rng.uniform(800, 8000), rng.uniform(1800, 5400))
    onset = rng.randint(EP_LEN // 5, EP_LEN // 2) if fault else -1
    # warm-up so state is settled
    for _ in range(90):
        S.step(DT, Env(r_au, 10.0, 76.7))
    X = np.zeros((EP_LEN, len(CHANNELS)), np.float32)
    y = np.zeros(EP_LEN, np.int8)
    for k in range(EP_LEN):
        t = k * DT
        if k == onset:
            S.inject(fault, rng.uniform(0.6, 1.0))
        # nominal operator actions
        if rng.random() < 0.002:
            S.apply("science", {"on": not S.science_on})
        if rng.random() < 0.001:
            S.apply("heaters_eco", {"on": not S.heaters_eco})
        if rng.random() < 0.0008 and fault != "pump_a_degradation":
            S.apply("pump_switch")
        dose = 76.7 * rng.uniform(0.95, 1.05)
        if storm and t > storm[0]:
            dt_s = t - storm[0]
            dose += storm[1] * (min(1, dt_s / 1200) ** 2) * math.exp(-max(0, dt_s - 1200) / storm[2])
        base = min(18.0, 14.0 - 20 * math.log10(d_au))
        env = Env(r_au=r_au, link_margin_db=base - pointing_loss_db(S.hga_err), dose_rate_usv_h=dose)
        S.step(DT, env)
        f = S.frame()
        X[k] = [f[c] for c in CHANNELS]
        y[k] = 1 if (fault and k >= onset) else 0
    return X, y, onset


def build(n_norm: int, n_fault: int, faults: list[str], seed0: int) -> dict:
    eps = []
    for i in range(n_norm):
        X, y, on = episode(seed0 + i, None)
        eps.append({"X": X, "y": y, "fault": "none", "onset": on})
    for j, f in enumerate(faults):
        for i in range(n_fault):
            X, y, on = episode(seed0 + 100_000 + 1000 * j + i, f)
            eps.append({"X": X, "y": y, "fault": f, "onset": on})
    return eps


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "ml" / "anomaly" / "data"))
    ap.add_argument("--quick", action="store_true")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    k = 0.25 if a.quick else 1.0
    splits = {
        "train": build(int(320 * k), 0, [], 1),
        "val": build(int(80 * k), int(20 * k), TUNE, 50_000),
        "test": build(int(160 * k), int(40 * k), TUNE + HELD_OUT, 900_000),
    }
    for name, eps in splits.items():
        np.savez_compressed(out / f"{name}.npz", X=np.stack([e["X"] for e in eps]), y=np.stack([e["y"] for e in eps]),
                            fault=np.array([e["fault"] for e in eps]), onset=np.array([e["onset"] for e in eps]))
        print(name, len(eps), "episodes")


if __name__ == "__main__":
    main()
