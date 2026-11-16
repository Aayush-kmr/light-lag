"""The design catalog, the crew of four, and the four seats with AI fill."""
import pytest

from lightlag.catalog import SEATS, catalog, side_of
from conftest import through_review


def test_catalog_citations_resolve():
    c = catalog()
    assert c.check() == []
    for item in c.vehicles + c.instruments:
        assert item.get("sources"), f"{item['id']} cites nothing"


def test_bvad_consumables_rate():
    """BVAD: ~2.4 kg/crew-day of expendables, plus make-up water and O2 the loop can't recover."""
    rate = catalog().consumables_kg_per_cm_day()
    assert 2.3 < rate < 2.7
    # four crew, a 203-day transit, 260 days at Mars, 30 days reserve
    assert 4.5 < catalog().consumables_t(4, 203 + 260) < 5.4


def test_co2_buildup_from_crew_and_volume():
    """Ideal gas: four crew at 1.04 kg CO2/day in 168 m^3 is ~0.43 mmHg/h unscrubbed."""
    from lightlag.sim.subsystems import ShipSystems
    s = ShipSystems()
    assert abs(s.co2_gen_mmhg_s() * 3600 - 0.43) < 0.02
    assert ShipSystems(crew=1).co2_gen_mmhg_s() < s.co2_gen_mmhg_s() / 3.9


def _universe():
    from lightlag.config import Config
    from lightlag.sim.universe import Universe
    return Universe(Config())


def test_seats_and_sides():
    assert SEATS == ("control", "scientist", "ship", "engineer")
    assert side_of("scientist") == "control" and side_of("engineer") == "ship"


def test_ai_seats_follow_the_humans():
    u = _universe()
    u.seat_join("control", +1)
    u.seat_join("ship", +1)
    u.vote("control", "GO")
    assert u.phase == "design"                      # the Commander hasn't said go
    assert u.votes["scientist"] is None             # AI waits for every human
    u.vote("ship", "GO")
    assert through_review(u) == "mission"
    assert u.votes["scientist"] == u.votes["engineer"] == "GO"


def test_human_no_go_holds_everyone():
    u = _universe()
    for s in SEATS:
        u.seat_join(s, +1)
    for s in ("control", "scientist", "ship"):
        u.vote(s, "GO")
    u.vote("engineer", "NO-GO")
    assert u.phase == "design"


def test_solo_player_can_make_ai_seat_calls():
    u = _universe()
    u.seat_join("scientist", +1)                    # alone in Houston, Flight seat is AI
    before = u.design["comms_plan"]
    u.set_design("scientist", {"comms_plan": "lean" if before != "lean" else "standard"})
    assert u.design["comms_plan"] != before
    u.set_design("scientist", {"shelter_gcm2": 5})  # a ship call: not from Earth
    assert u.design["shelter_gcm2"] != 5


def test_seat_snapshots_read_their_side():
    u = _universe()
    u.vote("control", "GO")                         # nobody seated: AI says go for everyone
    assert through_review(u) == "mission"
    u.tick(0.1)
    s = u.snapshot("scientist")
    assert s["seat"] == "scientist" and s["role"] == "control" and "control" in s and "ship" not in s
    e = u.snapshot("engineer")
    assert e["seat"] == "engineer" and "ship" in e and "control" not in e


def test_design_mass_includes_four_crew():
    u = _universe()
    m = u.design_eval()["mass"]
    assert m["crew"] == 4 and 4.0 < m["consumables_t"] < 6.0
