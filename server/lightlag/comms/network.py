"""Two-node DTN: Lantern Control <-> Meridian-1.

Each tick, for each direction whose contact is open, bundles are popped in
class-of-service order and serialized onto the link at the current data
rate. Each one then propagates at the speed of light: its arrival time is
`tx_et + one_way_light_time(tx_et)`, computed from SPICE ('XCN'). Arrival is
an event in the universe's event queue - nothing jumps ahead of light.
"""
from __future__ import annotations

from collections import deque

from .bundle import BULK, EID_CONTROL, EID_HOME, EID_SHIP, NORMAL, Bundle, BundleStore

# Anything that changes what the spacecraft does counts as commanding and is
# held during a solar-conjunction command moratorium.
COMMAND_KINDS = {"command", "policy", "policy_answer", "science_priority"}
# Big data products are serialized at the link rate instead of leaving in one
# tick: a chunk goes only once the link has carried its whole size (Phase 3).
METERED_KINDS = {"science"}


class Network:
    def __init__(self, ephem, contact_plan, schedule):
        self.ephem = ephem
        self.cp = contact_plan
        self.schedule = schedule            # schedule(et, kind, payload)
        self.stores = {EID_CONTROL: BundleStore(EID_CONTROL), EID_SHIP: BundleStore(EID_SHIP)}
        self.in_flight: dict[str, Bundle] = {}
        self.recent: deque[Bundle] = deque(maxlen=400)
        self.by_id: dict[str, Bundle] = {}
        self.stats = {"sent": 0, "delivered": 0, "expired": 0, "bytes_up": 0, "bytes_down": 0}
        self._credit = {"up": 0.0, "down": 0.0}

    def store_for(self, source: str) -> BundleStore:
        return self.stores[EID_SHIP if source == EID_SHIP else EID_CONTROL]

    def submit(self, b: Bundle) -> Bundle:
        b.state = "queued"
        self.by_id[b.id] = b
        displaced = self.store_for(b.source).put(b)
        self.stats["expired"] += len(displaced)
        return b

    def queue(self, node: str) -> list[Bundle]:
        return self.stores[node].items()

    def tick(self, et: float, dt: float, link) -> list[Bundle]:
        sent = []
        for node, store in self.stores.items():
            for b in store.purge_expired(et):
                self.stats["expired"] += 1
                self.recent.append(b)
                if b.report_delivery:
                    self._status(b, et, "expired")
        for direction, node in (("up", EID_CONTROL), ("down", EID_SHIP)):
            is_open = link.uplink_open if direction == "up" else link.downlink_open
            if not is_open:
                self._credit[direction] = 0.0
                continue
            self._credit[direction] += link.data_rate_bps * dt / 8.0
            store = self.stores[node]
            # While a metered bundle (a science chunk) waits for the link to carry it,
            # the same-or-lower class queued behind it waits too. Telemetry has its
            # own virtual channel and never does. With nothing metered queued, the
            # credit stays small, as before.
            metered = next((x for x in store.items() if x.kind in METERED_KINDS), None)
            if metered is None:
                self._credit[direction] = min(self._credit[direction], 65536.0)
            while len(store):
                def allowed(b: Bundle) -> bool:
                    if direction == "up" and b.kind in COMMAND_KINDS and link.moratorium:
                        return False
                    if b.kind in METERED_KINDS:
                        return self._credit[direction] >= b.size_bytes
                    if metered is not None and b.kind != "telemetry" and b.sort_key() > metered.sort_key():
                        return False
                    return True
                nxt = store.peek()
                if nxt is None:
                    break
                if self._credit[direction] < min(nxt.size_bytes, 4096) and sent:
                    break
                b = store.pop_where(allowed)
                if b is None:
                    break
                self._credit[direction] -= b.size_bytes
                self._transmit(b, et, direction)
                sent.append(b)
        return sent

    def _transmit(self, b: Bundle, et: float, direction: str) -> None:
        src, dst = ("EARTH", "SHIP") if direction == "up" else ("SHIP", "EARTH")
        lt = self.ephem.light_time(src, dst, et, "transmit")
        b.state, b.tx_et, b.true_lt_s, b.arrive_et = "in_flight", et, lt, et + lt
        self.in_flight[b.id] = b
        self.stats["sent"] += 1
        self.stats["bytes_up" if direction == "up" else "bytes_down"] += b.size_bytes
        self.schedule(b.arrive_et, "bundle_arrival", {"id": b.id})

    def arrive(self, bundle_id: str, et: float) -> Bundle | None:
        b = self.in_flight.pop(bundle_id, None)
        if b is None:
            return None
        if b.expires_et < et:
            b.state, b.note = "expired", "lifetime elapsed in flight"
            self.stats["expired"] += 1
        else:
            b.state, b.delivered_et = "delivered", et
            self.stats["delivered"] += 1
            if b.report_delivery:
                self._status(b, et, "delivered")
        self.recent.append(b)
        return b

    def _status(self, b: Bundle, et: float, what: str) -> None:
        """RFC 9171-style status report, itself a bundle that must cross space."""
        origin = EID_CONTROL if b.source == EID_HOME else b.source
        reporter = EID_SHIP if b.dest == EID_SHIP else EID_CONTROL
        if reporter == origin:
            return
        self.submit(Bundle(source=reporter, dest=origin, kind="status", created_et=et,
                           lifetime_s=7 * 86400, priority=NORMAL, size_bytes=96,
                           payload={"ref": b.id, "status": what, "ref_kind": b.kind}))

    def in_flight_public(self, et: float) -> list[dict]:
        out = []
        for b in self.in_flight.values():
            frac = (et - b.tx_et) / max(b.arrive_et - b.tx_et, 1e-6)
            out.append({"id": b.id, "kind": b.kind, "direction": b.direction, "frac": max(0.0, min(1.0, frac)),
                        "tx_et": b.tx_et, "arrive_et": b.arrive_et, "priority": b.priority,
                        "label": b.payload.get("label", b.kind)})
        return out


def make_bundle(source: str, dest: str, kind: str, et: float, payload: dict, priority: int = NORMAL,
                lifetime_s: float = 30 * 86400, size_bytes: int | None = None, report: bool = False) -> Bundle:
    if size_bytes is None:
        size_bytes = 256 + len(str(payload).encode()) + int(payload.get("audio_bytes", 0))
    if kind == "telemetry":
        priority = BULK
    return Bundle(source=source, dest=dest, kind=kind, created_et=et, lifetime_s=lifetime_s, priority=priority,
                  size_bytes=size_bytes, payload=payload, report_delivery=report)
