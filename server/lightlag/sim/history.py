"""A small, bounded time series of the flight, for the debrief.

Sampled at scene boundaries, at the start and end of every time skip, and every
few game-hours inside a scene. A few hundred points at most: the debrief draws
sparklines and one timeline from it, nothing more.
"""
from __future__ import annotations

from collections import deque

from ..astro.constants import DAY_S

EVERY_S = 3 * 3600.0       # MODEL: one sample every 3 game-hours inside a scene
MAX_POINTS = 400


class History:
    def __init__(self, u):
        self.u = u
        self.points: deque = deque(maxlen=MAX_POINTS)
        self._next = -1e30

    def sample(self, why: str = "tick") -> None:
        u = self.u
        if "launch" not in u.anchors:
            return
        f = u.frame or {}
        sci = u.science.totals()
        crew = {m.id: [round(m.stress, 3), round(m.morale, 3)] for m in u.crew.members.values()}
        lt = u.lt("up") if u.ephem.ship is not None else 0.0
        self.points.append({
            "why": why, "et": round(u.et, 1), "day": round((u.et - u.anchors["launch"]) / DAY_S, 3),
            "scene": u.director.scene["id"] if u.director.scene else None, "lt_s": round(lt, 2),
            "crew": crew, "dose_msv": round(u.rad.total_msv, 3), "storm_msv": round(u.rad.sep_msv, 3),
            "batt_soc": round(float(f.get("batt_soc", u.ship_sys.batt_soc)), 2),
            "recorder_frac": round(sci["stored_mbit"] / 160_000.0, 4),
            "delivered_gbit": round(sci["delivered_mbit"] / 1000.0, 3),
            "link_db": round(float(f.get("link_db", 0.0)), 2) if f else None})
        self._next = u.et + EVERY_S

    def on_frame(self) -> None:
        if self.u.et >= self._next:
            self.sample("tick")

    def public(self) -> list[dict]:
        return list(self.points)
