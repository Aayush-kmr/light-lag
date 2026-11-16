"""LIGHT-LAG universe server: FastAPI + WebSockets.

    /                 landing page (pick a role, LAN join links + QR codes)
    /control/         Lantern Control client        /ship/   Meridian-1 client
    /spectator/       big-screen + phone view       /ws/{role}  state API
    /api/...          REST helpers (see docs/ws-api.md)

The WebSocket API is engine-agnostic: the web clients, a Godot port and the
planned UE5 cinematic client all consume the same JSON.
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
import math
import logging
import os
import random
import re
import secrets
import shutil
import socket
import threading
import time
from collections import deque
from pathlib import Path

import yaml
from fastapi import FastAPI, File, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from ..ai.agents import Agent
from ..ai.llm import airplane_check
from ..astro.constants import AU_KM
from ..astro.kepler import EQ_TO_ECL
from ..comms.bundle import EXPEDITED, NORMAL
from ..config import CONFIG, DATA, ROOT
from ..data_sources import DSNNow
from ..catalog import catalog, side_of
from ..sim.universe import ROLES, Universe
from ..voice.radio import VoiceStore, quindar_demo_wav

log = logging.getLogger("lightlag.api")
PROTOCOL_VERSION = "1.0"


ROOM_LETTERS = "ABCDEFGHJKMNPQRSTUVWXYZ"     # no I, L or O: codes are read aloud and typed on phones
ROOM_RE = re.compile(r"^[A-Z]{4}$")


class RoomError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


class Room:
    """One game: one authoritative universe, its own lock, sockets, voice clips and
    files. A room's error never stops another room."""

    def __init__(self, game: "Game", code: str, judge: bool):
        self.game, self.code, self.judge = game, code, judge
        # MAIN keeps the server's own config (admin reset may change it); other rooms
        # get a copy, so a judge room runs the scripted demo and nobody else does.
        self.cfg = game.cfg if code == "MAIN" else dataclasses.replace(game.cfg, judge_mode=judge)
        self.dir = DATA / "runtime" / "rooms" / code
        self.lock = threading.RLock()
        self.clients: dict[WebSocket, str] = {}
        self.tokens: dict[str, WebSocket] = {}         # seat token -> socket, for fly-again
        self.created = self.last_active = time.time()
        self.voice = VoiceStore(self.cfg, self.cfg.audio_dir if code == "MAIN" else self.dir / "audio")
        self.u = Universe(self.cfg, self.dir)
        self._traj_cache = None
        self.errors = 0

    # --------------------------------------------------------------- state
    @property
    def idle_s(self) -> float:
        return 0.0 if self.clients else time.time() - self.last_active

    def _replace(self, u: Universe) -> None:
        old, self.u = self.u, u
        try:
            old.ephem.detach_ship()
        except Exception:  # pragma: no cover
            log.exception("room %s: detach", self.code)
        self._traj_cache = None

    def close(self) -> None:
        """Evicted: unload the ship kernel and delete the room's files."""
        try:
            self.u.ephem.detach_ship()
        except Exception:  # pragma: no cover
            pass
        if self.code != "MAIN":
            shutil.rmtree(self.dir, ignore_errors=True)

    def public(self) -> dict:
        with self.lock:
            return {"code": self.code, "judge": self.judge, "phase": self.u.phase, "clients": len(self.clients),
                    "seats": self.u.seats_public(), "created": self.created, "idle_s": round(self.idle_s)}

    def reset(self, judge: bool | None = None, delay_scale: float | None = None) -> None:
        with self.lock:
            if judge is not None:
                self.cfg.judge_mode = judge
                self.judge = judge
            if delay_scale is not None:
                self.cfg.delay_scale = delay_scale
            seats = dict(self.u.seats)
            self._replace(Universe(self.cfg, self.dir))
            self.u.seats.update(seats)      # the people still connected keep their seats

    def fly_again(self, apply_suggestion: bool = False) -> dict:
        """Phase 4: back to the design bay with the flown design (and TERN's rules)
        preloaded. Flight state is cleared; seats are kept."""
        import copy
        with self.lock:
            old = self.u
            rec = old.design_record
            choices = copy.deepcopy(rec["choices"] if rec else old.design)
            suggestion = None
            if apply_suggestion and rec:
                from ..sim.debrief import chains, fly_again
                suggestion = fly_again(old, chains(old))
                if suggestion:
                    choices.update(copy.deepcopy(suggestion["changes"]))
            policy = copy.deepcopy(rec["policy"] if rec else old.autonomy.policy)
            seats = dict(old.seats)
            self._replace(Universe(self.cfg, self.dir))
            self.u.seats.update(seats)
            self.u.design.update(choices)
            self.u.autonomy.update(policy, "control")
            self.u.design_changes = ["flown design loaded"] + ([suggestion["text"]] if suggestion else [])
            ev = self.u.design_eval()
            return {"ok": True, "phase": self.u.phase, "design": {k: ev.get(k) for k in ("valid", "reasons", "budgets")},
                    "applied": suggestion}

    def seated(self, token: str | None) -> bool:
        return bool(token) and self.tokens.get(token) in self.clients

    # ------------------------------------------------------------ messages
    async def handle(self, ws: WebSocket, role: str, m: dict) -> None:
        t = m.get("type", "")
        u = self.u
        reply = None
        side = side_of(role)
        self.last_active = time.time()
        with self.lock:
            if t == "design.set":
                reply = u.set_design(role, m.get("changes", {}))
                self._traj_cache = None
            elif t == "design.vote" and role in u.votes:
                reply = u.vote(role, m.get("vote", "NO-GO"))
            elif t == "policy.set":
                if u.phase == "design":
                    u.autonomy.update(m.get("rules", []), role)
                    reply = u.autonomy.public()
                elif side == "control":
                    u.autonomy_draft = m.get("rules", [])
                    reply = {"ok": True, "note": "Draft saved at Lantern. Sign & uplink to send it."}
            elif t == "policy.sign":
                if side == "control" and u.phase == "mission" and getattr(u, "autonomy_draft", None):
                    u.send_policy(["control"], rules=u.autonomy_draft)
                    u.autonomy.sign("control")
                    u.director.check_policy_signed()
                else:
                    u.director.sign_policy(side)
                reply = {"ok": True}
            elif t == "msg.send":
                prio = EXPEDITED if m.get("priority") == "expedited" else NORMAL
                reply = u.send(role, m.get("kind", "text"), m.get("text", ""), prio, m.get("audio_id"),
                               int(m.get("audio_bytes") or 0), m.get("to"), label=m.get("label"))
            elif t == "cmd.send" and side == "control":
                reply = u.send("control", "command", m.get("text", ""), EXPEDITED, command=m.get("command"),
                               args=m.get("args"), label="CMD " + str(m.get("command", "")).upper())
            elif t == "ship.action" and side == "ship":
                reply = u.ship_action(role, m.get("action", ""), m.get("args"))
            elif t == "letter.play" and role == "ship":
                reply = u.play_letter(m.get("id", ""))
            elif t == "request.answer" and side == "control":
                reply = u.answer_request(m.get("rule", ""), bool(m.get("approve")))
            elif t == "dsn.surge" and side == "control":
                reply = u.dsn_surge(float(m.get("hours", 4)))
            elif t == "comms.plan" and side == "control" and u.cp:
                u.cp.set_plan(m.get("plan", "standard"))
                u._passes_cache = (-1e18, [])
                reply = {"ok": True}
            elif t == "ping":
                reply = u.ping()
            elif t == "prompt.dismiss":
                u.director.prompt = None
            elif t == "science.priority" and side == "control":      # Phase 3: travels as a command
                reply = u.science.request_priority(role, m.get("value", ""))
            elif t == "crew.assign" and side == "ship":               # Phase 3: ask an AI crew member
                reply = u.crew.assign(role, m.get("crew", ""), m.get("task", ""))
        if t == "ai.ask" and side in ("control", "ship"):
            agent = self.game.agents[side]
            res = await asyncio.to_thread(self._ask, agent, m.get("text", ""))
            await self.game.send(ws, {"type": "ai.reply", **res})
            for w, r in list(self.clients.items()):
                if r == "spectator":
                    await self.game.send(w, {"type": "ai.reply", **res})
            return
        if reply is not None:
            await self.game.send(ws, {"type": "reply", "to": t, "data": reply})

    def _ask(self, agent: Agent, text: str) -> dict:
        # The model call runs outside the lock; each tool call takes it (see tools.call).
        return agent.respond(_LockedUniverse(self.u, self.lock), text)

    def trajectory(self) -> dict:
        if self._traj_cache:
            return self._traj_cache
        u = self.u
        if u.ephem.ship is None:
            return {"ship": [], "earth": [], "mars": []}
        a = u.anchors
        import numpy as np
        ts = np.linspace(a["launch"], a["mission_end"], 400)

        def xy(body, t):
            r = EQ_TO_ECL @ u.ephem.pos(body, float(t))
            return [round(float(r[0] / AU_KM), 5), round(float(r[1] / AU_KM), 5)]
        with self.lock:
            self._traj_cache = {"ship": [xy("SHIP", t) for t in ts],
                                "earth": [xy("EARTH", t) for t in np.linspace(a["launch"], a["launch"] + 365.25 * 86400, 120)],
                                "mars": [xy("MARS", t) for t in np.linspace(a["launch"], a["launch"] + 687 * 86400, 160)],
                                "anchors": a}
        return self._traj_cache


