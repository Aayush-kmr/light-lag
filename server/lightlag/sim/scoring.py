"""Mission report card.

Scored: what the players could actually control - storm dose (shelter,
timing, attitude), comms discipline, objectives, autonomy outcomes.
Shown but not scored: cosmic-ray dose (no design in this game can fix it;
a 15-month Mars mission exceeds NASA's 600 mSv career limit on GCR alone,
which is one of the real unsolved problems of human Mars flight).
Not scored at all: the letters. Some things aren't scored.
Scored (Phase 3): science return, the data delivered against the objective.
Shown, never scored: crew morale.
"""
from __future__ import annotations


def _grade(x: float, cuts: list[tuple[float, str]]) -> str:
    for c, g in cuts:
        if x <= c:
            return g
    return cuts[-1][1] if cuts else "-"


def score(u) -> dict:
    rad = u.rad.public()
    sep = rad["sep_msv"]
    storm_grade = _grade(sep, [(12, "A"), (25, "B"), (50, "C"), (100, "D"), (1e9, "F")])
    objs = [o for sc in u.scenario["scenes"] for o in sc.get("objectives", [])]
    done = {o["id"] for o in u.director.objectives_log}
    obj_pct = round(100.0 * len(done) / max(1, len({o["id"] for o in objs})), 1)
    st = u.net.stats if u.net else {"sent": 0, "delivered": 0, "expired": 0}
    breaks = [d for d in u.autonomy.log if d.outcome == "broke_policy"]
    requested = [d for d in u.autonomy.log if d.outcome == "requested"]
    answered = [d for d in u.autonomy.log if d.outcome in ("approved", "denied")]
    return {
        "storm_dose_msv": sep, "storm_grade": storm_grade,
        "shelter_before_onset": "shelter_before_onset" in u.flags,
        "gcr_dose_msv": rad["gcr_msv"], "total_dose_msv": rad["total_msv"],
        "career_limit_msv": rad["career_limit_msv"],
        "gcr_note": "Cosmic-ray dose is shown, not scored: no shielding in this game (or yet in reality) can stop it.",
        "objectives_pct": obj_pct, "objectives_done": sorted(done),
        "bundles": st, "tern_decisions": len(u.autonomy.log), "policy_breaks": len(breaks),
        "requests": len(requested), "requests_answered": len(answered),
        "pings_from_audience": u.pings,
        "letters": "not scored",
        "science": u.science.score() if hasattr(u, "science") else None,
        "crew": u.crew.debrief() if hasattr(u, "crew") else None,     # shown, never scored
    }
