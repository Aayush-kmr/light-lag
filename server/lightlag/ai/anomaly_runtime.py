"""Onboard anomaly detection, run in real time on the ship machine.

Primary: a 1D-convolutional autoencoder trained on nominal telemetry
(ml/anomaly/), exported to ONNX and executed with onnxruntime. The score
is reconstruction error over a sliding window; above the calibrated
threshold for `persist` consecutive frames -> alert.

Fallback (if no model file or no onnxruntime): a robust z-score detector
against fixed nominal statistics. The UI labels which detector is live, and
the model card reports numbers only for the trained model.

Design rule from the brief: the model DECIDES when to alert; the LLM only
EXPLAINS the alert using tool results (top contributing channels).
"""
from __future__ import annotations

import json
from collections import deque
from pathlib import Path

import numpy as np

from ..sim.subsystems import CHANNELS


class AnomalyRuntime:
    def __init__(self, model_path: Path, persist: int = 3):
        self.model_path = Path(model_path)
        self.meta_path = self.model_path.with_suffix(".json")
        self.persist = persist
        self.session = None
        self.meta = None
        self.kind = "z-score fallback"
        self.window = 64
        self.buf: deque = deque(maxlen=self.window)
        self.score = 0.0
        self.threshold = 1.0
        self.streak = 0
        self.alerting = False
        self.contrib: dict[str, float] = {}
        self._load()

    def _load(self) -> None:
        if not (self.model_path.exists() and self.meta_path.exists()):
            return
        try:
            import onnxruntime as ort
            self.meta = json.loads(self.meta_path.read_text())
            self.session = ort.InferenceSession(str(self.model_path), providers=["CPUExecutionProvider"])
            self.window = int(self.meta["window"])
            self.threshold = float(self.meta["threshold"])
            self.persist = int(self.meta.get("persist", self.persist))
            self.tail = int(self.meta.get("tail", 16))
            self.buf = deque(maxlen=self.window)
            self.kind = f"conv1d autoencoder (ONNX, {self.meta.get('params', '?')} params)"
        except Exception:  # pragma: no cover
            self.session = None

    def _norm(self, frame: dict) -> np.ndarray:
        if self.meta:
            mu = np.array(self.meta["mean"])
            sd = np.array(self.meta["std"])
        else:
            mu = np.array([120.0, 95.0, 70.0, 25.0, 4.0, 10.0, 21.8, 2.6, 1.4, 10.0, 80.0])
            sd = np.array([2.0, 15.0, 25.0, 8.0, 1.5, 1.5, 0.6, 0.6, 0.25, 6.0, 30.0])
        x = np.array([frame[c] for c in CHANNELS], dtype=np.float32)
        return ((x - mu) / sd).astype(np.float32)

    def update(self, frame: dict) -> dict:
        x = self._norm(frame)
        self.buf.append(x)
        rising = False
        if self.session is not None:
            if len(self.buf) == self.window:
                w = np.stack(self.buf)[None].transpose(0, 2, 1).astype(np.float32)   # (1, C, T)
                (rec,) = self.session.run(None, {"x": w})
                err = (rec - w) ** 2                                                 # (1, C, T)
                tail = err[0, :, -getattr(self, "tail", 16):]                        # most recent frames
                per_ch = tail.mean(axis=1)
                self.score = float(per_ch.mean())
                self.contrib = {c: float(v) for c, v in zip(CHANNELS, per_ch)}
        else:
            z = np.abs(x)
            self.score = float(np.sort(z)[-2:].mean()) / 4.0   # two worst channels, scaled so 1.0 ~ 4 sigma
            self.contrib = {c: float(v) for c, v in zip(CHANNELS, z)}
        if self.score > self.threshold:
            self.streak += 1
        else:
            self.streak = 0
            self.alerting = False
        if self.streak >= self.persist and not self.alerting:
            self.alerting, rising = True, True
        return {"score": round(self.score, 4), "threshold": self.threshold, "alert": self.alerting, "rising": rising,
                "top": sorted(self.contrib, key=self.contrib.get, reverse=True)[:3], "detector": self.kind}
