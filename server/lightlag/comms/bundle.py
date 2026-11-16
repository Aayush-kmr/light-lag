"""Store-and-forward bundles, modelled on the DTN Bundle Protocol (RFC 9171).

Every message - a voice note, a command, a telemetry frame, a goodnight - is
a bundle with a source and destination endpoint ID, a creation timestamp, a
lifetime and a class of service. Nodes keep bundles in persistent storage
until a contact (a scheduled, physically possible link) opens, then forward
them. Nothing is ever "sent and lost" because the other side is out of view:
it waits. What can be lost is time - a bundle whose lifetime runs out is
deleted and a status report says so.

Simplifications vs. RFC 9171: one hop per direction (no relays), no
fragmentation, custody is modelled only as optional delivery reports, and
priority uses the three BPv6-style classes (bulk / normal / expedited).
"""
from __future__ import annotations

import heapq
import itertools
from dataclasses import asdict, dataclass, field

BULK, NORMAL, EXPEDITED = 0, 1, 2
PRIORITY_NAMES = {BULK: "bulk", NORMAL: "normal", EXPEDITED: "expedited"}

EID_CONTROL = "dtn://lantern"
EID_SHIP = "dtn://meridian-1"
EID_HOME = "dtn://reyes-home"      # Lila's messages enter the network via Lantern

_seq = itertools.count(1)


@dataclass
class Bundle:
    source: str
    dest: str
    kind: str                       # text | voice | command | telemetry | letter | status | alert | policy | ping
    created_et: float
    lifetime_s: float
    priority: int = NORMAL
    size_bytes: int = 512
    payload: dict = field(default_factory=dict)
    report_delivery: bool = False
    seq: int = field(default_factory=lambda: next(_seq))
    id: str = ""
    state: str = "queued"           # queued | in_flight | delivered | expired | rejected
    tx_et: float | None = None
    arrive_et: float | None = None
    delivered_et: float | None = None
    true_lt_s: float | None = None
    note: str = ""

    def __post_init__(self):
        if not self.id:
            self.id = f"b{self.seq:05d}"

    @property
    def expires_et(self) -> float:
        return self.created_et + self.lifetime_s

    @property
    def direction(self) -> str:
        return "up" if self.dest == EID_SHIP else "down"

    def sort_key(self):
        return (-self.priority, self.created_et, self.seq)

    def public(self, include_payload: bool = True) -> dict:
        d = asdict(self)
        d["priority_name"] = PRIORITY_NAMES[self.priority]
        d["direction"] = self.direction
        d["expires_et"] = self.expires_et
        if not include_payload:
            d.pop("payload", None)
        return d


class BundleStore:
    """Outbound store at one node. Ordered by class of service, then
    creation time, then sequence number - so within a class, delivery is
    strictly in creation order."""

    # telemetry is a stream: keep only the freshest frames queued
    STREAM_LIMITS = {"telemetry": 24}

    def __init__(self, node: str):
        self.node = node
        self._heap: list[tuple] = []
        self.expired: list[Bundle] = []

    def __len__(self) -> int:
        return len(self._heap)

    def put(self, b: Bundle) -> list[Bundle]:
        heapq.heappush(self._heap, (b.sort_key(), b))
        displaced = []
        lim = self.STREAM_LIMITS.get(b.kind)
        if lim:
            same = sorted((x for _, x in self._heap if x.kind == b.kind), key=lambda x: x.created_et)
            if len(same) > lim:
                drop = {id(x) for x in same[: len(same) - lim]}
                keep = [(k, x) for k, x in self._heap if id(x) not in drop]
                displaced = [x for _, x in self._heap if id(x) in drop]
                for x in displaced:
                    x.state, x.note = "expired", "displaced by newer frame"
                self._heap = keep
                heapq.heapify(self._heap)
        return displaced

    def purge_expired(self, now_et: float) -> list[Bundle]:
        alive, dead = [], []
        for k, b in self._heap:
            (dead if b.expires_et <= now_et else alive).append((k, b))
        for _, b in dead:
            b.state, b.note = "expired", "lifetime elapsed in storage"
        if dead:
            self._heap = alive
            heapq.heapify(self._heap)
            self.expired += [b for _, b in dead]
        return [b for _, b in dead]

    def peek(self) -> Bundle | None:
        return self._heap[0][1] if self._heap else None

    def pop_where(self, ok) -> Bundle | None:
        """Pop the highest-ranked bundle that satisfies ok(bundle); bundles
        that are blocked (e.g. commands during a moratorium) keep their place."""
        held = []
        found = None
        while self._heap:
            item = heapq.heappop(self._heap)
            if ok(item[1]):
                found = item[1]
                break
            held.append(item)
        for it in held:
            heapq.heappush(self._heap, it)
        return found

    def items(self) -> list[Bundle]:
        return [b for _, b in sorted(self._heap, key=lambda kb: kb[0])]
