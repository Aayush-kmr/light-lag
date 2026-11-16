"""Crew AI: who it may act for, repairs that take time, the scrubber beat,
stale orders, trust and morale, and the LL_CREW_AI switch."""
import pytest

from lightlag.comms.bundle import EXPEDITED
from conftest import through_review


def _launched(monkeypatch=None, crew_ai=None, humans=()):
    if monkeypatch is not None and crew_ai is not None:
        monkeypatch.setenv("LL_CREW_AI", "1" if crew_ai else "0")
    from lightlag.config import Config
    from lightlag.sim.universe import Universe
    u = Universe(Config())
    for s in humans:
        u.seat_join(s, +1)
    for s in ("control", "scientist", "ship", "engineer"):
        if u.phase == "design":
            u.vote(s, "GO")
    assert through_review(u) == "mission"
    for _ in range(3):                  # a fresh frame after the coast into the first scene
        u.tick(0.1)
    return u


def _show_fault(u, fault, channel, level="caution"):
    u.ship_sys.inject(fault)
    u.frame = u.ship_sys.frame()
    u.alarms = [{"channel": channel, "level": level, "value": 0.0, "limit": 0.0}]
    u.crew._stale_frame = False


def test_ai_never_acts_for_a_human_seat_but_drives_the_science_officer_and_surgeon():
    u = _launched(humans=("ship", "engineer"))
    c = u.crew
    assert not c.ai_driven(c.cmdr) and not c.ai_driven(c.eng)
    assert c.ai_driven(c.sci) and c.ai_driven(c.doc)
    _show_fault(u, "hga_drift", "link_db")
    u.ship_sys.science_on = False
    u.ship_sys.batt_soc = 95.0
    u.frame = u.ship_sys.frame()
    c._notice()
    c._think()
    assert c.cmdr.task is None and c.eng.task is None           # the people at the console do the repair
    assert c.sci.task is not None and c.sci.task.kind == "science"
    assert c.say(c.eng, "repair_done", force=True) is None      # nobody puts words in a human's mouth
    assert u.ship_sys.science_on is False                        # not instant: powering up is a task


def test_ai_engineer_repairs_an_alarmed_fault_after_its_duration():
    u = _launched()
    c = u.crew
    _show_fault(u, "hga_drift", "link_db")
    c.eng.trust_ground = 0.0                                     # act on our own judgment
    c._notice()
    c._think()
    t = c.eng.task
    assert t is not None and t.kind == "repair" and t.fault == "hga_drift" and t.duration_s > 120
    c.tick(0.1)
    assert "hga_drift" in u.ship_sys.faults                      # not instant
    u.et += t.duration_s * 0.5
    c.tick(0.1)
    assert "hga_drift" in u.ship_sys.faults
    u.et += t.duration_s * 0.6
    c.tick(0.1)
    assert "hga_drift" not in u.ship_sys.faults                  # done, through Universe.ship_action
    assert any(e["kind"] == "action" and e["who"] == c.eng.id for e in c.log)


def test_scrubber_in_the_blackout_waits_for_terns_call():
    u = _launched()
    c = u.crew
    _show_fault(u, "scrubber_valve_stuck", "ppco2", "warning")
    c._notice()
    c._think()
    assert not any(m.task and m.task.kind == "repair" for m in c.members.values())
    assert "scrubber_valve_stuck" in u.ship_sys.faults
    u.director.after("crew_called")                              # TERN wakes the Commander
    c._notice()
    c._think()
    t = c.cmdr.task
    assert t is not None and t.fault == "scrubber_valve_stuck" and t.duration_s > 300
    assert "scrubber_valve_stuck" in u.ship_sys.faults


def _go_to_scene(u, sid):
    idx = next(i for i, sc in enumerate(u.director.scenes) if sc["id"] == sid)
    u.director.enter(idx)
    u.cp.add_surge(u.et, 24)                     # an antenna for the whole test
    u._passes_cache = (-1e18, [])
    u.rate = 50.0
    for _ in range(3):
        u.tick(0.1)


