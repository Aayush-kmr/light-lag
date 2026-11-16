"""Training corpus for the CAPCOM-protocol fine-tune.

Two sources, both recorded in data/training/MANIFEST.json with licenses:

1. NASA raw technical air-to-ground transcripts (JSC History Portal Mission
   Transcript Collection, e.g. AS11_TEC.PDF). NASA works; NASA's ALSJ pages
   state "no copyright is asserted" for these raw transcripts. We NEVER use
   the corrected Apollo Flight/Lunar Surface Journal texts (copyrighted).
   They teach register: callsigns, brevity, read-backs, "Roger", "Over".

2. Synthetic, tool-grounded dialogues built from scenario templates. They
   teach the behaviour the game needs: cite only tool values, flag stale
   data, refuse when there is no data, answer polls with GO / NO-GO.

    python ml/finetune/corpus.py fetch       # download the PDFs (needs internet)
    python ml/finetune/corpus.py parse       # PDFs -> transcript pairs (needs pdftotext)
    python ml/finetune/corpus.py synth       # synthetic dialogues (offline)
    python ml/finetune/corpus.py build       # mix -> data/training/sft.jsonl
"""
from __future__ import annotations

import json
import random
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "training"
JSC = "https://historycollection.jsc.nasa.gov/JSCHistoryPortal/history/mission_trans/"
# Technical air-to-ground transcripts listed on the JSC portal mission pages.
TRANSCRIPTS = ["AS07_TEC.PDF", "AS08_TEC.PDF", "AS11_TEC.PDF"]
LICENSE = ("NASA work (17 U.S.C. 105); raw transcript - NASA ALSJ: 'No copyright is asserted.' "
           "Corrected ALSJ/AFJ texts excluded.")

SPEAKERS_GROUND = {"CC", "CAPCOM", "CT"}
LINE_RE = re.compile(r"^\s*(\d{2})\s+(\d{2})\s+(\d{2})\s+(\d{2})\s+([A-Z]{2,4})\s+(.+)$")

SYSTEM_CAPCOM = ("You speak on a spacecraft air-to-ground loop. Open with the addressee then yourself, "
                 "be brief, read back instructions, end with 'Over.'")


# --------------------------------------------------------------------- fetch
def fetch() -> None:
    d = OUT / "raw_pdfs"
    d.mkdir(parents=True, exist_ok=True)
    manifest = []
    for name in TRANSCRIPTS:
        dst = d / name
        if not dst.exists():
            print("downloading", name)
            urllib.request.urlretrieve(JSC + name, dst)
        manifest.append({"file": name, "url": JSC + name, "license": LICENSE})
    _manifest("nasa_transcripts", manifest)


# --------------------------------------------------------------------- parse
def parse_text(text: str, mission: str) -> list[dict]:
    """Parse 'DD HH MM SS SPKR text' technical-transcript lines into
    ground->crew and crew->ground exchange pairs."""
    turns, cur = [], None
    for raw in text.splitlines():
        m = LINE_RE.match(raw)
        if m:
            if cur:
                turns.append(cur)
            cur = {"get": ":".join(m.group(i) for i in range(1, 5)), "spk": m.group(5), "text": m.group(6).strip()}
        elif cur and raw.strip() and not raw.strip().isdigit() and len(raw.strip()) > 3:
            cur["text"] += " " + raw.strip()
    if cur:
        turns.append(cur)
    pairs = []
    for a, b in zip(turns, turns[1:]):
        ga, gb = a["spk"] in SPEAKERS_GROUND, b["spk"] in SPEAKERS_GROUND
        if ga == gb:
            continue
        ta, tb = _clean(a["text"]), _clean(b["text"])
        if not (3 <= len(tb.split()) <= 45) or not ta:
            continue
        pairs.append({"messages": [{"role": "system", "content": SYSTEM_CAPCOM},
                                   {"role": "user", "content": ta}, {"role": "assistant", "content": tb}],
                      "source": f"JSC {mission} technical air-to-ground", "license": LICENSE,
                      "get": b["get"], "direction": "ground->crew" if ga else "crew->ground"})
    return pairs


def _clean(t: str) -> str:
    t = re.sub(r"\s+", " ", t)
    t = re.sub(r"[^\w\s.,'?\-/:()]", "", t)
    return t.strip()


def parse() -> None:
    out = []
    for pdf in sorted((OUT / "raw_pdfs").glob("*.PDF")):
        txt = subprocess.run(["pdftotext", "-layout", str(pdf), "-"], capture_output=True, text=True).stdout
        p = parse_text(txt, pdf.stem)
        print(pdf.name, len(p), "pairs")
        out += p
    _write(OUT / "transcript_pairs.jsonl", out)


# --------------------------------------------------------------------- synth
CH = {"ppco2": ("cabin ppCO2", "mmHg", (2.0, 7.5)), "batt_soc": ("battery charge", "percent", (15, 99)),
      "loop_a_c": ("coolant loop A", "degrees", (2, 22)), "link_db": ("link margin", "dB", (-4, 18)),
      "dose_rate": ("dose rate", "microsieverts an hour", (60, 6000)), "solar_kw": ("solar array output", "kilowatts", (9, 32))}
TRAIN_ASKS = {"ppco2": ["What's cabin CO2?", "Read me ppCO2.", "CO2 status?"],
              "batt_soc": ["Battery state?", "Where's the battery at?", "Give me state of charge."],
              "loop_a_c": ["Loop A temperature?", "How's coolant loop A?"],
              "link_db": ["What's our link margin?", "How's the signal?"],
              "dose_rate": ["Current dose rate?", "What's the dosimeter reading?"],
              "solar_kw": ["Array output?", "How much power are the arrays making?"]}
