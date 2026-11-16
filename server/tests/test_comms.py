"""Bundle store ordering/expiry, moratorium handling, light-time arrival."""
from types import SimpleNamespace

from lightlag.comms.bundle import BULK, EID_CONTROL, EID_SHIP, EXPEDITED, NORMAL, Bundle, BundleStore
from lightlag.comms.network import Network, make_bundle


def b(kind="text", prio=NORMAL, t=0.0, life=1000.0, src=EID_CONTROL, dst=EID_SHIP):
    return Bundle(source=src, dest=dst, kind=kind, created_et=t, lifetime_s=life, priority=prio)


def test_priority_then_creation_order():
    s = BundleStore(EID_CONTROL)
    for x in [b(t=5), b(prio=EXPEDITED, t=9), b(t=1), b(prio=BULK, t=0), b(prio=EXPEDITED, t=2)]:
        s.put(x)
    got = [(x.priority, x.created_et) for x in s.items()]
    assert got == [(2, 2), (2, 9), (1, 1), (1, 5), (0, 0)]


def test_fifo_within_class_even_with_same_timestamp():
    s = BundleStore(EID_CONTROL)
    first, second = b(t=3), b(t=3)
    s.put(second) if False else None
    s.put(first)
    s.put(second)
    assert [x.id for x in s.items()] == [first.id, second.id]


def test_expiry_purges_and_reports():
    s = BundleStore(EID_CONTROL)
    s.put(b(t=0, life=10))
    s.put(b(t=0, life=100))
    dead = s.purge_expired(50)
    assert len(dead) == 1 and dead[0].state == "expired" and len(s) == 1


def test_telemetry_stream_keeps_freshest():
    s = BundleStore(EID_SHIP)
    for i in range(30):
        s.put(make_bundle(EID_SHIP, EID_CONTROL, "telemetry", float(i), {"f": i}))
    tl = [x for x in s.items() if x.kind == "telemetry"]
    assert len(tl) == BundleStore.STREAM_LIMITS["telemetry"]
    assert min(x.created_et for x in tl) == 30 - BundleStore.STREAM_LIMITS["telemetry"]


class FakeEphem:
    def light_time(self, src, dst, et, mode="transmit"):
        return 600.0


def _link(**kw):
    d = dict(uplink_open=True, downlink_open=True, moratorium=False, data_rate_bps=1e6)
    d.update(kw)
    return SimpleNamespace(**d)


def test_arrival_is_transmit_plus_light_time_and_moratorium_holds_commands():
    sched = []
    net = Network(FakeEphem(), None, lambda et, k, d: sched.append((et, k, d)))
    cmd = net.submit(make_bundle(EID_CONTROL, EID_SHIP, "command", 0.0, {"command": "hga_lock"}, EXPEDITED))
    msg = net.submit(make_bundle(EID_CONTROL, EID_SHIP, "text", 1.0, {"text": "hi"}))
    sent = net.tick(100.0, 1.0, _link(moratorium=True))
    assert [x.id for x in sent] == [msg.id]                 # command held, text passes
    assert msg.arrive_et == 700.0 and sched[0][0] == 700.0  # 100 s + 600 s light time
    assert cmd.state == "queued"
    sent = net.tick(200.0, 1.0, _link())
    assert [x.id for x in sent] == [cmd.id]


def test_no_contact_no_transmit_and_delivery_report():
    sched = []
    net = Network(FakeEphem(), None, lambda et, k, d: sched.append((et, k, d)))
    m = net.submit(make_bundle(EID_CONTROL, EID_SHIP, "text", 0.0, {"text": "hi"}, report=True))
    assert net.tick(10.0, 1.0, _link(uplink_open=False)) == []
    net.tick(20.0, 1.0, _link())
    got = net.arrive(m.id, 620.0)
    assert got.state == "delivered"
    status = [x for x in net.queue(EID_SHIP) if x.kind == "status"]
    assert status and status[0].payload["ref"] == m.id     # the receipt must cross space too
