"""TERN (onboard, 3-4B class) and FIDO (Lantern, 7-8B class).

Pipeline for every question:
  1. LLM with tool calling (up to 3 rounds) -> draft reply
  2. grounding.check(draft, tool results)
  3. on violation: one retry with the violations quoted back to the model
  4. still violating, or no LLM: deterministic fallback (fallback.py)
Every step is recorded so the client's debug drawer can show exactly which
tools ran and why a reply was accepted or replaced.
"""
from __future__ import annotations

import json
import re
import time

from . import fallback, grounding
from . import tools as T
from .llm import LLMUnavailable, LocalLLM

PROTOCOL = """You speak on a spacecraft voice loop. Rules:
- Open with the addressee then yourself ("{to}, {me}."). End with "Over."
- Brevity: 1-3 short sentences. No filler, no exclamation marks.
- Numbers: state ONLY values returned by your tools in this turn. Never estimate, never invent telemetry.
- If a tool result is stale or has an age, say how old it is ("last good reading, 7 min old").
- If a tool returns no_data, say you have no data. Do not guess.
- Read back any instruction that contains values before acting on it.
- When asked for a poll, answer exactly GO or NO-GO, then the reason.
/no_think"""

PERSONAS = {
    "ship": dict(name="TERN", to="Commander", me="TERN", side="ship",
                 role="You are TERN, the onboard AI of Meridian-1, a crewed Mars transit. You speak to Commander Imani "
                      "Reyes. You run offline on the ship's computer. Your sensors are local, so your numbers are "
                      "current. You are calm, exact and kind, in very few words."),
    "control": dict(name="FIDO", to="Flight", me="FIDO", side="control",
                    role="You are FIDO, the flight dynamics assistant at Lantern Control in Houston. You speak to the "
                         "Flight Director. Everything you know about the ship arrived by radio and is at least one "
                         "light-time old; always say how old."),
}

THINK_RE = re.compile(r"<think>.*?</think>", re.S)


class Agent:
    def __init__(self, side: str, base_url: str, model: str, enabled: bool = True, timeout: float = 20.0,
                 allow_remote: bool = False):
        self.p = PERSONAS[side]
        self.side = side
        self.model = model
        self.llm: LocalLLM | None = None
        self.error = ""
        if enabled:
            try:
                self.llm = LocalLLM(base_url, model, timeout, allow_remote)
            except LLMUnavailable as e:
                self.error = str(e)

    def status(self) -> dict:
        up = bool(self.llm and self.llm.available())
        return {"name": self.p["name"], "model": self.model, "llm_available": up,
                "mode": "local LLM + grounding" if up else "deterministic fallback", "error": self.error}

    def system(self) -> str:
        return self.p["role"] + "\n\n" + PROTOCOL.format(to=self.p["to"], me=self.p["me"])

    def respond(self, u, question: str) -> dict:
        t0 = time.time()
        trace: dict = {"agent": self.p["name"], "question": question, "tool_calls": [], "attempts": []}
        if self.llm and self.llm.available():
            try:
                text, results = self._llm_turn(u, question, trace)
                rep = grounding.check(text, results, question)
                trace["attempts"].append({"text": text, "grounding": rep.public()})
                if not rep.ok:
                    fix = "Your reply broke the grounding rules: " + json.dumps(rep.violations) + \
                          ". Rewrite it using only the tool values, and state ages for stale data."
                    text, results2 = self._llm_turn(u, question, trace, correction=(text, fix))
                    results += results2
                    rep = grounding.check(text, results, question)
                    trace["attempts"].append({"text": text, "grounding": rep.public()})
                if rep.ok:
                    return self._done(trace, text, "llm", rep, t0)
                trace["fallback_reason"] = "grounding failed twice"
            except LLMUnavailable as e:
                trace["fallback_reason"] = f"LLM unavailable: {e}"
        else:
            trace["fallback_reason"] = "no local LLM running"
        text, calls = fallback.respond(u, self.side, question)
        trace["tool_calls"] += calls
        rep = grounding.check(text, [c["result"] for c in calls], question)
        return self._done(trace, text, "fallback", rep, t0)

    def _llm_turn(self, u, question: str, trace: dict, correction: tuple | None = None):
        msgs = [{"role": "system", "content": self.system()}, {"role": "user", "content": question}]
        if correction:
            msgs += [{"role": "assistant", "content": correction[0]}, {"role": "user", "content": correction[1]}]
        results: list[dict] = []
        tools = T.openai_tools(self.side)
        for _ in range(3):
            out = self.llm.chat(msgs, tools=tools)
            if not out["tool_calls"]:
                return THINK_RE.sub("", out["content"]).strip(), results
            msgs.append({"role": "assistant", "content": out["content"] or "",
                         "tool_calls": [{"id": c["id"], "type": "function",
                                         "function": {"name": c["name"], "arguments": json.dumps(c["args"])}}
                                        for c in out["tool_calls"]]})
            for c in out["tool_calls"]:
                r = T.call(u, self.side, c["name"], c["args"])
                results.append(r)
                trace["tool_calls"].append({"name": c["name"], "args": c["args"], "result": r})
                msgs.append({"role": "tool", "tool_call_id": c["id"], "content": json.dumps(r)})
        out = self.llm.chat(msgs)
        return THINK_RE.sub("", out["content"]).strip(), results

    def _done(self, trace, text, source, rep, t0) -> dict:
        trace.update(text=text, source=source, grounding=rep.public(), latency_ms=round(1000 * (time.time() - t0)))
        return trace