def test_stale_command_is_held_logged_and_reaches_earth_a_light_time_later():
    u = _launched()
    _go_to_scene(u, "s4")
    c = u.crew
    trust0 = c.cmdr.trust_ground
    lt_down = u.lt("down")
    assert lt_down > 100
    u.ctrl["modes"] = {**(u.ctrl.get("modes") or {}), "active_loop": "A"}    # what Lantern saw
    r = u.send("control", "command", "Switch loops.", EXPEDITED, command="pump_switch")
    assert r["ok"]
    sent = next(b for b in u.net.queue("dtn://lantern") if b.kind == "command")
    assert sent.payload["context"]["modes"]["active_loop"] == "A"
    u.ship_sys.active_loop = "B"                                 # ...but the crew switched already
    held = None
    for _ in range(4000):
        u.tick(0.1)
        u.drain()
        held = next((e for e in c.log if e["kind"] == "order_held"), None)
        if held:
            break
    assert held is not None and held["verdict"] == "contradicts"
    assert u.ship_sys.active_loop == "B"                         # not executed
    assert not (u.ctrl.get("crew_log") or [])                    # Lantern doesn't know yet
    while not any(e["kind"] == "order_held" for e in (u.ctrl.get("crew_log") or [])) and u.et < held["et"] + 6 * 3600:
        u.tick(0.1)
        u.drain()
    got = next(e for e in u.ctrl["crew_log"] if e["kind"] == "order_held")
    assert got["received_et"] - held["et"] >= lt_down - 5        # causality: a light time at least
    assert c.cmdr.trust_ground < trust0                          # stale orders cost trust


def test_trust_rises_when_an_order_helps_and_morale_rises_when_a_letter_is_played():
    u = _launched()
    c = u.crew
    m0 = c.cmdr.morale
    u.letters.append({"id": "Lx", "from": "lila", "text": "Hi. Over.", "created_et": u.et, "created_local": "",
                      "delivered_et": u.et, "lt_s": 1.0, "played": False, "flood": False, "beat": None})
    c._notice()
    m1 = c.cmdr.morale
    assert m1 >= m0
    assert u.play_letter("Lx")["ok"]
    c._notice()
    assert c.cmdr.morale > m1
    # an order that arrives while the fault is real raises trust
    t0 = c.cmdr.trust_ground
    u.ship_sys.inject("hga_drift")
    from types import SimpleNamespace
    b = SimpleNamespace(id="bX", payload={"command": "hga_lock", "context": c.order_context()}, created_et=u.et,
                        true_lt_s=1.0)
    item = {}
    assert c.on_order(b, item) in ("execute", "manual")
    assert c.cmdr.trust_ground > t0 and item["crew_judgment"]["verdict"] == "ok"


def test_crew_ai_switch_off_disables_every_crew_action(monkeypatch):
    u = _launched(monkeypatch, crew_ai=False)
    c = u.crew
    assert not c.enabled and not u.cfg.crew_ai
    _show_fault(u, "hga_drift", "link_db")
    u.director.after("crew_called")
    events = []
    for _ in range(50):
        u.tick(0.1)
        events += u.drain()
    assert all(m.task is None for m in c.members.values())
    assert not [e for e in events if e["event"]["kind"] == "crew_say"]
    assert "hga_drift" in u.ship_sys.faults
    from types import SimpleNamespace
    b = SimpleNamespace(id="bY", payload={"command": "pump_switch", "context": {}}, created_et=u.et, true_lt_s=1.0)
    assert c.on_order(b, {}) == "execute"                         # legacy path, unchanged


@pytest.mark.slow
def test_judge_mode_without_crew_ai_still_lands(monkeypatch):
    monkeypatch.setenv("LL_JUDGE", "1")
    monkeypatch.setenv("LL_CREW_AI", "0")
    from lightlag.config import Config
    from lightlag.sim.universe import Universe
    u = Universe(Config())
    u.vote("control", "GO")
    wall = 0.0
    while u.phase in ("review", "mission") and wall < 900:     # Phase 4: through the review, then the flight
        u.tick(0.1)
        wall += 0.1
        u.drain()
    s = u.after_action()["score"]
    assert s["objectives_pct"] == 100.0 and s["shelter_before_onset"]
    assert not [e for e in u.crew.log if e["kind"] in ("action", "order_done", "said")]


def test_a_weak_radio_link_does_not_stress_the_crew_but_a_co2_alarm_does():
    u = _launched()
    for m in u.crew.members.values():
        m.stress = 0.1
    u.alarms = [{"channel": "link_db", "level": "warning", "value": -0.5, "limit": 0.0}]
    u.crew._physiology(3600.0)
    calm = u.crew.cmdr.stress
    u.alarms = [{"channel": "ppco2", "level": "warning", "value": 6.5, "limit": 6.0}]
    u.crew._physiology(3600.0)
    assert calm <= 0.1 + 1e-9 and u.crew.cmdr.stress > calm


def test_load_shed_stands_down_when_the_battery_recovers():
    u = _launched()
    u.autonomy.active.add("load_shed_science")
    u.ship_sys.science_on = False
    f = u.ship_sys.frame()
    f["batt_soc"] = 95.0
    u._autonomy(f)
    assert "load_shed_science" not in u.autonomy.active
