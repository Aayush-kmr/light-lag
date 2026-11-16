"""The Mission Fact Sheet: one printable page about the mission the team designed
(after the review) and flew (after the debrief).

In the style of a mission fact sheet, with no agency logos, insignia or real
mission identifiers. Every number on the sheet is either traced to a source in
data/catalog/sources.yaml (listed in the sheet's sources block) or marked MODEL.
"""
from __future__ import annotations

from ..astro.timescale import et_to_iso

MISSION = "Meridian-1"
TEAM = "Goodnight, over"
ALWAYS = ["nssdc_mars_fact", "mro_ssr", "nasa_std_3001_tb020"]   # cited by fixed lines on the sheet


def _collect(node, out: set) -> None:
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "sources" and isinstance(v, list):
                out.update(x for x in v if isinstance(x, str))
            elif k == "source" and isinstance(v, str):
                out.add(v)
            else:
                _collect(v, out)
    elif isinstance(node, list):
        for v in node:
            _collect(v, out)


def build(u) -> dict:
    rec = u.design_record
    ev = rec["eval"] if rec else u.design_eval()
    ch = rec["choices"] if rec else u.design
    C = u.catalog
    review = (rec or {}).get("review") or u.review_last
    flown = rec is not None
    state = "debrief" if u.phase == "debrief" else "flight" if flown else "design"
    obj = C.get("objectives", ch["objective"])
    arr, bat = C.get("solar_arrays", ch["array"]), C.get("batteries", ch["battery"])
    payload = []
    for i in ch["payload"]:
        x = C.get("instruments", i)
        payload.append({"id": i, "name": x["name"], "heritage": x.get("heritage", ""), "mass_kg": x["mass_kg"],
                        "power_w": x["power_w"], "data_mbit_day": x["data_mbit_day"], "sources": x.get("sources", []),
                        "model": x.get("model", [])})
    crew = [{"name": m["name"], "role": m["role"], "home": m.get("home")} for m in C.crew["crew"]]
    p, d, camp = ev.get("power", {}), ev.get("data", {}), ev.get("campaign", {})
    sheet = {
        "state": state, "mission": MISSION, "team": TEAM, "scenario": u.scenario.get("title"),
        "tagline": "Design the mission. Then live with it at the speed of light.",
        "objective": {"name": obj["name"], "pitch": obj.get("pitch", ""), "need_gbit": obj["min_data_gbit"],
                      "cost_cap_musd": obj["cost_cap_musd"], "model": True},
        "trajectory": {"launch": et_to_iso(ch["launch_et"])[:10], "arrival": et_to_iso(ch["arrival_et"])[:10],
                       "tof_days": ev.get("tof_days"), "c3": ev.get("c3"), "dv_total": ev.get("dv_total"),
                       "note": "Lambert transfer on the real 2020 geometry (JPL ephemeris when available)."},
        "launch": {"vehicle": camp.get("name"), "campaign": camp.get("text"), "launches": camp.get("launches"),
                   "imleo_t": ev.get("mass", {}).get("imleo_t"), "sources": camp.get("sources", [])},
        "power": {"array": arr["name"], "array_kw_1au": arr["kw_at_1au"], "battery": bat["name"], "battery_kwh": bat["kwh"],
                  "supply_kw": p.get("supply_kw"), "load_kw": p.get("load_kw"), "margin": p.get("margin"),
                  "degraded_kw": p.get("degraded_kw"), "essential_kw": p.get("essential_kw"),
                  "sources": sorted(set(arr.get("sources", []) + bat.get("sources", []) + ["nssdc_mars_fact"])), "model": True},
        "payload": payload,
        "radio": {"plan": d.get("plan"), "hours": d.get("hours"), "capacity_gbit_day": round((d.get("capacity_mbit_day") or 0) / 1000.0, 2),
                  "demand_gbit_day": round((d.get("demand_mbit_day") or 0) / 1000.0, 2), "dark_days": d.get("dark_days"),
                  "returned_gbit": d.get("returned_gbit"), "recorder_gbit": 160, "sources": ["mro_ssr"], "model": True},
        "crew": crew,
        "shielding": {"shelter_gcm2": ch["shelter_gcm2"], "hull_liner_gcm2": ch["hull_liner_gcm2"],
                      "solar_monitor": bool(ch["solar_monitor"]), "sep_factor_shelter": ev.get("sep_factor_shelter"),
                      "gcr_factor": ev.get("gcr_factor"), "model": True},
        "budgets": {k: {kk: b.get(kk) for kk in ("label", "value", "limit", "unit", "margin", "band", "model", "sources")}
                    for k, b in (ev.get("budgets") or {}).items()},
        "mass_lines": ev.get("mass", {}).get("lines", []), "cost_lines": ev.get("cost", {}).get("lines", []),
        "review": None, "outcome": None,
    }
    if review:
        sheet["review"] = {"verdict": review["verdict"], "verdict_text": review["verdict_text"],
                           "areas": [{"title": a["title"], "verdict": a["verdict"], "verdict_text": a["verdict_text"],
                                      "lead": a["lead"], "sources": sorted({s for f in a["findings"] for s in f["sources"]}),
                                      "model": any(f["model"] for f in a["findings"])} for a in review["areas"]],
                           "liens": review.get("liens", []), "reviews": len(u.reviews)}
    if u.phase == "debrief":
        from .scoring import score
        sc = score(u)
        sci = sc.get("science") or {}
        sheet["outcome"] = {"days": round((u.et - u.anchors["launch"]) / 86400.0), "dose_total_msv": sc["total_dose_msv"],
                            "storm_dose_msv": sc["storm_dose_msv"], "storm_grade": sc["storm_grade"],
                            "gcr_dose_msv": sc["gcr_dose_msv"], "objectives_pct": sc["objectives_pct"],
                            "science_delivered_gbit": sci.get("delivered_gbit"), "science_relevant_gbit": sci.get("relevant_gbit"),
                            "objective_met": sci.get("objective_met"), "tern_decisions": sc["tern_decisions"],
                            "policy_breaks": sc["policy_breaks"],
                            "liens_true": [e["lien"] for e in u.callouts.ledger if e["trigger"] == "lien_true"],
                            "sources": ["nasa_std_3001_tb020"]}
    keys = set(ALWAYS)
    _collect(sheet, keys)
    src = C.sources
    sheet["sources"] = [{"key": k, "title": src[k].get("title", k), "url": src[k].get("url"), "publisher": src[k].get("publisher")}
                        for k in sorted(keys) if k in src]
    sheet["missing_sources"] = sorted(k for k in keys if k not in src)
    sheet["model_note"] = ("MODEL marks an assumption made for the game, not a measured or published value. "
                           "Every other number traces to a source below.")
    return sheet
