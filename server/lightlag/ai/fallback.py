"""Deterministic CAPCOM-style responder.

Three jobs:
1. The game's voice when no local LLM is running (the demo never depends
   on a model loading).
2. The last line of defence when an LLM reply fails grounding twice.
3. A rule-based baseline in the fine-tuning eval (ml/finetune/eval).

It can only say what the tools returned, by construction.
"""
from __future__ import annotations

import re

from . import tools as T

CALLSIGNS = {"ship": ("Commander", "TERN"), "control": ("Flight", "FIDO")}


def _fmt_val(v: dict) -> str:
    val = v["value"]
    unit = v["unit"]
    s = f"{val:.1f}" if abs(val) < 100 else f"{val:.0f}"
    unit_word = {"C": "degrees", "%": "percent", "uSv/h": "microsieverts an hour", "mmHg": "millimetres",
                 "dB": "dB", "kW": "kilowatts", "V": "volts", "A": "amps", "kPa": "kPa"}.get(unit, unit)
    return f"{v['label'].lower()} {s} {unit_word}"


def _age_phrase(res: dict) -> str:
    if res.get("age_s", 0) and res["age_s"] >= 60:
        m = res["age_s"] / 60.0
        old = f"{m:.0f} min old" if m < 120 else f"{m / 60:.1f} hours old"
        return f"last good reading, {old}" if res.get("stale") else f"as of {old}"
    if res.get("age_s", 0) > 0:
        return f"as of {res['age_s']:.0f} seconds ago"
    return ""


def respond(u, side: str, question: str, context_tool_results: list[dict] | None = None) -> tuple[str, list[dict]]:
    to, me = CALLSIGNS[side]
    q = question.lower()
    calls: list[dict] = []

    def use(name, args=None):
        r = T.call(u, side, name, args or {})
        calls.append({"name": name, "args": args or {}, "result": r})
        return r

    parts: list[str] = []
    if re.search(r"go.?no.?go|go/no|poll", q):
        r = use("get_telemetry")
        if r["status"] == "no_data":
            parts.append("No telemetry, I can't give you a GO. NO-GO pending data")
        else:
            bad = [c for c, v in r["values"].items() if v.get("limits") and _breach(c, v)]
            age = _age_phrase(r)
            parts.append(("NO-GO, " + ", ".join(r["values"][c]["label"].lower() for c in bad) + " out of limits")
                         if bad else "GO")
            if age:
                parts.append(age)
    elif re.search(r"delay|light.?time|how far|distance|round.?trip|lag", q):
        r = use("get_comms")
        if r["lt_up_s"] < 120:
            parts.append(f"One-way light time {r['lt_up_s']:.1f} seconds")
        else:
            parts.append(f"One-way light time {r['lt_up_min']:.1f} minutes, round trip {r['round_trip_min']:.1f}")
        parts.append(f"distance {r['distance_million_km']:.1f} million kilometres")
    elif re.search(r"\bpass|dsn|contact|antenna time|when can", q) and side == "control":
        r = use("get_dsn_passes")
        if r["passes"]:
            p = r["passes"][0]
            parts.append(f"Next pass {p['complex']}, {p['start'][11:16]} to {p['end'][11:16]} UTC")
        else:
            parts.append("No passes scheduled in the next 48 hours")
    elif re.search(r"storm|flare|sun|space weather|proton", q):
        if side == "control":
            r = use("get_space_weather")
            parts.append(r["events"][-1]["text"] if r["events"] else "Sun is quiet, no events observed")
        d = use("get_dose")
        if d["status"] == "no_data":
            parts.append("no dosimetry yet")
        else:
            rate = d.get("dose_rate_usv_h")
            if rate is not None:
                parts.append(f"dose rate {rate:.0f} microsieverts an hour")
            parts.append(f"mission dose {d['total_msv']:.1f} millisieverts")
            if d.get("age_s", 0) >= 60:
                parts.append(f"as of {d['age_s'] / 60:.0f} min ago")
    elif re.search(r"policy|allowed|autonomy|permission|authori", q):
        r = use("get_policy")
        auto = [x["id"].replace("_", " ") for x in r["rules"] if x["permission"] == "auto"]
        ask = [x["id"].replace("_", " ") for x in r["rules"] if x["permission"] == "ask"]
        parts.append(f"Policy version {r['version']}. Pre-authorized: {', '.join(auto) or 'none'}. "
                     f"Ask first: {', '.join(ask) or 'none'}")
    else:
        chans = T.guess_channels(q) or ["ppco2", "batt_soc", "loop_a_c", "dose_rate", "link_db"]
        r = use("get_telemetry")
        if r["status"] == "no_data":
            parts.append(f"No data. {r.get('reason', '')}".strip())
        else:
            vals = [_fmt_val(r["values"][c]) for c in chans[:4] if c in r["values"]]
            parts.append(", ".join(vals))
            age = _age_phrase(r)
            if age:
                parts.append(age)
    body = "; ".join(p for p in parts if p)
    return f"{to}, {me}. {body[0].upper() + body[1:] if body else 'Say again'}. Over.", calls


def _breach(c: str, v: dict) -> bool:
    lim = v["limits"]
    x = v["value"]
    low_bad = c in ("batt_soc", "link_db")
    thr = lim["warning"]
    return x <= thr if low_bad else x >= thr