class Game:
    """The server: every live room, the shared agents and DSN feed, and the tick loop.
    `game.u`, `game.lock`, `game.clients` and `game.voice` still mean the MAIN room, so
    the single-game API (and every test written for it) keeps working."""

    def __init__(self, cfg=CONFIG):
        self.cfg = cfg
        self.dsn = DSNNow(cfg.cache_dir, cfg.offline)
        self.agents = {
            "ship": Agent("ship", cfg.tern_url, cfg.tern_model, cfg.llm_enabled, cfg.llm_timeout_s, cfg.allow_remote_llm),
            "control": Agent("control", cfg.fido_url, cfg.fido_model, cfg.llm_enabled, cfg.llm_timeout_s, cfg.allow_remote_llm),
        }
        self.airplane = {"airplane_mode": None}
        self.rooms: dict[str, Room] = {}
        self.sockets: dict[WebSocket, Room] = {}
        self.created_by_ip: dict[str, deque] = {}
        self.tick_ms = 0.0                 # moving average of one loop over every room
        self._rng = random.SystemRandom()
        self._sweep_stale_files()
        self.rooms["MAIN"] = Room(self, "MAIN", cfg.judge_mode)
        # Phase 6 ("Right now" on the landing page): the same ephemeris the game uses,
        # asked about today. Cached for 1 s; None (or an error) means unavailable.
        self._rn_cache: tuple[float, dict | None] = (-1e9, None)
        try:
            from ..astro.ephemeris import EphemerisService
            self.right_now_ephem = EphemerisService(cfg.kernel_dir, cfg.ephemeris).load()
        except Exception:  # pragma: no cover - depends on host
            log.exception("right-now ephemeris unavailable")
            self.right_now_ephem = None

    def right_now(self) -> dict | None:
        """Earth-Mars light time at this second, from the game's ephemeris (1 s cache)."""
        now = time.time()
        if now - self._rn_cache[0] < 1.0:
            return self._rn_cache[1]
        out = None
        e = self.right_now_ephem
        if e is not None:
            try:
                from datetime import datetime, timezone
                from ..astro.timescale import utc_to_et
                t = datetime.fromtimestamp(now, timezone.utc)
                et = utc_to_et(t)
                lt = e.light_time("EARTH", "MARS", et)            # a signal leaving Earth now
                lt2 = e.light_time("EARTH", "MARS", et + 60.0)
                d = e.distance_km("EARTH", "MARS", et)
                if all(math.isfinite(x) and x > 0 for x in (lt, lt2, d)):
                    out = {"utc": t.isoformat().replace("+00:00", "Z"), "unix": now, "lt_s": lt, "lt_rate": (lt2 - lt) / 60.0,
                           "dist_km": d, "dist_au": d / AU_KM, "backend": e.backend, "label": right_now_label(e)}
            except Exception:
                log.exception("right-now: ephemeris query failed")
                out = None
        self._rn_cache = (now, out)
        return out

    @staticmethod
    def _sweep_stale_files() -> None:
        """Room folders and ship kernels left by a previous server process."""
        base = DATA / "runtime" / "rooms"
        if not base.exists():
            return
        for p in base.iterdir():
            if p.is_dir() and p.name != "MAIN" and ROOM_RE.match(p.name):
                shutil.rmtree(p, ignore_errors=True)
        for f in (base / "MAIN").glob("meridian1_*.bsp"):
            try:
                f.unlink()
            except OSError:      # still open in this process (tests): leave it
                pass

    # MAIN aliases (the single-game API) -----------------------------------
    main = property(lambda self: self.rooms["MAIN"])
    lock = property(lambda self: self.main.lock)
    clients = property(lambda self: self.main.clients)
    voice = property(lambda self: self.main.voice)

    @property
    def u(self):
        return self.main.u

    @u.setter
    def u(self, value):
        self.main.u = value
        self.main._traj_cache = None

    def reset(self, judge: bool | None = None, delay_scale: float | None = None) -> None:
        self.main.reset(judge, delay_scale)

    def fly_again(self, apply_suggestion: bool = False) -> dict:
        return self.main.fly_again(apply_suggestion)

    def trajectory(self) -> dict:
        return self.main.trajectory()

    async def handle(self, ws: WebSocket, role: str, m: dict) -> None:
        await (self.sockets.get(ws) or self.main).handle(ws, role, m)

    # ----------------------------------------------------------------- rooms
    def room(self, code: str | None) -> Room:
        code = (code or "MAIN").upper()
        r = self.rooms.get(code)
        if r is None:
            raise RoomError(404, f"No mission room {code}. It may have ended; start a new one from the landing page.")
        return r

    def _new_code(self) -> str:
        while True:
            code = "".join(self._rng.choice(ROOM_LETTERS) for _ in range(4))
            if code not in self.rooms and code != "MAIN":
                return code

    def evict(self, code: str) -> None:
        r = self.rooms.pop(code, None)
        if r is not None:
            log.info("room %s evicted", code)
            r.close()

    def evict_idle(self) -> list[str]:
        limit = self.cfg.room_idle_min * 60.0
        gone = [c for c, r in self.rooms.items() if c != "MAIN" and not r.clients and r.idle_s >= limit]
        for c in gone:
            self.evict(c)
        return gone

    def create_room(self, judge: bool = False, ip: str = "local") -> Room:
        now = time.time()
        q = self.created_by_ip.setdefault(ip, deque())
        while q and now - q[0] > 3600:
            q.popleft()
        if len(q) >= self.cfg.rooms_per_ip_hour:
            raise RoomError(429, "Too many new missions from this address. Try again later, or join an existing room with its code.")
        self.evict_idle()
        if len(self.rooms) >= self.cfg.max_rooms:
            idle = sorted((r for c, r in self.rooms.items() if c != "MAIN" and not r.clients), key=lambda r: r.last_active)
            if not idle:
                raise RoomError(503, "All mission rooms are busy, try again in a few minutes.")
            self.evict(idle[0].code)
        r = Room(self, self._new_code(), judge)
        self.rooms[r.code] = r
        q.append(now)
        log.info("room %s created (judge=%s)", r.code, judge)
        return r

    # ------------------------------------------------------------------ io
    async def send(self, ws: WebSocket, msg: dict) -> None:
        try:
            await ws.send_text(dumps(msg))
        except Exception:
            self.drop(ws)

    def drop(self, ws: WebSocket) -> None:
        room = self.sockets.pop(ws, None) or self.main
        role = room.clients.pop(ws, None)
        for t, w in list(room.tokens.items()):
            if w is ws:
                room.tokens.pop(t, None)
        if role is not None:
            room.last_active = time.time()
            with room.lock:
                room.u.seat_join(role, -1)

    async def broadcast_events(self, events: list[dict], room: Room | None = None) -> None:
        room = room or self.main
        for ev in events:
            for ws, role in list(room.clients.items()):
                if role in ev["audience"] or side_of(role) in ev["audience"]:
                    await self.send(ws, {"type": "event", **ev["event"]})

    async def loop(self) -> None:
        last = time.perf_counter()
        last_bc = last_evict = 0.0
        while True:
            await asyncio.sleep(1.0 / self.cfg.tick_hz)
            now = time.perf_counter()
            dt, last = min(now - last, 0.5), now
            bc = now - last_bc >= 1.0 / self.cfg.broadcast_hz
            if bc:
                last_bc = now
            spent = 0.0
            dsn = self.dsn.public()
            for room in list(self.rooms.values()):
                try:
                    t0 = time.perf_counter()
                    with room.lock:
                        room.u.tick(dt)
                        room.u.dsn_now = dsn
                        events = room.u.drain()
                    spent += time.perf_counter() - t0
                    await self.broadcast_events(events, room)
                    if bc:
                        for ws, role in list(room.clients.items()):
                            with room.lock:
                                snap = room.u.snapshot(role)
                            snap["room"] = room.code
                            await self.send(ws, snap)
                except Exception:      # one room's failure must not stop the others
                    room.errors += 1
                    log.exception("room %s: tick failed", room.code)
            self.tick_ms = 0.9 * self.tick_ms + 0.1 * spent * 1000.0
            if now - last_evict > 30.0:
                last_evict = now
                self.evict_idle()

    async def dsn_loop(self) -> None:
        while True:
            await self.dsn.refresh()
            self.airplane = await asyncio.to_thread(airplane_check)
            await asyncio.sleep(10.0)


