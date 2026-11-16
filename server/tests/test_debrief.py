"""Phase 4 C and D: the cause-and-effect debrief, fly it again, the fact sheet."""
from fastapi.testclient import TestClient


def test_debrief_chains_and_history(judge_run):
    u = judge_run["u"]
    assert u.phase == "debrief"
    assert "review" in judge_run["phases"] and judge_run["wall"] < 8 * 60
    d = u.after_action()["debrief"]
    assert 5 <= len(d["chains"]) <= 7
    for c in d["chains"]:
        assert c["decision"]["text"] and c["event"]["text"] and c["outcome"]["text"]
        assert c["decision"]["title"]
    assert any(c["lien_true"] for c in d["chains"])                       # the power lien came true
    assert any(L["came_true"] for L in d["liens"])
    h = u.history.public()
    assert 10 <= len(h) <= 400
    whys = {p["why"] for p in h}
    assert {"launch", "coast_start", "coast_end", "end"} <= whys
    days = [p["day"] for p in h]
    assert days == sorted(days) and days[0] < 1 and days[-1] > 400          # spans the coasts, launch to the end
    assert all(set(p["crew"]) == set(u.crew.members) for p in h)
    tl = d["timeline"]
    assert tl["conjunction"] and tl["storm_day"] and len(tl["scenes"]) == 8 and tl["callouts"]
    assert len(d["crew"]) == 4 and all(c["stress"] and c["decision"] for c in d["crew"])
    assert d["fly_again"] and d["fly_again"]["text"] and d["fly_again"]["changes"]
    labels = {x["label"] for x in d["not_scored"]}
    assert {"Cosmic-ray dose", "Crew morale", "The letters"} <= labels


def _client(monkeypatch):
    monkeypatch.setenv("LL_LLM", "0")
    monkeypatch.setenv("LL_OFFLINE", "1")
    from lightlag.api.app import create_app
    from lightlag.config import Config
    app = create_app(Config())
    return app, TestClient(app)


def _sources_cited(node, out):
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "sources" and isinstance(v, list) and v and isinstance(v[0], str):
                out.update(v)
            elif k == "source" and isinstance(v, str):
                out.add(v)
            elif k != "sources":
                _sources_cited(v, out)
    elif isinstance(node, list):
        for v in node:
            _sources_cited(v, out)


def test_factsheet_both_states_lists_every_source(monkeypatch, judge_run):
    app, c = _client(monkeypatch)
    page = c.get("/factsheet/")
    assert page.status_code == 200 and "Fact Sheet" in page.text
    fs = c.get("/api/factsheet").json()
    assert fs["state"] == "design" and fs["mission"] == "Meridian-1" and fs["outcome"] is None
    keys = set()
    _sources_cited(fs, keys)
    listed = {s["key"] for s in fs["sources"]}
    assert keys <= listed and not fs["missing_sources"]
    # after the debrief: design plus outcome
    game = app.state.game
    game.u = judge_run["u"]
    fs2 = c.get("/api/factsheet").json()
    assert fs2["state"] == "debrief" and fs2["outcome"] and fs2["review"]["liens"]
    keys = set()
    _sources_cited(fs2, keys)
    assert keys <= {s["key"] for s in fs2["sources"]}
    for s in fs2["sources"]:
        assert s["title"] and s["url"]


def test_fly_again_keeps_the_design_and_clears_flight(monkeypatch, judge_run):
    app, c = _client(monkeypatch)
    game = app.state.game
    u = judge_run["u"]
    game.u = u
    flown = dict(u.design_record["choices"])
    # Phase 5: only a seat connected to the room may fly it again
    assert c.post("/api/fly-again").status_code == 403
    assert c.post("/api/fly-again", headers={"X-Seat-Token": "made-up"}).status_code == 403
    with c.websocket_connect("/ws/control") as ws:
        token = ws.receive_json()["token"]
        r = c.post("/api/fly-again", headers={"X-Seat-Token": token}).json()
    assert r["ok"] and r["phase"] == "design"
    v = game.u
    assert v is not u and v.phase == "design" and v.design_record is None
    assert v.net is None and not v.callouts.ledger and not v.letters and not v.anchors
    for k in ("array", "battery", "comms_plan", "shelter_gcm2", "payload", "objective", "launch_et", "arrival_et"):
        assert v.design[k] == flown[k], k
    assert all(x is None for x in v.votes.values())
    # with the suggested change applied
    game.u = u
    with c.websocket_connect("/ws/control") as ws:
        token = ws.receive_json()["token"]
        r = c.post(f"/api/fly-again?suggestion=1&token={token}").json()
    assert r["applied"] and all(game.u.design[k] == val for k, val in r["applied"]["changes"].items())
    game.u = u
