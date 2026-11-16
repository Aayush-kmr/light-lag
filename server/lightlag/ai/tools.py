"""Tools the onboard AI (TERN) and the ground assistant (FIDO) may call.

The asymmetry is the point:
* TERN reads the ship's own sensors: values are fresh (age 0).
* FIDO reads only what has *arrived* at Lantern: every value carries its
  age, which is never less than the one-way light time, and is flagged
  stale when telemetry has stopped arriving.
Both models may state a number only if it came back from one of these
calls (enforced by grounding.py).
"""
from __future__ import annotations

from ..astro.timescale import et_to_iso
from ..sim.subsystems import CHANNELS, LABELS, LIMITS, UNITS

CHANNEL_KEYWORDS = {
    "ppco2": ["co2", "carbon", "scrubber", "air"], "scrub_dp": ["scrubber", "bed"],
    "batt_soc": ["battery", "charge", "soc"], "solar_kw": ["solar", "array", "power"],
    "bus_v": ["bus", "voltage", "power"], "bus_a": ["current", "amps"],
    "loop_a_c": ["coolant", "loop", "thermal", "pump"], "loop_b_c": ["coolant", "loop", "thermal", "pump"],
    "cabin_c": ["cabin", "temperature", "temp"], "link_db": ["link", "margin", "antenna", "hga", "signal"],
    "dose_rate": ["dose", "radiation", "rad", "proton", "dosimeter"],
}

_SCHEMA = {
    "get_telemetry": {"description": "Latest telemetry. Optional channel name; omit for all channels.",
                      "parameters": {"type": "object", "properties": {"channel": {"type": "string", "enum": CHANNELS}}}},
    "get_dose": {"description": "Crew radiation dose totals and current dose rate.", "parameters": {"type": "object", "properties": {}}},
    "get_comms": {"description": "Light time, link state, Sun-Earth-probe angle, queues, next contact.",
                  "parameters": {"type": "object", "properties": {}}},
    "get_policy": {"description": "TERN autonomy policy rules and permissions.", "parameters": {"type": "object", "properties": {}}},
    "get_anomaly": {"description": "Onboard anomaly detector score and top contributing channels.",
                    "parameters": {"type": "object", "properties": {}}},
    "get_dsn_passes": {"description": "Scheduled Deep Space Network passes, next 48 hours.", "parameters": {"type": "object", "properties": {}}},
    "get_space_weather": {"description": "Solar events observed from Earth.", "parameters": {"type": "object", "properties": {}}},
    "get_requests": {"description": "Pending authorization requests from TERN.", "parameters": {"type": "object", "properties": {}}},
    "get_clock": {"description": "Current UTC and Houston time, mission day.", "parameters": {"type": "object", "properties": {}}},
}

TOOLS_BY_SIDE = {
    "ship": ["get_telemetry", "get_dose", "get_comms", "get_policy", "get_anomaly", "get_clock"],
    "control": ["get_telemetry", "get_dose", "get_comms", "get_dsn_passes", "get_space_weather", "get_requests",
                "get_policy", "get_clock"],
}


def openai_tools(side: str) -> list[dict]:
    return [{"type": "function", "function": {"name": n, **_SCHEMA[n]}} for n in TOOLS_BY_SIDE[side]]


def guess_channels(text: str) -> list[str]:
    t = text.lower()
    hits = [c for c, kws in CHANNEL_KEYWORDS.items() if any(k in t for k in kws)]
    return hits


def call(u, side: str, name: str, args: dict | None = None) -> dict:
    args = args or {}
    if name not in TOOLS_BY_SIDE.get(side, []):
        return {"status": "error", "error": f"tool {name} not available to {side}"}
    lock = getattr(u, "_lock", None)
    if lock is not None:            # _LockedUniverse proxy from the server
        with lock:
            return call(u._u, side, name, args)
    if u.phase != "mission":
        return {"status": "no_data", "reason": "mission not launched"}
    fn = globals()["_" + name]
    return fn(u, side, args)


