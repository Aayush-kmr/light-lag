"""Science data in flight: the payload fills a recorder, the recorder drains
through the same antenna as everything else.

* Generation. Every instrument in the design's payload produces its
  catalogued `data_mbit_day` while the science payload is powered
  (`ShipSystems.science_on`; TERN can shed it). Radiation instruments
  (`serves.radiation > 0`) run in cruise and in orbit; cameras and
  spectrometers only after Mars arrival.
* Recorder. A fixed capacity (MRO's solid-state recorder, 160 Gbit). When
  it is full the oldest data is overwritten and counted as lost.
* Downlink. In a scene the recorder is packaged into fixed-size `science`
  bundles and handed to the existing DTN network from the ship node, so
  science competes with telemetry, alerts and letters through the normal
  priority classes and the link rate (comms/network.py meters big bundles).
* Time skips. Between scenes nothing is packaged. The ledger accounts
  analytically, one day at a time: data generated vs. what the link could
  carry (pass hours for the plan x data rate at that distance, nothing
  behind the Sun).
* The Mission Scientist's lever (`science_first` / `crew_first`) is itself a
  command bundle: it changes the class of science aboard only when it
  arrives, a light time later, and never through the blackout or the
  command moratorium.

Conservation holds at all times:
    generated = delivered + on the recorder + in flight + lost
("in flight" counts chunks packaged aboard and waiting for the antenna, and
chunks crossing space.)

Every coefficient here is MODEL unless it says otherwise; see
docs/science.md, "Science data".
"""
from __future__ import annotations

import math
from collections import deque

from ..astro.conjunction import BLACKOUT_DEG
from ..astro.constants import DAY_S
from ..astro.dsn import COMMS_PLANS
from ..catalog import catalog
from ..comms.bundle import BULK, EID_CONTROL, EID_SHIP, NORMAL
from ..comms.network import make_bundle

# MRO's solid-state recorder holds 160 gigabits (NASA MRO launch press kit,
# 2005; also the MRO arrival press kit, 2006). SOURCED.
RECORDER_MBIT = 160_000.0
CHUNK_MBIT = 250.0            # MODEL: one science bundle; big enough that a scene makes a handful, not thousands
MAX_PACKAGED = 2              # MODEL: chunks packaged ahead of the antenna
LIFETIME_S = 30 * DAY_S       # MODEL: a chunk that cannot leave in 30 days is dropped (counted lost)
# MODEL: share of downlink time science gets during a time skip; the rest is
# telemetry, commands, receipts, letters and ranging.
SCIENCE_SHARE = {"crew_first": 0.60, "science_first": 0.85}
PRIORITY = {"science_first": NORMAL, "crew_first": BULK}
PRIORITY_LABEL = {"science_first": "Science first", "crew_first": "Crew first"}
DEFAULT_PAYLOAD = ["grs_class", "ctx_class", "rad_class"]
MILESTONES = (0.1, 0.25, 0.5, 1.0)


def _gbit(mbit: float) -> float:
    return round(mbit / 1000.0, 3)


def plan_hours_per_day(plan: str) -> float:
    """MODEL: antenna hours per day the plan buys (passes x hours, capped at 24)."""
    p = COMMS_PLANS.get(plan) or COMMS_PLANS["standard"]
    return min(24.0, float(p["passes_per_day"]) * float(p["hours"]))


