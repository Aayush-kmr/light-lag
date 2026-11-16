"""Train the 1D-conv autoencoder, calibrate, evaluate, export to ONNX.

    python ml/anomaly/generate.py          # build the dataset
    python ml/anomaly/train.py             # train + eval + export
Outputs
    data/models/anomaly.onnx + anomaly.json   (runtime model + normalisation + threshold)
    reports/anomaly_eval.json, reports/anomaly_eval.md
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "server"))
sys.path.insert(0, str(Path(__file__).parent))
from generate import DT, HELD_OUT, TUNE  # noqa: E402
from lightlag.sim.subsystems import CHANNELS  # noqa: E402

WIN, TAIL, STRIDE = 64, 16, 4


class ConvAE(nn.Module):
    def __init__(self, c: int = len(CHANNELS), h: int = 32, z: int = 12):
        super().__init__()
        self.enc = nn.Sequential(
            nn.Conv1d(c, h, 5, padding=2), nn.GELU(),
            nn.Conv1d(h, h, 5, stride=2, padding=2), nn.GELU(),
            nn.Conv1d(h, z, 5, stride=2, padding=2), nn.GELU())
        self.dec = nn.Sequential(
            nn.ConvTranspose1d(z, h, 4, stride=2, padding=1), nn.GELU(),
            nn.ConvTranspose1d(h, h, 4, stride=2, padding=1), nn.GELU(),
            nn.Conv1d(h, c, 5, padding=2))

    def forward(self, x):          # x: (B, C, T)
        return self.dec(self.enc(x))


def windows(X: np.ndarray, stride: int) -> np.ndarray:
    T = X.shape[0]
    idx = np.arange(WIN, T + 1, stride)
    return np.stack([X[i - WIN:i] for i in idx]), idx - 1     # (N, WIN, C), index of last frame


def load(name):
    d = np.load(ROOT / "ml" / "anomaly" / "data" / f"{name}.npz")
    return d["X"], d["y"], d["fault"], d["onset"]


@torch.no_grad()
def episode_scores(model, Xn: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    W, last = windows(Xn, 1)
    w = torch.from_numpy(W).permute(0, 2, 1)
    rec = model(w)
    err = ((rec - w) ** 2)[:, :, -TAIL:].mean(dim=(1, 2)).numpy()
    return err, last


def detect(scores: np.ndarray, thr: float, persist: int) -> int | None:
    run = 0
    for i, s in enumerate(scores):
        run = run + 1 if s > thr else 0
        if run >= persist:
            return i
    return None


def all_scores(model, X, mu, sd) -> list:
    return [episode_scores(model, ((X[i] - mu) / sd).astype(np.float32)) for i in range(len(X))]


def evaluate(model, X, y, fault, onset, mu, sd, thr, persist, horizon_frames=360, cache=None) -> dict:
    """Episode-level: a fault episode is a true positive if the first alert is
    at/after onset and within `horizon_frames` (1 h); an alert before onset or
    in a nominal episode is a false positive."""
    per = {}
    for i in range(len(X)):
        sc, last = cache[i] if cache is not None else episode_scores(model, ((X[i] - mu) / sd).astype(np.float32))
        k = detect(sc, thr, persist)
        frame = int(last[k]) if k is not None else None
        f = str(fault[i])
        r = per.setdefault(f, {"n": 0, "tp": 0, "fp": 0, "fn": 0, "latency_min": []})
        r["n"] += 1
        if f == "none":
            r["fp"] += frame is not None
        else:
            on = int(onset[i])
            if frame is not None and frame < on:
                r["fp"] += 1
                r["fn"] += 1
            elif frame is not None and frame - on <= horizon_frames:
                r["tp"] += 1
                r["latency_min"].append((frame - on) * DT / 60)
            else:
                r["fn"] += 1
    out = {}
    for f, r in per.items():
        lat = r.pop("latency_min")
        rec = r["tp"] / max(1, r["tp"] + r["fn"]) if f != "none" else None
        out[f] = {**r, "recall": None if rec is None else round(rec, 3),
                  "median_latency_min": round(float(np.median(lat)), 1) if lat else None,
                  "false_alarm_rate": round(r["fp"] / r["n"], 3)}
    def group(fs):
        tp = sum(out[f]["tp"] for f in fs if f in out)
        fn = sum(out[f]["fn"] for f in fs if f in out)
        fp = sum(out[f]["fp"] for f in fs if f in out) + out.get("none", {}).get("fp", 0)
        return {"precision": round(tp / max(1, tp + fp), 3), "recall": round(tp / max(1, tp + fn), 3), "tp": tp, "fp": fp, "fn": fn}
    return {"per_type": out, "tune_types": group(TUNE), "held_out_types": group(HELD_OUT), "all": group(TUNE + HELD_OUT)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--epochs", type=int, default=12)
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args()
    torch.manual_seed(a.seed)
    np.random.seed(a.seed)
    t0 = time.time()
    Xtr, *_ = load("train")
    flat = Xtr.reshape(-1, Xtr.shape[-1])
    mu, sd = flat.mean(0), flat.std(0) + 1e-3
    W = np.concatenate([windows(((x - mu) / sd).astype(np.float32), STRIDE)[0] for x in Xtr])
    data = torch.from_numpy(W).permute(0, 2, 1).contiguous()
    model = ConvAE()
    n_params = sum(p.numel() for p in model.parameters())
    opt = torch.optim.AdamW(model.parameters(), lr=2e-3, weight_decay=1e-4)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, a.epochs)
    for ep in range(a.epochs):
        perm = torch.randperm(len(data))
        tot = 0.0
        for i in range(0, len(data), 256):
            b = data[perm[i:i + 256]]
            loss = ((model(b) - b) ** 2).mean()
            opt.zero_grad()
            loss.backward()
            opt.step()
            tot += loss.item() * len(b)
        sched.step()
        print(f"epoch {ep + 1}/{a.epochs} loss {tot / len(data):.4f}")
    model.eval()
    # calibrate threshold on nominal validation episodes; tune persistence on TUNE faults
    Xv, yv, fv, ov = load("val")
    vcache = all_scores(model, Xv, mu, sd)
    nominal_scores = np.concatenate([vcache[i][0] for i in range(len(Xv)) if fv[i] == "none"])
    best = None
    for q in (99.5, 99.8, 99.9):
        thr = float(np.percentile(nominal_scores, q))
        for persist in (3, 6, 12):
            r = evaluate(model, Xv, yv, fv, ov, mu, sd, thr, persist, cache=vcache)["tune_types"]
            f1 = 2 * r["precision"] * r["recall"] / max(1e-9, r["precision"] + r["recall"])
            if best is None or f1 > best[0]:
                best = (f1, q, thr, persist)
    _, q, thr, persist = best
    print(f"chosen threshold p{q} = {thr:.4f}, persistence {persist} frames")
    Xt, yt, ft, ot = load("test")
    res = evaluate(model, Xt, yt, ft, ot, mu, sd, thr, persist, cache=all_scores(model, Xt, mu, sd))
    # export
    mdir = ROOT / "data" / "models"
    mdir.mkdir(parents=True, exist_ok=True)
    dummy = torch.zeros(1, len(CHANNELS), WIN)
    torch.onnx.export(model, dummy, str(mdir / "anomaly.onnx"), input_names=["x"], output_names=["rec"],
                      dynamic_axes={"x": {0: "b"}, "rec": {0: "b"}}, opset_version=17)
    meta = {"window": WIN, "tail": TAIL, "threshold": thr, "persist": persist, "mean": mu.tolist(), "std": sd.tolist(),
            "channels": CHANNELS, "params": n_params, "trained_on": "nominal synthetic telemetry only",
            "threshold_rule": f"p{q} of nominal validation window scores"}
    (mdir / "anomaly.json").write_text(json.dumps(meta, indent=1))
    rep = {"model": "Conv1d autoencoder", "params": n_params, "epochs": a.epochs, "train_windows": len(data),
           "threshold": thr, "threshold_quantile": q, "persist_frames": persist, "frame_dt_s": DT,
           "detection_horizon_min": 60, "tune_fault_types": TUNE, "held_out_fault_types": HELD_OUT,
           "results": res, "train_seconds": round(time.time() - t0, 1),
           "caveat": "Synthetic data from the game's own ship model. These numbers say the detector works on "
                     "this simulator, not on spacecraft."}
    rdir = ROOT / "reports"
    rdir.mkdir(exist_ok=True)
    (rdir / "anomaly_eval.json").write_text(json.dumps(rep, indent=1))
    lines = ["# Anomaly model evaluation", "", f"Conv1d autoencoder, {n_params:,} parameters, trained on nominal telemetry only.",
             f"Threshold: p{q} of nominal validation scores; alert after {persist} consecutive frames ({persist * DT:.0f} s).", "",
             "| group | precision | recall | TP | FP | FN |", "|---|---|---|---|---|---|"]
    for g in ("tune_types", "held_out_types", "all"):
        r = res[g]
        lines.append(f"| {g} | {r['precision']} | {r['recall']} | {r['tp']} | {r['fp']} | {r['fn']} |")
    lines += ["", "| fault type | group | n | recall | median latency (min) | false alarms |", "|---|---|---|---|---|---|"]
    for f, r in res["per_type"].items():
        grp = "tune" if f in TUNE else "held-out" if f in HELD_OUT else "nominal"
        lines.append(f"| {f} | {grp} | {r['n']} | {r['recall'] if r['recall'] is not None else '-'} | "
                     f"{r['median_latency_min'] if r['median_latency_min'] is not None else '-'} | {r['fp']} |")
    lines += ["", rep["caveat"]]
    (rdir / "anomaly_eval.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