ROLES = {"control": ("Flight", "FIDO"), "ship": ("Commander", "TERN")}


def _fmt(v: float) -> str:
    return f"{v:.1f}" if abs(v) < 100 else f"{v:.0f}"


def synth_example(rng: random.Random, kind: str, asks=TRAIN_ASKS) -> dict:
    side = rng.choice(["control", "ship"])
    to, me = ROLES[side]
    ch = rng.choice(list(CH))
    label, unit, (lo, hi) = CH[ch]
    v = round(rng.uniform(lo, hi), 1 if hi < 100 else 0)
    age = 0 if side == "ship" else rng.choice([140, 260, 420, 610, 1230])
    stale = side == "control" and rng.random() < 0.35
    tool = {"status": "stale" if stale else "ok", "age_s": age, "stale": stale, "values": {ch: {"value": v, "unit": unit}}}
    if kind == "query":
        q = rng.choice(asks[ch])
        ans = f"{to}, {me}. {label.capitalize()} {_fmt(v)} {unit}"
        if age >= 60:
            ans += f", {'last good reading' if stale else 'as of'} {round(age / 60)} min {'old' if stale else 'ago'}"
        return _ex(side, q, [("get_telemetry", {"channel": ch}, tool)], ans + ". Over.", "query")
    if kind == "nodata":
        q = rng.choice(asks[ch])
        nd = {"status": "no_data", "reason": "no telemetry has reached Lantern yet"}
        return _ex(side, q, [("get_telemetry", {"channel": ch}, nd)], f"{to}, {me}. No data on {label} yet. I won't guess. Over.", "nodata")
    if kind == "readback":
        az, el = round(rng.uniform(-30, 30), 1), round(rng.uniform(-10, 10), 1)
        q = f"{me}, set high-gain gimbal azimuth {az}, elevation {el}, then lock the brake."
        return _ex(side, q, [], f"{to}, {me}. Copy: gimbal azimuth {az}, elevation {el}, brake on. Executing. Over.", "readback")
    if kind == "gonogo":
        bad = rng.random() < 0.5
        vals = {"ppco2": {"value": 6.4 if bad else 2.5, "unit": "mmHg", "limits": {"warning": 6.0}},
                "batt_soc": {"value": 81.0, "unit": "%", "limits": {"warning": 25.0}}}
        tl = {"status": "ok", "age_s": age, "values": vals}
        reason = "ppCO2 6.4 above the 6.0 warning limit" if bad else "all channels inside limits"
        return _ex(side, "Go/no-go for the burn?", [("get_telemetry", {}, tl)],
                   f"{to}, {me}. {'NO-GO' if bad else 'GO'}. {reason.capitalize()}. Over.", "gonogo")
    raise ValueError(kind)


def _ex(side, q, calls, final, kind) -> dict:
    to, me = ROLES[side]
    sys_msg = f"You are {me}. Speak to {to}. State only values returned by tools; say how old stale data is; refuse when there is no data."
    msgs = [{"role": "system", "content": sys_msg}, {"role": "user", "content": q}]
    if calls:
        msgs.append({"role": "assistant", "content": "", "tool_calls": [
            {"id": f"c{i}", "type": "function", "function": {"name": n, "arguments": json.dumps(a)}} for i, (n, a, _) in enumerate(calls)]})
        for i, (_, _, r) in enumerate(calls):
            msgs.append({"role": "tool", "tool_call_id": f"c{i}", "content": json.dumps(r)})
    msgs.append({"role": "assistant", "content": final})
    return {"messages": msgs, "source": "synthetic template", "license": "generated by the LIGHT-LAG team (MIT)", "kind": kind}


def synth(n: int = 1600, seed: int = 11) -> None:
    rng = random.Random(seed)
    kinds = ["query"] * 5 + ["nodata", "readback", "gonogo"] * 2
    _write(OUT / "synth_dialogues.jsonl", [synth_example(rng, rng.choice(kinds)) for _ in range(n)])
    _manifest("synthetic", [{"file": "synth_dialogues.jsonl", "license": "MIT (team-generated)", "n": n, "seed": seed}])


def build(transcript_share: float = 0.3, seed: int = 3) -> None:
    rng = random.Random(seed)
    syn = _read(OUT / "synth_dialogues.jsonl")
    tr = _read(OUT / "transcript_pairs.jsonl") if (OUT / "transcript_pairs.jsonl").exists() else []
    k = min(len(tr), int(len(syn) * transcript_share / (1 - transcript_share)))
    mix = syn + rng.sample(tr, k)
    rng.shuffle(mix)
    _write(OUT / "sft.jsonl", mix)
    print(f"sft.jsonl: {len(syn)} synthetic + {k} transcript pairs")


def _manifest(key: str, entries: list) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / "MANIFEST.json"
    m = json.loads(p.read_text()) if p.exists() else {}
    m[key] = entries
    p.write_text(json.dumps(m, indent=1))


def _write(p: Path, rows: list) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n")


def _read(p: Path) -> list:
    return [json.loads(x) for x in p.read_text().splitlines() if x.strip()]


if __name__ == "__main__":
    {"fetch": fetch, "parse": parse, "synth": synth, "build": build}[sys.argv[1] if len(sys.argv) > 1 else "synth"]()
