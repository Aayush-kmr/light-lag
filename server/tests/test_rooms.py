"""Phase 5: rooms (one universe per game) and endpoint safety."""
import time

import numpy as np
import pytest
from fastapi.testclient import TestClient

from conftest import SPK


def _game(monkeypatch, **env):
    monkeypatch.setenv("LL_LLM", "0")
    monkeypatch.setenv("LL_OFFLINE", "1")
    for k, v in env.items():
        monkeypatch.setenv(k, str(v))
    from lightlag.api.app import Game
    from lightlag.config import Config
    return Game(Config())


def _client(monkeypatch, **env):
    monkeypatch.setenv("LL_LLM", "0")
    monkeypatch.setenv("LL_OFFLINE", "1")
    for k, v in env.items():
        monkeypatch.setenv(k, str(v))
    from lightlag.api.app import create_app
    from lightlag.config import Config
    app = create_app(Config())
    return app, TestClient(app)


def _tick(room, wall_s, dt=0.5):
    w = 0.0
    while w < wall_s:
        with room.lock:
            room.u.tick(dt)
            room.u.drain()
        w += dt


def test_rooms_are_independent(monkeypatch):
    g = _game(monkeypatch)
    a, b = g.create_room(), g.create_room()
    assert a.code != b.code and len(a.code) == 4 and not set(a.code) & set("ILO")
    assert a.u is not b.u and a.u is not g.u
    a.u.vote("control", "GO")                                  # every other seat is AI-held: they follow
    assert a.u.phase == "review"
    assert b.u.phase == "design" and g.u.phase == "design"
    assert all(v is None for v in b.u.votes.values())
    b.u.set_design("engineer", {"array": "array_large"})
    assert a.u.design["array"] != "array_large"


def test_main_is_the_old_single_game(monkeypatch):
    app, c = _client(monkeypatch)
    g = app.state.game
    assert g.u is g.rooms["MAIN"].u and g.lock is g.rooms["MAIN"].lock
    h = c.get("/api/health").json()
    assert h["room"] == "MAIN" and h["rooms"]["live"] == 1 and "tick_ms" in h["rooms"]
    with c.websocket_connect("/ws/control") as ws:            # no room in the URL: MAIN
        hello = ws.receive_json()
        assert hello["room"] == "MAIN" and hello["token"]
        assert g.rooms["MAIN"].clients


def test_room_api_and_unknown_room(monkeypatch):
    app, c = _client(monkeypatch)
    r = c.post("/api/rooms", json={"judge": False}).json()
    code = r["code"]
    assert r["links"]["engineer"] == f"/ship/?room={code}&seat=engineer"
    info = c.get(f"/api/rooms/{code}").json()
    assert info["phase"] == "design" and info["judge"] is False and len(info["seats"]) == 4
    assert c.get("/api/rooms/ZZZZ").status_code == 404
    assert c.get("/api/factsheet?room=ZZZZ").status_code == 404
    assert c.get(f"/api/factsheet?room={code}").json()["state"] == "design"
    with c.websocket_connect(f"/ws/ship?room={code}") as ws:
        assert ws.receive_json()["room"] == code
        assert app.state.game.rooms[code].clients and not app.state.game.rooms["MAIN"].clients
    with c.websocket_connect("/ws/ship?room=ZZZZ") as ws:
        assert ws.receive_json()["code"] == "no_room"


