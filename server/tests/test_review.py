"""Phase 4 A: the Flight Readiness Review between design and launch."""
import pytest

from conftest import through_review


def _u(monkeypatch, **env):
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    from lightlag.config import Config
    from lightlag.sim.universe import Universe
    return Universe(Config())


def test_all_go_enters_review_and_flag_off_launches_directly(monkeypatch):
    u = _u(monkeypatch, LL_REVIEW="1")
    u.vote("control", "GO")                       # nobody seated: the AI seats follow
    assert u.phase == "review"
    s = u.snapshot("spectator")
    assert s["phase"] == "review" and s["review"]["active"] and len(s["review"]["areas"]) == 5
    assert through_review(u) == "mission"
    u2 = _u(monkeypatch, LL_REVIEW="0")
    u2.vote("control", "GO")
    assert u2.phase == "mission"                  # as before Phase 4


def test_default_design_passes_with_one_or_two_liens(monkeypatch):
    u = _u(monkeypatch)
    from lightlag.sim import review
    r = review.run(u, u.design_eval())
    assert r["verdict"] == "lien"
    assert 1 <= len(r["liens"]) <= 2
    for a in r["areas"]:
        assert a["verdict"] in ("pass", "lien")
        for f in a["findings"]:
            assert f["number"] is not None and f["threshold"]
            assert f["text"] and "{" not in f["text"]
            assert f["model"] or f["sources"], f["id"]        # a source or a MODEL flag, always
    ids = {L["id"] for L in r["liens"]}
    assert "power" in ids                          # 7% power margin, and one string short sheds the payload


def test_review_is_fast_enough_for_judge_mode():
    from lightlag.sim import review
    assert review.timing(True)["total_s"] <= 25.0


def _fail_design(u):
    u.set_design("engineer", {"array": "array_thin"})
    ev = u.design_eval()
    assert ev["valid"], ev["reasons"]               # the design bay allows it...
    return ev


def test_degraded_power_fails_slips_and_resets_votes(monkeypatch):
    u = _u(monkeypatch)
    from lightlag.astro.constants import DAY_S
    from lightlag.sim import review
    before = _fail_design(u)
    assert review.run(u, before)["verdict"] == "fail"   # ...a real board would not
    launch0 = u.design["launch_et"]
    u.vote("control", "GO")
    assert u.phase == "review"
    assert through_review(u) == "design"
    assert all(v is None for v in u.votes.values())
    days = review.board()["slip"]["days"]
    assert abs(u.design["launch_et"] - (launch0 + days * DAY_S)) < 1
    after = u.design_eval()
    assert after["c3"] != before["c3"] and after["mass"]["imleo_t"] != before["mass"]["imleo_t"]
    assert any("slipped" in c for c in after["changes"]) and any("IMLEO" in c for c in after["changes"])
    s = u.snapshot("control")
    assert s["review"]["verdict"] == "fail" and not s["review"]["active"] and s["review"]["fails"]


def test_repeated_failures_past_the_window_scrub(monkeypatch):
    u = _u(monkeypatch)
    _fail_design(u)
    for _ in range(12):
        if u.phase != "design":
            break
        u.vote("control", "GO")
        if u.phase == "design":                  # the slipped dates no longer close: pick a valid trip again
            ev = u.design_eval()
            assert not ev["valid"]
            break
        through_review(u)
    if u.phase == "design":                      # walk the date to the end of the window directly
        from lightlag.astro.timescale import utc_to_et
        u.design["launch_et"] = utc_to_et(u.scenario["design"]["launch_window"][1]) - 5 * 86400
        u.design["arrival_et"] = utc_to_et("2021-03-20T00:00:00")
        assert u.design_eval()["valid"], u.design_eval()["reasons"]
        u.vote("control", "GO")
        through_review(u)
    assert u.phase == "scrubbed"
    assert len(u.reviews) >= 2 and all(r["verdict"] == "fail" for r in u.reviews)   # failed again and again
    s = u.snapshot("spectator")
    assert s["scrub"]["synodic_days"] == pytest.approx(779.94) and "26 months" in s["scrub"]["text"]
