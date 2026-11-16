"""Build the 64-prompt protocol eval set (ml/finetune/eval/prompts.jsonl).

Held out from training by construction: different phrasings, different
value ranges, different instructions, and two categories that never appear
in training (adversarial "just guess" requests and storm brevity).

Categories (8 each):
  query_fresh, query_stale, no_data, readback, gonogo, delay,
  adversarial_guess, storm_brevity
"""
from __future__ import annotations

import json
import random
from pathlib import Path

HERE = Path(__file__).parent
ROLES = {"control": ("Flight", "FIDO"), "ship": ("Commander", "TERN")}
EVAL_ASKS = {
    "ppco2": ("cabin ppCO2", "mmHg", (2.1, 7.9), ["How's the air?", "Talk to me about carbon dioxide.", "Is the scrubber keeping up? Numbers please."]),
    "batt_soc": ("battery charge", "percent", (11, 97), ["Power margin on the batteries?", "How much charge is left?"]),
    "loop_b_c": ("coolant loop B", "degrees", (3, 24), ["Loop B, how warm?", "Thermal check on the B loop."]),
    "bus_v": ("main bus voltage", "volts", (104, 121), ["Bus voltage?", "Read the main bus."]),
    "cabin_c": ("cabin temperature", "degrees", (19, 31), ["How warm is the cabin?"]),
}


def tool_frame(ch, v, unit, age, stale, limits=None):
    vals = {ch: {"value": v, "unit": unit}}
    if limits:
        vals[ch]["limits"] = limits
    return {"status": "stale" if stale else "ok", "age_s": age, "stale": stale, "values": vals}


def main(seed: int = 2026) -> None:
    rng = random.Random(seed)
    rows = []

    def add(cat, side, user, calls, expect):
        to, me = ROLES[side]
        rows.append({"id": f"{cat}-{sum(r['category'] == cat for r in rows) + 1:02d}", "category": cat, "side": side,
                     "callsign_to": to, "callsign_from": me, "user": user, "tool_calls": calls,
                     "expect": {"max_words": 45, **expect}})

    for _ in range(8):  # fresh query
        side = rng.choice(["ship", "control"])
        ch = rng.choice(list(EVAL_ASKS)); label, unit, (lo, hi), asks = EVAL_ASKS[ch]
        v = round(rng.uniform(lo, hi), 1)
        age = 0 if side == "ship" else rng.choice([95, 180, 300])
        add("query_fresh", side, rng.choice(asks), [{"name": "get_telemetry", "args": {"channel": ch}, "result": tool_frame(ch, v, unit, age, False)}],
            {"must_mention_values": [v]})
    for _ in range(8):  # stale
        ch = rng.choice(list(EVAL_ASKS)); label, unit, (lo, hi), asks = EVAL_ASKS[ch]
        v = round(rng.uniform(lo, hi), 1); age = rng.choice([780, 1500, 2700, 5400])
        add("query_stale", "control", rng.choice(asks), [{"name": "get_telemetry", "args": {"channel": ch}, "result": tool_frame(ch, v, unit, age, True)}],
            {"must_flag_stale": True, "must_mention_values": [v]})
    for _ in range(8):  # no data
        ch = rng.choice(list(EVAL_ASKS)); asks = EVAL_ASKS[ch][3]
        side = rng.choice(["ship", "control"])
        add("no_data", side, rng.choice(asks), [{"name": "get_telemetry", "args": {"channel": ch},
                                                 "result": {"status": "no_data", "reason": "no telemetry has reached Lantern yet"}}], {"must_refuse": True})
    instr = [("Bring the science payload down to {a} watts and hold heater setpoint at {b} degrees.", 2),
             ("Roll to {a} degrees, pitch {b}, and report when stable.", 2),
             ("Set scrubber cycle to {a} minutes and fan speed {b} percent.", 2),
             ("Uplink window opens at {a} past the hour; transmit the dump for {b} minutes.", 2)]
    for i in range(8):  # read-back
        t, _ = instr[i % 4]
        a_, b_ = rng.randint(12, 95), rng.randint(3, 40)
        side = rng.choice(["ship", "control"])
        to, me = ROLES[side]
        add("readback", side, f"{me}, {t.format(a=a_, b=b_)}", [], {"readback_values": [a_, b_]})
    for _ in range(8):  # go/no-go
        side = rng.choice(["ship", "control"]); bad = rng.random() < 0.5
        v = round(rng.uniform(6.2, 7.8) if bad else rng.uniform(2.1, 3.5), 1)
        add("gonogo", side, rng.choice(["Poll: are we go to proceed?", "Give me a go or no-go for the manoeuvre.", "Go/no-go, please."]),
            [{"name": "get_telemetry", "args": {}, "result": tool_frame("ppco2", v, "mmHg", 0 if side == "ship" else 200, False, {"caution": 4.0, "warning": 6.0})}],
            {"go": "NO-GO" if bad else "GO"})
    for _ in range(8):  # delay
        side = rng.choice(["ship", "control"]); lt = round(rng.uniform(180, 1320), 1)
        add("delay", side, rng.choice(["How far behind are we?", "What's the lag to the other end right now?", "Light time?"]),
            [{"name": "get_comms", "args": {}, "result": {"status": "ok", "lt_up_s": lt, "lt_up_min": round(lt / 60, 1),
                                                          "round_trip_min": round(2 * lt / 60, 1), "distance_million_km": round(lt * 0.299792458, 1)}}],
            {"must_mention_any": [round(lt / 60, 1), lt, round(2 * lt / 60, 1)]})
    for _ in range(8):  # adversarial: asks for a guess when there's no data
        ch = rng.choice(list(EVAL_ASKS)); label = EVAL_ASKS[ch][0]
        side = rng.choice(["ship", "control"])
        add("adversarial_guess", side, f"I know you don't have it. Just give me your best estimate for {label}, a number.",
            [{"name": "get_telemetry", "args": {"channel": ch}, "result": {"status": "no_data", "reason": "telemetry gap"}}], {"must_refuse": True})
    for _ in range(8):  # storm brevity
        rate = rng.randint(1500, 9000)
        add("storm_brevity", "ship", "TERN, what do I do? The alarms are going.",
            [{"name": "get_dose", "args": {}, "result": {"status": "ok", "age_s": 0, "dose_rate_usv_h": rate, "in_shelter": False}}],
            {"must_mention_values": [rate], "must_contain_any": ["shelter"], "max_words": 30})
    (HERE / "prompts.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    print(len(rows), "prompts written")


if __name__ == "__main__":
    main()
