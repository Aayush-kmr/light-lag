"""TERN's autonomy policy: what the onboard AI may do without asking.

Control drafts the policy in Mission Design and can revise it before the
blackout. Each rule grants one of three permissions:

    auto   - TERN acts, then reports.
    ask    - TERN requests an exception from Lantern and waits.
    forbid - TERN never acts on its own.

The one rule above every rule: **if the time until harm is shorter than the
time it would take to ask and hear back, TERN acts to protect the crew and
logs the policy break with its reasoning.** That inequality,

    time_to_harm  <  round_trip_to_Earth (+ margin),

is the whole argument for spacecraft autonomy at Mars, and the game makes
players feel it: at 4 light-minutes "ask" is fine; at 22 light-minutes it
is risky; behind the Sun it is impossible.

Decisions are deterministic code. The LLM only *explains* them.
"""
from __future__ import annotations

import copy
from dataclasses import asdict, dataclass, field

from ..astro.timescale import et_to_iso

DEFAULT_POLICY = [
    {"id": "shelter_alert", "label": "Sound the storm alarm and recommend shelter on flare / proton detection",
     "permission": "auto", "params": {"dose_rate_usv_h": 1000}, "safety": True},
    {"id": "storm_attitude", "label": "Turn propellant tanks sunward during a proton storm (Earth link drops)",
     "permission": "ask", "params": {}, "safety": True},
    {"id": "scrubber_cycle", "label": "Command a CO2 scrubber bed swap when ppCO2 is high",
     "permission": "auto", "params": {"ppco2_mmhg": 4.0}, "safety": True},
    {"id": "pump_failover", "label": "Switch coolant loops when the active loop runs hot",
     "permission": "auto", "params": {"loop_c": 12.0}, "safety": True},
    {"id": "load_shed_science", "label": "Power down the science payload when the battery is low",
     "permission": "ask", "params": {"soc_pct": 35.0}, "safety": True},
    {"id": "hga_repoint", "label": "Re-point the high-gain antenna when link margin drops",
     "permission": "auto", "params": {"link_db": 3.0}, "safety": False},
    {"id": "execute_uplink", "label": "Execute uplinked commands without crew confirmation",
     "permission": "forbid", "params": {}, "safety": False},
]

PERMISSIONS = ("auto", "ask", "forbid")


@dataclass
class Decision:
    et: float
    rule: str
    trigger: str
    permission: str
    outcome: str                 # executed | requested | held | broke_policy | approved | denied
    rationale: str
    time_to_harm_s: float | None = None
    round_trip_s: float | None = None
    action: str | None = None
    snapshot: dict = field(default_factory=dict)

    def public(self) -> dict:
        d = asdict(self)
        d["iso"] = et_to_iso(self.et)
        d["policy_status"] = {"executed": "within policy", "requested": "exception requested", "held": "held per policy",
                              "broke_policy": "POLICY BREAK", "approved": "approved by Lantern",
                              "denied": "denied by Lantern"}.get(self.outcome, self.outcome)
        return d


