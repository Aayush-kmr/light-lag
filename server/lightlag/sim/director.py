"""The Director: acts, scenes, story beats, objectives and Judge Mode bots.

A mission spans fifteen months; a play session spans ~30 minutes (or 8 in
Judge Mode). The Director cuts the mission into *scenes* - short windows of
continuous play anchored to real events (launch, the storm, the conjunction
windows computed from the ephemeris) - and skips time between them. Inside
a scene the universe runs at 1/DELAY_SCALE; Judge Mode compresses further,
but every light-time shown is still the true one.

Bots ("cues") only act in Judge Mode, and only if the human hasn't already
done the thing (`unless: <objective>`), so a judge can grab the controls at
any moment.
"""
from __future__ import annotations

import string
from datetime import datetime, time, timedelta

from ..astro.constants import DAY_S
from ..astro.timescale import et_to_iso, et_to_utc, utc_to_et
from ..comms.bundle import EID_HOME, EID_SHIP, EXPEDITED, NORMAL
from ..comms.network import make_bundle


# Judge Mode cues for the Phase 3 features (crew AI, science data). Judge Mode
# only, like every cue: a human at the console is never overridden.
PHASE3_CUES = {
    # The Mission Scientist asks for science first; it reaches the ship one light time later.
    "s3": [{"at_s": 60, "side": "scientist", "action": "science_priority", "value": "science_first"}],
    # Lantern sees loop A running hot in telemetry that is minutes old and orders a loop
    # switch. By the time it arrives TERN has usually switched already: a stale order.
    "s4": [{"at_s": 2300, "side": "control", "action": "send_command", "command": "pump_switch",
            "text": "Meridian, Lantern. Loop A is running hot. Switch coolant loops. Over."}],
    # Before the silence, the antenna goes back to the families.
    "s6": [{"at_s": 300, "side": "scientist", "action": "science_priority", "value": "crew_first"}],
}


class _SafeDict(dict):
    def __missing__(self, k):
        return "{" + k + "}"


