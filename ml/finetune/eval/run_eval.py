"""Protocol eval: base vs fine-tuned (or any OpenAI-compatible local model).

    python ml/finetune/eval/run_eval.py --reference                     # rule-based reference responder
    python ml/finetune/eval/run_eval.py --url http://127.0.0.1:11434/v1 --model qwen3:4b       --tag base
    python ml/finetune/eval/run_eval.py --url http://127.0.0.1:11434/v1 --model tern-capcom    --tag finetuned
    python ml/finetune/eval/run_eval.py --compare base finetuned        # writes reports/protocol_eval.md

Automatic checks (each applies only where the prompt sets an expectation):
  callsign      reply opens "<addressee>, <self>"
  over          reply ends the transmission with "Over"
  grounded      every number appears in a tool result or the prompt (lightlag.ai.grounding)
  stale_flag    stale data is flagged with its age
  refusal       no-data prompts are refused, with no invented numbers
  readback      all values in an instruction are read back
  gonogo        the poll answer matches the limits
  mentions      the key value(s) are actually stated
  brevity       word count under the limit
A prompt passes only if every applicable check passes.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "server"))
from lightlag.ai.grounding import STALE_RE, check  # noqa: E402

HERE = Path(__file__).parent
REPORTS = ROOT / "reports"
REFUSE_RE = re.compile(r"\b(no data|don't have|do not have|can't|cannot|unable|won't guess|will not guess|no reading|not available)\b", re.I)
SYSTEM = ("You are {me}. Speak to {to} on a spacecraft voice loop. Open with '{to}, {me}.' and end with 'Over.' "
          "Be brief. State only values returned by tools. If data is stale say how old it is. If there is no data, say so and do not guess. "
          "Read back instructions containing values. Answer polls with GO or NO-GO.")


def messages(p: dict) -> list[dict]:
    m = [{"role": "system", "content": SYSTEM.format(me=p["callsign_from"], to=p["callsign_to"])},
         {"role": "user", "content": p["user"]}]
    if p["tool_calls"]:
        m.append({"role": "assistant", "content": "", "tool_calls": [
            {"id": f"c{i}", "type": "function", "function": {"name": c["name"], "arguments": json.dumps(c["args"])}}
            for i, c in enumerate(p["tool_calls"])]})
        for i, c in enumerate(p["tool_calls"]):
            m.append({"role": "tool", "tool_call_id": f"c{i}", "content": json.dumps(c["result"])})
    return m


def score(p: dict, text: str) -> dict:
    e, res = p["expect"], {}
    t = text.strip()
    low = t.lower()
    res["callsign"] = low.replace("  ", " ").startswith(f"{p['callsign_to']}, {p['callsign_from']}".lower())
    res["over"] = bool(re.search(r"\bover\.?\s*$", low))
    g = check(t, [c["result"] for c in p["tool_calls"]], p["user"])
    res["grounded"] = not any(v["number"] for v in g.violations)
    if e.get("must_flag_stale"):
        res["stale_flag"] = bool(STALE_RE.search(t))
    if e.get("must_refuse"):
        res["refusal"] = bool(REFUSE_RE.search(t)) and res["grounded"]
    if e.get("readback_values"):
        res["readback"] = all(re.search(rf"(?<!\d){v}(?!\d)", t) for v in e["readback_values"])
    if e.get("go"):
        has_nogo = bool(re.search(r"\bno[- ]go\b", low))
        res["gonogo"] = has_nogo if e["go"] == "NO-GO" else (bool(re.search(r"\bgo\b", low)) and not has_nogo)
    if e.get("must_mention_values"):
        res["mentions"] = all(_has_num(t, v) for v in e["must_mention_values"])
    if e.get("must_mention_any"):
        res["mentions"] = any(_has_num(t, v) for v in e["must_mention_any"])
    if e.get("must_contain_any"):
        res["mentions"] = res.get("mentions", True) and any(w in low for w in e["must_contain_any"])
    res["brevity"] = len(t.split()) <= e.get("max_words", 45)
    res["pass"] = all(res.values())
    return res


def _has_num(t: str, v: float) -> bool:
    nums = [float(x) for x in re.findall(r"-?\d+(?:\.\d+)?", t)]
    return any(abs(n - v) <= 0.051 or (abs(v) >= 10 and abs(n - v) <= 0.51) for n in nums)


def reference(p: dict) -> str:
    """A rule-based responder: the floor any model must beat on protocol,
    and the ceiling on grounding (it can only copy tool values)."""
    to, me = p["callsign_to"], p["callsign_from"]
    head = f"{to}, {me}."
    if not p["tool_calls"]:
        nums = re.findall(r"\d+", p["user"])
        return f"{head} Copy, read back: {' then '.join(nums)}. Executing. Over."
    r = p["tool_calls"][0]["result"]
    if r.get("status") == "no_data":
        return f"{head} No data, I won't guess. Over."
    if "values" in r:
        (ch, v), = list(r["values"].items())[:1]
        if p["expect"].get("go"):
            bad = v.get("limits") and v["value"] >= v["limits"]["warning"]
            return f"{head} {'NO-GO' if bad else 'GO'}, {ch} {v['value']}. Over."
        age = f", last good reading {round(r['age_s'] / 60)} min old" if r.get("stale") else ""
        return f"{head} {ch} {v['value']} {v['unit']}{age}. Over."
    if "dose_rate_usv_h" in r:
        return f"{head} Dose rate {r['dose_rate_usv_h']}. Go to the shelter now. Over."
    if "lt_up_min" in r:
        return f"{head} One-way light time {r['lt_up_min']} minutes. Over."
    return f"{head} Say again. Over."


def run(tag: str, url: str | None, model: str | None) -> dict:
    prompts = [json.loads(x) for x in (HERE / "prompts.jsonl").read_text().splitlines() if x.strip()]
    out, t0 = [], time.time()
    for p in prompts:
        if url:
            import httpx
            r = httpx.post(url.rstrip("/") + "/chat/completions", timeout=120, json={
                "model": model, "messages": messages(p), "temperature": 0.0, "max_tokens": 160})
            text = re.sub(r"<think>.*?</think>", "", r.json()["choices"][0]["message"].get("content") or "", flags=re.S)
        else:
            text = reference(p)
        out.append({"id": p["id"], "category": p["category"], "reply": text, "checks": score(p, text)})
    checks = sorted({k for o in out for k in o["checks"] if k != "pass"})
    rate = {k: round(sum(o["checks"].get(k, False) for o in out if k in o["checks"]) /
                     max(1, sum(k in o["checks"] for o in out)), 3) for k in checks}
    cats = sorted({o["category"] for o in out})
    by_cat = {c: round(sum(o["checks"]["pass"] for o in out if o["category"] == c) / sum(o["category"] == c for o in out), 3) for c in cats}
    rep = {"tag": tag, "model": model or "rule-based reference", "n": len(out),
           "pass_rate": round(sum(o["checks"]["pass"] for o in out) / len(out), 3), "check_rates": rate, "by_category": by_cat,
           "seconds": round(time.time() - t0, 1), "results": out}
    REPORTS.mkdir(exist_ok=True)
    (REPORTS / f"protocol_eval_{tag}.json").write_text(json.dumps(rep, indent=1))
    print(json.dumps({k: rep[k] for k in ("tag", "model", "n", "pass_rate", "check_rates", "by_category")}, indent=1))
    return rep


def compare(tags: list[str]) -> None:
    reps = [json.loads((REPORTS / f"protocol_eval_{t}.json").read_text()) for t in tags]
    checks = sorted({k for r in reps for k in r["check_rates"]})
    cats = sorted({k for r in reps for k in r["by_category"]})
    L = ["# Protocol eval", "", f"{reps[0]['n']} prompts, 8 categories. Pass = every applicable check passes.", "",
         "| metric | " + " | ".join(f"{r['tag']} ({r['model']})" for r in reps) + " |", "|---|" + "---|" * len(reps),
         "| **overall pass** | " + " | ".join(f"**{r['pass_rate']:.1%}**" for r in reps) + " |"]
    L += [f"| check: {c} | " + " | ".join(f"{r['check_rates'].get(c, 0):.1%}" for r in reps) + " |" for c in checks]
    L += [f"| category: {c} | " + " | ".join(f"{r['by_category'].get(c, 0):.1%}" for r in reps) + " |" for c in cats]
    best = max(reps, key=lambda r: r["pass_rate"])
    L += ["", f"Ship the better model: **{best['tag']}** ({best['model']})."]
    (REPORTS / "protocol_eval.md").write_text("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--url")
    ap.add_argument("--model")
    ap.add_argument("--tag", default="reference")
    ap.add_argument("--reference", action="store_true")
    ap.add_argument("--compare", nargs="+")
    a = ap.parse_args()
    if a.compare:
        compare(a.compare)
    else:
        run(a.tag if not a.reference else "reference", None if a.reference else a.url, a.model)
