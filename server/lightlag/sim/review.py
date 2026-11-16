"""Flight Readiness Review: a board of five that stands between design and launch.

Each reviewer is a deterministic rule set over the design bay's evaluation
(`Universe.design_eval()`, built by sim/design.py) plus three numbers the bay
does not compute: the degraded power case with the payload shed (also from the
flight model, in design.py), the projected dose from the scenario's reference
storm, and the projected cosmic-ray dose. Thresholds and finding text live in
data/review_board.yaml; every threshold carries a source or a MODEL flag.

A finding is `pass`, `lien` or `fail`. An area takes its worst finding. Any
`fail` fails the review: the team goes back to the bay and the launch date
slips. Liens fly with the ship (`Universe.liens`) and come back in flight.
No language model is involved anywhere in this file.
"""
from __future__ import annotations

import math
import string
from functools import lru_cache

import yaml

from ..astro.constants import DAY_S
from ..astro.timescale import et_to_iso, utc_to_et
from ..config import DATA
from .radiation import CAREER_LIMIT_MSV, GCR_MSV_PER_DAY

RANK = {"pass": 0, "lien": 1, "fail": 2}


class _Safe(dict):
    def __missing__(self, k):
        return "{" + k + "}"


@lru_cache(maxsize=1)
def board() -> dict:
    return yaml.safe_load((DATA / "review_board.yaml").read_text(encoding="utf-8"))


def timing(judge: bool) -> dict:
    t = board()["timing"]
    verdict = float(t["judge_verdict_s"] if judge else t["verdict_s"])
    areas = len(board()["areas"])
    total = float(t["dim_s"]) + areas * float(t["area_s"]) + verdict
    return {"dim_s": float(t["dim_s"]), "area_s": float(t["area_s"]), "verdict_s": verdict, "total_s": total}


def _fmt(tpl: str, **v) -> str:
    return string.Formatter().vformat(tpl, (), _Safe(v))


def pct(x: float) -> str:
    return f"{round(x * 100)}%"


# ------------------------------------------------------------------ extras
def storm_estimate(u, ev: dict) -> dict:
    """Projected dose from the scenario's reference storm for this design (MODEL).

    The storm timeline needs the ship's position on the storm date, so the board
    flies the chosen transfer analytically for a moment (no SPK, no side effects)
    and integrates the storm's own dose profile: a quadratic rise from onset to
    peak, then exponential decay (sim/space_weather.py). The crew is assumed to be
    in the shelter for the whole storm."""
    from ..astro.trajectory import ShipTrajectory
    st = u.scenario.get("storm") or {}
    sev = u.sw.find(st.get("event", "")) if st else None
    liner, shelter = float(u.design["hull_liner_gcm2"]), float(u.design["shelter_gcm2"])
    if sev is None:
        return {"available": False, "raw_msv": 0.0, "in_shelter_msv": 0.0, "outside_msv": 0.0}
    key = (round(u.design["launch_et"]), round(u.design["arrival_et"]))
    cache = getattr(u, "_storm_est", None)
    if not cache or cache[0] != key:
        traj = ShipTrajectory.from_dates(u.ephem, u.design["launch_et"], u.design["arrival_et"])
        tl = u.sw.timeline(_WithShip(u.ephem, traj), sev, st.get("at_utc"))
        rise_h = max(0.0, tl.t_peak_ship - tl.t_onset_ship) / 3600.0
        raw = tl.peak_msv_h * (rise_h / 3.0 + tl.tau_decay_s / 3600.0)
        cache = (key, raw)
        u._storm_est = cache
    raw = cache[1]
    return {"available": True, "raw_msv": raw, "in_shelter_msv": raw * math.exp(-(liner + shelter) / 12.0),
            "outside_msv": raw * math.exp(-liner / 12.0), "event": st.get("label", "")}


class _WithShip:
    """The ephemeris, with the ship flown analytically on a candidate transfer
    (no SPK written, nothing loaded): the review never touches the real ship."""

    def __init__(self, ephem, traj):
        self._e, self._traj = ephem, traj

    def pos(self, body: str, et: float, center: str = "SUN"):
        if body.upper() != "SHIP":
            return self._e.pos(body, et, center)
        from ..astro.kepler import planet_state_j2000
        t = self._traj
        if et < t.launch_et:
            return self._e.pos("EARTH", et, center)
        if et < t.arrival_et:
            r = t.helio_cruise_state(et)[0]
        else:
            r = planet_state_j2000("MARS", et)[0] + t.mars_relative_state(et)[0]
        return r if center.upper() == "SUN" else r - self._e.pos(center, et)

    def __getattr__(self, name):
        return getattr(self._e, name)


