"""The crew of Meridian-1 as people who act on their own.

Four crew members (data/catalog/crew.yaml), each with fatigue, stress, trust
in the ground and morale (0..1), skills, a current task and the last thing
they said.

Who the AI drives
    The Science Officer and the Flight Surgeon always. The Commander and the
    Flight Engineer only while their seat has no human (`Universe.seat_is_ai`).
    The AI never acts for a seat a human holds; it may only suggest (a bark).

How it decides (deterministic utility AI, no dice)
    Every THINK_S universe-seconds in a scene, or at once when something
    changes (a new alarm, a TERN call, a storm flag), each AI-driven crew
    member scores its candidate actions and takes the best one above a
    threshold. Repairs, shelter and science are executed only through
    `Universe.ship_action`, so they count toward objectives exactly like a
    human's. A task takes time; tiredness makes it slower.

Stale orders
    Every Earth command carries the telemetry Lantern was looking at when it
    sent it (`order_context`). On arrival the crew compares it with now: an
    order whose target is already fixed, or that would undo what the crew
    already did, is held and logged; one the situation has outrun is held
    and queried; otherwise it is executed under the autonomy policy. The
    crew log is downlinked, so Lantern learns of the judgment a light time
    later.

Trust and morale
    Trust in the ground rises when an order arrives in time and helps, and
    falls when orders arrive stale or contradict the crew. Low trust makes
    the crew act sooner on its own judgment; high trust makes it wait for
    the ground when time allows. Morale rises with contact from home and
    falls in long silences; it sets how fast stress recovers.
    **Morale is shown, never scored. Letters are never scored.**

Every coefficient is MODEL: see docs/science.md, "Crew model".
"""
from __future__ import annotations

import re
import string
from dataclasses import asdict, dataclass, field

import yaml

from ..astro.constants import DAY_S
from ..astro.timescale import et_to_iso
from ..catalog import catalog
from ..comms.bundle import EID_SHIP, NORMAL
from ..config import DATA

THINK_S = 30.0                 # MODEL: decision cadence, universe seconds
HOUR = 3600.0

# MODEL: skills 0..1
SKILLS = {
    "reyes":     {"comms": 0.9, "decisions": 0.9, "antenna": 0.7, "power": 0.5, "thermal": 0.5, "life_support": 0.6, "payload": 0.4, "medical": 0.3},
    "okafor":    {"comms": 0.5, "decisions": 0.6, "antenna": 0.8, "power": 0.95, "thermal": 0.9, "life_support": 0.9, "payload": 0.5, "medical": 0.3},
    "lindqvist": {"comms": 0.5, "decisions": 0.5, "antenna": 0.5, "power": 0.4, "thermal": 0.4, "life_support": 0.4, "payload": 0.95, "medical": 0.4},
    "rao":       {"comms": 0.5, "decisions": 0.6, "antenna": 0.3, "power": 0.3, "thermal": 0.3, "life_support": 0.5, "payload": 0.3, "medical": 0.95},
}
# fault -> (ship action, skill, base minutes, urgency 0..1, plain name). MODEL durations.
REPAIRS = {
    "scrubber_valve_stuck": ("scrubber_swap_bed", "life_support", 10.0, 0.45, "the scrubber valve"),
    "pump_a_degradation":   ("pump_switch", "thermal", 4.0, 0.25, "the coolant pump"),
    "hga_drift":            ("hga_lock", "antenna", 6.0, 0.20, "the antenna drift"),
    "heater_stuck_on":      ("heater_relay_open", "power", 8.0, 0.15, "the stuck heater relay"),
    "bus_short":            ("breaker_reset", "power", 5.0, 0.15, "the bus short"),
    "sensor_stuck_ppco2":   ("sensor_recal", "life_support", 5.0, 0.10, "the frozen CO2 sensor"),
    "sensor_drift_loop_b":  ("sensor_recal", "thermal", 5.0, 0.10, "the drifting loop B sensor"),
}
SYMPTOMS = {   # alarm channels that make a fault visible aboard
    "pump_a_degradation": {"loop_a_c", "loop_b_c"}, "hga_drift": {"link_db"}, "heater_stuck_on": {"cabin_c"},
    "scrubber_valve_stuck": {"ppco2"}, "bus_short": set(), "sensor_stuck_ppco2": set(), "sensor_drift_loop_b": {"loop_b_c"},
}
FAULT_NAMES = {**{k: v[4] for k, v in REPAIRS.items()}, "array_string_loss": "the solar array string"}
ORDER_NAMES = {"hga_lock": "gimbal brake command", "pump_switch": "loop switch command", "science": "science power command",
               "heaters_eco": "heater eco command", "sensor_recal": "sensor recalibration"}
CTX_KEYS = ("batt_soc", "ppco2", "loop_a_c", "loop_b_c", "link_db", "dose_rate", "cabin_c")
ASK_TASKS = {"rest": "Rest", "repair": "Fix what the alarm shows", "science": "Run the science payload",
             "talk": "Report to Lantern", "medical": "Check on the crew"}

