"""Upgrade A (Phase 6): the trans-Mars injection burn.

When the review board passes the design, the room records the moment and every
client in that room plays the end of the TMI burn before the first scene card.
The universe clock does not run while it plays: the burn is wall time only, like
the review reveal, so Judge Mode pays exactly its length (data/story/launch.yaml).

The delta-v on the engineering display is the design bay's own "Leaving Earth"
figure: the design's C3 as one impulsive burn from a 300 km circular parking
orbit (MODEL). Earth seats play the same moment one light time later.
"""
from __future__ import annotations

import yaml

from ..astro.constants import C_KM_S, R_EARTH_KM
from ..astro.porkchop import PARKING_ALT_KM
from ..astro.timescale import et_to_iso
from ..config import DATA


def load() -> dict:
    return yaml.safe_load((DATA / "story" / "launch.yaml").read_text(encoding="utf-8"))


def timing() -> dict:
    t = load()["timing"]
    out = {k: float(t[k]) for k in ("countdown_s", "silence_s", "settle_s")}
    out["total_s"] = round(out["countdown_s"] + out["silence_s"] + out["settle_s"], 3)
    return out


def build(u, ev: dict) -> dict:
    """The burn as recorded for this room, from the design as it passed review."""
    y = load()
    lt = (R_EARTH_KM + PARKING_ALT_KM) / C_KM_S          # MODEL: still in the parking orbit at cutoff
    return {
        "title": y["title"], "timing": timing(), "cockpit": y["cockpit"], "earth": y["earth"],
        "dv_kms": float(ev.get("dv_tmi") or 0.0), "c3": ev.get("c3"),
        "dv_label": y["dv"]["label"], "dv_model": bool(y["dv"].get("model", True)), "dv_note": y["dv"]["note"],
        "lt_s": round(lt, 4), "lt_note": y["light_time"]["note"],
        "launch_iso": et_to_iso(u.design["launch_et"]),
        # The ship as designed, for the spectator's engine-glow pulse (no telemetry, no faults).
        "vehicle": {**u.design, "crew": u.ship_sys.crew, "habitat_m3": u.ship_sys.cabin_m3, "model": True},
    }
