"""Science data in flight: generation by phase, the recorder, conservation,
the metered downlink, the Mission Scientist's lever and science return."""
from types import SimpleNamespace

from lightlag.comms.bundle import BULK, EID_CONTROL, EID_SHIP, NORMAL
from lightlag.comms.network import Network, make_bundle
from conftest import through_review


def _launched():
    from lightlag.config import Config
    from lightlag.sim.universe import Universe
    u = Universe(Config())
    u.vote("control", "GO")
    assert through_review(u) == "mission"
    u.tick(0.1)
    return u


def _go_to_scene(u, sid, surge=True):
    idx = next(i for i, sc in enumerate(u.director.scenes) if sc["id"] == sid)
    u.director.enter(idx)
    if surge:
        u.cp.add_surge(u.et, 24)
        u._passes_cache = (-1e18, [])
    u.rate = 50.0
    u.tick(0.1)


def _conserved(L):
    t = L.totals()
    assert abs(L.conservation_error_mbit()) <= 1e-6 * max(1.0, t["generated_mbit"]), t


def test_imaging_waits_for_mars_but_radiation_runs_in_cruise():
    u = _launched()
    L = u.science
    u.design["payload"] = ["rad_class", "ctx_class", "hirise_class"]
    cruise = u.anchors["launch"] + 30 * 86400
    orbit = u.anchors["arrival"] + 86400
    r = L.rates_mbit_s(cruise, True)
    assert "rad_class" in r and "ctx_class" not in r and "hirise_class" not in r
    assert abs(r["rad_class"] * 86400 - 1.0) < 1e-9                     # 1 Mbit per day, from the catalog
    r = L.rates_mbit_s(orbit, True)
    assert {"rad_class", "ctx_class", "hirise_class"} <= set(r)
    assert L.rates_mbit_s(orbit, False) == {}                            # TERN shed the payload: nothing


def test_recorder_overflow_is_lost_and_conservation_holds(monkeypatch):
    import lightlag.sim.science as sci
    u = _launched()
    L = u.science
    monkeypatch.setattr(sci, "RECORDER_MBIT", 1000.0)
    L._record("ctx_class", 900.0)
    L._record("grs_class", 600.0)
    L._overflow()
    assert abs(L.stored_mbit() - 1000.0) < 1e-6           # (plus a little cruise data already aboard)
    assert abs(sum(L.lost.values()) - 500.0) < 0.1 and L.lost.get("ctx_class", 0) > 499   # oldest goes first
    _conserved(L)
    # across a time skip: generation, analytic downlink, overflow
    u.design["payload"] = ["hirise_class", "ctx_class", "rad_class"]
    t0 = u.anchors["arrival"] + 2 * 86400
    L.coast(t0, t0 + 20 * 86400)
    _conserved(L)
    assert sum(L.delivered.values()) > 0 and sum(L.lost.values()) > 500.0  # HiRISE fills a small recorder
    # across a scene: chunks packaged and handed to the network
    _go_to_scene(u, "s6")
    monkeypatch.setattr(sci, "RECORDER_MBIT", 160_000.0)
    L._record("ctx_class", 3 * sci.CHUNK_MBIT)
    for _ in range(20):
        u.tick(0.1)
        u.drain()
        _conserved(L)
    assert L.flight, "science chunks should be packaged into bundles"
    assert all(u.net.by_id[b].kind == "science" for b in L.flight)
    u.director.enter(u.director.i + 1)                                    # and over the coast into the next scene
    _conserved(L)


