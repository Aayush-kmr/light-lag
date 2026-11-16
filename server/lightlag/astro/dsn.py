"""Deep Space Network geometry: which complex can see Meridian-1, and when.

The DSN has three complexes ~120 deg apart in longitude so that, as Earth
turns, one of them can always see a deep-space target. Visibility alone is
not a contact, though: antenna time is scheduled, and our players buy a
comms plan (hours per day). Gaps between scheduled passes - "handover
gaps" - are when bundles queue.

Station positions: when NAIF's station SPK (earthstns_itrf93_201023.bsp)
and topocentric frame kernel (earth_topo_201023.tf) are loaded, positions
come from SPICE. Otherwise we use the geodetic coordinates below. DSS-14's
ITRF93 XYZ is quoted from the NAIF kernel comments (source: DSN No. 810-005,
301, Rev. M); the other two are approximate geodetic values (~100 m), which
change elevation angles by microdegrees.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from .constants import DAY_S
from .timescale import et_to_iso, et_to_jd_utc

WGS84_A = 6378.137
WGS84_F = 1 / 298.257223563
ELEVATION_MASK_DEG = 10.0


@dataclass(frozen=True)
class Station:
    dss: str
    complex: str
    city: str
    lat_deg: float
    lon_deg: float
    h_km: float
    dish_m: int

    def ecef_km(self) -> np.ndarray:
        lat, lon = math.radians(self.lat_deg), math.radians(self.lon_deg)
        e2 = WGS84_F * (2 - WGS84_F)
        N = WGS84_A / math.sqrt(1 - e2 * math.sin(lat) ** 2)
        return np.array([(N + self.h_km) * math.cos(lat) * math.cos(lon),
                         (N + self.h_km) * math.cos(lat) * math.sin(lon),
                         (N * (1 - e2) + self.h_km) * math.sin(lat)])

    def up_ecef(self) -> np.ndarray:
        lat, lon = math.radians(self.lat_deg), math.radians(self.lon_deg)
        return np.array([math.cos(lat) * math.cos(lon), math.cos(lat) * math.sin(lon), math.sin(lat)])


STATIONS = [
    Station("DSS-14", "Goldstone", "California, USA", 35.42590, -116.88950, 1.002, 70),
    Station("DSS-63", "Madrid", "Robledo de Chavela, Spain", 40.43121, -4.24801, 0.865, 70),
    Station("DSS-43", "Canberra", "Tidbinbilla, Australia", -35.40242, 148.98127, 0.689, 70),
]
# From earthstns_itrf93_201023.cmt (NAIF), metres:
DSS14_ITRF93_M = (-2353621.420, -4641341.472, 3677052.318)

COMMS_PLANS = {
    "lean": {"label": "Lean - 1 pass/day (8 h)", "passes_per_day": 1, "hours": 8, "cost": 1},
    "standard": {"label": "Standard - 2 passes/day (2 x 8 h)", "passes_per_day": 2, "hours": 8, "cost": 2},
    "continuous": {"label": "Continuous - every complex, every hour it can see us", "passes_per_day": 3, "hours": 24, "cost": 4},
}


def _era_rotation(et: float) -> np.ndarray:
    """Earth-fixed -> J2000 using the IAU 2000 Earth Rotation Angle only
    (precession/nutation ignored: <0.4 deg over 2000-2030, fine for masks)."""
    du = et_to_jd_utc(et) - 2451545.0
    theta = 2 * math.pi * ((0.7790572732640 + 1.00273781191135448 * du) % 1.0)
    c, s = math.cos(theta), math.sin(theta)
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])


class DSNModel:
    def __init__(self, ephem):
        self.ephem = ephem
        self._frame = None
        if ephem.backend == "spice":
            import spiceypy as sp
            for fr in ("ITRF93", "IAU_EARTH"):
                try:
                    sp.pxform(fr, "J2000", 0.0)
                    self._frame = fr
                    break
                except Exception:
                    continue

    @property
    def method(self) -> str:
        return f"SPICE {self._frame}" if self._frame else "IAU 2000 Earth Rotation Angle (analytic)"

    def _rot(self, et: float) -> np.ndarray:
        if self._frame:
            import spiceypy as sp
            return np.asarray(sp.pxform(self._frame, "J2000", et))
        return _era_rotation(et)

    def elevation_deg(self, st: Station, et: float, target: str = "SHIP") -> float:
        M = self._rot(et)
        r_st = M @ st.ecef_km()
        up = M @ st.up_ecef()
        rho = self.ephem.pos(target, et, "EARTH") - r_st
        return math.degrees(math.asin(float(np.dot(rho, up) / np.linalg.norm(rho))))

    def visibility(self, start_et: float, end_et: float, step_s: float = 600.0, target: str = "SHIP"):
        """Intervals (per station) with elevation above the mask."""
        ts = np.arange(start_et, end_et + step_s, step_s)
        out = {}
        for st in STATIONS:
            el = np.array([self.elevation_deg(st, float(t), target) for t in ts])
            up = el > ELEVATION_MASK_DEG
            intervals, i = [], 0
            while i < len(ts):
                if up[i]:
                    j = i
                    while j + 1 < len(ts) and up[j + 1]:
                        j += 1
                    k = int(i + np.argmax(el[i:j + 1]))
                    intervals.append({"start": float(ts[i]), "end": float(ts[j]), "max_el": float(el[k]), "culm": float(ts[k])})
                    i = j + 1
                else:
                    i += 1
            out[st.complex] = intervals
        return out

    def schedule(self, start_et: float, end_et: float, plan: str = "standard", target: str = "SHIP") -> list[dict]:
        """Allocate antenna passes per UTC day according to the comms plan."""
        cfg = COMMS_PLANS[plan]
        vis = self.visibility(start_et - DAY_S, end_et + DAY_S, target=target)
        passes = []
        if plan == "continuous":
            for cx, ivs in vis.items():
                passes += [dict(iv, complex=cx) for iv in ivs]
        else:
            day0 = math.floor(start_et / DAY_S) * DAY_S - DAY_S
            d = day0
            rotate = 0
            while d < end_et + DAY_S:
                cands = []
                for cx, ivs in vis.items():
                    for iv in ivs:
                        if d <= iv["culm"] < d + DAY_S:
                            cands.append(dict(iv, complex=cx))
                cands.sort(key=lambda c: -c["max_el"])
                # rotate which complexes we favour so the lean plan doesn't always pick one site
                if cands and cfg["passes_per_day"] == 1:
                    cands = cands[rotate % len(cands):] + cands[:rotate % len(cands)]
                    rotate += 1
                for c in cands[: cfg["passes_per_day"]]:
                    half = cfg["hours"] * 3600 / 2
                    s, e = max(c["start"], c["culm"] - half), min(c["end"], c["culm"] + half)
                    passes.append({"complex": c["complex"], "start": s, "end": e, "max_el": c["max_el"], "culm": c["culm"]})
                d += DAY_S
        passes = [p for p in passes if p["end"] > start_et and p["start"] < end_et]
        passes.sort(key=lambda p: p["start"])
        for p in passes:
            p["start_iso"], p["end_iso"] = et_to_iso(p["start"]), et_to_iso(p["end"])
            p["max_el"] = round(p["max_el"], 1)
        return passes


def merge_intervals(passes: list[dict]) -> list[tuple[float, float]]:
    iv = sorted((p["start"], p["end"]) for p in passes)
    out: list[list[float]] = []
    for s, e in iv:
        if out and s <= out[-1][1]:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return [(a, b) for a, b in out]
