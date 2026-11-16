"""Phase 6 upgrades: the TMI burn, "Meanwhile, in reality", and "Right now"."""
import os
import re
from datetime import datetime

import pytest
import yaml
from fastapi.testclient import TestClient

from conftest import ROOT, through_review

FLAGS = ("LL_JUDGE", "LL_REVIEW", "LL_LAUNCH_FX", "LL_REALITY", "LL_RIGHT_NOW")


def _universe(monkeypatch, **env):
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    from lightlag.config import Config
    from lightlag.sim.universe import Universe
    return Universe(Config())


def _judge(on: bool) -> dict:
    """A full Judge Mode mission from a fresh room: the 45 s design hold (the bots vote),
    the review, the burn, eight scenes, the debrief. Returns wall time and events."""
    keep = {k: os.environ.get(k) for k in FLAGS}
    flag = "1" if on else "0"
    os.environ.update(LL_JUDGE="1", LL_REVIEW="1", LL_LAUNCH_FX=flag, LL_REALITY=flag)
    try:
        from lightlag.config import Config
        from lightlag.sim.universe import Universe
        u = Universe(Config())
    finally:
        for k, v in keep.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    wall, reality, burns, et_during_burn = 0.0, [], [], set()
    while u.phase in ("design", "review", "mission") and wall < 900:
        u.tick(0.1)
        wall += 0.1
        if u.burn and u.phase == "review":
            et_during_burn.add(round(u.et, 3))
        for e in u.drain():
            ev = e["event"]
            if ev["kind"] == "reality":
                reality.append({**ev["reality"], "et": ev["et"], "audience": e["audience"]})
            if ev["kind"] == "tmi_burn":
                burns.append(ev["burn"])
    return {"u": u, "wall": wall, "reality": reality, "burns": burns, "et_during_burn": et_during_burn}


@pytest.fixture(scope="module")
def judge_on():
    return _judge(True)


@pytest.fixture(scope="module")
def judge_off():
    return _judge(False)


# ------------------------------------------------------------- A: the TMI burn
def test_burn_recorded_after_review_pass_and_clock_stopped(monkeypatch):
    u = _universe(monkeypatch, LL_JUDGE="0", LL_REVIEW="1", LL_LAUNCH_FX="1")
    u.vote("control", "GO")
    assert u.phase == "review" and u.burn is None
    et0 = u.et
    w = 0.0
    while u.burn is None and w < 60:
        u.tick(0.1)
        w += 0.1
    b = u.burn_public()
    assert b and b["active"] and u.phase == "review"                 # the moment is recorded; still before launch
    assert b["title"].startswith("Trans-Mars injection")
    assert abs(b["dv_kms"] - u.design_eval()["dv_tmi"]) < 1e-3       # the design bay's own TMI figure
    assert 0 < b["lt_s"] < 0.05                                       # Houston hears it ~0.02 s late (MODEL)
    assert 10.0 <= b["timing"]["total_s"] <= 12.5
    assert any(e["event"]["kind"] == "tmi_burn" for e in u.drain())
    s = u.snapshot("ship")
    assert s["burn"]["active"] and s["phase"] == "review"
    for _ in range(int(b["timing"]["total_s"] * 10) - 2):            # server time does not advance during the burn
        u.tick(0.1)
        assert u.et == et0 and u.phase == "review"
    for _ in range(10):
        u.tick(0.1)
    assert u.phase == "mission" and u.director.scene["id"] == "s1"


def test_burn_is_per_room():
    from lightlag.api.app import Game
    from lightlag.config import Config
    game = Game(Config())
    a, b = game.create_room(judge=False, ip="t1"), game.create_room(judge=False, ip="t2")
    try:
        a.u.vote("control", "GO")
        w = 0.0
        while a.u.burn is None and w < 60:
            a.u.tick(0.1)
            b.u.tick(0.1)
            w += 0.1
        assert a.u.burn is not None
        assert b.u.burn is None and b.u.phase == "design" and b.u.snapshot("ship")["burn"] is None
    finally:
        game.evict(a.code)
        game.evict(b.code)


def test_flag_off_changes_nothing(monkeypatch):
    u = _universe(monkeypatch, LL_JUDGE="0", LL_REVIEW="1", LL_LAUNCH_FX="0")
    u.vote("control", "GO")
    total = u.review["timing"]["total_s"]
    w = 0.0
    while u.phase == "review" and w < 60:
        u.tick(0.1)
        w += 0.1
    assert u.phase == "mission" and u.burn is None
    assert w == pytest.approx(total, abs=0.25)                        # launches right after the verdict, as before
    assert not any(e["event"]["kind"] == "tmi_burn" for e in u.drain())


def test_burn_story_data_has_no_em_dashes():
    for f in ("launch.yaml", "reality.yaml"):
        assert "\u2014" not in (ROOT / "data" / "story" / f).read_text(encoding="utf-8")