def test_metered_chunk_waits_for_the_link_and_holds_the_class_behind_it():
    class Eph:
        def light_time(self, *a, **k):
            return 600.0
    net = Network(Eph(), None, lambda *a: None)
    link = SimpleNamespace(uplink_open=True, downlink_open=True, moratorium=False, data_rate_bps=8000.0)  # 1 kB/s
    chunk = net.submit(make_bundle(EID_SHIP, EID_CONTROL, "science", 0.0, {"label": "SCIENCE"}, NORMAL,
                                   size_bytes=10_000))
    letter = net.submit(make_bundle(EID_SHIP, EID_CONTROL, "letter", 1.0, {"text": "hi"}, NORMAL))
    tlm = net.submit(make_bundle(EID_SHIP, EID_CONTROL, "telemetry", 2.0, {"f": 1}))
    sent = net.tick(10.0, 1.0, link)
    assert tlm in sent and chunk not in sent and letter not in sent      # telemetry has its own channel
    for t in range(11, 30):
        sent += net.tick(float(t), 1.0, link)
        if chunk in sent:
            break
    assert chunk in sent and chunk.tx_et >= 19.0                         # 10 kB at 1 kB/s
    sent += net.tick(31.0, 1.0, link)
    assert letter in sent and letter.tx_et > chunk.tx_et                 # science first: the letter waited
    bulk = net.submit(make_bundle(EID_SHIP, EID_CONTROL, "science", 40.0, {"label": "SCIENCE"}, BULK, size_bytes=50_000))
    note = net.submit(make_bundle(EID_SHIP, EID_CONTROL, "letter", 41.0, {"text": "goodnight"}, NORMAL))
    assert note in net.tick(42.0, 1.0, link) and bulk.state == "queued"  # crew first: letters go ahead


def test_science_priority_takes_effect_only_after_the_uplink_light_time():
    u = _launched()
    _go_to_scene(u, "s4")
    L = u.science
    lt = u.lt("up")
    assert lt > 100 and L.aboard_priority == "crew_first"
    r = L.request_priority("scientist", "science_first")
    assert r["ok"]
    sent_et = u.et
    assert L.aboard_priority == "crew_first"                             # not instant
    for _ in range(5000):
        u.tick(0.1)
        u.drain()
        if L.aboard_priority == "science_first":
            break
    assert L.aboard_priority == "science_first"
    assert L.aboard_since_et - sent_et >= lt - 1.0
    assert L.uplink_view()["state"] in ("arrived_unconfirmed", "confirmed")
    # only the scientist sets it, or Flight when the scientist seat is AI-held
    u.seat_join("scientist", +1)
    assert not L.request_priority("control", "crew_first")["ok"]
    u.seat_join("scientist", -1)
    assert L.request_priority("control", "crew_first")["ok"]


def test_science_priority_never_crosses_the_blackout():
    u = _launched()
    _go_to_scene(u, "s7", surge=False)
    L = u.science
    assert u.in_blackout_window()
    assert L.request_priority("scientist", "science_first")["ok"]
    u.rate = 3000.0
    for _ in range(300):
        u.tick(0.1)
        u.drain()
        if not u.in_blackout_window():
            break
        assert L.aboard_priority == "crew_first"
    u.coast_to(u.anchors["blackout_end"] - 60)                          # a time skip doesn't sneak it through
    assert L.aboard_priority == "crew_first"
    assert L.uplink_view()["state"] == "queued"


def test_science_value_uses_the_objective_weights():
    u = _launched()
    L = u.science
    u.design["payload"] = ["grs_class", "ctx_class"]
    L.delivered = {"grs_class": 1000.0, "ctx_class": 2000.0}             # Mbit
    u.design["objective"] = "ice"                                        # grs 1.0, ctx 0.2
    assert abs(L.value_gbit() - (1.0 + 0.4)) < 1e-9
    u.design["objective"] = "landing_sites"                              # grs 0.2, ctx 0.5
    assert abs(L.value_gbit() - (0.2 + 1.0)) < 1e-9
    st = L.objective_status()
    assert st["covered"] and not st["met"]                               # 3 Gbit < 2300 Gbit floor
    u.design["objective"] = "radiation"                                  # grs 0.2 > 0, floor 90 Gbit
    assert not L.objective_status()["met"]
    L.delivered["grs_class"] = 95000.0                                   # 95 Gbit of GRS data
    assert L.objective_status()["met"]
    assert u.after_action()["score"]["science"]["objective"] == "radiation"