def test_room_cap_and_eviction(monkeypatch):
    g = _game(monkeypatch, LL_MAX_ROOMS=3, LL_ROOM_IDLE_MIN=30)
    a = g.create_room()
    b = g.create_room()
    assert len(g.rooms) == 3
    a.last_active -= 10                                        # a is the oldest idle room
    c = g.create_room()                                        # full: the oldest idle room goes
    assert a.code not in g.rooms and {b.code, c.code} <= set(g.rooms) and "MAIN" in g.rooms
    for r in (b, c):
        r.clients[object()] = "control"                        # somebody is playing in each
    from lightlag.api.app import RoomError
    with pytest.raises(RoomError) as e:
        g.create_room()
    assert e.value.status == 503 and "busy" in e.value.message
    # idle eviction: nobody connected for longer than LL_ROOM_IDLE_MIN
    c.clients.clear()
    c.last_active = time.time() - 31 * 60
    c.dir.mkdir(parents=True, exist_ok=True)
    assert g.evict_idle() == [c.code] and not c.dir.exists()
    g.rooms["MAIN"].last_active -= 10 ** 6
    assert g.evict_idle() == [] and "MAIN" in g.rooms        # MAIN is never evicted


def test_judge_room_runs_the_demo_and_a_normal_room_does_not(monkeypatch):
    g = _game(monkeypatch)
    j, n = g.create_room(judge=True), g.create_room(judge=False)
    assert j.cfg.judge_mode and not n.cfg.judge_mode and not g.cfg.judge_mode
    _tick(j, 50)
    _tick(n, 50)
    assert j.u.phase in ("review", "mission")                  # Judge Mode voted by itself after 45 s
    assert n.u.phase == "design" and g.u.phase == "design"


@pytest.mark.skipif(not SPK, reason="needs a SPICE planetary kernel")
def test_each_room_flies_its_own_ship_kernel(monkeypatch):
    g = _game(monkeypatch, LL_EXTRA_SPK=SPK, LL_REVIEW=0)
    a, b = g.create_room(), g.create_room()
    assert a.u.ephem.backend == "spice"
    from lightlag.astro.timescale import utc_to_et
    b.u.design["launch_et"] = utc_to_et("2020-08-09T00:00:00")
    for r in (a, b):
        r.u.vote("control", "GO")
        assert r.u.phase == "mission"
    pa, pb = a.u.ephem.ship_spk, b.u.ephem.ship_spk
    assert pa != pb and f"rooms/{a.code}/" in pa.replace("\\", "/") and f"rooms/{b.code}/" in pb.replace("\\", "/")
    assert a.u.ephem.ship_id != b.u.ephem.ship_id
    et = a.u.anchors["launch"] + 30 * 86400
    for r in (a, b):                                           # each SPICE ship matches its own trajectory
        spice = r.u.ephem.pos("SHIP", et)
        analytic = r.u.ephem.ship.helio_cruise_state(et)[0]
        assert np.linalg.norm(spice - analytic) < 50.0, r.code
    assert np.linalg.norm(a.u.ephem.pos("SHIP", et) - b.u.ephem.pos("SHIP", et)) > 1e5
    # a new room created mid-flight must not clear the others' kernels
    g.create_room()
    assert np.linalg.norm(a.u.ephem.pos("SHIP", et) - a.u.ephem.ship.helio_cruise_state(et)[0]) < 50.0
    g.evict(b.code)
    from pathlib import Path
    assert not Path(pb).exists() and not b.dir.exists()
    assert np.linalg.norm(a.u.ephem.pos("SHIP", et) - a.u.ephem.ship.helio_cruise_state(et)[0]) < 50.0


def test_tick_cost_with_four_rooms_in_flight(monkeypatch):
    g = _game(monkeypatch, LL_REVIEW=0)
    rooms = [g.create_room() for _ in range(4)]
    for r in rooms:
        r.u.vote("control", "GO")
        _tick(r, 20)                                           # past launch, into the first scene
    t0 = time.perf_counter()
    n = 50
    for _ in range(n):
        for r in rooms:
            with r.lock:
                r.u.tick(0.1)
                r.u.drain()
    per_tick_ms = (time.perf_counter() - t0) / n * 1000
    assert per_tick_ms < 25.0, per_tick_ms                     # the loop ticks every 100 ms


