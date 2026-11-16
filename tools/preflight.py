"""Demo-day preflight: one screen that says whether the demo will work offline.

    python -m tools.preflight
Checks kernels, DONKI cache, DSN Now snapshot, anomaly model, local LLMs,
whisper.cpp / Piper, and whether the machine can reach the internet
("airplane mode" should be TRUE for the offline proof).
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))

from lightlag.ai.llm import LocalLLM, airplane_check  # noqa: E402
from lightlag.config import CONFIG  # noqa: E402


def line(ok, what, detail=""):
    print(f"  [{'OK' if ok else '--'}] {what:38s} {detail}")


def main() -> None:
    print("\nLIGHT-LAG preflight\n")
    kd = CONFIG.kernel_dir
    for f in ("de440s.bsp", "naif0012.tls", "pck00011.tpc", "earthstns_itrf93_201023.bsp", "earth_topo_201023.tf"):
        line((kd / f).exists(), f"kernel {f}", "" if (kd / f).exists() else "python -m tools.fetch_kernels")
    don = list((CONFIG.cache_dir / "donki").glob("*.json"))
    line(bool(don), "DONKI cache", f"{len(don)} files" if don else "python -m tools.fetch_donki (else literature reference)")
    dsn = CONFIG.cache_dir / "dsn_now" / "latest.xml"
    line(dsn.exists(), "DSN Now snapshot", "" if dsn.exists() else "python -m tools.fetch_dsn_now")
    line(CONFIG.anomaly_model.exists(), "anomaly model (ONNX)", "" if CONFIG.anomaly_model.exists() else "make anomaly")
    for name, url, model in (("TERN", CONFIG.tern_url, CONFIG.tern_model), ("FIDO", CONFIG.fido_url, CONFIG.fido_model)):
        try:
            ok = LocalLLM(url, model, 3.0).available()
        except Exception as e:
            ok = False
        line(ok, f"{name} local LLM {model}", url if ok else f"ollama pull {model}  (fallback works without it)")
    line(bool(shutil.which(CONFIG.whisper_bin)) and Path(CONFIG.whisper_model).exists(), "whisper.cpp STT", CONFIG.whisper_bin)
    line(bool(shutil.which(CONFIG.piper_bin)), "Piper TTS", CONFIG.piper_bin)
    line(bool(shutil.which("ffmpeg")), "ffmpeg")
    a = airplane_check()
    line(a["airplane_mode"], "airplane mode (no internet)", "internet reachable: unplug for the offline proof" if not a["airplane_mode"] else "offline - good")
    print()


if __name__ == "__main__":
    main()
