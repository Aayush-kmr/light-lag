"""The cause-and-effect debrief: decision, then flight event, then outcome.

Built once at the end of the mission from the frozen design record, the
callout ledger, the flight history and the score. Everything here is
deterministic. Counterfactuals are the callouts' own (MODEL), and the one
"fly it again" suggestion re-evaluates the design bay with a single change.
"""
from __future__ import annotations

import copy

from ..astro.constants import DAY_S

# Which callouts make the best chains, best first. Seven at most.
CHAIN_ORDER = ["power_distance", "power_string", "storm_shelter", "tern_rule_broken", "emergency_pass", "conjunction", "recorder_gap",
               "battery_bridge", "science_result", "science_milestone", "tern_acted_alone"]
MAX_CHAINS, MIN_CHAINS = 7, 5
SEAT_TITLE = {"control": "Flight Director", "scientist": "Mission Scientist", "ship": "Commander",
              "engineer": "Flight Engineer", "both": "The whole team"}


def _who(rec: dict, seat: str) -> dict:
    seats = rec.get("seats") or {}
    s = seats.get(seat)
    if not s:
        return {"seat": seat, "title": SEAT_TITLE.get(seat, seat), "name": None, "ai": None}
    return {"seat": seat, "title": s["title"], "name": s["name"], "ai": s["ai"]}


def chains(u) -> list[dict]:
    rec = u.design_record or {}
    led = [e for e in u.callouts.ledger if e["trigger"] != "lien_true"]
    defs = u.callouts.defs
    lien_true = {e["lien"] for e in u.callouts.ledger if e["trigger"] == "lien_true" and e.get("lien")}
    picked, seen = [], set()
    for trig in CHAIN_ORDER:
        for e in led:
            if e["trigger"] == trig and trig not in seen:
                seen.add(trig)
                picked.append(e)
                break
        if len(picked) >= MAX_CHAINS:
            break
    out = []
    for e in sorted(picked, key=lambda x: x["day"]):
        out.append({"id": e["id"], "trigger": e["trigger"], "step": e["step"],
                    "decision": {"text": e["choice"], "step": e["step"], **_who(rec, e["seat"])},
                    "event": {"day": e["day"], "text": defs[e["trigger"]].get("event", ""), "scene": e["scene"]},
                    "outcome": {"text": e["text"], "counter": e["counter"], "model": e["model"], "numbers": e["numbers"]},
                    "sources": e["sources"], "lien": e.get("lien"), "lien_true": bool(e.get("lien") and e["lien"] in lien_true)})
    if len(out) < MIN_CHAINS:
        out += _fallback_chains(u, MIN_CHAINS - len(out), {c["trigger"] for c in out})
    return out


def _fallback_chains(u, n: int, have: set) -> list[dict]:
    """With callouts off (LL_CALLOUTS=0) the debrief still tells cause and effect
    from the score and the design record."""
    rec, sc = u.design_record or {}, None
    from .scoring import score
    sc = score(u)
    ch = rec.get("choices") or u.design
    day_end = (u.et - u.anchors.get("launch", u.et)) / DAY_S
    cands = [
        ("storm_shelter", "Crew protection", "ship", f"Storm shelter {ch['shelter_gcm2']:.0f} g/cm²",
         "The solar storm reached the ship", f"Storm dose {sc['storm_dose_msv']:.1f} mSv, grade {sc['storm_grade']}."),
        ("science_result", "Objective", "scientist", f"Objective: {(rec.get('names') or {}).get('objective', ch['objective'])}",
         "The mission ended", f"{(sc.get('science') or {}).get('relevant_gbit', 0):.1f} Gbit of the objective's data came home."),
        ("tern", "TERN's rules", "both", "TERN's rules as signed", "TERN decided on its own",
         f"{sc['tern_decisions']} decisions, {sc['policy_breaks']} against the rules."),
        ("conjunction", "Launch date", "control", "Arrival date", "The Sun slid between Earth and Mars",
         f"{(rec.get('derived') or {}).get('dark_days', 0)} days of silence inside the stay."),
        ("radio", "Radio time", "control", f"{ch['comms_plan']} radio plan", "Messages waited for antenna passes",
         f"{sc['bundles']['delivered']} of {sc['bundles']['sent']} bundles delivered."),
    ]
    out = []
    for trig, step, seat, choice, event, outcome in cands:
        if trig in have or len(out) >= n:
            continue
        out.append({"id": f"f-{trig}", "trigger": trig, "step": step, "decision": {"text": choice, "step": step, **_who(rec, seat)},
                    "event": {"day": round(day_end, 1), "text": event, "scene": None},
                    "outcome": {"text": outcome, "counter": "", "model": False, "numbers": {}}, "sources": [], "lien": None,
                    "lien_true": False})
    return out