class ScienceLedger:
    def __init__(self, u):
        self.u = u
        self.generated: dict[str, float] = {}      # Mbit, per instrument
        self.delivered: dict[str, float] = {}
        self.lost: dict[str, float] = {}
        # The recorder is FIFO: a deque of blocks, each {instrument: Mbit}. A block
        # grows to CHUNK_MBIT before the next one starts, so the deque stays short.
        self.recorder: deque[dict[str, float]] = deque()
        self._stored = 0.0
        self.flight: dict[str, dict[str, float]] = {}   # bundle id -> parts (packaged aboard or crossing space)
        self.aboard_priority = "crew_first"
        self.aboard_since_et: float | None = None
        self.uplink: dict | None = None             # Earth's record of the last priority order it sent
        self.milestones: set[float] = set()
        self.events: list[dict] = []                # notable moments, for the crew's barks and the debrief

    # ------------------------------------------------------------ design contract
    def payload(self) -> list[str]:
        p = self.u.design.get("payload", DEFAULT_PAYLOAD) or []
        return [i for i in p if catalog().get("instruments", i)]

    def objective(self) -> str:
        return self.u.design.get("objective", "ice") or "ice"

    def objective_info(self) -> dict:
        return catalog().get("objectives", self.objective()) or {"id": self.objective(), "name": self.objective(),
                                                                 "min_data_gbit": 0}

    @staticmethod
    def _inst(i: str) -> dict:
        return catalog().get("instruments", i) or {}

    def _serves(self, i: str, obj: str | None = None) -> float:
        return float((self._inst(i).get("serves") or {}).get(obj or self.objective(), 0.0))

    def is_radiation(self, i: str) -> bool:
        return float((self._inst(i).get("serves") or {}).get("radiation", 0.0)) > 0.0

    def rates_mbit_s(self, et: float, science_on: bool) -> dict[str, float]:
        """What each instrument is writing to the recorder right now."""
        if not science_on or "launch" not in self.u.anchors:
            return {}
        arrived = et >= self.u.anchors.get("arrival", float("inf"))
        out = {}
        for i in self.payload():
            if self.is_radiation(i) or arrived:
                out[i] = float(self._inst(i).get("data_mbit_day", 0.0)) / DAY_S
        return out

    # ------------------------------------------------------------ recorder
    def stored_mbit(self) -> float:
        return self._stored

    def in_flight_mbit(self) -> float:
        return sum(sum(p.values()) for p in self.flight.values())

    def _record(self, i: str, mbit: float) -> None:
        if mbit <= 0:
            return
        self.generated[i] = self.generated.get(i, 0.0) + mbit
        if not self.recorder or sum(self.recorder[-1].values()) >= CHUNK_MBIT:
            self.recorder.append({})
        blk = self.recorder[-1]
        blk[i] = blk.get(i, 0.0) + mbit
        self._stored += mbit

    def _take(self, mbit: float) -> dict[str, float]:
        """Remove `mbit` from the head of the recorder (oldest first)."""
        out: dict[str, float] = {}
        need = min(mbit, self._stored)
        while need > 1e-9 and self.recorder:
            blk = self.recorder[0]
            tot = sum(blk.values())
            if tot <= need + 1e-9:
                self.recorder.popleft()
                for i, v in blk.items():
                    out[i] = out.get(i, 0.0) + v
                need -= tot
                self._stored -= tot
            else:
                f = need / tot
                for i in list(blk):
                    take = blk[i] * f
                    blk[i] -= take
                    out[i] = out.get(i, 0.0) + take
                self._stored -= need
                need = 0.0
        if not self.recorder:
            self._stored = 0.0
        return out

    def _put_back(self, parts: dict[str, float]) -> None:
        if parts:
            self.recorder.appendleft(dict(parts))
            self._stored += sum(parts.values())

    def _overflow(self) -> None:
        excess = self._stored - RECORDER_MBIT
        if excess > 1e-9:
            gone = self._take(excess)
            for i, v in gone.items():
                self.lost[i] = self.lost.get(i, 0.0) + v
            if "overwrite" not in {e["kind"] for e in self.events[-5:]}:
                self._event("overwrite", f"Recorder full: overwriting the oldest {_gbit(excess)} Gbit.")

    def _event(self, kind: str, text: str, **extra) -> None:
        self.events.append({"et": self.u.et, "kind": kind, "text": text, **extra})
        del self.events[:-60]

    # ------------------------------------------------------------ in a scene
    def tick(self, dt: float) -> None:
        u = self.u
        for i, r in self.rates_mbit_s(u.et, u.ship_sys.science_on).items():
            self._record(i, r * dt)
        self._overflow()
        self._reconcile()
        self._package()
        self._milestones()

    def _packaged_waiting(self) -> int:
        n = 0
        for bid in self.flight:
            b = self.u.net.by_id.get(bid)
            if b is not None and b.state == "queued":
                n += 1
        return n

    def _package(self) -> None:
        u = self.u
        waiting = self._packaged_waiting()
        while waiting < MAX_PACKAGED and self._stored >= CHUNK_MBIT - 1e-9:
            parts = self._take(CHUNK_MBIT)
            mbit = sum(parts.values())
            b = make_bundle(EID_SHIP, EID_CONTROL, "science", u.et,
                            {"label": "SCIENCE", "mbit": round(mbit, 3), "parts": parts,
                             "instruments": sorted(parts)},
                            PRIORITY[self.aboard_priority], lifetime_s=LIFETIME_S, size_bytes=int(mbit * 1e6 / 8))
            u.net.submit(b)
            self.flight[b.id] = parts
            waiting += 1

    def _reconcile(self) -> None:
        """A chunk whose lifetime ran out in storage or in flight is lost."""
        for bid in list(self.flight):
            b = self.u.net.by_id.get(bid)
            if b is not None and b.state == "expired":
                for i, v in self.flight.pop(bid).items():
                    self.lost[i] = self.lost.get(i, 0.0) + v

    def _milestones(self) -> None:
        floor = float(self.objective_info().get("min_data_gbit") or 0.0)
        if floor <= 0:
            return
        rel = sum(v for i, v in self.generated.items() if self._serves(i) > 0) / 1000.0
        for m in MILESTONES:
            if m not in self.milestones and rel >= m * floor:
                self.milestones.add(m)
                self._event("milestone", f"{round(m * 100)}% of the objective's data has been recorded aboard.",
                            pct=round(m * 100), gbit=round(rel, 1))

    # ------------------------------------------------------------ time skips
    def before_coast(self) -> None:
        """Chunks still waiting for the antenna go back onto the recorder; the
        skip is accounted analytically instead."""
        u = self.u
        if u.net is None:
            return
        store = u.net.stores[EID_SHIP]
        while True:
            b = store.pop_where(lambda x: x.kind == "science")
            if b is None:
                break
            parts = self.flight.pop(b.id, None) or dict(b.payload.get("parts") or {})
            b.state, b.note = "rejected", "returned to the recorder for the time skip"
            self._put_back(parts)

    def coast(self, t0: float, t1: float) -> None:
        """Analytic accounting over [t0, t1], one day (or less) at a time."""
        u = self.u
        if t1 <= t0 or u.cp is None or "launch" not in u.anchors:
            return
        n = max(1, math.ceil((t1 - t0) / DAY_S))
        step = (t1 - t0) / n
        hours = plan_hours_per_day(u.cp.plan)
        share = SCIENCE_SHARE.get(self.aboard_priority, 0.6)
        on = u.ship_sys.science_on
        hga = u.cp.hga_error_deg
        u.cp.hga_error_deg = 0.05            # between scenes the antenna is on point
        try:
            for k in range(n):
                mid = t0 + (k + 0.5) * step
                for i, r in self.rates_mbit_s(mid, on).items():
                    self._record(i, r * step)
                sep = u.ephem.sep_angle_deg(mid)
                if sep >= BLACKOUT_DEG:
                    rate = u.cp.data_rate_bps(u.cp.margin_db(mid, sep))
                    cap = rate * 1e-6 * step * (hours / 24.0) * share       # Mbit
                    for i, v in self._take(cap).items():
                        self.delivered[i] = self.delivered.get(i, 0.0) + v
                self._overflow()
        finally:
            u.cp.hga_error_deg = hga
        self._milestones()

    # ------------------------------------------------------------ deliveries
    def on_delivered(self, b) -> None:
        u = self.u
        P = b.payload
        if b.kind == "science" and b.dest == EID_CONTROL:
            parts = self.flight.pop(b.id, None)
            if parts is None:
                parts = dict(P.get("parts") or {})
            bucket = self.delivered if b.state == "delivered" else self.lost
            for i, v in parts.items():
                bucket[i] = bucket.get(i, 0.0) + v
            if b.state == "delivered":
                u.emit(("control", "spectator"), "science_rx", mbit=P.get("mbit"), instruments=P.get("instruments"),
                       delivered_gbit=_gbit(sum(self.delivered.values())), lt_s=b.true_lt_s)
        elif b.kind == "science_priority" and b.dest == EID_SHIP and b.state == "delivered":
            self._apply_priority(P.get("value", "crew_first"), b)
        elif b.kind == "status" and b.dest == EID_CONTROL and self.uplink and P.get("ref") == self.uplink["bundle"]:
            self.uplink["state"] = "confirmed" if P.get("status") == "delivered" else P.get("status", "unknown")
            self.uplink["confirmed_et"] = u.et

    def _apply_priority(self, value: str, b) -> None:
        u = self.u
        if value not in PRIORITY:
            return
        self.aboard_priority = value
        self.aboard_since_et = u.et
        # chunks already packaged and waiting take the new class of service
        store = u.net.stores[EID_SHIP]
        moved = []
        while True:
            x = store.pop_where(lambda z: z.kind == "science")
            if x is None:
                break
            moved.append(x)
        for x in moved:
            x.priority = PRIORITY[value]
            store.put(x)
        self._event("priority", f"Lantern Science: {PRIORITY_LABEL[value].lower()} (sent {u._ago(b.created_et)} ago).",
                    value=value)
        u.note("ship", "science", f"Science priority now {value} aboard (order sent {u._ago(b.created_et)} ago).")

    # ------------------------------------------------------------ the lever (Earth)
    def may_set_priority(self, role: str) -> bool:
        return role == "scientist" or (role == "control" and self.u.seat_is_ai("scientist"))

    def request_priority(self, role: str, value: str) -> dict:
        u = self.u
        if u.phase != "mission" or u.net is None:
            return {"ok": False, "error": "The mission has not launched."}
        if value not in PRIORITY:
            return {"ok": False, "error": f"Unknown priority '{value}'."}
        if not self.may_set_priority(role):
            return {"ok": False, "error": "The Mission Scientist sets science priority."}
        b = make_bundle(EID_CONTROL, EID_SHIP, "science_priority", u.et,
                        {"value": value, "label": "SCI " + PRIORITY_LABEL[value].upper(), "from": "Lantern Science"},
                        NORMAL, lifetime_s=40 * DAY_S, report=True)
        u.net.submit(b)
        self.uplink = {"value": value, "bundle": b.id, "sent_et": u.et, "state": "queued"}
        u.note("control", "science", f"Lantern Science uplinked science priority: {value}.", bundle=b.id)
        return {"ok": True, "note": f"{PRIORITY_LABEL[value]}: queued for uplink. It takes effect aboard only when it arrives.",
                "bundle": b.id}

    def uplink_view(self) -> dict | None:
        """What Earth can know about its own order: its bundle, the predicted
        arrival, and the receipt when (if) it comes back."""
        if not self.uplink:
            return None
        u = self.u
        v = dict(self.uplink)
        b = u.net.by_id.get(v["bundle"]) if u.net else None
        if v["state"] not in ("confirmed", "expired"):
            if b is None or b.state == "queued":
                L = u.link
                v["state"] = "queued"
                v["why"] = ("held: the Sun is in the way" if L and L.blackout else
                            "held: no commands this close to the Sun" if L and L.moratorium else
                            "waiting for an antenna" if L and not L.uplink_open else "leaving now")
            elif b.arrive_et is not None:
                v["arrive_et"] = b.arrive_et
                v["state"] = "in_flight" if u.et < b.arrive_et else "arrived_unconfirmed"
                v["arrives_in_s"] = max(0.0, b.arrive_et - u.et)
        v["label"] = PRIORITY_LABEL[v["value"]]
        return v

    # ------------------------------------------------------------ views
    def totals(self) -> dict:
        gen = sum(self.generated.values())
        return {"generated_mbit": gen, "delivered_mbit": sum(self.delivered.values()), "stored_mbit": self._stored,
                "in_flight_mbit": self.in_flight_mbit(), "lost_mbit": sum(self.lost.values())}

    def conservation_error_mbit(self) -> float:
        t = self.totals()
        return t["generated_mbit"] - (t["delivered_mbit"] + t["stored_mbit"] + t["in_flight_mbit"] + t["lost_mbit"])

    def value_gbit(self, obj: str | None = None) -> float:
        obj = obj or self.objective()
        return sum(v / 1000.0 * self._serves(i, obj) for i, v in self.delivered.items())

    def objective_status(self) -> dict:
        info = self.objective_info()
        obj = info.get("id", self.objective())
        floor = float(info.get("min_data_gbit") or 0.0)
        relevant = sum(v for i, v in self.delivered.items() if self._serves(i, obj) > 0) / 1000.0
        covered = any(self._serves(i, obj) > 0 for i in self.payload())
        return {"id": obj, "name": info.get("name", obj), "min_data_gbit": floor, "relevant_gbit": round(relevant, 3),
                "value_gbit": round(self.value_gbit(obj), 3), "covered": covered,
                "met": bool(covered and relevant >= floor), "progress": round(min(1.0, relevant / floor), 4) if floor else 1.0}

    def summary(self) -> dict:
        """Compact, ship-side summary: rides in every telemetry frame."""
        t = self.totals()
        return {"gen": _gbit(t["generated_mbit"]), "rec": _gbit(t["stored_mbit"]), "fly": _gbit(t["in_flight_mbit"]),
                "lost": _gbit(t["lost_mbit"]), "cap": _gbit(RECORDER_MBIT), "prio": self.aboard_priority,
                "on": bool(self.u.ship_sys.science_on)}

    def _instruments_public(self) -> list[dict]:
        out = []
        for i in self.payload():
            inst = self._inst(i)
            out.append({"id": i, "name": inst.get("name", i), "data_mbit_day": inst.get("data_mbit_day"),
                        "radiation": self.is_radiation(i), "serves": round(self._serves(i), 2)})
        return out

    def public(self, role: str) -> dict:
        from ..catalog import side_of
        side = side_of(role)
        u = self.u
        base = {"payload": self._instruments_public(), "objective": self.objective_status(),
                "chunk_gbit": _gbit(CHUNK_MBIT), "recorder_capacity_gbit": _gbit(RECORDER_MBIT)}
        aboard = {**{k.replace("_mbit", "_gbit"): _gbit(v) for k, v in self.totals().items() if k != "delivered_mbit"},
                  "priority": self.aboard_priority, "science_on": bool(u.ship_sys.science_on),
                  "recorder_frac": round(self._stored / RECORDER_MBIT, 4),
                  "rates_mbit_day": {i: round(r * DAY_S, 1) for i, r in self.rates_mbit_s(u.et, u.ship_sys.science_on).items()},
                  "events": self.events[-8:]}
        earth = {"delivered_gbit": _gbit(sum(self.delivered.values())),
                 "delivered_by_instrument": {i: _gbit(v) for i, v in self.delivered.items()},
                 "lever": self.uplink_view(), "may_set": self.may_set_priority(role) if side == "control" else False}
        if side == "ship":
            return {**base, "aboard": aboard}
        ctl = u.ctrl.get("science")
        asof = {"summary": ctl, "sample_et": u.ctrl.get("science_sample_et"),
                "age_s": (u.et - u.ctrl["science_sample_et"]) if u.ctrl.get("science_sample_et") is not None else None}
        if side == "control":
            return {**base, "earth": earth, "as_of": asof}
        return {**base, "earth": earth, "as_of": asof, "aboard": aboard,
                "conservation_error_mbit": round(self.conservation_error_mbit(), 6)}

    def score(self) -> dict:
        t = self.totals()
        st = self.objective_status()
        return {"generated_gbit": _gbit(t["generated_mbit"]), "delivered_gbit": _gbit(t["delivered_mbit"]),
                "lost_gbit": _gbit(t["lost_mbit"]), "on_recorder_gbit": _gbit(t["stored_mbit"]),
                "in_flight_gbit": _gbit(t["in_flight_mbit"]), "value_gbit": st["value_gbit"],
                "objective": st["id"], "objective_name": st["name"], "min_data_gbit": st["min_data_gbit"],
                "relevant_gbit": st["relevant_gbit"], "payload_covers_objective": st["covered"], "objective_met": st["met"]}
