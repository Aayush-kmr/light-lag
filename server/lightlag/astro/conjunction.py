"""Solar conjunction detection from the Sun-Earth-Probe (SEP) angle.

Below a few degrees of SEP the radio path grazes the solar corona; plasma
turbulence scrambles phase and amplitude ("solar scintillation"). NASA's
Mars missions stop commanding for ~2 weeks around each conjunction (the
"command moratorium"; Oct 2-16 in 2021). We model two thresholds:

* moratorium: SEP < MORATORIUM_DEG -> no commands may be uplinked;
  messages still trickle through with degraded margin;
* blackout:   SEP < BLACKOUT_DEG   -> no reliable link in either direction.
"""
from __future__ import annotations

from dataclasses import dataclass

from .constants import DAY_S
from .timescale import et_to_iso

MORATORIUM_DEG = 3.0
BLACKOUT_DEG = 2.0


@dataclass
class Window:
    start_et: float
    end_et: float
    min_sep_deg: float
    min_et: float

    def as_dict(self) -> dict:
        return {
            "start_et": self.start_et, "end_et": self.end_et,
            "start_iso": et_to_iso(self.start_et), "end_iso": et_to_iso(self.end_et),
            "min_sep_deg": round(self.min_sep_deg, 3), "min_iso": et_to_iso(self.min_et),
            "duration_days": round((self.end_et - self.start_et) / DAY_S, 2),
        }


def _refine(f, a: float, b: float, thresh: float, iters: int = 40) -> float:
    """Bisection for f(t) == thresh on [a, b] where sign changes."""
    fa = f(a) - thresh
    for _ in range(iters):
        m = 0.5 * (a + b)
        fm = f(m) - thresh
        if (fa < 0) == (fm < 0):
            a, fa = m, fm
        else:
            b = m
    return 0.5 * (a + b)


def find_windows(ephem, start_et: float, end_et: float, thresh_deg: float, target: str = "SHIP",
                 step_s: float = 6 * 3600.0) -> list[Window]:
    f = lambda t: ephem.sep_angle_deg(t, target)  # noqa: E731
    out: list[Window] = []
    t, prev = start_et, f(start_et)
    in_win = prev < thresh_deg
    w_start = start_et if in_win else None
    min_v, min_t = (prev, start_et) if in_win else (999.0, start_et)
    while t < end_et:
        t2 = min(t + step_s, end_et)
        v = f(t2)
        if not in_win and v < thresh_deg:
            w_start = _refine(f, t, t2, thresh_deg)
            in_win, min_v, min_t = True, v, t2
        elif in_win:
            if v < min_v:
                min_v, min_t = v, t2
            if v >= thresh_deg:
                out.append(Window(w_start, _refine(f, t, t2, thresh_deg), min_v, min_t))
                in_win = False
        t = t2
    if in_win:
        out.append(Window(w_start, end_et, min_v, min_t))
    return out