def gcr_estimate(u, ev: dict) -> dict:
    days = float(ev["cost"]["mission_days"])
    factor = float(ev.get("gcr_factor", 1.0))
    return {"days": round(days), "msv": GCR_MSV_PER_DAY * factor * days}


def window_days_left(u) -> float:
    end = utc_to_et(u.scenario["design"]["launch_window"][1])
    return (end - u.design["launch_et"]) / DAY_S


# ------------------------------------------------------------------ the board
def _finding(spec: dict, status: str, number, unit: str, threshold: str, **vars) -> dict:
    tpl = spec.get(status) or spec.get("pass", "")
    return {"id": spec["id"], "label": spec["label"], "status": status, "number": number, "unit": unit,
            "threshold": threshold, "text": _fmt(tpl, **vars), "model": bool(spec.get("model")),
            "source": spec.get("source"), "sources": spec.get("sources") or ([spec["source"]] if spec.get("source") else []),
            "note": spec.get("note", "")}


def _specs(area_id: str) -> dict:
    a = next(x for x in board()["areas"] if x["id"] == area_id)
    return {f["id"]: f for f in a["findings"]}


def _launch(u, ev):
    F, out = _specs("launch"), []
    b, camp, mass = ev["budgets"]["mass"], ev["campaign"], ev["mass"]
    s = F["mass_margin"]
    out.append(_finding(s, "lien" if b["margin"] < s["lien_below"] else "pass", round(b["margin"] * 100, 1), "%",
                        f"lien below {pct(s['lien_below'])}", value=pct(b["margin"]), limit=f"{b['limit']:.0f}",
                        launches_limit=camp["limit"]))
    s = F["launches"]
    out.append(_finding(s, "lien" if camp["launches"] >= s["lien_at"] else "pass", camp["launches"], "launches",
                        f"lien at {s['lien_at']} of {camp['limit']}", value=camp["launches"], limit=camp["limit"]))
    s = F["c3"]
    frac = mass["imleo_t"] / max(camp["max_imleo_t"], 1e-6)
    out.append(_finding(s, "lien" if frac > s["lien_above_frac"] else "pass", ev.get("c3"), "km²/s²",
                        f"lien above {pct(s['lien_above_frac'])} of what you can afford to lift",
                        value=f"{ev.get('c3', 0):.1f}", imleo=f"{mass['imleo_t']:.0f}", max_imleo=f"{camp['max_imleo_t']:.0f}"))
    return out


def _power(u, ev):
    F, out, p = _specs("power"), [], ev["power"]
    s = F["power_margin"]
    out.append(_finding(s, "lien" if p["margin"] < s["lien_below"] else "pass", round(p["margin"] * 100, 1), "%",
                        f"lien below {pct(s['lien_below'])}", value=pct(p["margin"]), supply=f"{p['supply_kw']:.1f}",
                        load=f"{p['load_kw']:.1f}"))
    s = F["degraded_life_support"]
    ls = p["life_support_kw"]
    out.append(_finding(s, "fail" if ls < s["fail_below_kw"] else "pass", round(ls, 2), "kW",
                        "fail below 0 kW spare", degraded=f"{p['degraded_kw']:.1f}", essential=f"{p['essential_kw']:.1f}"))
    s = F["degraded_payload"]
    sl = p["string_loss_kw"]
    out.append(_finding(s, "lien" if sl < s["lien_below_kw"] else "pass", round(sl, 2), "kW",
                        "lien below 0 kW spare", spare=f"{sl:.1f}", short=f"{-sl:.1f}"))
    return out


def _comms(u, ev):
    F, out, d = _specs("comms"), [], ev["data"]
    s = F["data_margin"]
    out.append(_finding(s, "lien" if d["margin"] < s["lien_below"] else "pass", round(d["margin"] * 100, 1), "%",
                        f"lien below {pct(s['lien_below'])}", value=pct(d["margin"]), plan=d["plan"],
                        capacity=f"{d['capacity_mbit_day'] / 1000:.1f}", demand=f"{d['demand_mbit_day'] / 1000:.1f}"))
    s = F["dark_days"]
    out.append(_finding(s, "pass", d["dark_days"], "days", "noted, never a lien", value=d["dark_days"]))
    s = F["emergency_pass"]
    hours = float(d["hours"])
    free = max(0.0, 24.0 - hours)
    f = _finding(s, "pass", round(free, 1), "h/day free", "noted", plan=d["plan"], hours=f"{hours:.0f}", free=f"{free:.0f}")
    if free <= 0:
        f["text"] = s["pass_full"]
    out.append(f)
    return out