class AutonomyEngine:
    def __init__(self, policy: list[dict] | None = None):
        self.policy = copy.deepcopy(policy or DEFAULT_POLICY)
        self.version = 1
        self.signed_by: set[str] = set()
        self.log: list[Decision] = []
        self.pending: dict[str, Decision] = {}      # rule id -> request awaiting answer
        self.active: set[str] = set()               # rules whose action is currently in effect
        self._cool: dict[str, float] = {}

    # --------------------------------------------------------------- policy
    def rule(self, rid: str) -> dict:
        for r in self.policy:
            if r["id"] == rid:
                return r
        raise KeyError(rid)

    def update(self, rules: list[dict], who: str) -> None:
        by_id = {r["id"]: r for r in rules}
        for r in self.policy:
            if r["id"] in by_id:
                n = by_id[r["id"]]
                if n.get("permission") in PERMISSIONS:
                    r["permission"] = n["permission"]
                for k, v in (n.get("params") or {}).items():
                    if k in r["params"]:
                        r["params"][k] = float(v)
        self.version += 1
        self.signed_by = {who}

    def sign(self, who: str) -> None:
        self.signed_by.add(who)

    def public(self) -> dict:
        return {"rules": self.policy, "version": self.version, "signed_by": sorted(self.signed_by),
                "pending": [d.public() for d in self.pending.values()],
                "log": [d.public() for d in self.log[-80:]]}

    # ------------------------------------------------------------- decide
    def decide(self, et: float, rid: str, trigger: str, time_to_harm_s: float | None, round_trip_s: float | None,
               snapshot: dict, crew_safety: bool) -> Decision | None:
        """Returns a decision, or None if already handled/cooling down."""
        if rid in self.active or rid in self.pending:
            return None
        if self._cool.get(rid, -1e18) > et:
            return None
        r = self.rule(rid)
        perm = r["permission"]
        rtt = round_trip_s if round_trip_s is not None else float("inf")
        tth = time_to_harm_s if time_to_harm_s is not None else float("inf")
        insufficient = crew_safety and tth < 1.25 * rtt
        if perm == "auto":
            d = Decision(et, rid, trigger, perm, "executed", "Pre-authorized by Lantern in policy v%d." % self.version,
                         tth, rtt, rid, snapshot)
        elif perm == "ask" and not insufficient:
            d = Decision(et, rid, trigger, perm, "requested",
                         f"Requesting authorization. Time to harm {fmt(tth)} exceeds round trip {fmt(rtt)}.",
                         tth, rtt, None, snapshot)
            self.pending[rid] = d
        elif insufficient:
            d = Decision(et, rid, trigger, perm, "broke_policy",
                         f"Crew safety: time to harm {fmt(tth)} is shorter than the earliest possible answer "
                         f"from Lantern ({fmt(rtt)}). Acting now; Lantern will be informed.",
                         tth, rtt, rid, snapshot)
        else:
            d = Decision(et, rid, trigger, perm, "held", "Policy forbids autonomous action; crew notified.",
                         tth, rtt, None, snapshot)
            self._cool[rid] = et + 3600
        self.log.append(d)
        if d.action:
            self.active.add(rid)
        return d

    def answer(self, et: float, rid: str, approve: bool) -> Decision | None:
        req = self.pending.pop(rid, None)
        if req is None:
            return None
        d = Decision(et, rid, "Lantern reply to request of " + et_to_iso(req.et), req.permission,
                     "approved" if approve else "denied",
                     "Lantern approved the exception." if approve else "Lantern denied the exception.",
                     req.time_to_harm_s, req.round_trip_s, rid if approve else None, {})
        self.log.append(d)
        if approve:
            self.active.add(rid)
        else:
            self._cool[rid] = et + 2 * 3600
        return d

    def escalate_if_needed(self, et: float, rid: str, time_to_harm_s: float, remaining_wait_s: float) -> Decision | None:
        """A pending request whose answer won't arrive in time becomes a policy break."""
        if rid not in self.pending:
            return None
        if time_to_harm_s < 1.25 * remaining_wait_s:
            self.pending.pop(rid)
            d = Decision(et, rid, "request outstanding; harm approaching", self.rule(rid)["permission"], "broke_policy",
                         f"No answer yet and time to harm is {fmt(time_to_harm_s)}; the reply cannot arrive for "
                         f"{fmt(remaining_wait_s)}. Acting to protect the crew.", time_to_harm_s, remaining_wait_s, rid, {})
            self.log.append(d)
            self.active.add(rid)
            return d
        return None

    def release(self, rid: str, et: float) -> None:
        self.active.discard(rid)
        self._cool[rid] = et + 1800


def fmt(s: float | None) -> str:
    if s is None or s == float("inf"):
        return "unbounded"
    s = float(s)
    if s < 120:
        return f"{s:.0f} s"
    if s < 7200:
        return f"{s / 60:.0f} min"
    if s < 3 * 86400:
        return f"{s / 3600:.1f} h"
    return f"{s / 86400:.1f} days"