# ------------------------------------------------------- Judge Mode, on and off
@pytest.mark.slow
def test_judge_mode_with_upgrades_on(judge_on):
    u, wall = judge_on["u"], judge_on["wall"]
    assert u.phase == "debrief"
    assert wall < 8 * 60, wall                                       # design hold + review + burn + flight
    assert u.after_action()["score"]["objectives_pct"] == 100.0
    assert len(judge_on["burns"]) == 1
    assert len(judge_on["et_during_burn"]) == 1                       # the universe clock stood still
    assert sum(len(r["items"]) for r in judge_on["reality"]) >= 3     # at least three real events reach the screens


@pytest.mark.slow
def test_judge_mode_with_upgrades_off(judge_off, judge_on):
    u = judge_off["u"]
    assert u.phase == "debrief" and judge_off["wall"] < 8 * 60
    assert not judge_off["burns"] and not judge_off["reality"] and u.reality.public() is None
    assert judge_on["wall"] - judge_off["wall"] == pytest.approx(12.0, abs=0.5)   # the burn is the only cost


# ------------------------------------------------------- B: meanwhile, in reality
def test_reality_events_are_dated_short_and_sourced():
    y = yaml.safe_load((ROOT / "data" / "story" / "reality.yaml").read_text(encoding="utf-8"))
    sources = yaml.safe_load((ROOT / "data" / "catalog" / "sources.yaml").read_text(encoding="utf-8"))
    ids = set()
    for e in y["events"]:
        datetime.fromisoformat(e["utc"])
        assert e["date_label"] and e["text"]
        assert len(e["text"].split()) < 20, e["id"]
        assert e["source"] in sources and sources[e["source"]]["url"].startswith("https://"), e["id"]
        assert e["id"] not in ids
        ids.add(e["id"])
    assert len(ids) >= 3


@pytest.mark.slow
def test_reality_fires_once_in_date_order_and_batches_coasts(judge_on):
    rs = judge_on["reality"]
    items = [i for r in rs for i in r["items"]]
    assert len({i["id"] for i in items}) == len(items)               # once each
    assert [i["utc"] for i in items] == sorted(i["utc"] for i in items)   # date order
    assert all(set(r["audience"]) >= {"control", "ship", "spectator"} for r in rs)
    assert any(r["coasted"] and len(r["items"]) > 1 for r in rs)      # the long coast arrives as one list
    per_scene = [r["scene"] for r in rs]
    assert len(per_scene) == len(set(per_scene))                      # at most one line per scene
    from lightlag.astro.timescale import utc_to_et
    u = judge_on["u"]
    for i in items:                                                   # nothing before its date, nothing outside the window
        assert utc_to_et(i["utc"]) <= i["shown_et"]
        assert u.reality.window[0] <= utc_to_et(i["utc"]) <= u.reality.window[1]
    assert u.reality.compare is None                                  # Perseverance's own dates: no comparison line


def test_reality_respects_the_teams_own_window(monkeypatch):
    from lightlag.astro.timescale import utc_to_et
    u = _universe(monkeypatch, LL_JUDGE="0", LL_REVIEW="0")
    u.design["launch_et"] = utc_to_et("2020-08-12T12:00:00")
    u.design["arrival_et"] = utc_to_et("2021-03-10T12:00:00")
    u.launch()
    ids = [e["id"] for e in u.reality.queue]
    assert "m2020_launch" not in ids and "hope_launch" not in ids and "tianwen1_launch" not in ids
    assert "m2020_landing" in ids
    assert u.reality.compare == "Perseverance flew 2020-07-30 to 2021-02-18."
    assert u.snapshot("spectator")["reality"]["compare"] == u.reality.compare


def test_reality_flag_off(monkeypatch):
    u = _universe(monkeypatch, LL_JUDGE="0", LL_REVIEW="0", LL_REALITY="0")
    u.vote("control", "GO")
    for _ in range(5):
        u.tick(0.1)
    assert u.snapshot("spectator")["reality"] is None
    assert not any(e["event"]["kind"] == "reality" for e in u.drain())


def test_reality_science_card():
    cards = yaml.safe_load((ROOT / "data" / "science_cards.yaml").read_text(encoding="utf-8"))["cards"]
    c = next(c for c in cards if c["id"] == "july_2020")
    assert "26 months" in c["body"] and "Press Kit" in c["source"]


# ------------------------------------------------------------- C: right now
@pytest.fixture()
def client():
    from lightlag.api.app import create_app
    from lightlag.config import Config
    return TestClient(create_app(Config()))


def test_right_now_light_time(client):
    r = client.get("/api/right-now")
    assert r.status_code == 200
    j = r.json()
    assert 180 <= j["lt_s"] <= 23 * 60
    assert j["dist_km"] > 5e7 and j["dist_au"] == pytest.approx(j["dist_km"] / 149_597_870.7, rel=1e-6)
    assert j["label"] == "analytic ephemeris, MODEL" or re.match(r"NASA JPL SPICE( DE\d{3})?$", j["label"])
    assert abs(j["lt_rate"]) < 1e-3                                  # the light time changes slowly


def test_right_now_503_when_ephemeris_unavailable(client):
    game = client.app.state.game
    game.right_now_ephem = None
    game._rn_cache = (-1e9, None)
    r = client.get("/api/right-now")
    assert r.status_code == 503 and "error" in r.json()