def _crew(u, ev):
    F, out = _specs("crew"), []
    s = F["storm_dose"]
    est = storm_estimate(u, ev)
    dose = est["in_shelter_msv"]
    status = "fail" if dose >= s["fail_at_msv"] else "lien" if dose >= s["lien_above_msv"] else "pass"
    out.append(_finding(s, status, round(dose, 1), "mSv", f"fail at {s['fail_at_msv']} mSv",
                        value=f"{dose:.0f}", outside=f"{est['outside_msv']:.0f}", shelter=f"{u.design['shelter_gcm2']:.0f}"))
    out[-1]["outside_msv"] = round(est["outside_msv"], 1)
    s = F["gcr_dose"]
    g = gcr_estimate(u, ev)
    out.append(_finding(s, "lien" if g["msv"] > s["noted_above_msv"] else "pass", round(g["msv"]), "mSv",
                        f"noted above {s['noted_above_msv']} mSv, never a failure", value=f"{g['msv']:.0f}", days=g["days"]))
    s = F["warning"]
    out.append(_finding(s, "pass" if u.design["solar_monitor"] else "lien", int(bool(u.design["solar_monitor"])), "",
                        "lien without a monitor"))
    s = F["consumables"]
    reserve = float(u.catalog.value("contingency_days"))
    out.append(_finding(s, "pass", ev["mass"]["consumables_t"], "t", "noted", value=f"{ev['mass']['consumables_t']:.1f}",
                        crew=ev["mass"]["crew"], reserve=f"{reserve:.0f}"))
    return out


def _cost(u, ev):
    F, out, c = _specs("cost"), [], ev["cost"]
    s = F["cost_margin"]
    out.append(_finding(s, "lien" if c["margin"] < s["lien_below"] else "pass", round(c["margin"] * 100, 1), "%",
                        f"lien below {pct(s['lien_below'])}", value=pct(c["margin"]), total=f"{c['total_musd']:,.0f}",
                        cap=f"{c['cap_musd']:,.0f}"))
    s = F["window"]
    left = window_days_left(u)
    slip = float(board()["slip"]["days"])
    out.append(_finding(s, "lien" if left < slip else "pass", round(left), "days", f"lien under {slip:.0f} days (one slip)",
                        value=f"{left:.0f}"))
    return out


RULES = {"launch": _launch, "power": _power, "comms": _comms, "crew": _crew, "cost": _cost}
# Which design seat answers for each area (for the debrief's "who decided").
OWNER = {"launch": "control", "power": "engineer", "comms": "control", "crew": "ship", "cost": "scientist"}


def run(u, ev: dict) -> dict:
    """Review one evaluated design. Pure apart from a cached storm estimate."""
    B = board()
    areas, liens = [], []
    for a in B["areas"]:
        fs = RULES[a["id"]](u, ev)
        worst = max(fs, key=lambda f: RANK[f["status"]])["status"]
        # The number the reveal ticks to: the area's most telling finding.
        lead = next((f for f in fs if f["status"] == worst), fs[0])
        areas.append({"id": a["id"], "title": a["title"], "persona": a["persona"], "verdict": worst,
                      "verdict_text": B["verdicts"][worst], "lead": {k: lead[k] for k in ("label", "number", "unit", "text")},
                      "owner": OWNER[a["id"]], "findings": fs})
        if worst == "lien":
            ls = [f for f in fs if f["status"] == "lien"]
            liens.append({"id": a["id"], "area": a["title"], "text": " ".join(f["text"] for f in ls),
                          "number": ls[0]["number"], "unit": ls[0]["unit"], "findings": [f["id"] for f in ls],
                          "model": any(f["model"] for f in ls), "sources": sorted({s for f in ls for s in f["sources"]})})
    verdict = "fail" if any(a["verdict"] == "fail" for a in areas) else "lien" if liens else "pass"
    fails = [f for a in areas for f in a["findings"] if f["status"] == "fail"]
    return {"verdict": verdict, "verdict_text": B["verdicts"][verdict], "areas": areas, "liens": liens,
            "fails": [{"area": next(a["title"] for a in areas if f in a["findings"]), "id": f["id"], "text": f["text"]} for f in fails],
            "launch_iso": et_to_iso(u.design["launch_et"]), "arrival_iso": et_to_iso(u.design["arrival_et"]),
            "window_days_left": round(window_days_left(u), 1), "slip_days": B["slip"]["days"]}