def right_now_label(e) -> str:
    """'NASA JPL SPICE DE440' with kernels, else the analytic fallback, labelled MODEL."""
    if e.backend == "spice":
        m = re.search(r"de(\d{3})", os.path.basename(e.loaded.get("spk", "")).lower())
        return f"NASA JPL SPICE DE{m.group(1)}" if m else "NASA JPL SPICE"
    return "analytic ephemeris, MODEL"


class _LockedUniverse:
    """Proxy handed to the agents: tools.call() unwraps it and runs each tool
    under the server lock, so a slow LLM never blocks the simulation."""

    def __init__(self, u, lock):
        self._u, self._lock = u, lock

    def __getattr__(self, name):
        with self._lock:
            return getattr(self._u, name)


def _finite(o):
    """JSON has no Infinity or NaN and browsers refuse them: send null instead."""
    if isinstance(o, float):
        return o if math.isfinite(o) else None
    if isinstance(o, dict):
        return {k: _finite(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_finite(v) for v in o]
    return o


def dumps(o) -> str:
    return json.dumps(_finite(json.loads(json.dumps(o, default=_json_default))), allow_nan=False)


def _json_default(o):
    import numpy as np
    if isinstance(o, (np.floating, np.integer)):
        return o.item()
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, set):
        return sorted(o)
    return str(o)