class Director:
    def __init__(self, u):
        self.u = u
        self.scenes = u.scenario["scenes"]
        self.beats = u.story["beats"]
        self.i = -1
        self.scene = None
        self.start_et = 0.0
        self.end_et = 0.0
        self.objectives: dict[str, dict] = {}
        self.objectives_log: list[dict] = []
        self.prompt: dict | None = None
        self.flood_ids: set[str] = set()
        self.flood_expected = 0
        self.flood_done_et: float | None = None
        self.scene_rate = 0.0
        self.warp_factor = 1.0
        self._slow_until = -1.0
        self._design_wall = 0.0
        self.done = False
        self.scene_log: list[dict] = []      # Phase 4: every scene as flown, for the debrief timeline

    # ------------------------------------------------------------- timing
    def _anchor_et(self, spec: dict) -> float:
        u = self.u
        if "anchor" not in spec:
            return utc_to_et(spec["utc"])
        base = u.anchors[spec["anchor"]]
        if "local" in spec:
            local_day = (et_to_utc(base).astimezone(u.home_tz) + timedelta(days=spec.get("day", 0))).date()
            hh, mm = (int(x) for x in spec["local"].split(":"))
            dt = datetime.combine(local_day, time(hh, mm), tzinfo=u.home_tz)
            return utc_to_et(dt)
        return base + spec.get("offset_h", 0) * 3600 + spec.get("offset_min", 0) * 60 + spec.get("offset_s", 0) \
            + spec.get("day", 0) * DAY_S

    def _scene_window(self, sc: dict) -> tuple[float, float]:
        s = self._anchor_et(sc["start"])
        e = self._anchor_et(sc["end"]) if "end" in sc else s + sc.get("duration_s", 1800)
        return s, e

    def _rate_for(self, sc: dict) -> float:
        u = self.u
        dur = self.end_et - self.start_et
        base = u.base_rate
        if u.cfg.judge_mode and sc.get("judge_wall_s"):
            return max(base, dur / sc["judge_wall_s"])
        if sc.get("wall_s"):
            return max(base, dur / sc["wall_s"])
        return base

    # -------------------------------------------------------------- start
    def start(self) -> None:
        self.enter(0)

    def enter(self, idx: int) -> None:
        u = self.u
        if idx >= len(self.scenes):
            self.finish()
            return
        sc = self.scenes[idx]
        s, e = self._scene_window(sc)
        if self.scene_log:
            self.scene_log[-1]["end_et"] = u.et
        coast_from = u.et if s > u.et else None     # Phase 6: real events inside a coast arrive as one list
        if s > u.et:
            u.history.sample("coast_start")
            u.coast_to(s)
            u.history.sample("coast_end")
            u.callouts.on_coast_end()        # Phase 4: still the previous scene's slot
        else:
            s = u.et
            # No skip between these scenes: a frame cadence set at the last scene's warp
            # (hours apart in the silence) must not carry over into this one.
            u._tele_next = min(u._tele_next, u.et)
        self.i, self.scene, self.start_et, self.end_et = idx, sc, s, max(e, s + 60)
        self.scene_log.append({"id": sc["id"], "title": sc["title"], "act": sc.get("act"), "start_et": s, "end_et": self.end_et})
        self.warp_factor = 1.0
        self.scene_rate = self._rate_for(sc)
        u.rate = self.scene_rate
        self.prompt = None
        self.objectives = {o["id"]: {**o, "done": False} for o in sc.get("objectives", [])}
        for b in self.beats:
            if b.get("scene") != sc["id"] or b.get("trigger") or b.get("on_delivery"):
                continue
            self.u.schedule(self._beat_et(b), "director", {"type": "beat", "id": b["id"], "scene": sc["id"]})
        for f in sc.get("faults", []):
            t = s + f.get("at_s", 0) + f.get("at_day", 0) * DAY_S
            u.schedule(t, "director", {"type": "fault", "name": f["name"], "sev": f.get("severity", 1.0), "scene": sc["id"]})
        for c in sc.get("cues", []):
            if "at_s" in c:
                u.schedule(s + c["at_s"], "director", {"type": "cue", "cue": c, "scene": sc["id"]})
        for c in PHASE3_CUES.get(sc["id"], []):
            u.schedule(s + c["at_s"], "director", {"type": "cue", "cue": c, "scene": sc["id"]})
        if sc["id"] == "s8":
            u.cp.add_surge(s - 600, 12)   # Lantern books every antenna for the exit
            self.flood_ids = {b.id for b in u.net.queue("dtn://lantern") if b.kind == "letter"}
            self.flood_expected = len(self.flood_ids)
            for b in u.net.queue("dtn://lantern"):
                if b.kind == "letter":
                    b.payload["flood"] = True
        for L in u.letters:            # earlier letters move to the archive when a new scene starts
            if not L["played"] and not L.get("flood"):
                L["played"], L["archived"] = True, True
        u.note("both", "scene", f"Act {sc.get('act')} / {sc['title']}")
        u.emit(("control", "ship", "spectator", "observer"), "scene", scene=self.public("spectator"))
        u.reality.on_scene(sc["id"], coast_from)

    def _beat_et(self, b: dict) -> float:
        u = self.u
        if "night" in b:
            start_local = et_to_utc(self.start_et).astimezone(u.home_tz)
            first = start_local.date() if start_local.time() < time(20, 0) else start_local.date() + timedelta(days=1)
            day = first + timedelta(days=b["night"] - 1)
            hh, mm = (int(x) for x in b.get("at_local", "20:00").split(":"))
            return utc_to_et(datetime.combine(day, time(hh, mm), tzinfo=u.home_tz))
        return self.start_et + b.get("at_s", 0)

    # --------------------------------------------------------------- loop
    def check(self) -> bool:
        u = self.u
        if self.scene is None or self.done:
            return False
        if self._slow_until > 0 and u.et > self._slow_until:
            self._slow_until = -1.0
            u.rate = self.scene_rate * self.warp_factor
        sc_end = self.end_et
        if self.scene["id"] == "s8" and self.flood_done_et and "reply_sent" in u.flags:
            sc_end = min(sc_end, self.flood_done_et + 600)
        if u.et >= sc_end:
            self.enter(self.i + 1)
            return True
        return False

    def wall_tick(self, dt: float) -> None:
        u = self.u
        if u.phase == "design" and u.cfg.judge_mode:
            self._design_wall += dt
            if self._design_wall > u.scenario["design"].get("judge_wall_s", 45):
                for r in list(u.votes):
                    if u.phase == "design" and u.votes.get(r) != "GO":
                        u.vote(r, "GO")

    def slowdown(self, reason: str) -> None:
        u = self.u
        if self.scene is None:
            return
        wall = 12.0 if u.cfg.judge_mode else 30.0
        u.rate = max(u.base_rate, 900.0 / wall)
        self._slow_until = u.et + 900.0
        u.emit(("ship", "spectator"), "slowdown", reason=reason)

    def after(self, flag: str) -> None:
        u = self.u
        u.flags.add(flag)
        if self.scene is None:
            return
        for w in self.scene.get("warp", []):
            if w["after"] == flag:
                self.warp_factor = w["factor"]
                u.rate = self.scene_rate * self.warp_factor
        for c in self.scene.get("cues", []):
            if c.get("after") == flag and not c.get("_fired"):
                c["_fired"] = True
                u.schedule(u.et + c.get("delay_s", 0), "director", {"type": "cue", "cue": c, "scene": self.scene["id"]})

    # ------------------------------------------------------------- events
    def on_event(self, data: dict) -> None:
        if data.get("scene") and self.scene and data["scene"] != self.scene["id"] and data["type"] == "cue":
            return
        t = data["type"]
        if t == "beat":
            self.fire(next(b for b in self.beats if b["id"] == data["id"]))
        elif t == "fault":
            self.u.ship_sys.inject(data["name"], data["sev"])
            self.u.note("truth", "fault", f"Fault injected: {data['name']}")
            self.u.emit(("spectator", "observer"), "fault", name=data["name"])
        elif t == "cue":
            self.run_cue(data["cue"])
        elif t == "debrief":
            self.finish()

    def _fmt_vars(self) -> dict:
        u = self.u
        g = {"sep_deg": f"{u.ephem.sep_angle_deg(u.et):.1f}", "lt_up": u._fmt(u.lt('up')),
             "round_trip": u._fmt(u.lt('up') + u.lt('down'))}
        return _SafeDict(g)

    def fire(self, b: dict) -> None:
        u = self.u
        cond = b.get("condition")
        if cond:
            replied = any(r["delivered_et"] >= self.start_et + 1200 for r in u.home_received)
            if (cond == "reply_before_deadline") != replied:
                return
        text = string.Formatter().vformat(b.get("text", ""), (), self._fmt_vars())
        who, kind = b.get("from"), b.get("kind")
        if kind == "letter" and who in ("lila", "theo"):
            if b.get("to") == "lantern":
                item = {"id": b["id"], "kind": "text", "from": "Theo Reyes (family)", "text": text, "created_et": u.et,
                        "delivered_et": u.et, "lt_s": 0.0, "family": True}
                u.inbox["control"].append(item)
                u.emit(("control", "spectator"), "message", message=item)
                self.after("theo_to_lantern")
                return
            payload = {"from": who, "text": text, "created_local": u.local_str(u.et), "beat": b["id"],
                       "label": "LETTER", "flood": self.scene and self.scene["id"] == "s7"}
            u.net.submit(make_bundle(EID_HOME, EID_SHIP, "letter", u.et, payload, NORMAL, lifetime_s=90 * DAY_S, report=True))
            u.note("home", "letter", f"{who.title()} recorded a message ({u.local_str(u.et)}): {text}")
            u.emit("spectator", "letter_created", who=who, text=text, local=payload["created_local"])
        elif kind in ("bot", "prompt") and who == "imani":
            self.prompt = {"id": b["id"], "text": b.get("prompt") or text if kind == "bot" else text,
                           "suggestion": text if kind == "bot" else None, "et": u.et}
            u.emit(("ship", "spectator"), "prompt", prompt=self.prompt)
            if kind == "bot" and u.cfg.judge_mode:
                u.schedule(u.et + 2, "director", {"type": "cue", "cue": {"side": "ship", "action": "send_letter",
                                                                          "text": text, "unless": None}})
        elif kind == "tern":
            u.tern_say(text)
        elif kind == "note":
            u.emit(("control", "ship", "spectator"), "note", text=text)

    def run_cue(self, c: dict) -> None:
        u = self.u
        if not u.cfg.judge_mode:
            return
        unless = c.get("unless")
        if unless and (unless in u.flags or (unless in self.objectives and self.objectives[unless]["done"])):
            return
        a = c["action"]
        if a == "send_text":
            u.send(c["side"], "text", c["text"], EXPEDITED if c["side"] == "control" else NORMAL)
        elif a == "send_alert":
            u.send("control", "alert", c["text"], EXPEDITED, label="STORM WARNING")
        elif a == "send_family":
            u.send("control", "text", c["text"], to="home")
        elif a == "send_letter":
            u.send("ship", "letter", c["text"], NORMAL, to="home")
        elif a == "ship_action":
            u.ship_action("bot", c["name"])
        elif a == "sign_policy":
            self.sign_policy(c["side"])
        elif a == "play_all_letters":
            for L in sorted([x for x in u.letters if not x["played"] and x.get("flood")], key=lambda x: x["created_et"]):
                u.play_letter(L["id"])
        elif a == "dsn_surge":
            u.dsn_surge(c.get("hours", 4))
        elif a == "answer_request":
            u.answer_request(c["rule"], True)
        elif a == "science_priority":
            u.science.request_priority("scientist", c.get("value", "crew_first"))
        elif a == "send_command":
            u.send("control", "command", c.get("text", ""), EXPEDITED, command=c["command"],
                   label="CMD " + c["command"].upper())

    # --------------------------------------------------------- objectives
    def objective_done(self, oid: str) -> None:
        u = self.u
        u.flags.add(oid)
        o = self.objectives.get(oid)
        if o and not o["done"]:
            o["done"] = True
            o["done_et"] = u.et
            self.objectives_log.append({"scene": self.scene["id"], "id": oid, "text": o["text"], "side": o["side"],
                                        "iso": et_to_iso(u.et), "wall_s": round(u.wall, 1)})
            u.emit(("control", "ship", "spectator"), "objective", id=oid, text=o["text"])

    def on_frame(self, f: dict) -> None:
        u = self.u
        S = u.ship_sys
        if "hga_point" in self.objectives and "hga_drift" not in S.faults and S.hga_err < 0.1 and u.et - self.start_et > 200:
            self.objective_done("hga_point")
        if "pump_fixed" in self.objectives and u.et - self.start_et > 800:
            hot = f["loop_a_c"] if S.active_loop == "A" else f["loop_b_c"]
            if S.active_loop == "B" or hot < 8:
                self.objective_done("pump_fixed")
        if f["ppco2"] > 7.5 and "scrubber_valve_stuck" in S.faults:
            S.clear("scrubber_valve_stuck")
            S.bed_sat = 0.05
            u.tern_say("ppCO2 at emergency level. Deployed lithium hydroxide backup canisters. CO2 coming down.", "warning")
            u.note("ship", "tern_decision", "TERN [emergency] deployed LiOH backup; crew did not perform manual swap in time.")

    def on_send(self, role: str, kind: str, text: str, b) -> None:
        u = self.u
        sid = self.scene["id"] if self.scene else ""
        low = text.lower()
        if role == "control":
            if sid == "s1" and kind in ("text", "voice"):
                self.objective_done("comm_check")
            if sid == "s5" and (kind == "alert" or any(w in low for w in ("flare", "storm", "shelter", "proton"))):
                if "flare_earth" in u.flags:
                    self.objective_done("storm_warning")
            if sid == "s6" and kind in ("text", "voice"):
                self.objective_done("control_letter")
        if role in ("ship", "tern", "bot") and kind in ("text", "voice", "letter"):
            if sid == "s1" and u.last_instruction and self._is_readback(u.last_instruction, text):
                self.objective_done("readback")
            if kind == "letter":
                if sid == "s2":
                    self.objective_done("goodnight_live")
                if sid in ("s3", "s4"):
                    self.objective_done("answer_lila")
                if sid == "s6":
                    self.objective_done("explain_lila")
                if sid == "s8" and "flood_all_played" in u.flags:
                    u.flags.add("reply_sent")
            self.prompt = None

    @staticmethod
    def _is_readback(instr: str, reply: str) -> bool:
        import re
        key = lambda s: {w for w in re.findall(r"[a-z0-9.]+", s.lower()) if len(w) > 3} - {  # noqa: E731
            "meridian", "lantern", "over", "copy", "this", "that", "with", "your", "have", "check", "comm"}
        a, b = key(instr), key(reply)
        nums_a = set(re.findall(r"-?\d+(?:\.\d+)?", instr))
        nums_ok = nums_a <= set(re.findall(r"-?\d+(?:\.\d+)?", reply))
        return nums_ok and len(a & b) >= max(2, int(0.4 * len(a)))

    def sign_policy(self, role: str) -> None:
        u = self.u
        if role == "control":
            u.autonomy.sign("control")
            if u.phase == "mission":
                u.send_policy(["control"])
                u.note("control", "policy", f"Lantern signed and uplinked policy v{u.autonomy.version}")
        else:
            u.autonomy.sign("ship")
            u.flags.add("ship_countersigned")
            u.note("ship", "policy", "Commander countersigned the autonomy policy")
        self.check_policy_signed()

    def check_policy_signed(self) -> None:
        u = self.u
        if u.phase == "mission" and "policy_uplinked" not in u.flags:
            if any(b.kind == "policy" and b.state == "delivered" for b in u.net.recent):
                u.flags.add("policy_uplinked")
        if "policy_uplinked" in u.flags and "ship_countersigned" in u.flags:
            self.objective_done("policy_signed")

    # ------------------------------------------------------------- letters
    def on_letter_delivered(self, item: dict) -> None:
        for b in self.beats:
            if b.get("on_delivery") and b["on_delivery"] == item.get("beat"):
                self.u.schedule(self.u.et + b.get("delay_s", 5), "director", {"type": "beat", "id": b["id"]})
        if item["id"] in self.flood_ids:
            self.flood_ids.discard(item["id"])
            if not self.flood_ids and self.flood_expected:
                self.after("flood_arrived")
                self.u.tern_say(f"{self.flood_expected} messages from home. They were all waiting for us.")

    def on_letter_played(self, L: dict) -> None:
        u = self.u
        flood = [x for x in u.letters if x.get("flood")]
        if self.scene and self.scene["id"] == "s8" and flood and all(x["played"] for x in flood) and \
                "flood_all_played" not in u.flags:
            self.flood_done_et = u.et
            self.after("flood_all_played")
            self.objective_done("letters_played")
            for b in self.beats:
                if b.get("trigger") == "flood_all_played":
                    self.fire(b)

    def on_home_letter(self, rec: dict) -> None:
        pass

    # --------------------------------------------------------------- end
    def finish(self) -> None:
        u = self.u
        if self.done:
            return
        self.done = True
        if self.scene_log:
            self.scene_log[-1]["end_et"] = u.et
        u.callouts.on_finish()
        u.history.sample("end")
        u.phase = "debrief"
        u.rate = 0.0
        u.note("both", "debrief", "Mission debrief")
        u.emit(("control", "ship", "spectator", "observer"), "debrief", report=u.after_action())

    def public(self, role: str) -> dict:
        sc = self.scene
        if sc is None:
            return {"scene": None, "index": -1, "total": len(self.scenes)}
        from ..catalog import side_of
        side = side_of(role)
        objs = [o for o in self.objectives.values() if side in ("spectator", "observer") or o["side"] in (side, "both")]
        nxt = self.scenes[self.i + 1]["title"] if self.i + 1 < len(self.scenes) else "Debrief"
        return {"scene": {k: sc.get(k) for k in ("id", "act", "act_title", "title", "card", "audio")},
                "act_title": next((s.get("act_title") for s in reversed(self.scenes[: self.i + 1]) if s.get("act_title")), ""),
                "index": self.i, "total": len(self.scenes), "start_et": self.start_et, "end_et": self.end_et,
                "objectives": [{"id": o["id"], "text": o["text"], "side": o["side"], "done": o["done"]} for o in objs],
                "prompt": self.prompt if side in ("ship", "spectator") else None, "next": nxt,
                "flood": {"expected": self.flood_expected, "pending": len(self.flood_ids)},
                "warp": self.warp_factor, "slow": self._slow_until > 0}
