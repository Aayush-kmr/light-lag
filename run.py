#!/usr/bin/env python3
"""One command: python run.py            (normal play, DELAY_SCALE 0.1)
               python run.py --judge    (8-minute scripted Judge Mode)
               python run.py --true     (True Delay: real light time, 1x clock)
               python run.py --offline  (never touch the network)
Then open the printed URL. Players join from any device on the LAN."""
import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ap = argparse.ArgumentParser()
ap.add_argument("--judge", action="store_true")
ap.add_argument("--true", dest="true_delay", action="store_true")
ap.add_argument("--offline", action="store_true")
ap.add_argument("--port", default=os.environ.get("LL_PORT") or os.environ.get("PORT") or "8765")  # PORT: Heroku
a = ap.parse_args()
env = dict(os.environ, LL_PORT=str(a.port))
if a.judge:
    env["LL_JUDGE"] = "1"
if a.true_delay:
    env["LL_DELAY_SCALE"] = "1.0"
if a.offline:
    env["LL_OFFLINE"] = "1"
sys.exit(subprocess.call([sys.executable, "-m", "lightlag"], cwd=ROOT / "server", env=env))
