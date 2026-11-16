"""Phase 4 B: design-to-flight callouts."""
import copy
import math
from collections import Counter

# The B2 triggers that exist in the default scenario (no recorder overflow with the
# default payload, so `recorder_gap` fires in its "held" form).
EXPECTED = {"tern_acted_alone", "tern_rule_broken", "power_distance", "power_string", "battery_bridge", "lien_true",
            "storm_shelter", "emergency_pass", "conjunction", "recorder_gap", "science_milestone", "science_result"}


def test_every_trigger_fires_and_no_screen_gets_two_per_scene(judge_run):
    u = judge_run["u"]
    fired = {e["trigger"] for e in u.callouts.ledger}
    assert EXPECTED <= fired, EXPECTED - fired
    # one callout per scene per screen, counting the scene in which the screen showed it
    seen = Counter()
    for e in judge_run["shown"]:
        seen[(e["audience"][0], e["shown_scene"])] += 1
    assert max(seen.values()) == 1, seen
    spectator = [e for e in judge_run["shown"] if e["audience"][0] == "spectator"]
    assert len(spectator) >= 5                                             # Judge Mode shows at least five
    for e in u.callouts.ledger:
        assert "—" not in e["text"] and "{" not in e["text"] and "{" not in e["counter"]


def test_ship_callouts_reach_earth_only_after_the_light_time(judge_run):
    u = judge_run["u"]
    ship = [e for e in u.callouts.ledger if e["side"] == "ship" and "earth_et" in e]
    assert ship
    for e in ship:
        lt = u.ephem.light_time("SHIP", "EARTH", e["et"])
        assert e["earth_et"] >= e["et"] + lt - 1e-3, (e["trigger"], e["earth_et"] - e["et"], lt)
    for e in u.callouts.earthk:
        if e["side"] == "ship":
            assert e["arrived_et"] - e["et"] >= e["lt_s"] - 1e-3 > 0


def test_design_record_is_frozen(judge_run):
    u = judge_run["u"]
    before = copy.deepcopy(u.after_action()["debrief"])
    rec = copy.deepcopy(u.design_record)
    live = copy.deepcopy(u.design)
    try:
        u.design.update(array="array_large", comms_plan="continuous", shelter_gcm2=40.0, payload=["hirise_class"])
        after = u.after_action()["debrief"]
        assert after["chains"] == before["chains"]
        assert after["fly_again"] == before["fly_again"]
        assert u.design_record == rec
        # a callout fired now still reads the record, not the live design
        u.callouts.fired.discard("emergency_pass")
        u.callouts.on_surge()
        assert "standard plan" in u.callouts.ledger[-1]["text"]
    finally:
        u.design.clear()
        u.design.update(live)


def test_counterfactuals_follow_the_shielding_formula(judge_run):
    u = judge_run["u"]
    R = u.rad
    e = next(x for x in u.callouts.ledger if x["trigger"] == "storm_shelter")
    li, sh = R.hull_liner_gcm2, R.shelter_gcm2
    without = R.sep_raw_in_msv * math.exp(-li / 12) + R.sep_raw_out_msv * math.exp(-li / 12)
    flown = R.sep_raw_in_msv * math.exp(-(li + sh) / 12) + R.sep_raw_out_msv * math.exp(-li / 12)
    assert math.isclose(R.sep_counterfactual(shelter_gcm2=0.0), without, rel_tol=1e-12)
    assert math.isclose(e["numbers"]["without_msv"], round(without, 2), abs_tol=0.011)
    assert math.isclose(flown, R.sep_msv, rel_tol=1e-6)                   # the split integral is the flown dose
    assert e["numbers"]["without_msv"] > e["numbers"]["dose_msv"]
    assert R.sep_counterfactual(shelter_gcm2=0.0) == R.sep_counterfactual(shelter_gcm2=0.0)   # deterministic


def test_callouts_flag_off_keeps_the_flight(monkeypatch):
    monkeypatch.setenv("LL_CALLOUTS", "0")
    monkeypatch.setenv("LL_REVIEW", "0")
    from lightlag.config import Config
    from lightlag.sim.universe import Universe
    u = Universe(Config())
    u.vote("control", "GO")
    assert u.phase == "mission" and u.design_record is not None
    assert u.callouts.fire("conjunction", dark=1) is None and not u.callouts.ledger