# ---------------------------------------------------------------- fly it again
def alt_eval(u, changes: dict) -> dict:
    """The design bay's evaluation of the flown design with one change (pure)."""
    rec = u.design_record
    saved = u.design
    u.design = {**copy.deepcopy(rec["choices"] if rec else saved), **changes}
    try:
        return u.design_eval()
    finally:
        u.design = saved


def fly_again(u, ch: list[dict]) -> dict | None:
    rec = u.design_record
    if not rec:
        return None
    trig = {c["trigger"] for c in ch}
    before = rec["eval"]
    C = u.catalog
    cands = []
    if trig & {"power_string", "power_distance"} and rec["choices"]["array"] != "array_large":
        cands.append(({"array": "array_large"}, "Try the oversized array",
                      lambda a: f"one string short it still carries the payload ({a['power']['string_loss_kw']:+.1f} kW)"))
    lost = (u.science.totals()["lost_mbit"] or 0) / 1000.0
    if lost > 0.5 and len(rec["choices"]["payload"]) > 1:
        big = max(rec["choices"]["payload"], key=lambda i: C.get("instruments", i)["data_mbit_day"])
        cands.append(({"payload": [i for i in rec["choices"]["payload"] if i != big]},
                      f"Try leaving the {C.get('instruments', big)['name'].lower()} at home",
                      lambda a: f"{a['data']['demand_mbit_day'] / 1000:.1f} Gbit a day to bring home instead of {before['data']['demand_mbit_day'] / 1000:.1f}"))
    if rec["choices"]["comms_plan"] != "continuous":
        cands.append(({"comms_plan": "continuous"}, "Try the continuous radio plan",
                      lambda a: f"about {a['data']['returned_gbit'] - before['data']['returned_gbit']:+.0f} Gbit home"))
    if float(rec["choices"]["shelter_gcm2"]) < 30:
        cands.append(({"shelter_gcm2": float(rec["choices"]["shelter_gcm2"]) + 10}, "Try 10 g/cm² more shelter water",
                      lambda a: f"the storm dose falls to about {round(100 * a['sep_factor_shelter'] / max(before['sep_factor_shelter'], 1e-9))}% of what you took"))
    for changes, title, gain in cands:
        a = alt_eval(u, changes)
        if not a.get("budgets"):
            continue
        moved = []
        for k in ("mass", "power", "data", "cost"):
            m0, m1 = before["budgets"][k]["margin"], a["budgets"][k]["margin"]
            if round(m0 * 100) != round(m1 * 100):
                moved.append(f"{k} margin {round(m0 * 100)}% → {round(m1 * 100)}%")
        text = f"{title}: {gain(a)}" + (f", {', '.join(moved)}" if moved else "") + "."
        return {"text": text, "changes": changes, "valid": bool(a.get("valid")), "reasons": a.get("reasons", []),
                "model": True, "budgets": {k: {"before": before["budgets"][k]["margin"], "after": a["budgets"][k]["margin"],
                                                 "band": a["budgets"][k]["band"]} for k in ("mass", "power", "data", "cost")}}
    return None


