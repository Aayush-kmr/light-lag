"""Grounding guardrail: an AI voice may only state numbers that came back
from a tool call (or that the human just said, for read-backs).

Every number in a model reply is extracted and matched against the numeric
leaves of this turn's tool results, allowing for display rounding and the
unit conversions a speaker naturally makes (s -> min -> h -> days,
km -> million km, uSv -> mSv). A number with no source is a violation.

Second rule: if a value came from a stale record, the reply must say so
("last good reading, 7 min old"). Stale-but-unflagged is a violation.

Third rule: identifiers are not measurements. Callsigns and designators
like "Meridian-1", "DSS-14", "Apollo 11", "loop A" are stripped first.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

IDENTIFIERS = [r"Meridian-1", r"DSS-\d+", r"Apollo \d+", r"Gemini \w+", r"[A-Z]\d+\.\d+-class", r"[XMC]\d+(?:\.\d+)?",
               r"\bS\d{2}[EW]\d{2}\b", r"\bN\d{2}[EW]\d{2}\b", r"\bv\d+\b", r"policy v\d+", r"AR ?\d{5}", r"GLE ?\d+",
               r"\b20\d\d-\d\d-\d\d(?:T[\d:]+Z?)?\b", r"Lantern-\d", r"b\d{5}", r"\bCO2\b", r"ppCO2", r"\bH2O\b"]
NUM_RE = re.compile(r"(?<![\w.])[-+]?\d+(?:[.,]\d+)?(?![\w])")
STALE_RE = re.compile(r"\b(old|ago|stale|last good|as of|delayed|minutes? behind|age)\b", re.I)
CONVERSIONS = (1.0, 1 / 60.0, 1 / 3600.0, 1 / 86400.0, 1e-6, 1e-3, 1e3, 100.0, 0.01)


@dataclass
class Report:
    ok: bool
    numbers: list[str] = field(default_factory=list)
    violations: list[dict] = field(default_factory=list)
    stale_values_used: bool = False
    stale_flagged: bool = True

    def public(self) -> dict:
        return self.__dict__.copy()


def _leaves(obj, stale_ctx: bool, out: list[tuple[float, bool]]):
    if isinstance(obj, dict):
        st = stale_ctx or bool(obj.get("stale")) or obj.get("status") == "stale"
        for k, v in obj.items():
            if k in ("limits",):
                _leaves(v, False, out)
            else:
                _leaves(v, st, out)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _leaves(v, stale_ctx, out)
    elif isinstance(obj, bool):
        return
    elif isinstance(obj, (int, float)):
        out.append((float(obj), stale_ctx))
    elif isinstance(obj, str):
        for m in re.findall(r"\d+(?:\.\d+)?", obj):
            try:
                out.append((float(m.replace(",", "")), stale_ctx))
            except ValueError:
                pass


def _decimals(s: str) -> int:
    s = s.replace(",", "")
    return len(s.split(".")[1]) if "." in s else 0


def _matches(x: float, dec: int, v: float) -> bool:
    tol = 0.5 * 10 ** (-dec) + 1e-9
    for k in CONVERSIONS:
        w = v * k
        if abs(abs(x) - abs(w)) <= tol:
            return True
        if dec == 0 and abs(x) >= 10 and abs(abs(x) - abs(w)) <= 0.51:
            return True
    return False


def strip_identifiers(text: str) -> str:
    for pat in IDENTIFIERS:
        text = re.sub(pat, " ", text)
    return text


def check(text: str, tool_results: list[dict], prompt: str = "", extra_allowed: list[float] | None = None) -> Report:
    leaves: list[tuple[float, bool]] = []
    for r in tool_results:
        _leaves(r, False, leaves)
    for m in NUM_RE.findall(strip_identifiers(prompt or "")):
        leaves.append((float(m.replace(",", "")), False))
    for v in extra_allowed or []:
        leaves.append((float(v), False))
    body = strip_identifiers(text)
    nums = NUM_RE.findall(body)
    rep = Report(ok=True, numbers=nums)
    for s in nums:
        try:
            x = float(s.replace(",", ""))
        except ValueError:
            continue
        dec = _decimals(s)
        srcs = [st for v, st in leaves if _matches(x, dec, v)]
        if not srcs:
            rep.violations.append({"number": s, "reason": "not found in any tool result (possible invented telemetry)"})
        elif all(srcs):
            rep.stale_values_used = True
    if rep.stale_values_used and not STALE_RE.search(text):
        rep.stale_flagged = False
        rep.violations.append({"number": None, "reason": "used a stale value without saying how old it is"})
    rep.ok = not rep.violations
    return rep