# ------------------------------------------------------------------ safety
def test_admin_reset_needs_a_token(monkeypatch):
    app, c = _client(monkeypatch)
    remote = {"X-Forwarded-For": "203.0.113.9"}
    assert c.post("/api/admin/reset", headers=remote).status_code == 403
    assert c.post("/api/admin/reset").status_code == 200        # localhost, no token configured
    app2, c2 = _client(monkeypatch, LL_ADMIN_TOKEN="s3cret")
    assert c2.post("/api/admin/reset").status_code == 403       # a token is configured: localhost needs it too
    assert c2.post("/api/admin/reset", headers={"X-Admin-Token": "wrong"}).status_code == 403
    assert c2.post("/api/admin/reset", headers={"X-Admin-Token": "s3cret", **remote}).status_code == 200
    app3, c3 = _client(monkeypatch, LL_PUBLIC=1)
    assert c3.post("/api/admin/reset").status_code == 403       # public build: never without a token
    assert app3.state.game.cfg.llm_enabled is False or "LL_LLM" in __import__("os").environ


def test_voice_upload_limits(monkeypatch):
    app, c = _client(monkeypatch)
    big = b"\x1aE\xdf\xa3" + b"\0" * (2 * 1024 * 1024 + 10)
    r = c.post("/api/voice", files={"file": ("note.webm", big, "audio/webm")})
    assert r.status_code == 413
    r = c.post("/api/voice", files={"file": ("note.webm", b"<html>not audio</html>", "audio/webm")})
    assert r.status_code == 415
    r = c.post("/api/voice", files={"file": ("x.png", b"\x89PNG\r\n\x1a\n" + b"\0" * 64, "image/png")})
    assert r.status_code == 415


def test_voice_clips_are_capped_per_room(monkeypatch, tmp_path):
    from lightlag.config import Config
    from lightlag.voice.radio import VoiceStore
    vs = VoiceStore(Config(), tmp_path, max_clips=3)
    for i in range(6):
        (tmp_path / f"clip{i}.src.webm").write_bytes(b"x")
        (tmp_path / f"clip{i}.radio.wav").write_bytes(b"x")
        t = time.time() - 100 + i
        import os
        os.utime(tmp_path / f"clip{i}.src.webm", (t, t))
        os.utime(tmp_path / f"clip{i}.radio.wav", (t, t))
    removed = vs.prune()
    assert sorted(removed) == ["clip0", "clip1", "clip2"]
    assert sorted({f.name.split(".")[0] for f in tmp_path.iterdir()}) == ["clip3", "clip4", "clip5"]


def test_fly_again_only_from_a_seat_in_that_room(monkeypatch):
    app, c = _client(monkeypatch)
    code = c.post("/api/rooms", json={}).json()["code"]
    with c.websocket_connect("/ws/control") as ws:            # seated in MAIN, not in the new room
        main_token = ws.receive_json()["token"]
        assert c.post(f"/api/fly-again?room={code}", headers={"X-Seat-Token": main_token}).status_code == 403
    with c.websocket_connect(f"/ws/spectator?room={code}") as ws:
        tok = ws.receive_json()["token"]
        assert c.post(f"/api/fly-again?room={code}", headers={"X-Seat-Token": tok}).status_code == 200
    # the token dies with the socket
    assert c.post(f"/api/fly-again?room={code}", headers={"X-Seat-Token": tok}).status_code == 403


def test_room_creation_is_rate_limited_per_ip(monkeypatch):
    app, c = _client(monkeypatch, LL_ROOMS_PER_IP_HOUR=2, LL_MAX_ROOMS=20)
    h = {"CF-Connecting-IP": "198.51.100.7"}
    assert c.post("/api/rooms", json={}, headers=h).status_code == 200
    assert c.post("/api/rooms", json={}, headers=h).status_code == 200
    r = c.post("/api/rooms", json={}, headers=h)
    assert r.status_code == 429 and "Too many" in r.json()["error"]
    assert c.post("/api/rooms", json={}, headers={"CF-Connecting-IP": "198.51.100.8"}).status_code == 200