def _get_clock(u, side, args):
    return {"status": "ok", "utc": et_to_iso(u.et), "houston": u.local_str(u.et),
            "mission_day": round((u.et - u.anchors["launch"]) / 86400.0, 1)}


def _get_telemetry(u, side, args):
    ch = args.get("channel")
    chans = [ch] if ch in CHANNELS else CHANNELS
    if side == "ship":
        f = u.frame
        if f is None:
            return {"status": "no_data", "reason": "no frame yet"}
        age, stale, src = 0.0, False, "onboard sensors"
    else:
        f = u.ctrl["frame"]
        if f is None:
            return {"status": "no_data", "reason": "no telemetry has reached Lantern yet"}
        age = u.et - u.ctrl["sample_et"]
        stale = age > u.lt("down") + 120.0
        src = "downlinked telemetry"
    vals = {c: {"value": round(f[c], 2), "unit": UNITS[c], "label": LABELS[c], "limits": LIMITS.get(c)} for c in chans}
    return {"status": "stale" if stale else "ok", "source": src, "age_s": round(age, 0),
            "age_min": round(age / 60.0, 1), "stale": stale, "values": vals}


def _get_dose(u, side, args):
    if side == "ship":
        d = u.rad.public()
        rate = u.frame["dose_rate"] if u.frame else None
        return {"status": "ok", "age_s": 0, "dose_rate_usv_h": rate, "in_shelter": u.shelter, **d}
    d = u.ctrl["dose"]
    if d is None:
        return {"status": "no_data", "reason": "no dosimetry downlinked yet"}
    age = u.et - u.ctrl["sample_et"]
    return {"status": "ok", "age_s": round(age, 0), "age_min": round(age / 60.0, 1),
            "in_shelter": u.ctrl["shelter"], **d}


def _get_comms(u, side, args):
    L = u.link.public() if u.link else {}
    g = u.geometry()
    return {"status": "ok", "lt_up_s": round(g["lt_up_s"], 1), "lt_down_s": round(g["lt_down_s"], 1),
            "lt_up_min": round(g["lt_up_s"] / 60.0, 1), "round_trip_min": round((g["lt_up_s"] + g["lt_down_s"]) / 60.0, 1),
            "distance_million_km": round(g["dist_km"] / 1e6, 2), "sep_deg": round(g["sep_deg"], 2),
            "uplink_open": L.get("uplink_open"), "downlink_open": L.get("downlink_open"),
            "link_margin_db": round(L.get("margin_db", 0.0), 1), "reason": L.get("reason"),
            "moratorium": L.get("moratorium"), "blackout": L.get("blackout"),
            "queued_at_lantern": len(u.net.stores["dtn://lantern"]), "queued_aboard": len(u.net.stores["dtn://meridian-1"])}


def _get_policy(u, side, args):
    return {"status": "ok", "version": u.autonomy.version,
            "rules": [{"id": r["id"], "permission": r["permission"], "params": r["params"], "label": r["label"]}
                      for r in u.autonomy.policy]}


def _get_anomaly(u, side, args):
    a = u.anomaly_state or {}
    return {"status": "ok", "score": a.get("score"), "threshold": a.get("threshold"), "alert": a.get("alert"),
            "top_channels": [LABELS[c] for c in a.get("top", [])], "detector": a.get("detector")}


def _get_dsn_passes(u, side, args):
    return {"status": "ok", "plan": u.cp.plan,
            "passes": [{"complex": p["complex"], "start": p["start_iso"], "end": p["end_iso"], "max_el_deg": p["max_el"]}
                       for p in u.passes()[:6]]}


def _get_space_weather(u, side, args):
    items = u.ctrl["space_weather"][-5:]
    if not items:
        return {"status": "ok", "events": [], "note": "no solar events observed"}
    return {"status": "ok", "events": [{"time": et_to_iso(i["et"]), "kind": i["kind"], "text": i["text"]} for i in items],
            "replay_label": u.storm_note}


def _get_requests(u, side, args):
    return {"status": "ok", "requests": [{"rule": r["rule"], "trigger": r["trigger"], "rationale": r["rationale"]}
                                         for r in u.ctrl["requests"].values()]}