# MODEL physiology, per hour
FATIGUE_AWAKE, FATIGUE_WORK, FATIGUE_SLEEP = 0.055, 0.03, -0.12
STRESS_FLOOR, STRESS_RECOVERY = 0.10, 0.15
STRESS_WARNING, STRESS_EMERGENCY, STRESS_STORM, STRESS_BLACKOUT = 0.08, 0.25, 0.15, 0.01
MORALE_SILENCE_PER_DAY = 0.02
# MODEL trust deltas: (commander, everyone else)
TRUST = {"helpful": (0.06, 0.03), "neutral": (0.01, 0.005), "stale": (-0.06, -0.03), "contradicts": (-0.10, -0.05),
         "query": (-0.03, -0.015), "denied": (-0.05, -0.02), "approved": (0.04, 0.02)}


def _clamp(x: float) -> float:
    return 0.0 if x < 0 else 1.0 if x > 1 else x


class _Safe(dict):
    def __missing__(self, k):
        if k.endswith("_cap") and k[:-4] in self:
            v = str(self[k[:-4]])
            return v[:1].upper() + v[1:]
        return "{" + k + "}"


_SENTENCE_START = re.compile(r"(^|[.!?]\s+)\{(\w+)\}")


@dataclass
class Task:
    kind: str                      # repair | shelter_enter | shelter_exit | sleep | science | talk | order | medical | rest
    label: str
    started_et: float
    duration_s: float | None       # None: open-ended (sleep)
    action: str | None = None
    args: dict = field(default_factory=dict)
    fault: str | None = None
    order_id: str | None = None
    assigned_by: str | None = None
    utility: float = 0.0

    def progress(self, et: float) -> float:
        if not self.duration_s:
            return 0.0
        return _clamp((et - self.started_et) / self.duration_s)

    def public(self, et: float) -> dict:
        eta = None if not self.duration_s else max(0.0, self.started_et + self.duration_s - et)
        return {"kind": self.kind, "label": self.label, "progress": round(self.progress(et), 3), "eta_s": eta,
                "assigned_by": self.assigned_by}


@dataclass
class CrewMember:
    id: str
    name: str
    role: str
    seat: str | None
    want: str = ""
    flaw: str = ""
    fatigue: float = 0.2
    stress: float = 0.15
    trust_ground: float = 0.6
    morale: float = 0.7
    skills: dict = field(default_factory=dict)
    task: Task | None = None
    said: dict | None = None
    asleep: bool = False

    @property
    def first(self) -> str:
        n = self.name.replace("Dr. ", "")
        return n.split()[0]