# ---------------------------------------------------------------- the page
def build(u) -> dict:
    from .scoring import score
    sc = score(u)
    rec = u.design_record or {}
    ch = chains(u)
    hist = u.history.public()
    launch = u.anchors.get("launch", u.et)
    day = lambda et: round((et - launch) / DAY_S, 2)  # noqa: E731
    sci = sc.get("science") or {}
    obj_name = (rec.get("names") or {}).get("objective") or sci.get("objective_name", "")
    met = bool(sci.get("objective_met"))
    storm = sc["storm_dose_msv"]
    sentence = (f"Meridian-1 flew {day(u.et):.0f} days and brought its crew through the storm with a dose of {storm:.1f} mSv. "
                f"{sci.get('relevant_gbit', 0):,.1f} Gbit of the objective's data came home, of "
                f"{sci.get('min_data_gbit', 0):,.0f} Gbit needed: objective {'met' if met else 'missed'}.")
    grades = [{"label": "Storm dose", "value": f"{storm:.1f} mSv", "grade": sc["storm_grade"], "scored": True},
              {"label": "Objectives", "value": f"{sc['objectives_pct']:.0f}%", "grade": None, "scored": True},
              {"label": "Science home", "value": f"{sci.get('relevant_gbit', 0):,.1f} of {sci.get('min_data_gbit', 0):,.0f} Gbit",
               "grade": "met" if met else "missed", "scored": True},
              {"label": "Rule breaks by TERN", "value": str(sc["policy_breaks"]), "grade": None, "scored": True}]
    # crew cards: sparklines from the history, the decision from the bio, the last line said
    crew = []
    bios = {c["id"]: c for c in u.catalog.crew["crew"]}
    for m in u.crew.members.values():
        last = next((e["text"] for e in reversed(u.crew.log) if e.get("who") == m.id and e["kind"] == "said"), None)
        crew.append({"id": m.id, "name": m.name, "role": m.role, "decision": bios.get(m.id, {}).get("decision"),
                     "last_line": last, "ai": u.crew.ai_driven(m),
                     "stress": [[p["day"], p["crew"].get(m.id, [None, None])[0]] for p in hist if m.id in p["crew"]],
                     "morale": [[p["day"], p["crew"].get(m.id, [None, None])[1]] for p in hist if m.id in p["crew"]],
                     "final": {"stress": round(m.stress, 2), "morale": round(m.morale, 2)}})
    lost_why = []
    for e in u.science.events:
        if e["kind"] in ("overwrite",) and e["text"] not in lost_why:
            lost_why.append(e["text"])
    science = {**sci, "lost_why": lost_why[-3:] or (["Nothing was lost."] if not sci.get("lost_gbit") else []),
               "objective_name": obj_name}
    sl = u.director.scene_log
    timeline = {"days": day(u.et), "scenes": [{"id": s["id"], "title": s["title"], "act": s.get("act"), "d0": day(s["start_et"]),
                                                "d1": day(s["end_et"])} for s in sl],
                "storm_day": day(u.anchors["storm"]) if "storm" in u.anchors else None,
                "conjunction": [day(u.anchors["blackout_start"]), day(u.anchors["blackout_end"])] if "blackout_start" in u.anchors else None,
                "arrival_day": day(u.anchors["arrival"]) if "arrival" in u.anchors else None,
                "lt": [[p["day"], round(p["lt_s"] / 60.0, 2)] for p in hist],
                "callouts": [{"day": e["day"], "id": e["id"], "trigger": e["trigger"], "text": e["text"]}
                             for e in u.callouts.ledger if e["show"]]}
    liens = []
    true_ids = {e["lien"] for e in u.callouts.ledger if e["trigger"] == "lien_true"}
    for L in rec.get("liens") or []:
        liens.append({**L, "came_true": L["id"] in true_ids})
    scored = [{"label": g["label"], "value": g["value"], "grade": g["grade"]} for g in grades]
    not_scored = [
        {"label": "Cosmic-ray dose", "value": f"{sc['gcr_dose_msv']:.1f} of {sc['career_limit_msv']:.0f} mSv career limit", "why": sc["gcr_note"]},
        {"label": "Crew morale", "value": ", ".join(f"{c['name'].replace('Dr. ', '').split()[0]} {c['final']['morale']:.2f}" for c in crew),
         "why": "Morale is shown, never scored."},
        {"label": "The letters", "value": f"{len(u.letter_order)} played", "why": "Some things aren't scored."}]
    return {"verdict": {"sentence": sentence, "grades": grades, "objectives_pct": sc["objectives_pct"], "science_met": met},
            "chains": ch, "timeline": timeline, "crew": crew, "science": science, "scored": scored, "not_scored": not_scored,
            "liens": liens, "review": rec.get("review"), "reviews": rec.get("reviews", []),
            "fly_again": fly_again(u, ch), "history_points": len(hist), "callouts_on": bool(u.cfg.callouts)}
