"""The design bay: four budgets that must close before launch (sim/design.py)."""
from lightlag.catalog import SEATS
from conftest import through_review


def _universe():
    from lightlag.config import Config
    from lightlag.sim.universe import Universe
    return Universe(Config())


def test_default_design_is_valid_and_flies_as_before():
    u = _universe()
    ev = u.design_eval()
    assert ev["valid"] and ev["reasons"] == []
    assert all(b["band"] in ("green", "yellow") for b in ev["budgets"].values())
    assert ev["campaign"]["text"].startswith("3 × Falcon Heavy (expendable)")
    u.apply_design()
    assert u.ship_sys.batt_kwh == 60 and u.ship_sys.array_kw_1au == 32


def test_launch_applies_the_chosen_power_hardware():
    u = _universe()
    u.set_design("engineer", {"array": "array_large", "battery": "batt_90"})
    u.vote("control", "GO")                          # nobody seated: AI says go for everyone
    assert through_review(u) == "mission"
    assert u.ship_sys.batt_kwh == 90 and u.ship_sys.array_kw_1au == 42


def test_hirise_adds_65_kg_and_ripples():
    u = _universe()
    before = u.design_eval()
    after = u.set_design("scientist", {"payload": u.design["payload"] + ["hirise_class"]})
    assert after["mass"]["instruments_kg"] - before["mass"]["instruments_kg"] == 65
    assert after["mass"]["imleo_t"] > before["mass"]["imleo_t"]
    assert "+65 kg instruments" in after["changes"]
    assert u.design_eval()["changes"] == after["changes"]     # kept for every snapshot


def test_launch_count_and_cost_cap():
    u = _universe()
    base = u.design_eval()
    more = u.set_design("ship", {"shelter_gcm2": 40})           # +4.8 t of water: a fourth rocket
    assert more["campaign"]["launches"] == base["campaign"]["launches"] + 1
    assert more["cost"]["total_musd"] - base["cost"]["total_musd"] >= 150
    assert f"launches {base['campaign']['launches']} → {more['campaign']['launches']}" in more["changes"]
    small = u.set_design("control", {"vehicle": "falcon_9"})   # 22.8 t a launch: too many launches
    assert small["campaign"]["launches"] > 4 and not small["valid"]
    assert any(r.startswith("Mass:") and "launches of Falcon 9" in r for r in small["reasons"])
    sls = u.set_design("control", {"vehicle": "sls_block1"})   # few launches, far over the cap
    assert sls["campaign"]["launches"] <= 4 and not sls["valid"]
    assert any(r.startswith("Cost:") and "over the" in r for r in sls["reasons"])
    assert not u.vote("control", "GO")["valid"] and u.phase == "design"


def test_power_goes_red_on_the_light_array():
    u = _universe()
    ev = u.set_design("engineer", {"array": "array_light", "payload": [i["id"] for i in u.catalog.instruments]})
    p = ev["power"]
    assert ev["budgets"]["power"]["band"] == "red" and not ev["valid"]
    assert p["load_kw"] > p["supply_kw"]
    assert f"Power: {p['short_kw']:.1f} kW short at Mars with the light array" in ev["reasons"]
    assert p["reserve_h"] == round(p["batt_kwh"] / p["load_kw"], 1)


def test_data_capacity_follows_the_radio_plan():
    ev = _universe().design_eval()
    cap = {k: v["capacity_mbit_day"] for k, v in ev["data"]["by_plan"].items()}
    assert cap["lean"] < cap["standard"] < cap["continuous"]
    assert ev["data"]["dark_days"] > 0                          # the 2021 conjunction falls in the stay


def test_design_ownership_and_ai_seats():
    u = _universe()
    for s in SEATS:
        u.seat_join(s, +1)
    u.set_design("scientist", {"objective": "radiation", "payload": ["rad_class", "marie_class"]})
    assert u.design["objective"] == "radiation" and u.design["payload"] == ["rad_class", "marie_class"]
    u.set_design("engineer", {"array": "array_large", "battery": "batt_40"})
    assert u.design["array"] == "array_large" and u.design["battery"] == "batt_40"
    u.set_design("scientist", {"battery": "batt_90"})           # not the scientist's call
    u.set_design("ship", {"battery": "batt_90"})                # the engineer is human-held
    assert u.design["battery"] == "batt_40"
    u.seat_join("engineer", -1)                                 # the engineer's seat goes to AI
    u.set_design("scientist", {"battery": "batt_90"})           # still not from Earth
    assert u.design["battery"] == "batt_40"
    u.set_design("ship", {"battery": "batt_90"})                # the Commander may now make it
    assert u.design["battery"] == "batt_90"


def test_unknown_catalog_ids_are_ignored():
    u = _universe()
    before = dict(u.design, payload=list(u.design["payload"]))
    u.set_design("spectator", {"vehicle": "saturn_v", "array": "nope", "battery": 7, "objective": "gold"})
    u.set_design("spectator", {"payload": ["rad_class", "warp_drive", "rad_class"]})
    assert {k: u.design[k] for k in ("vehicle", "array", "battery", "objective")} == \
        {k: before[k] for k in ("vehicle", "array", "battery", "objective")}
    assert u.design["payload"] == ["rad_class"]
    u.set_design("spectator", {"payload": "rad_class"})         # not a list: ignored
    assert u.design["payload"] == ["rad_class"]


def test_porkchop_feasibility_line_follows_the_design():
    u = _universe()
    first = u.porkchop_data()["budget_t"]
    assert first == u.design_eval()["campaign"]["max_imleo_t"] != 160.0
    u.set_design("control", {"vehicle": "sls_block1"})
    assert u.porkchop_data()["budget_t"] == u.design_eval()["campaign"]["max_imleo_t"] != first


def test_objective_need_makes_the_radio_plan_matter():
    """Phase 5 retune: the default design meets its objective with a thin margin,
    and the same payload on the lean radio plan misses it (MODEL need)."""
    u = _universe()
    obj = u.catalog.get("objectives", u.design["objective"])
    assert "min_data_gbit" in obj["model"] and obj.get("note")
    ev = u.design_eval()
    need, got = ev["science"]["need_gbit"], ev["science"]["returned_gbit"]
    assert ev["science"]["verdict"] == "covers" and 0.02 <= (got - need) / need <= 0.10
    u.design["comms_plan"] = "lean"
    lean = u.design_eval()["science"]
    assert lean["returned_gbit"] < need and lean["verdict"] != "covers"


def test_lean_radio_plan_misses_the_objective_in_flight():
    import os
    from lightlag.config import Config
    from lightlag.sim.universe import Universe
    keep = {k: os.environ.get(k) for k in ("LL_JUDGE", "LL_REVIEW")}
    os.environ.update(LL_JUDGE="1", LL_REVIEW="1")
    try:
        u = Universe(Config())
    finally:
        for k, v in keep.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
    u.design["comms_plan"] = "lean"
    u.vote("control", "GO")
    wall = 0.0
    while u.phase in ("review", "mission") and wall < 900:
        u.tick(0.1)
        wall += 0.1
        u.drain()
    st = u.science.objective_status()
    assert u.phase == "debrief" and st["covered"] and not st["met"]