def lan_urls(port: int) -> list[str]:
    ips = set()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ips.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    ips.discard("127.0.0.1")
    return [f"http://{ip}:{port}" for ip in sorted(ips)] or [f"http://127.0.0.1:{port}"]


def qr_svg(text: str) -> str | None:
    try:
        import qrcode
        import qrcode.image.svg
        img = qrcode.make(text, image_factory=qrcode.image.svg.SvgPathImage, box_size=8)
        return img.to_string(encoding="unicode")
    except Exception:
        return None


AUDIO_MAGIC = (b"\x1aE\xdf\xa3", b"OggS", b"RIFF", b"ID3", b"fLaC")     # webm/mkv, ogg, wav, mp3, flac


def looks_like_audio(data: bytes, content_type: str | None) -> bool:
    if content_type and not (content_type.startswith("audio/") or content_type in ("video/webm", "application/octet-stream")):
        return False
    head = data[:12]
    return head.startswith(AUDIO_MAGIC) or head[4:8] == b"ftyp" or (len(head) > 1 and head[0] == 0xFF and head[1] & 0xE0 == 0xE0)


def create_app(cfg=CONFIG) -> FastAPI:
    app = FastAPI(title="LIGHT-LAG universe server", version=PROTOCOL_VERSION)
    game = Game(cfg)
    app.state.game = game
    clients_dir = ROOT / "clients"

    @app.exception_handler(RoomError)
    async def _room_error(request: Request, exc: RoomError):
        return JSONResponse({"error": exc.message}, exc.status)

    def client_ip(request: Request) -> str:
        # Behind a Cloudflare tunnel every request comes from 127.0.0.1; the visitor is here.
        return (request.headers.get("cf-connecting-ip") or (request.client.host if request.client else "unknown"))

    def is_local(request: Request) -> bool:
        return (request.client.host if request.client else "") in ("127.0.0.1", "::1", "localhost", "testclient") \
            and not request.headers.get("cf-connecting-ip") and not request.headers.get("x-forwarded-for")

    def links(code: str) -> dict:
        q = f"?room={code}"
        return {"control": f"/control/{q}", "scientist": f"/control/{q}&seat=scientist", "ship": f"/ship/{q}",
                "engineer": f"/ship/{q}&seat=engineer", "spectator": f"/spectator/{q}", "factsheet": f"/factsheet/{q}"}

    @app.on_event("startup")
    async def _start():
        asyncio.create_task(game.loop())
        asyncio.create_task(game.dsn_loop())

    @app.get("/")
    async def index():
        return FileResponse(clients_dir / "index.html")

    @app.get("/api/health")
    async def health(room: str = "MAIN"):
        r = game.room(room)
        u = r.u
        return {"ok": True, "protocol": PROTOCOL_VERSION, "room": r.code, "phase": u.phase,
                "phases": ["design", "review", "mission", "debrief"],
                "review": ({"verdict": u.review_last["verdict"], "n": len(u.reviews)} if u.review_last else None),
                "rooms": {"live": len(game.rooms), "max": cfg.max_rooms, "tick_ms": round(game.tick_ms, 3),
                          "phases": {c: x.u.phase for c, x in game.rooms.items()},
                          "clients": sum(len(x.clients) for x in game.rooms.values())},
                "config": r.cfg.public(),
                "ephemeris": u.ephem.describe(), "space_weather": u.sw.source_summary,
                "agents": {k: a.status() for k, a in game.agents.items()}, "voice": r.voice.capabilities(),
                "anomaly_detector": u.anomaly.kind, "dsn_now": game.dsn.public().get("source"),
                "airplane": game.airplane}

    @app.get("/api/right-now")
    async def right_now():
        # Phase 6: the landing page's first line. Never a fake number: 503 hides it.
        if not cfg.right_now:
            return JSONResponse({"error": "disabled"}, 404)
        r = game.right_now()
        if r is None:
            return JSONResponse({"error": "The ephemeris is unavailable right now."}, 503)
        return r

    @app.post("/api/rooms")
    async def create_room(request: Request):
        try:
            body = await request.json()
        except Exception:
            body = {}
        r = game.create_room(bool((body or {}).get("judge")), client_ip(request))
        return {"code": r.code, "judge": r.judge, "links": links(r.code)}

    @app.get("/api/rooms/{code}")
    async def room_info(code: str):
        r = game.room(code)
        return {**r.public(), "links": links(r.code)}

    @app.get("/api/meta")
    async def meta(room: str = "MAIN"):
        return game.room(room).u.meta()

    @app.get("/api/porkchop")
    async def pork(room: str = "MAIN"):
        r = game.room(room)
        with r.lock:
            return JSONResponse(r.u.porkchop_data())

    @app.get("/api/trajectory")
    async def traj(room: str = "MAIN"):
        return game.room(room).trajectory()

    @app.get("/api/after-action")
    async def aar(room: str = "MAIN"):
        r = game.room(room)
        with r.lock:
            return JSONResponse(json.loads(dumps(r.u.after_action())))

    @app.get("/api/provenance")
    async def prov():
        return yaml.safe_load((DATA / "provenance.yaml").read_text())

    @app.get("/api/catalog")
    async def catalog_api():
        return catalog().public()

    @app.get("/api/seats")
    async def seats_api(room: str = "MAIN"):
        r = game.room(room)
        with r.lock:
            return r.u.seats_public()

    @app.get("/api/science-cards")
    async def cards():
        return yaml.safe_load((DATA / "science_cards.yaml").read_text())

    @app.get("/api/model-card")
    async def model_card():
        out = {}
        for f in sorted((ROOT / "reports").glob("*.json")):
            out[f.stem] = json.loads(f.read_text())
        return out

    @app.get("/api/lan")
    async def lan(room: str = "MAIN"):
        urls = lan_urls(cfg.port)
        q = "" if room.upper() == "MAIN" else f"room={room.upper()}"
        paths = {"control": "control/", "scientist": "control/?seat=scientist", "ship": "ship/",
                 "engineer": "ship/?seat=engineer", "spectator": "spectator/"}
        if q:
            paths = {k: v + ("&" if "?" in v else "?") + q for k, v in paths.items()}
        return {"urls": [{"url": u, "qr": {r: qr_svg(f"{u}/{p}") for r, p in paths.items()}} for u in urls]}

    @app.post("/api/voice")
    async def voice(file: UploadFile = File(...), room: str = "MAIN"):
        r = game.room(room)
        data = await file.read(cfg.voice_max_bytes + 1)
        if len(data) > cfg.voice_max_bytes:
            return JSONResponse({"error": f"Voice notes are limited to {cfg.voice_max_bytes // (1024 * 1024)} MB."}, 413)
        if not looks_like_audio(data, file.content_type):
            return JSONResponse({"error": "That upload is not an audio clip."}, 415)
        return await asyncio.to_thread(r.voice.ingest, data, file.filename or "clip.webm")

    @app.get("/api/audio/{aid}")
    async def audio(aid: str, room: str = "MAIN"):
        p = game.room(room).voice.path("".join(c for c in aid if c.isalnum()))
        return FileResponse(p, media_type="audio/wav") if p.exists() else JSONResponse({"error": "not found"}, 404)

    @app.get("/api/quindar.wav")
    async def quindar():
        return Response(quindar_demo_wav(), media_type="audio/wav")

    @app.post("/api/fly-again")
    async def fly_again(request: Request, suggestion: int = 0, fresh: int = 0, room: str = "MAIN", token: str | None = None):
        r = game.room(room)
        # Only someone seated in this room may send everyone back to the bay.
        if not r.seated(request.headers.get("x-seat-token") or token):
            return JSONResponse({"error": "Only a seat connected to this mission can fly it again."}, 403)
        if fresh:                       # "Start over" after a scrub: a new mission, default design
            r.reset()
            return {"ok": True, "phase": r.u.phase, "fresh": True}
        return JSONResponse(json.loads(dumps(r.fly_again(bool(suggestion)))))

    @app.get("/api/factsheet")
    async def factsheet_api(room: str = "MAIN"):
        from ..sim.factsheet import build
        r = game.room(room)
        with r.lock:
            return JSONResponse(json.loads(dumps(build(r.u))))

    @app.post("/api/admin/reset")
    async def reset(request: Request, judge: int | None = None, delay_scale: float | None = None, room: str = "MAIN"):
        token = request.headers.get("x-admin-token", "")
        if cfg.admin_token:
            ok = secrets.compare_digest(token, cfg.admin_token)
        else:
            ok = is_local(request) and not cfg.public_mode
        if not ok:
            return JSONResponse({"error": "Reset needs the admin token (header X-Admin-Token)."}, 403)
        game.room(room).reset(None if judge is None else bool(judge), delay_scale)
        return {"ok": True, "room": room.upper(), "config": game.room(room).cfg.public()}

    @app.websocket("/ws/{role}")
    async def ws(websocket: WebSocket, role: str, room: str = "MAIN"):
        if role not in ROLES:
            await websocket.close(code=4400)
            return
        r = game.rooms.get((room or "MAIN").upper())
        if r is None:
            await websocket.accept()
            await websocket.send_text(dumps({"type": "error", "error": f"No mission room {room.upper()}.", "code": "no_room"}))
            await websocket.close(code=4404)
            return
        await websocket.accept()
        r.clients[websocket] = role
        game.sockets[websocket] = r
        token = secrets.token_urlsafe(12)
        r.tokens[token] = websocket
        r.last_active = time.time()
        with r.lock:
            r.u.seat_join(role, +1)
            hello = {"type": "hello", "role": role, "protocol": PROTOCOL_VERSION, "config": r.cfg.public(),
                     "meta": r.u.meta(), "ephemeris": r.u.ephem.describe(),
                     "agents": {k: a.status() for k, a in game.agents.items()}, "voice": r.voice.capabilities(),
                     "seat": role, "side": side_of(role), "seats": r.u.seats_public(), "room": r.code, "token": token}
        await game.send(websocket, hello)
        try:
            while True:
                m = json.loads(await websocket.receive_text())
                await r.handle(websocket, role, m)
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            if websocket in r.clients:
                game.sockets.setdefault(websocket, r)
                game.drop(websocket)

    for name in ("control", "ship", "spectator", "factsheet", "shared", "vendor"):
        d = clients_dir / name
        if d.exists():
            app.mount(f"/{name}", StaticFiles(directory=str(d), html=True), name=name)
    return app