class Crew:
    def __init__(self, u):
        self.u = u
        self.members: dict[str, CrewMember] = {}
        for c in catalog().crew["crew"]:
            self.members[c["id"]] = CrewMember(id=c["id"], name=c["name"], role=c["role"], seat=c.get("seat"),
                                               want=c.get("want", ""), flaw=c.get("flaw", ""),
                                               skills=dict(SKILLS.get(c["id"], {})))
        path = DATA / "story" / "barks.yaml"
        self.barks = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {"barks": {}}
        self.cool_wall = float(self.barks.get("cooldown_wall_s", 6))
        self.cool_trigger = float(self.barks.get("cooldown_trigger_s", 1800))
        self.log: list[dict] = []
        self._pending_log: list[dict] = []
        self._next_think = -1e18
        self._sig = None
        self._bark_n: dict[tuple, int] = {}
        self._bark_last_wall: dict[str, float] = {}
        self._bark_last_et: dict[tuple, float] = {}
        self._known: set[str] = set()
        self._flags_seen: set[str] = set()
        self._breaks_seen = 0
        self._letters_played = 0
        self._letters_seen = 0
        self._blackout_day = -1
        self._was_shelter = False
        self._last_earth_et: float | None = None
        self._talked_scene = None
        self._tired_warned: set[str] = set()
        self._sci_events = 0
        self._sci_on = None
        self._stale_frame = False      # after a time skip, wait for a fresh frame before reacting
        self._tired_scene: dict[str, str] = {}

    # ------------------------------------------------------------ helpers
    @property
    def enabled(self) -> bool:
        return bool(getattr(self.u.cfg, "crew_ai", True))

    def ai_driven(self, m: CrewMember) -> bool:
        return m.seat is None or self.u.seat_is_ai(m.seat)

    def by_role(self, role: str) -> CrewMember | None:
        return next((m for m in self.members.values() if m.role == role), None)

    @property
    def cmdr(self) -> CrewMember:
        return self.by_role("Commander") or next(iter(self.members.values()))

    @property
    def eng(self) -> CrewMember:
        return self.by_role("Flight Engineer") or self.cmdr

    @property
    def sci(self) -> CrewMember:
        return self.by_role("Science Officer") or self.cmdr

    @property
    def doc(self) -> CrewMember:
        return self.by_role("Flight Surgeon") or self.cmdr

    def pick(self, *prefs: CrewMember) -> CrewMember:
        """The first AI-driven crew member among `prefs`, else the surgeon (always AI)."""
        return next((m for m in prefs if self.ai_driven(m)), self.doc)

    def repairer_for(self, fault: str) -> CrewMember | None:
        """Who the AI may send to fix it. TERN wakes the Commander for the scrubber;
        everything else is the Flight Engineer's. Never a seat a human holds."""
        m = self.cmdr if fault == "scrubber_valve_stuck" else self.eng
        return m if self.ai_driven(m) else None

    def urgent_faults(self) -> set[str]:
        return {f for f in self._known if f in REPAIRS}

    def _frame(self) -> dict:
        return self.u.frame or {}

    def _vars(self, **extra) -> _Safe:
        u, f = self.u, self._frame()
        lt = u.lt("down") if u.ephem.ship is not None else 0.0
        v = {"lt_min": f"{lt / 60:.0f}" if lt >= 60 else f"{lt:.0f} seconds",
             "dose": f"{f.get('dose_rate', 0):.0f}", "ppco2": f"{f.get('ppco2', 0):.1f}",
             "batt": f"{f.get('batt_soc', 0):.0f}"}
        sci = getattr(u, "science", None)
        if sci is not None:
            v["rec_pct"] = f"{100 * sci.stored_mbit() / 160_000.0:.0f}"
        v.update({k: (f"{x:.0f}" if isinstance(x, float) else x) for k, x in extra.items()})
        return _Safe(v)

    # ------------------------------------------------------------ barks
    def say(self, m: CrewMember | None, trigger: str, force: bool = False, **extra) -> str | None:
        """Pick a line deterministically (rotating, with cooldowns) and emit it."""
        if m is None or not self.enabled or not self.ai_driven(m):
            return None                 # a person plays this one: we never put words in their mouth
        lines = (self.barks.get("barks", {}).get(m.id) or {}).get(trigger) or []
        if not lines:
            return None
        u = self.u
        key = (m.id, trigger)
        if not force:
            if u.wall - self._bark_last_wall.get(m.id, -1e18) < self.cool_wall:
                return None
            if u.et - self._bark_last_et.get(key, -1e18) < self.cool_trigger:
                return None
        n = self._bark_n.get(key, 0)
        self._bark_n[key] = n + 1
        line = _SENTENCE_START.sub(lambda mm: mm.group(1) + "{" + mm.group(2) + "_cap}", lines[n % len(lines)])
        text = string.Formatter().vformat(line, (), self._vars(**extra)).strip()
        self._bark_last_wall[m.id] = u.wall
        self._bark_last_et[key] = u.et
        m.said = {"text": text, "et": u.et, "trigger": trigger}
        u.note("ship", "crew_say", f"{m.first}: {text}", who=m.id)
        u.emit(("ship", "spectator"), "crew_say", who=m.id, name=m.name, role=m.role, text=text, trigger=trigger,
               ai=self.ai_driven(m))
        self._log("said", m, text, trigger=trigger)
        return text

    def _log(self, kind: str, m: CrewMember | None, text: str, **extra) -> dict:
        e = {"et": self.u.et, "iso": et_to_iso(self.u.et), "kind": kind, "who": m.id if m else "crew",
             "name": m.name if m else "Crew", "text": text, **extra}
        self.log.append(e)
        del self.log[:-200]
        self._pending_log.append(e)
        return e

    def flush_log(self) -> None:
        """Downlink what the crew said and decided since the last frame."""
        u = self.u
        self._stale_frame = False      # called from every new telemetry frame
        if not self._pending_log or u.net is None:
            return
        entries, self._pending_log = self._pending_log[-20:], []
        u.downlink("crew_log", {"label": "CREW LOG", "entries": entries}, NORMAL)

    # ------------------------------------------------------------ the loop
    def tick(self, dt: float) -> None:
        u = self.u
        if u.phase != "mission":
            return
        self._physiology(dt)
        if not self.enabled or self._stale_frame:
            return
        self._notice()
        for m in self.members.values():
            if m.task is not None and m.task.duration_s is not None and m.task.progress(u.et) >= 1.0:
                self._complete(m)
        sig = self._signature()
        if u.et >= self._next_think or sig != self._sig:
            self._sig = sig
            self._next_think = u.et + THINK_S
            self._think()

    def _signature(self):
        u = self.u
        return (tuple(sorted(a["channel"] + a["level"] for a in (u.alarms or []))),
                tuple(sorted(f for f in u.flags if f in ("crew_called", "shelter_alarm_done", "warning_arrived",
                                                         "flare_ship_seen", "protons_ship", "storm_peak"))),
                tuple(sorted(self._known)), u.shelter)

    def _physiology(self, dt: float) -> None:
        u = self.u
        h = dt / HOUR
        f = self._frame()
        # A weak radio link is Lantern's problem, not a threat to the crew's lives:
        # only life-support, power, thermal and radiation alarms raise stress.
        levels = {a["level"] for a in (u.alarms or []) if a.get("channel") != "link_db"}
        storm = f.get("dose_rate", 0.0) > 300
        blackout = u.in_blackout_window()
        last = u.shipk.get("last_from_earth_et")
        silence_d = (u.et - last) / DAY_S if last is not None else 0.0
        for m in self.members.values():
            if self.ai_driven(m) or not self.enabled:
                if m.asleep:
                    m.fatigue += FATIGUE_SLEEP * h
                else:
                    work = m.task is not None and m.task.kind not in ("sleep", "rest")
                    m.fatigue += (FATIGUE_AWAKE + (FATIGUE_WORK if work else 0.0)) * (1 + 0.5 * m.stress) * h
            else:                              # a human plays this one: we don't know when they sleep
                m.fatigue += (0.35 - m.fatigue) * min(1.0, h / 12.0)
            up = (STRESS_EMERGENCY if "emergency" in levels else STRESS_WARNING if "warning" in levels else 0.0) \
                + (STRESS_STORM if storm else 0.0) + (STRESS_BLACKOUT if blackout else 0.0)
            down = STRESS_RECOVERY * (0.5 + m.morale) * (m.stress - STRESS_FLOOR)
            m.stress += (up - down) * h
            if silence_d > 1.0:
                m.morale -= MORALE_SILENCE_PER_DAY * dt / DAY_S
            m.fatigue, m.stress, m.morale = _clamp(m.fatigue), _clamp(m.stress), _clamp(m.morale)

    # ------------------------------------------------------------ noticing
    def known_faults(self) -> set[str]:
        """Faults the crew can see: an alarm on the symptom, a TERN call, or an
        anomaly-detector alert. The scrubber valve only after TERN wakes them."""
        u = self.u
        S = u.ship_sys
        chans = {a["channel"] for a in (u.alarms or [])}
        anomaly = bool(u.anomaly_state.get("alert"))
        known = set()
        for f in S.faults:
            if f == "scrubber_valve_stuck":
                if "crew_called" in u.flags:
                    known.add(f)
            elif f in REPAIRS and (chans & SYMPTOMS.get(f, set()) or anomaly):
                known.add(f)
            elif f == "array_string_loss" and ("load_shed_science" in u.autonomy.active or "batt_soc" in chans):
                known.add(f)
        if "hga_drift" in S.faults and "hga_repoint" in {d.rule for d in u.autonomy.log[-6:]}:
            known.add("hga_drift")
        return known

    def _notice(self) -> None:
        """Turn changes in the ship into crew reactions (barks, morale)."""
        u = self.u
        known = self.known_faults()
        for f in sorted(known - self._known):
            name = FAULT_NAMES.get(f, f.replace("_", " "))
            if f == "array_string_loss":
                self.say(self.pick(self.eng, self.sci), "fault_seen", fault=name)
            elif self.repairer_for(f) is None:      # a person holds that job: suggest, never act
                who = self.cmdr if f == "scrubber_valve_stuck" else self.eng
                self.say(self.pick(self.eng, self.cmdr, self.sci) if who is self.cmdr else self.pick(self.cmdr, self.sci),
                         "suggest_repair", force=True, fault=name, who=who.first)
            else:
                self.say(self.repairer_for(f), "fault_seen", fault=name)
        self._known = known
        if "crew_called" in u.flags and "crew_called" not in self._flags_seen:
            c = self.cmdr
            c.asleep = False
            if c.task and c.task.kind == "sleep":
                c.task = None
            c.stress = _clamp(c.stress + 0.2)
        if "protons_ship" in u.flags and "protons_ship" not in self._flags_seen:
            self.say(self.doc, "storm_onset_in" if u.shelter else "storm_onset", force=True)
        self._flags_seen |= set(u.flags)
        if u.shelter != self._was_shelter:
            self._was_shelter = u.shelter
            if u.shelter:
                for m in self.members.values():
                    m.asleep = False
                    if m.task and m.task.kind in ("sleep", "science", "rest"):
                        m.task = None
                self.say(self.doc, "shelter_enter", force=True)
            else:
                self.say(self.doc, "shelter_exit", force=True,
                         dose=u.rad.rate_usv_h(u.et, False, u.storm_attitude))   # what RAD sees outside
        breaks = sum(1 for d in u.autonomy.log if d.outcome == "broke_policy")
        if breaks > self._breaks_seen:
            self._breaks_seen = breaks
            for m in self.members.values():
                m.stress = _clamp(m.stress + 0.1)
            self.say(self.pick(self.eng, self.cmdr), "tern_break", force=True)
        # contact from home: delivered and played letters, texts from Lantern
        live = [L for L in u.letters if not L.get("archived")]
        if len(u.letters) > self._letters_seen:
            for m in self.members.values():
                m.morale = _clamp(m.morale + 0.01 * (len(u.letters) - self._letters_seen))
            self._letters_seen = len(u.letters)
        played = sum(1 for L in live if L["played"])
        if played > self._letters_played:
            n = played - self._letters_played
            self._letters_played = played
            for m in self.members.values():
                m.morale = _clamp(m.morale + (0.03 if m is self.cmdr else 0.015) * n)
                m.stress = max(STRESS_FLOOR, m.stress - 0.05 * n)   # a voice from home takes the edge off
            self.say(self.pick(self.cmdr, self.doc), "letter_home")
        last = u.shipk.get("last_from_earth_et")
        if last is not None and last != self._last_earth_et:
            self._last_earth_et = last
            for m in self.members.values():
                m.morale = _clamp(m.morale + 0.01)
                m.stress = max(min(m.stress, STRESS_FLOOR), m.stress - 0.01)
        # the silence
        if u.in_blackout_window():
            day = int((u.et - u.anchors["blackout_start"]) // DAY_S)
            if self._blackout_day < 0:
                self._blackout_day = 0
                self.say(self.pick(self.cmdr, self.eng), "blackout_start", force=True)
            elif day > self._blackout_day:
                self._blackout_day = day
                order = [m for m in (self.cmdr, self.eng, self.sci, self.doc) if self.ai_driven(m)]
                self.say(order[day % len(order)], "blackout_day", force=True, day=day + 1)
        sc = u.director.scene["id"] if u.director.scene else ""
        for m in self.members.values():
            if m.fatigue > 0.8 and self._tired_scene.get(m.id) != sc:
                self._tired_scene[m.id] = sc
                self.say(m, "fatigue_high")
        sci = getattr(u, "science", None)
        if sci is not None and len(sci.events) != self._sci_events:
            new = sci.events[self._sci_events:]
            ms = [e for e in new if e["kind"] == "milestone"]
            if ms:                       # a time skip can cross several at once: say the last one
                self.say(self.sci, "science_milestone", force=True, gbit=ms[-1].get("gbit", 0), pct=ms[-1].get("pct", 0))
            for e in new:
                if e["kind"] == "overwrite":
                    self.say(self.sci, "recorder_full")
                elif e["kind"] == "priority":
                    self.say(self.sci, "science_priority_" + e.get("value", "crew_first"), force=True)
            self._sci_events = len(sci.events)

    # ------------------------------------------------------------ deciding
    def _threshold(self, m: CrewMember) -> float:
        """Low trust in the ground: act sooner on your own judgment."""
        return 0.40 + 0.25 * m.trust_ground

    def _ground_can_weigh_in(self) -> bool:
        u = self.u
        L = u.link
        return bool(L and L.uplink_open and L.downlink_open and not u.in_blackout_window() and u.lt("down") < 600)

    def _assigned_faults(self) -> set[str]:
        return {m.task.fault for m in self.members.values() if m.task and m.task.fault}

    def _repair_duration(self, m: CrewMember, f: str) -> float:
        base = REPAIRS[f][2] * 60.0
        return base / (0.5 + m.skills.get(REPAIRS[f][1], 0.3)) * (1.0 + m.fatigue)

    def candidates(self, m: CrewMember) -> list[tuple[float, Task, str]]:
        """(utility, task, reason) for every action this crew member could take now."""
        u = self.u
        S, f = u.ship_sys, self._frame()
        et = u.et
        out: list[tuple[float, Task, str]] = []
        storm = bool({"shelter_alarm_done", "warning_arrived", "flare_ship_seen", "protons_ship"} & u.flags) \
            and "storm_peak" not in u.flags or f.get("dose_rate", 0) > 1000
        urgent = bool(self.urgent_faults()) or storm
        # repairs: the scrubber is the Commander's (TERN wakes her), the rest the Flight Engineer's
        if not u.shelter:
            taken = self._assigned_faults()
            for flt in sorted(self.urgent_faults()):
                if flt in taken or self.repairer_for(flt) is not m:
                    continue
                if flt == "pump_a_degradation" and S.active_loop != "A":
                    continue
                act, skill, _, urg, name = REPAIRS[flt]
                score = 0.55 + urg + 0.2 * m.skills.get(skill, 0.3) - 0.2 * m.fatigue
                if flt != "scrubber_valve_stuck" and self._ground_can_weigh_in():
                    score -= 0.25 * m.trust_ground       # high trust: give Lantern a chance first
                dur = self._repair_duration(m, flt)
                out.append((score, Task("repair", f"Fixing {name}", et, dur, act, {}, flt), f"{name} needs fixing"))
        # the storm shelter: the Commander moves the crew, on the surgeon's advice
        if m is self.cmdr:
            if storm and not u.shelter:
                out.append((0.95, Task("shelter_enter", "Moving everyone into the shelter", et, 0.0, "shelter_enter"),
                            "a proton storm is coming"))
            outside = u.rad.rate_usv_h(et, False, u.storm_attitude)     # what RAD sees outside the water wall
            if u.shelter and "storm_peak" in u.flags and outside < 150:
                out.append((0.7, Task("shelter_exit", "Leaving the shelter", et, 0.0, "shelter_exit"),
                            "Arjun says the dose rate is down"))
            if self._talk_due():
                out.append((0.6, Task("talk", "Reporting to Lantern", et, 180.0), "time for a status report"))
        # science: the Science Officer
        if m is self.sci and not S.science_on and not u.shelter:
            ok = f.get("batt_soc", 0) > 70 and "load_shed_science" not in u.autonomy.active \
                and "array_string_loss" not in S.faults and not storm
            if ok:
                out.append((0.6, Task("science", "Powering up the payload", et, 300.0, "science", {"on": True}),
                            "the battery can carry the payload"))
        # sleep: everyone, when tired and nothing is on fire
        thr = 0.8 if m is self.sci else 0.7       # Sofia trades sleep for one more observation
        if m.fatigue > thr and not urgent and not m.asleep:
            out.append((0.5 + (m.fatigue - thr), Task("sleep", "Sleeping", et, None), "tired"))
        if m is self.doc:
            tired = [x for x in self.members.values() if x.fatigue > 0.85 and x is not m]
            if tired:
                out.append((0.65, Task("medical", f"Checking on {tired[0].first}", et, 600.0), "someone is exhausted"))
        return out

    def _talk_due(self) -> bool:
        u = self.u
        d = u.director
        sc = d.scene["id"] if d.scene else None
        if sc is None or sc == self._talked_scene or u.in_blackout_window():
            return False
        return u.et - d.start_et > 900 and u.lt("down") > 30

    def _think(self) -> None:
        u = self.u
        for m in self.members.values():
            if not self.ai_driven(m):
                continue
            if m.asleep:
                wake = m.fatigue < 0.15 or any(self.repairer_for(x) is m for x in self.urgent_faults()) or u.shelter
                if not wake:
                    continue
                m.asleep, m.task = False, None
            opts = sorted(self.candidates(m), key=lambda x: -x[0])
            if m.task is not None:
                # keep working unless something clearly more urgent comes up (MODEL: +0.25)
                if not opts or opts[0][0] < m.task.utility + 0.25 or opts[0][1].kind == m.task.kind:
                    continue
                self._log("interrupted", m, f"Dropped '{m.task.label}' for '{opts[0][1].label}'.")
                m.task = None
            if opts and opts[0][0] >= self._threshold(m):
                opts[0][1].utility = opts[0][0]
                self._start(m, opts[0][1])

    def _start(self, m: CrewMember, t: Task) -> None:
        u = self.u
        t.started_et = u.et
        m.task = t
        if t.kind == "sleep":
            m.asleep = True
            return
        if t.kind == "repair":
            self.say(m, "repair_start", force=True, fault=FAULT_NAMES.get(t.fault, ""), minutes=t.duration_s / 60)
        if t.kind == "talk":
            self._talked_scene = u.director.scene["id"] if u.director.scene else None
        if not t.duration_s:
            self._complete(m)

    def _complete(self, m: CrewMember) -> None:
        u = self.u
        t, m.task = m.task, None
        if t is None:
            return
        role = f"crew:{m.id}"
        if t.kind == "repair":
            if t.fault not in u.ship_sys.faults:
                return                      # somebody else fixed it first
            r = u.ship_action(role, t.action, t.args)
            if r.get("ok"):
                self._log("action", m, f"{t.label[0].lower() + t.label[1:]}: {r.get('text', '')}", action=t.action)
                self.say(m, "repair_done", force=True, fault=FAULT_NAMES.get(t.fault, ""))
        elif t.kind in ("shelter_enter", "shelter_exit"):
            r = u.ship_action(role, t.action)
            self._log("action", m, r.get("text", t.label), action=t.action)
            if t.kind == "shelter_enter":
                self.say(m, "shelter_enter", force=True)
        elif t.kind == "science":
            if not u.ship_sys.science_on:
                u.ship_action(role, "science", {"on": True})
                self._log("action", m, "Science payload powered on.", action="science")
                self.say(m, "science_on", force=True)
        elif t.kind == "talk":
            self._status_report(m)
        elif t.kind == "order":
            cmd = u.shipk["commands"].get(t.order_id)
            if cmd:
                r = u.ship_action(role, "execute_command", {"id": t.order_id})
                self._log("order_done", m, f"Carried out Lantern's {ORDER_NAMES.get(cmd.get('command'), 'command')}: "
                                           f"{r.get('text', '')}", order=cmd.get("command"))
        elif t.kind == "medical":
            for x in self.members.values():
                if x.fatigue > 0.85 and x is not m and self.ai_driven(x) and not x.asleep:
                    x.task, x.asleep = Task("sleep", "Sleeping (doctor's orders)", u.et, None), True
                    self.say(m, "med_advice", force=True, who=x.first, fatigue=x.fatigue * 100)
                    break

    def _status_report(self, m: CrewMember) -> None:
        u, f = self.u, self._frame()
        asleep = sum(1 for x in self.members.values() if x.asleep)
        rest = f"{asleep} asleep, the rest on shift" if asleep else "all four on shift"
        text = (f"Lantern, Meridian. Crew status: {rest}. ppCO2 {f.get('ppco2', 0):.1f} mmHg, "
                f"battery {f.get('batt_soc', 0):.0f} percent. Over.")
        u.downlink("text", {"text": text, "from": f"{m.first} (crew)", "label": "CREW STATUS"}, NORMAL)
        self._log("report", m, text)

    # ------------------------------------------------------------ the human Commander's "Ask" menu
    def assign(self, role: str, crew_id: str, task: str) -> dict:
        u = self.u
        m = self.members.get(crew_id)
        if u.phase != "mission":
            return {"ok": False, "text": "The mission has not launched."}
        if m is None or task not in ASK_TASKS:
            return {"ok": False, "text": "No such crew member or task."}
        if not self.enabled:
            return {"ok": False, "text": "Crew AI is switched off (LL_CREW_AI=0)."}
        if not self.ai_driven(m):
            return {"ok": False, "text": f"{m.first} is played by a person. Ask them yourself."}
        opts = sorted(self.candidates(m), key=lambda x: -x[0])
        best = opts[0] if opts else None
        want = None
        et = u.et
        if task == "rest":
            want = (0.3 + m.fatigue, Task("sleep", "Resting (asked by the Commander)", et, None, assigned_by=role))
        elif task == "repair":
            rep = [o for o in opts if o[1].kind == "repair"]
            if not rep:
                return self._decline(m, "No alarm is showing anything I can fix.")
            want = rep[0][:2]
        elif task == "science":
            if m is not self.sci:
                return self._decline(m, "Sofia runs the payload.")
            if u.ship_sys.science_on:
                return self._decline(m, "The payload is already on.")
            f = self._frame()
            if f.get("batt_soc", 0) < 50 or "load_shed_science" in u.autonomy.active:
                return self._decline(m, f"Battery is at {f.get('batt_soc', 0):.0f} percent and TERN shed the payload for a reason.")
            want = (0.6, Task("science", "Powering up the payload", et, 300.0, "science", {"on": True}))
        elif task == "talk":
            want = (0.5, Task("talk", "Reporting to Lantern", et, 180.0))
        elif task == "medical":
            if m is not self.doc:
                return self._decline(m, "Arjun is the doctor.")
            want = (0.5, Task("medical", "Checking on the crew", et, 600.0))
        score, t = want
        t.assigned_by = role
        bonus = 0.15                                    # MODEL: deference to the Commander
        if best is not None and best[1].kind != t.kind and best[0] > score + bonus:
            return self._decline(m, f"I'm needed elsewhere: {best[2]}.")
        m.asleep = False
        self._start(m, t)
        self._log("assigned", m, f"Took the Commander's request: {t.label}.", task=task)
        return {"ok": True, "accepted": True, "text": f"{m.first}: copy. {t.label}."}

    def _decline(self, m: CrewMember, reason: str) -> dict:
        self.say(m, "ask_decline", force=True, reason=reason)
        self._log("declined", m, reason)
        return {"ok": True, "accepted": False, "text": f"{m.first}: {reason}"}

    # ------------------------------------------------------------ stale orders
    def order_context(self) -> dict:
        """What Lantern was looking at when it sent the order (delayed telemetry)."""
        u = self.u
        c = u.ctrl
        fr = c.get("frame") or {}
        return {"sample_et": c.get("sample_et"), "sent_et": u.et,
                "age_s": (u.et - c["sample_et"]) if c.get("sample_et") is not None else None,
                "telemetry": {k: fr.get(k) for k in CTX_KEYS if k in fr}, "modes": dict(c.get("modes") or {})}

    def judge(self, cmd: str | None, ctx: dict) -> tuple[str, str]:
        """-> (verdict, reason). verdict: ok | stale | contradicts | query."""
        u = self.u
        S, f = u.ship_sys, self._frame()
        was_m, was_t = ctx.get("modes") or {}, ctx.get("telemetry") or {}
        if cmd == "hga_lock":
            if "hga_drift" not in S.faults and S.hga_err < 0.12:
                return "stale", f"The antenna drift is already fixed; pointing error is {S.hga_err:.2f} degrees."
            return "ok", "The antenna is still drifting."
        if cmd == "pump_switch":
            was, now = was_m.get("active_loop"), S.active_loop
            if was and was != now:
                return "contradicts", f"We already switched to loop {now}. Switching again puts us back on loop {was}."
            hot = f.get("loop_a_c" if now == "A" else "loop_b_c", 0.0)
            other = f.get("loop_b_c" if now == "A" else "loop_a_c", 0.0)
            if hot < 8.0 and not (now == "A" and "pump_a_degradation" in S.faults):
                return "stale", f"Loop {now} is running at {hot:.1f} C. Nothing to fix."
            if other > 18.0:
                return "query", f"The other loop is at {other:.1f} C too. Switching may not help."
            return "ok", f"Loop {now} is still running hot at {hot:.1f} C."
        if cmd in ("science", "heaters_eco"):
            key = "science_on" if cmd == "science" else "heaters_eco"
            was, now = was_m.get(key), bool(getattr(S, key))
            if was is not None and bool(was) != now:
                return "contradicts", (f"That assumed it was {'on' if was else 'off'}. It is already "
                                       f"{'on' if now else 'off'}; toggling would undo it.")
            if cmd == "science" and not now:
                b0, b1 = was_t.get("batt_soc"), f.get("batt_soc", 0.0)
                if b1 < 40 or (b0 is not None and b0 - b1 > 15):
                    return "query", f"Battery is at {b1:.0f} percent, lower than the {b0 or 0:.0f} percent you saw."
            return "ok", "Still makes sense."
        b0, b1 = was_t.get("batt_soc"), f.get("batt_soc")
        if b0 is not None and b1 is not None and b0 - b1 > 20:
            return "query", f"Battery has dropped from {b0:.0f} to {b1:.0f} percent since you sent this."
        return "ok", "Still makes sense."

    def on_order(self, b, item: dict) -> str:
        """Called when an Earth command arrives aboard. Returns 'execute' to let
        the autonomy policy carry it out, or 'held' / 'manual'."""
        u = self.u
        if not self.enabled:
            return "execute"
        P = b.payload
        cmd = P.get("command")
        ctx = P.get("context") or {}
        verdict, reason = self.judge(cmd, ctx)
        age = u.et - (ctx.get("sample_et") if ctx.get("sample_et") is not None else b.created_et)
        lt = b.true_lt_s or 0.0
        c = self.cmdr
        cmdr_ai = self.ai_driven(c)
        speaker = c if cmdr_ai else next((m for m in (self.eng, self.sci, self.doc) if self.ai_driven(m)), self.doc)
        order = ORDER_NAMES.get(cmd, "command")
        item["crew_judgment"] = {"verdict": verdict, "reason": reason, "age_s": age}
        ages = u._fmt(age)
        if verdict in ("stale", "contradicts", "query"):
            self._trust(verdict)
            self._log("order_" + ("query" if verdict == "query" else "held"), speaker,
                      f"Held Lantern's {order}: {reason}", order=cmd, verdict=verdict, age_s=age, lt_s=lt, bundle=b.id)
            if verdict == "query":
                self.say(speaker, "order_query", force=True, order=order, reason=reason, age=ages)
                u.downlink("text", {"text": f"Lantern, Meridian. Holding your {order}. {reason} Please confirm. Over.",
                                    "from": f"{c.first} (crew)", "label": "CREW QUERY"}, NORMAL)
            else:
                self.say(speaker, "order_held", force=True, order=order, reason=reason, age=ages)
            if cmdr_ai:
                u.shipk["commands"].pop(b.id, None)      # the AI Commander shelves it
            return "held"
        helpful = (cmd == "hga_lock" and "hga_drift" in u.ship_sys.faults) or \
            (cmd == "pump_switch" and "pump_a_degradation" in u.ship_sys.faults and u.ship_sys.active_loop == "A")
        self._trust("helpful" if helpful else "neutral")
        self._log("order_ok", speaker, f"Lantern's {order} checks out: {reason}", order=cmd, verdict="ok", age_s=age,
                  lt_s=lt, bundle=b.id)
        self.say(speaker, "order_ok", force=True, order=order, age=ages)
        if u.autonomy.rule("execute_uplink")["permission"] == "auto":
            return "execute"
        if cmdr_ai:
            doer = self.eng if (self.ai_driven(self.eng) and cmd in ("pump_switch", "hga_lock", "sensor_recal")) else c
            doer.asleep = False
            doer.task = Task("order", f"Carrying out Lantern's {order}", u.et, 120.0 * (1 + doer.fatigue),
                             order_id=b.id, assigned_by="lantern")
        return "manual"

    def _trust(self, what: str) -> None:
        a, b = TRUST[what]
        for m in self.members.values():
            m.trust_ground = _clamp(m.trust_ground + (a if m is self.cmdr else b))

    def on_delivered(self, b) -> None:
        """Hook for every delivered bundle (both directions)."""
        u = self.u
        P = b.payload
        if b.dest == EID_SHIP:
            if b.kind == "policy_answer" and self.enabled:
                self._trust("approved" if P.get("approve") else "denied")
            return
        if b.kind == "crew_log" and b.state == "delivered":
            got = [{**e, "received_et": u.et} for e in P.get("entries", [])]
            u.ctrl.setdefault("crew_log", []).extend(got)
            del u.ctrl["crew_log"][:-120]
            u.emit(("control", "spectator"), "crew_log", entries=got)

    # ------------------------------------------------------------ time skips
    def coast(self, t0: float, t1: float) -> None:
        """Weeks pass between scenes: tasks finish, people sleep, stress settles.
        A skip that spans the blackout costs morale (MODEL)."""
        u = self.u
        days = max(0.0, (t1 - t0) / DAY_S)
        if days <= 0:
            return
        a, b = u.anchors.get("blackout_start"), u.anchors.get("blackout_end")
        silent = max(0.0, min(t1, b) - max(t0, a)) / DAY_S if a is not None else 0.0
        for m in self.members.values():
            m.task, m.asleep = None, False
            m.fatigue = 0.3
            m.stress = STRESS_FLOOR + (m.stress - STRESS_FLOOR) * 0.2
            m.morale = _clamp(m.morale + (0.7 - m.morale) * min(1.0, (days - silent) / 30.0)
                              - MORALE_SILENCE_PER_DAY * silent)
        self._known = set()
        self._next_think = -1e18
        self._stale_frame = True
        self._was_shelter = False      # the time skip always ends outside the shelter

    # ------------------------------------------------------------ views
    def summary(self) -> dict:
        """Compact crew state for the telemetry frame (what Lantern will see)."""
        u = self.u
        return {m.id: {"task": (m.task.label if m.task else ("Asleep" if m.asleep else "Off duty")),
                       "f": round(m.fatigue, 2), "s": round(m.stress, 2), "t": round(m.trust_ground, 2),
                       "m": round(m.morale, 2), "ai": self.ai_driven(m)} for m in self.members.values()} | \
            {"_et": u.et}

    def _member_public(self, m: CrewMember) -> dict:
        u = self.u
        d = {k: v for k, v in asdict(m).items() if k not in ("task", "skills")}
        d.update(first=m.first, ai=self.ai_driven(m), human=not self.ai_driven(m),
                 task=m.task.public(u.et) if m.task else None, skills=m.skills,
                 ask=[{"id": k, "label": v} for k, v in ASK_TASKS.items()
                      if k in ("rest", "talk", "repair") and m in (self.cmdr, self.eng) or
                      k in ("rest", "science") and m is self.sci or k in ("rest", "medical") and m is self.doc])
        for k in ("fatigue", "stress", "trust_ground", "morale"):
            d[k] = round(d[k], 3)
        return d

    def public(self, role: str) -> dict:
        from ..catalog import side_of
        side = side_of(role)
        u = self.u
        if side == "ship":
            return {"enabled": self.enabled, "members": [self._member_public(m) for m in self.members.values()],
                    "log": self.log[-30:], "morale_scored": False}
        asof = {"summary": u.ctrl.get("crew"), "sample_et": u.ctrl.get("crew_sample_et"),
                "age_s": (u.et - u.ctrl["crew_sample_et"]) if u.ctrl.get("crew_sample_et") is not None else None}
        names = {m.id: {"name": m.name, "first": m.first, "role": m.role} for m in self.members.values()}
        out = {"enabled": self.enabled, "names": names, "as_of": asof, "log": (u.ctrl.get("crew_log") or [])[-30:],
               "morale_scored": False}
        if side in ("spectator", "observer"):
            out["members"] = [self._member_public(m) for m in self.members.values()]
        return out

    def debrief(self) -> dict:
        return {"scored": False, "note": "Morale is shown, never scored.",
                "members": {m.id: {"name": m.name, "trust_ground": round(m.trust_ground, 2), "morale": round(m.morale, 2),
                                   "fatigue": round(m.fatigue, 2), "stress": round(m.stress, 2)} for m in self.members.values()},
                "judgments": [e for e in self.log if e["kind"].startswith("order_")]}
