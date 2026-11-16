"""The Universe: the single authoritative simulation both players connect to.

Responsibilities
* the universe clock (ET) and its rate (1 / DELAY_SCALE during play),
* an event queue: nothing happens out of causal order - every arrival is
  scheduled at transmit_time + light_time,
* ship physics, radiation, space weather, comms, autonomy, anomaly model,
* two *separate* knowledge stores: what Lantern knows (only what has
  arrived) and what Meridian knows (local truth plus what has arrived),
* per-role views for the clients.

The Director (director.py) drives scenes and story beats on top of this.
"""
from __future__ import annotations

import heapq
import itertools
import logging
import math
from pathlib import Path
from collections import deque
from datetime import timedelta
from zoneinfo import ZoneInfo

import numpy as np
import yaml

from ..ai.anomaly_runtime import AnomalyRuntime
from ..astro.conjunction import BLACKOUT_DEG, MORATORIUM_DEG, find_windows
from ..astro.constants import AU_KM, DAY_S, R_EARTH_KM, R_MARS_KM
from ..astro.ephemeris import EphemerisService
from ..astro.porkchop import MassModel, porkchop, transfer
from ..astro.timescale import et_to_iso, et_to_utc, utc_to_et
from ..astro.trajectory import ShipTrajectory
from ..catalog import SEATS, catalog, side_of
from ..comms.bundle import BULK, EID_CONTROL, EID_HOME, EID_SHIP, EXPEDITED, NORMAL
from ..comms.contact_plan import ContactPlan
from ..comms.network import Network, make_bundle
from ..config import DATA, Config
from . import design as bay
from . import review as board
from .autonomy import AutonomyEngine
from .director import Director
from .radiation import Radiation
from .space_weather import SpaceWeather
from .subsystems import CHANNELS, LABELS, LIMITS, UNITS, Env, ShipSystems

log = logging.getLogger("lightlag.universe")

ROLES = SEATS + ("spectator", "observer")
MANUAL_ACTIONS = {"scrubber_swap_bed", "breaker_reset", "heater_relay_open", "sensor_recal"}


class Universe:
    def __init__(self, cfg: Config, runtime_dir: Path | None = None):
        self.cfg = cfg
        # Files this universe writes (the ship SPK) live in its room's folder, so two
        # rooms never write the same file (Phase 5).
        self.runtime_dir = Path(runtime_dir) if runtime_dir else DATA / "runtime" / "rooms" / "MAIN"
        self.scenario = yaml.safe_load((DATA / "scenarios" / f"{cfg.scenario}.yaml").read_text())
        self.story = yaml.safe_load((DATA / "story" / "beats.yaml").read_text())
        self.home_tz = ZoneInfo(self.scenario.get("home_tz", "America/Chicago"))
        self.ephem = EphemerisService(cfg.kernel_dir, cfg.ephemeris).load()
        self.sw = SpaceWeather(DATA, cfg.cache_dir)
        d = self.scenario["design"]
        self.catalog = catalog()
        C = self.catalog
        crew_n = len(C.crew["crew"])
        mm = {"crew": crew_n, "habitat_dry_t": float(C.value("habitat", "dry_mass_t")),
              "shelter_area_m2": float(C.value("habitat", "shelter_area_m2")),
              "contingency_days": float(C.value("contingency_days")),
              "consumables_kg_cm_day": C.consumables_kg_per_cm_day(), **d.get("mass_model", {})}
        self.mass = MassModel(**mm)
        self.design = {
            "launch_et": utc_to_et(d["default_launch"]), "arrival_et": utc_to_et(d["default_arrival"]),
            "comms_plan": d["comms_plan"], "shelter_gcm2": float(d["shelter_gcm2"]),
            "hull_liner_gcm2": float(d["hull_liner_gcm2"]), "solar_monitor": bool(d["solar_monitor"]),
            "objective": d["objective"], "payload": list(d["payload"]), "vehicle": d["vehicle"],
            "array": d["array"], "battery": d["battery"],
        }
        self._contact = (None, None)           # (arrival_et, contact info) for the data budget
        self._last_bay: dict | None = None     # last evaluation, for the ripple list
        self.design_changes: list[str] = []
        # Four seats. A seat with no human connected is played by AI.
        self.seats: dict[str, int] = {s: 0 for s in SEATS}
        self.votes: dict[str, str | None] = {s: None for s in SEATS}
        self.phase = "design"
        self.et = utc_to_et(d["launch_window"][0])
        self.rate = 0.0
        self.base_rate = cfg.time_rate
        self.wall = 0.0
        self._heap: list = []
        self._seq = itertools.count()
        self.flags: set[str] = set()
        self.ship_sys = ShipSystems(crew=crew_n, cabin_m3=float(C.value("habitat", "net_habitable_m3")),
                                    co2_kg_cm_day=float(C.crew["consumables_per_cm_day"]["co2_produced"]["value"]))
        self.rad = Radiation()
        self.autonomy = AutonomyEngine()
        self.anomaly = AnomalyRuntime(cfg.anomaly_model)
        self.anomaly_state: dict = {"score": 0.0, "alert": False, "detector": self.anomaly.kind}
        self.cp: ContactPlan | None = None
        self.net: Network | None = None
        self.link = None
        self.anchors: dict[str, float] = {}
        self.windows: dict = {}
        self.storm = None
        self.storm_note = ""
        self.shelter = False
        self.storm_attitude = False
        self.ctrl = {"frame": None, "sample_et": None, "modes": None, "dose": None, "anomaly": None,
                     "shelter": None, "attitude": None, "tern_log": [], "requests": {}, "alarms": [],
                     "space_weather": [], "last_from_ship_et": None, "tele_count": 0}
        self.shipk = {"bulletins": [], "commands": {}, "last_from_earth_et": None, "policy_version": 1}
        self.inbox = {"control": deque(maxlen=300), "ship": deque(maxlen=300)}
        self.letters: list[dict] = []
        self.letter_order: list[str] = []
        self.home_received: list[dict] = []
        self.timeline: deque = deque(maxlen=4000)
        self.out: list[dict] = []
        self.pings = 0
        self.frame: dict | None = None
        self.alarms: list = []
        self._tele_next = 0.0
        self._dose_hist: deque = deque(maxlen=40)
        self._soc_hist: deque = deque(maxlen=40)
        self._co2_hist: deque = deque(maxlen=40)
        self._rtt_cache = (-1e18, 0.0)
        self._porkchop = None
        self._passes_cache = (-1e18, [])
        self.last_instruction: str | None = None
        self.autonomy_draft: list[dict] | None = None
        self.dsn_now: dict | None = None
        self.director = Director(self)
        from . import crew as _crew, science as _science   # Phase 3: crew AI and science data in flight
        self.science, self.crew = _science.ScienceLedger(self), _crew.Crew(self)
        # Phase 4: the review board, the frozen design record, callouts and the flight history
        from .callouts import Callouts
        from .history import History
        self.review: dict | None = None          # the review in progress (phase "review")
        self.review_last: dict | None = None     # the last finished review (a failure stays visible in the bay)
        self.reviews: list[dict] = []            # every verdict, for the debrief and the fact sheet
        self.liens: list[dict] = []
        self.scrub: dict | None = None
        self.design_record: dict | None = None
        self.callouts = Callouts(self)
        self.history = History(self)
        # Phase 6: the TMI burn after a review pass, and "Meanwhile, in reality" lines
        from .reality import Reality
        self.burn: dict | None = None
        self.reality = Reality(self)

    # ======================================================== utilities
    def schedule(self, et: float, kind: str, data: dict | None = None) -> None:
        heapq.heappush(self._heap, (et, next(self._seq), kind, data or {}))

    def emit(self, audience: tuple | str, kind: str, **data) -> None:
        aud = (audience,) if isinstance(audience, str) else tuple(audience)
        self.out.append({"audience": aud, "event": {"kind": kind, "et": self.et, "iso": et_to_iso(self.et), **data}})

    def note(self, side: str, kind: str, text: str, **extra) -> None:
        self.timeline.append({"et": self.et, "iso": et_to_iso(self.et), "side": side, "kind": kind, "text": text, **extra})

    def drain(self) -> list[dict]:
        out, self.out = self.out, []
        return out

    def local_str(self, et: float) -> str:
        return et_to_utc(et).astimezone(self.home_tz).strftime("%a %b %d, %I:%M %p %Z").replace(" 0", " ")

    def lt(self, direction: str = "up") -> float:
        if self.ephem.ship is None:
            return 0.0
        return self.ephem.light_time("EARTH", "SHIP", self.et) if direction == "up" else \
            self.ephem.light_time("SHIP", "EARTH", self.et)

    def in_blackout_window(self, et: float | None = None) -> bool:
        et = self.et if et is None else et
        a, b = self.anchors.get("blackout_start"), self.anchors.get("blackout_end")
        return a is not None and a <= et <= b

    # ======================================================== design
    def porkchop_data(self) -> dict:
        if self._porkchop is None:
            d = self.scenario["design"]
            n1, n2 = d.get("grid", [40, 40])
            self._porkchop = porkchop(self.ephem, d["launch_window"][0], d["launch_window"][1],
                                      d["arrival_window"][0], d["arrival_window"][1], n1, n2)
        # The feasibility line is what the chosen rocket can lift within the launch
        # limit and the cost cap, not a fixed 160 t, so it follows the design.
        ev = self.design_eval() if self.design else {}
        self._porkchop["budget_t"] = ev.get("campaign", {}).get("max_imleo_t", self.mass.imleo_budget_t)
        return self._porkchop

    def design_eval(self) -> dict:
        cell = transfer(self.ephem, self.design["launch_et"], self.design["arrival_et"])
        out = {k: (list(v) if isinstance(v, list) else v) for k, v in self.design.items()}
        out["launch_iso"], out["arrival_iso"] = et_to_iso(self.design["launch_et"]), et_to_iso(self.design["arrival_et"])
        out["votes"] = dict(self.votes)
        out["seats"] = self.seats_public()
        out["changes"] = list(self.design_changes)
        if cell is None:
            out.update(valid=False, reasons=["Launch date: no transfer for these dates"], reason="no transfer for these dates")
            return out
        out.update(bay.evaluate(self.design, cell, self.catalog, self.mass, self._contact_info()))
        out.update(reason="; ".join(out["reasons"]), tof_days=round(cell.tof_days, 1),
                   c3=round(cell.c3, 2), vinf_arr=round(cell.vinf_arr, 2), dv_tmi=round(cell.dv_tmi, 3),
                   dv_moi=round(cell.dv_moi, 3), dv_total=round(cell.dv_total, 3),
                   gcr_factor=round(max(0.75, 1 - 0.012 * self.design["hull_liner_gcm2"]), 3),
                   sep_factor_shelter=round(math.exp(-(self.design["hull_liner_gcm2"] + self.design["shelter_gcm2"]) / 12), 3))
        return out

    def _contact_info(self) -> dict:
        """Downlink rates over the stay depend only on the arrival date: cache them."""
        a = self.design["arrival_et"]
        if self._contact[0] != a:
            self._contact = (a, bay.contact_info(self.ephem, a, self.mass.stay_days))
        return self._contact[1]

    DESIGN_OWNERS = {"control": {"launch_et", "arrival_et", "comms_plan", "vehicle"},
                     "ship": {"shelter_gcm2", "hull_liner_gcm2", "solar_monitor"},
                     "scientist": {"objective", "payload"}, "engineer": {"array", "battery"}}
    # design keys whose value is a catalog id, and the catalog list it must come from
    CATALOG_KEYS = {"objective": "objectives", "vehicle": "vehicles", "array": "solar_arrays", "battery": "batteries"}

    def _clean(self, key: str, value):
        """A design value checked against the catalog, or None to ignore it."""
        if key in self.CATALOG_KEYS:
            return value if self.catalog.get(self.CATALOG_KEYS[key], value) else None
        if key == "payload":
            if not isinstance(value, (list, tuple)):
                return None
            return list(dict.fromkeys(i for i in value if isinstance(i, str) and self.catalog.get("instruments", i)))
        if key == "comms_plan":
            return value if value in ("lean", "standard", "continuous") else None
        if key == "solar_monitor":
            return bool(value)
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def set_design(self, role: str, changes: dict) -> dict:
        if self.phase != "design":
            return self.design_eval()
        owned = self.DESIGN_OWNERS
        everything = set().union(*owned.values())
        allowed = everything if role == "spectator" else set(owned.get(role, set()))
        # An Earth seat may make the calls of an AI-held Earth seat, and the same aboard,
        # so a solo player is never locked out of a decision.
        for s in SEATS:
            if s != role and self.seat_is_ai(s) and side_of(s) == side_of(role):
                allowed |= owned[s]
        before = self._last_bay or self.design_eval()
        for k, v in changes.items():
            v = self._clean(k, v) if k in allowed else None
            if v is not None:
                self.design[k] = v
        self.votes = {s: None for s in SEATS}
        new = self.design_eval()
        self.design_changes = bay.ripple(before, new)
        new["changes"] = list(self.design_changes)
        self._last_bay = new
        return new

    def apply_design(self) -> None:
        """Fly what was designed: the chosen battery and array become the ship's."""
        self.ship_sys.batt_kwh = float(self.catalog.get("batteries", self.design["battery"])["kwh"])
        arr = self.catalog.get("solar_arrays", self.design["array"])
        self.ship_sys.array_kw_1au = float(arr["kw_at_1au"])
        self.ship_sys.string_loss_frac = float(arr.get("string_loss_frac", 0.18))

    # ------------------------------------------------------------ seats
    def seat_is_ai(self, seat: str) -> bool:
        return self.seats.get(seat, 0) <= 0

    def seat_join(self, role: str, delta: int) -> None:
        if role in self.seats:
            self.seats[role] = max(0, self.seats[role] + delta)
            self.emit(("control", "ship", "spectator"), "seats", seats=self.seats_public())

    def seats_public(self) -> dict:
        out = {}
        for s in SEATS:
            info = self.catalog.crew["seats"][s]
            member = self.catalog.crew_member(s)
            out[s] = {"title": info["title"], "side": info["side"], "ai": self.seat_is_ai(s),
                      "name": member["name"] if member else info["title"], "vote": self.votes.get(s)}
        return out

    def vote(self, role: str, vote: str) -> dict:
        if self.phase != "design":
            return self.design_eval()
        ev = self.design_eval()
        if vote == "GO" and not ev.get("valid"):
            self.emit(role, "design_nogo", reason=ev.get("reason"), reasons=ev.get("reasons", []))
            return ev
        if role in self.votes:
            self.votes[role] = vote
        self.emit(("control", "ship", "spectator"), "vote", role=role, vote=vote)
        # AI seats follow the humans: once every human-held seat (and the voter) says GO,
        # the AI seats say GO too. Any NO-GO holds everyone.
        if vote == "GO" and all(self.votes[s] == "GO" for s in SEATS if not self.seat_is_ai(s)):
            for s in SEATS:
                if self.seat_is_ai(s) and self.votes[s] != "GO":
                    self.votes[s] = "GO"
                    self.emit(("control", "ship", "spectator"), "vote", role=s, vote="GO", ai=True)
        if all(v == "GO" for v in self.votes.values()):
            if self.cfg.review:
                self.start_review()
            else:
                self.launch()
        return self.design_eval()

    # ======================================================== review
    def start_review(self) -> None:
        """Every seat said go: the Flight Readiness Review takes the design."""
        ev = self.design_eval()
        res = board.run(self, ev)
        t = board.timing(self.cfg.judge_mode)
        self.review = {**res, "n": len(self.reviews) + 1, "started_wall": self.wall, "timing": t}
        self.phase = "review"
        self.emit(("control", "ship", "spectator", "observer"), "review", review=self.review_public())

    def review_public(self) -> dict | None:
        r = self.review or self.review_last
        if r is None:
            return None
        out = {k: v for k, v in r.items() if k != "started_wall"}
        out["elapsed_s"] = round(self.wall - r["started_wall"], 2) if self.review else None
        out["active"] = self.review is not None
        return out

    # ======================================================== TMI burn (Phase 6)
    def start_burn(self) -> None:
        """The review passed: record the moment, and every client in this room plays the
        end of the trans-Mars injection burn before the first scene card."""
        from . import launch_fx
        self.burn = {**launch_fx.build(self, self.design_eval()), "started_wall": self.wall, "n": self.review["n"]}
        self.timeline.append({"et": self.design["launch_et"], "iso": et_to_iso(self.design["launch_et"]), "side": "both",
                              "kind": "tmi_burn", "text": f"Trans-Mars injection: {self.burn['dv_kms']:.2f} km/s (MODEL, impulsive)."})
        self.emit(("control", "ship", "spectator", "observer"), "tmi_burn", burn=self.burn_public())

    def burn_public(self) -> dict | None:
        b = self.burn
        if b is None:
            return None
        out = {k: v for k, v in b.items() if k != "started_wall"}
        out["elapsed_s"] = round(self.wall - b["started_wall"], 2)
        out["active"] = self.phase == "review" and out["elapsed_s"] < b["timing"]["total_s"]
        return out

    def end_review(self) -> None:
        r, self.review = self.review, None
        self.review_last = r
        self.reviews.append({k: r[k] for k in ("n", "verdict", "verdict_text", "liens", "fails", "launch_iso")})
        if r["verdict"] != "fail":
            self.liens = [dict(L) for L in r["liens"]]
            self.emit(("control", "ship", "spectator", "observer"), "review_done", verdict=r["verdict"], liens=self.liens)
            self.launch()
            return
        # A failed review costs launch-window days: the design goes back for rework,
        # and every seat votes again.
        self.votes = {s: None for s in SEATS}
        B = board.board()
        days = float(B["slip"]["days"])
        end = utc_to_et(self.scenario["design"]["launch_window"][1])
        old = self.design["launch_et"]
        if old + days * DAY_S > end:
            self.phase = "scrubbed"
            self.scrub = {"text": B["scrub"]["text"], "synodic_days": B["scrub"]["synodic_days"],
                          "source": B["scrub"]["source"], "reviews": len(self.reviews),
                          "window_end_iso": et_to_iso(end), "next_window_days": B["scrub"]["synodic_days"]}
            self.note("both", "scrub", "Mission scrubbed: the launch window closed after a failed review.")
            self.emit(("control", "ship", "spectator", "observer"), "scrubbed", scrub=self.scrub)
            return
        before = self.design_eval()
        self.design["launch_et"] = old + days * DAY_S
        after = self.design_eval()
        self.design_changes = [f"review failed: launch slipped {days:.0f} days to {et_to_iso(self.design['launch_et'])[:10]}"] \
            + bay.ripple(before, after)
        after["changes"] = list(self.design_changes)
        self._last_bay = after
        self.phase = "design"
        self.director._design_wall = 0.0
        self.note("both", "review", f"Review {r['n']} failed: " + "; ".join(f["text"] for f in r["fails"]))
        self.emit(("control", "ship", "spectator", "observer"), "review_done", verdict="fail", fails=r["fails"],
                  slip_days=days, launch_iso=et_to_iso(self.design["launch_et"]))

    # ======================================================== launch
    def launch(self) -> None:
        d = self.design
        traj = ShipTrajectory.from_dates(self.ephem, d["launch_et"], d["arrival_et"])
        self.ephem.ship = traj  # for analytic window search
        a = d["arrival_et"]
        blk = find_windows(self.ephem, a, a + 420 * DAY_S, BLACKOUT_DEG, target="MARS")
        if not blk:
            raise RuntimeError("no conjunction found after arrival")
        end = blk[0].end_et + float(self.scenario.get("mission_end_after_blackout_days", 20)) * DAY_S
        self.ephem.attach_ship(traj, end + 2 * DAY_S, self.runtime_dir)
        blk = find_windows(self.ephem, a, end, BLACKOUT_DEG)
        mor = find_windows(self.ephem, a, end, MORATORIUM_DEG)
        self.windows = {"blackout": blk[0].as_dict(), "moratorium": mor[0].as_dict()}
        self.anchors = {"launch": d["launch_et"], "arrival": a, "mission_end": end,
                        "moratorium_start": mor[0].start_et, "moratorium_end": mor[0].end_et,
                        "blackout_start": blk[0].start_et, "blackout_end": blk[0].end_et}
        self.cp = ContactPlan(self.ephem, d["comms_plan"])
        self.net = Network(self.ephem, self.cp, self.schedule)
        self.rad = Radiation(hull_liner_gcm2=d["hull_liner_gcm2"], shelter_gcm2=d["shelter_gcm2"])
        st = self.scenario.get("storm") or {}
        ev = self.sw.find(st.get("event", "")) if st else None
        if ev is not None:
            self.storm = self.sw.timeline(self.ephem, ev, st.get("at_utc"))
            self.storm_note = st.get("label", "")
            self.rad.storms = [self.storm]
            self.anchors["storm"] = self.storm.t_flare_earth
            self.anchors["storm_peak"] = self.storm.t_peak_ship
            self.schedule(self.storm.t_flare_earth, "flare_earth")
            self.schedule(self.storm.t_photons_ship, "flare_ship")
            self.schedule(self.storm.t_onset_earth, "protons_earth")
            self.schedule(self.storm.t_onset_ship, "protons_ship")
            self.schedule(self.storm.t_peak_ship, "storm_peak")
            if self.storm.t_cme_ship:
                self.schedule(self.storm.t_cme_ship, "cme_ship")
        else:
            self.storm_note = "No DONKI cache and no reference event found: storm beat skipped."
            self.anchors["storm"] = utc_to_et(st.get("at_utc", "2020-11-29T15:35:00")) if st else a - 80 * DAY_S
        self.phase = "mission"
        self.et = d["launch_et"]
        self.reality.start()               # Phase 6: the team's own window decides which real events fire
        self.note("both", "launch", f"Meridian-1 launched {et_to_iso(d['launch_et'])}; arrival {et_to_iso(a)}.")
        self.emit(("control", "ship", "spectator", "observer"), "launched", anchors=self.anchors, windows=self.windows)
        self.apply_design()
        self.design_record = self._freeze_design()       # Phase 4: everything downstream reads this
        self.history.sample("launch")
        self.director.start()

    def _freeze_design(self) -> dict:
        """The design as flown, frozen at launch. Callouts, the debrief and the fact
        sheet read this record, never the live `self.design`."""
        import copy
        ev = copy.deepcopy(self.design_eval())
        C = self.catalog
        arr, bat = C.get("solar_arrays", self.design["array"]), C.get("batteries", self.design["battery"])
        names = {"vehicle": ev["campaign"]["name"], "array": arr["name"], "battery": bat["name"],
                 "objective": C.get("objectives", self.design["objective"])["name"],
                 "payload": [C.get("instruments", i)["name"] for i in self.design["payload"]], "plan": self.design["comms_plan"]}
        mass = {x["id"]: x["t"] for x in ev["mass"]["lines"]}
        rv = self.review_last if self.cfg.review else None
        return {"choices": copy.deepcopy(self.design), "names": names, "eval": ev,
                "budgets": {k: {kk: b[kk] for kk in ("label", "value", "limit", "unit", "margin", "band")} for k, b in ev["budgets"].items()},
                "liens": copy.deepcopy(self.liens),
                "review": ({k: rv[k] for k in ("verdict", "verdict_text", "areas", "liens")} if rv else None),
                "reviews": copy.deepcopy(self.reviews),
                "policy": copy.deepcopy(self.autonomy.policy), "seats": self.seats_public(),
                "derived": {"gcr_factor": ev["gcr_factor"], "sep_factor_shelter": ev["sep_factor_shelter"],
                            "batt_kwh": float(bat["kwh"]), "array_kw_1au": float(arr["kw_at_1au"]),
                            "string_loss_frac": float(arr.get("string_loss_frac", 0.18)),
                            "shelter_t": mass.get("shelter", 0.0), "liner_t": mass.get("liner", 0.0),
                            "power": dict(ev["power"]), "data_by_plan": copy.deepcopy(ev["data"]["by_plan"]),
                            "demand_mbit_day": ev["data"]["demand_mbit_day"], "dark_days": ev["data"]["dark_days"],
                            "plan_hours": ev["data"]["hours"], "payload": list(self.design["payload"])},
                "frozen_et": self.et}

    # ======================================================== clock
    def tick(self, wall_dt: float) -> None:
        self.wall += wall_dt
        self.director.wall_tick(wall_dt)
        if self.phase == "review" and self.review and self.wall - self.review["started_wall"] >= self.review["timing"]["total_s"]:
            # Phase 6: a pass first plays the trans-Mars injection burn (wall time only,
            # the universe clock stays stopped), then launches.
            if self.review["verdict"] != "fail" and self.cfg.launch_fx:
                if self.burn is None:
                    self.start_burn()
                elif self.wall - self.burn["started_wall"] >= self.burn["timing"]["total_s"]:
                    self.end_review()
                return
            self.end_review()
            return
        if self.phase != "mission" or self.rate <= 0:
            return
        target = self.et + wall_dt * self.rate
        max_sub = max(2.0, (target - self.et) / 12.0)
        guard = 0
        while self.et < target - 1e-9 and guard < 600:
            guard += 1
            step = min(max_sub, target - self.et)
            if self._heap and self._heap[0][0] < self.et + step:
                step = max(0.0, self._heap[0][0] - self.et)
            if step > 0:
                self._physics(step)
                self.et += step
            self._process_due()
            if self.director.check():
                break
        if self.phase == "mission":
            self.reality.check()           # Phase 6: at most one real-world line per scene

    def coast_to(self, target_et: float) -> None:
        """Skip time between scenes. Physics still holds: queued bundles go
        out as soon as the link physically allows (never through the Sun,
        never commands during a moratorium), every arrival waits out its
        light time, and cosmic-ray dose accumulates analytically."""
        if target_et <= self.et:
            return
        from ..comms.network import COMMAND_KINDS
        self.science.before_coast()        # Phase 3: chunks not yet sent go back on the recorder
        coast_t0 = self.et
        while True:
            self._coast_flush()
            if not self._heap or self._heap[0][0] > target_et:
                break
            et, _, kind, data = heapq.heappop(self._heap)
            if et > self.et:
                self._coast_dose(self.et, et)
                self.science.coast(self.et, et)        # Phase 3: analytic science between events
                self.et = et
            self._handle(kind, data)
        if target_et > self.et:
            self._coast_dose(self.et, target_et)
            self.science.coast(self.et, target_et)
        self.crew.coast(coast_t0, target_et)       # Phase 3: tasks end, people sleep, the silence costs morale
        # Routine passes during the skip kept Lantern's telemetry current - except
        # through the blackout, where the last frame is the one that left before it.
        last_link = target_et
        if self.in_blackout_window(target_et):
            last_link = self.anchors["blackout_start"]
        S = self.ship_sys
        S.faults.clear()
        S.stuck_values.clear()
        S.hga_err, S.pump_a_health, S.ppco2, S.bed_sat = 0.05, 1.0, 2.4, 0.1
        S.batt_soc = max(S.batt_soc, 80.0)
        self.storm_attitude = False
        self.autonomy.release("storm_attitude", target_et)   # so the rule can fire for the next storm
        self.shelter = False          # the crew leaves the shelter once a storm has passed
        self.anomaly.buf.clear()
        self.et = target_et
        self._tele_next = target_et
        self._rtt_cache = (-1e18, 0.0)
        lt_dn = self.ephem.light_time("SHIP", "EARTH", last_link)
        sample = last_link - lt_dn - 300.0
        if self.ctrl["sample_et"] is None or sample > self.ctrl["sample_et"]:
            # The frame describes the ship at the sample time, so its link margin
            # must come from that geometry, not the stale pre-skip environment.
            self.cp.hga_error_deg = S.hga_err
            r_au = float(np.linalg.norm(self.ephem.pos("SHIP", sample))) / AU_KM
            S._env = Env(r_au=r_au, link_margin_db=self.cp.state(sample).margin_db,
                         dose_rate_usv_h=self.rad.rate_usv_h(sample, False, False), in_shelter=False)
            f = self.ship_sys.frame()
            self.ctrl.update(frame=f, sample_et=sample, modes=self.ship_sys.modes(), dose=self.rad.public(),
                             shelter=self.shelter, attitude=self.storm_attitude, anomaly=self.anomaly_state)
            self.ctrl["last_from_ship_et"] = sample
            self.ctrl.update(science=self.science.summary(), science_sample_et=sample,    # Phase 3
                             crew=self.crew.summary(), crew_sample_et=sample)

    def _coast_dose(self, t0: float, t1: float) -> None:
        self.rad.coast((t1 - t0) / DAY_S)
        self.rad.coast_storms(t0, t1, self.shelter, self.storm_attitude)

    def _coast_flush(self) -> None:
        from ..comms.network import COMMAND_KINDS
        if self.in_blackout_window():
            return
        moratorium = self.ephem.sep_angle_deg(self.et) < MORATORIUM_DEG
        for node, store in self.net.stores.items():
            keep = []
            for b in store.items():
                if b.kind == "telemetry":
                    continue
                if node == EID_CONTROL and moratorium and b.kind in COMMAND_KINDS:
                    keep.append(b)
                    continue
                self.net._transmit(b, self.et, "up" if node == EID_CONTROL else "down")
            store._heap = []
            for b in keep:
                store.put(b)

    # ======================================================== physics
    def _physics(self, dt: float) -> None:
        self.cp.hga_error_deg = 2.0 if self.storm_attitude else self.ship_sys.hga_err
        self.link = self.cp.state(self.et)
        r_au = float(np.linalg.norm(self.ephem.pos("SHIP", self.et))) / AU_KM
        dose_rate = self.rad.rate_usv_h(self.et, self.shelter, self.storm_attitude)
        env = Env(r_au=r_au, link_margin_db=self.link.margin_db, dose_rate_usv_h=dose_rate, in_shelter=self.shelter)
        self.ship_sys.step(dt, env)
        self.rad.accumulate(self.et, dt, self.shelter, self.storm_attitude)
        self.science.tick(dt)          # Phase 3: instruments fill the recorder; chunks go to the network
        self.crew.tick(dt)             # Phase 3: the crew acts on its own
        sent = self.net.tick(self.et, dt, self.link)
        for b in sent:
            if b.kind not in ("telemetry",):
                self.emit(("control", "ship", "spectator"), "bundle_tx", bundle=b.public(include_payload=False))
        if self.et >= self._tele_next:
            self._on_frame(self.ship_sys.frame())
            self._tele_next = self.et + max(10.0, self.rate * 0.5)

    def _on_frame(self, frame: dict) -> None:
        self.frame = frame
        self.alarms = self.ship_sys.alarms(frame)
        self._dose_hist.append((self.et, frame["dose_rate"]))
        self._soc_hist.append((self.et, frame["batt_soc"]))
        self._co2_hist.append((self.et, frame["ppco2"]))
        cadence = max(10.0, self.rate * 0.5)
        if cadence <= 30.0:          # the detector was trained on 10 s frames; pause it during time warp
            a = self.anomaly.update(frame)
        else:
            a = {**self.anomaly_state, "rising": False, "paused": True, "detector": self.anomaly.kind + " (paused: time warp)"}
        self.anomaly_state = a
        if a["rising"] and self.et - getattr(self, "_last_anom_alert", -1e18) > 1800:
            self._last_anom_alert = self.et
            chans = ", ".join(LABELS[c] for c in a["top"])
            self.tern_say(f"Anomaly detector: telemetry doesn't look like nominal. Largest deviations: {chans}.",
                          level="caution", source="anomaly")
            self.downlink("alert", {"label": "ANOMALY", "text": f"Onboard anomaly detector alert ({a['detector']}); "
                                    f"top channels: {chans}", "anomaly": a}, EXPEDITED)
        self._autonomy(frame)
        self.director.on_frame(frame)
        self.callouts.on_frame()       # Phase 4: geometry-driven design callouts
        self.history.on_frame()
        self.crew.flush_log()          # Phase 3: what the crew said and decided goes down too
        self.net.submit(make_bundle(EID_SHIP, EID_CONTROL, "telemetry", self.et, {
            "frame": frame, "sample_et": self.et, "modes": self.ship_sys.modes(), "dose": self.rad.public(),
            "shelter": self.shelter, "attitude": self.storm_attitude, "anomaly": a, "label": "TLM",
            "science": self.science.summary(), "crew": self.crew.summary()},
            lifetime_s=6 * 3600, size_bytes=900))

    # ======================================================== autonomy
    def round_trip(self) -> float:
        t, v = self._rtt_cache
        if abs(self.et - t) < 120:
            return v
        lt_d, lt_u = self.lt("down"), self.lt("up")
        think = 120.0
        if self.in_blackout_window():
            wait = self.anchors["blackout_end"] - self.et
        elif self.link and self.link.downlink_open:
            wait = 0.0
        else:
            nxt = self.cp.next_open(self.et, "down", horizon_s=DAY_S, step_s=600)
            wait = (nxt - self.et) if nxt else DAY_S
        v = wait + lt_d + think + lt_u
        self._rtt_cache = (self.et, v)
        return v

    @staticmethod
    def _slope(hist) -> float:
        if len(hist) < 4:
            return 0.0
        t0, v0 = hist[0]
        t1, v1 = hist[-1]
        return (v1 - v0) / max(t1 - t0, 1.0)

    def _time_until(self, hist, current: float, limit: float, rising: bool) -> float:
        s = self._slope(hist)
        if rising and s > 1e-9:
            return max(0.0, (limit - current) / s)
        if not rising and s < -1e-9:
            return max(0.0, (current - limit) / -s)
        return float("inf")

    def _autonomy(self, f: dict) -> None:
        A = self.autonomy
        rtt = self.round_trip()
        snap = {k: f[k] for k in ("ppco2", "batt_soc", "dose_rate", "link_db", "loop_a_c", "loop_b_c")}
        sep_now = self.rad.components_usv_h(self.et, self.shelter, self.storm_attitude)[1]

        def act(rid: str, trigger: str, tth: float, safety: bool):
            d = A.decide(self.et, rid, trigger, tth, rtt, snap, safety)
            if d:
                self._on_decision(d)

        p = {r["id"]: r["params"] for r in A.policy}
        if f["dose_rate"] > p["shelter_alert"]["dose_rate_usv_h"] or "flare_ship_seen" in self.flags or \
                "warning_arrived" in self.flags:
            if "shelter_alarm_done" not in self.flags:
                act("shelter_alert", "flare or protons detected", self._time_until(self._dose_hist, f["dose_rate"], 5000, True), True)
        if sep_now > 300 and not self.storm_attitude:
            act("storm_attitude", f"proton dose rate {f['dose_rate']:.0f} uSv/h and rising",
                self._time_until(self._dose_hist, f["dose_rate"], 5000, True), True)
        if self.storm_attitude and sep_now < 80 and "storm_peak" in self.flags:
            self.storm_attitude = False
            A.release("storm_attitude", self.et)
            self.tern_say("Proton flux is down. Returning to Earth-point attitude; the link will come back.")
        if f["ppco2"] > p["scrubber_cycle"]["ppco2_mmhg"]:
            act("scrubber_cycle", f"ppCO2 {f['ppco2']:.1f} mmHg", self._time_until(self._co2_hist, f["ppco2"], 8.0, True), True)
        elif "scrubber_cycle" in A.active and f["ppco2"] < 3.0:
            A.release("scrubber_cycle", self.et)
        loop = f["loop_a_c"] if self.ship_sys.active_loop == "A" else f["loop_b_c"]
        if loop > p["pump_failover"]["loop_c"]:
            act("pump_failover", f"active coolant loop {loop:.1f} C", 3600.0, True)
        if f["batt_soc"] < p["load_shed_science"]["soc_pct"] and self.ship_sys.science_on:
            act("load_shed_science", f"battery {f['batt_soc']:.0f}%", self._time_until(self._soc_hist, f["batt_soc"], 12.0, False), True)
        elif "load_shed_science" in A.active and f["batt_soc"] > 70 and "array_string_loss" not in self.ship_sys.faults:
            # The battery has recovered: the rule stands down. TERN does not power the
            # payload back up on its own; that is the Science Officer's call (or Earth's).
            A.release("load_shed_science", self.et)
            self.tern_say("Battery is healthy again. Science payload may be powered back up.")
        if f["link_db"] < p["hga_repoint"]["link_db"] and not self.storm_attitude and self.ship_sys.hga_err > 0.15:
            act("hga_repoint", f"link margin {f['link_db']:.1f} dB", float("inf"), False)
        # pending requests that can no longer be answered in time
        for rid, req in list(A.pending.items()):
            cur = {"storm_attitude": (self._dose_hist, f["dose_rate"], 5000, True),
                   "load_shed_science": (self._soc_hist, f["batt_soc"], 12.0, False),
                   "scrubber_cycle": (self._co2_hist, f["ppco2"], 8.0, True)}.get(rid)
            if not cur:
                continue
            tth = self._time_until(*cur)
            remaining = max(0.0, req.et + (req.round_trip_s or rtt) - self.et)
            d = A.escalate_if_needed(self.et, rid, tth, remaining)
            if d:
                self._on_decision(d)

    def _on_decision(self, d) -> None:
        pub = d.public()
        self.note("ship", "tern_decision", f"TERN [{pub['policy_status']}] {d.rule}: {d.rationale}", decision=pub)
        self.emit(("ship", "spectator"), "tern_decision", decision=pub)
        if d.outcome == "requested":
            self.downlink("policy_request", {"label": "TERN REQUEST", "rule": d.rule, "trigger": d.trigger,
                                             "rationale": d.rationale, "decision": pub}, EXPEDITED)
            self.tern_say(f"I've asked Lantern for authorization ({d.rule.replace('_', ' ')}). "
                          f"Earliest answer in {self._fmt(d.round_trip_s)}.")
        else:
            self.downlink("tern_log", {"label": "TERN LOG", "decision": pub}, NORMAL)
        if d.action:
            self._execute_rule(d.action, d)
        if d.outcome == "held":
            self.tern_say(f"Policy forbids me to act on '{d.rule.replace('_', ' ')}'. Commander, it's your call.",
                          level="caution")
        self.callouts.on_decision(d)   # Phase 4: did a design choice decide this?

    def _execute_rule(self, rid: str, d) -> None:
        S = self.ship_sys
        if rid == "shelter_alert":
            self.flags.add("shelter_alarm_done")
            self.tern_say("Storm alarm. Solar protons expected or arriving. Recommend storm shelter now.", level="warning")
            self.director.after("warning_arrived_or_onset")
        elif rid == "storm_attitude":
            self.storm_attitude = True
            self.tern_say("Rolling to put the propellant tanks between you and the Sun. Earth link will drop until it passes.",
                          level="warning")
        elif rid == "scrubber_cycle":
            if "scrubber_valve_stuck" in S.faults:
                self.director.after("crew_called")
                self.director.slowdown("crew_called")
                self.tern_say("Commander, wake up. I commanded a scrubber bed swap and the valve did not move. "
                              "ppCO2 is climbing. It needs a manual swap. Lantern can't help us right now.", level="warning")
            else:
                S.apply("scrubber_swap_bed")
                self.tern_say("Swapped scrubber beds.")
        elif rid == "pump_failover":
            S.apply("pump_switch")
            self.tern_say(f"Coolant loop was running hot. Switched to loop {S.active_loop}.")
            self.flags.add("pump_fixed")
            self.autonomy.release(rid, self.et)
        elif rid == "load_shed_science":
            S.apply("science", {"on": False})
            self.tern_say("Battery trending low. Science payload powered down to protect life support.", level="caution")
        elif rid == "hga_repoint":
            S.apply("hga_trim")
            self.autonomy.release(rid, self.et)
            self.tern_say("Trimmed the high-gain antenna. The gimbal is still drifting; recommend engaging the brake.")
        elif rid == "execute_uplink":
            pass

    # ======================================================== events
    def _process_due(self) -> None:
        while self._heap and self._heap[0][0] <= self.et + 1e-9:
            _, _, kind, data = heapq.heappop(self._heap)
            self._handle(kind, data)

    def _handle(self, kind: str, data: dict) -> None:
        if kind == "bundle_arrival":
            b = self.net.arrive(data["id"], self.et)
            if b is not None:
                self._on_delivered(b)
        elif kind == "director":
            self.director.on_event(data)
        elif kind == "flare_earth":
            self.flags.add("flare_earth")
            ev = self.storm.event
            item = {"et": self.et, "iso": et_to_iso(self.et), "kind": "FLARE", "text": f"GOES X-ray: {ev.flare_class} flare at {ev.location}",
                    "source": ev.source, "label": self.storm_note}
            self.ctrl["space_weather"].append(item)
            self.note("control", "space_weather", item["text"])
            self.emit(("control", "spectator"), "alert", level="warning", title=f"{ev.flare_class} FLARE",
                      text=f"{item['text']}. {self.storm_note}")
            self.director.after("flare_earth")
        elif kind == "flare_ship":
            self.flags.add("flare_light_ship")
            if self.design["solar_monitor"]:
                self.flags.add("flare_ship_seen")
                self.tern_say(f"Solar monitor: X-ray flux jump, {self.storm.event.flare_class}-class. "
                              "Protons may follow. I'll watch the particle counters.", level="warning")
            self.emit("spectator", "note", text="Flare light reaches Meridian-1.")
        elif kind == "protons_earth":
            item = {"et": self.et, "iso": et_to_iso(self.et), "kind": "SEP", "text": "GOES protons: >10 MeV flux rising at Earth"}
            self.ctrl["space_weather"].append(item)
            self.emit(("control", "spectator"), "alert", level="caution", title="PROTON EVENT AT EARTH", text=item["text"])
        elif kind == "protons_ship":
            self.flags.add("protons_ship")
            self.director.after("warning_arrived_or_onset")
            self.emit("spectator", "note", text="First protons reach Meridian-1.")
        elif kind == "storm_peak":
            self.flags.add("storm_peak")
            self.director.after("storm_peak")
        elif kind == "cme_ship":
            self.tern_say("Cosmic-ray counts just dropped about ten percent. That's the CME's magnetic cloud passing us "
                          "(a Forbush decrease).")

    def _on_delivered(self, b) -> None:
        P = b.payload
        pub = b.public()
        if b.dest == EID_SHIP:
            self.shipk["last_from_earth_et"] = b.created_et
            if b.kind in ("text", "voice", "alert", "command"):
                item = {"id": b.id, "kind": b.kind, "from": P.get("from", "Lantern"), "text": P.get("text", ""),
                        "audio_id": P.get("audio_id"), "created_et": b.created_et, "delivered_et": self.et,
                        "lt_s": b.true_lt_s, "command": P.get("command")}
                self.inbox["ship"].append(item)
                if b.kind == "alert":
                    self.flags.add("warning_arrived")
                    self.director.after("warning_arrived_or_onset")
                if b.kind == "command":
                    self.shipk["commands"][b.id] = item
                    verdict = self.crew.on_order(b, item)      # Phase 3: the crew checks a stale order against now
                    if verdict == "execute" and self.autonomy.rule("execute_uplink")["permission"] == "auto" \
                            and P.get("command"):
                        self.ship_action("tern", P["command"], P.get("args") or {})
                if P.get("text"):
                    self.last_instruction = P["text"]
                self.emit(("ship", "spectator"), "message", message=item)
            elif b.kind == "letter":
                item = {"id": b.id, "from": P.get("from"), "text": P.get("text"), "created_et": b.created_et,
                        "created_local": P.get("created_local"), "delivered_et": self.et, "lt_s": b.true_lt_s,
                        "played": False, "flood": bool(P.get("flood")), "beat": P.get("beat")}
                self.letters.append(item)
                self.emit(("ship", "spectator"), "letter", letter=item)
                self.director.on_letter_delivered(item)
            elif b.kind == "policy":
                self.autonomy.update(P["rules"], "control")
                for who in P.get("signed_by", []):
                    self.autonomy.sign(who)
                self.shipk["policy_version"] = self.autonomy.version
                self.tern_say(f"New autonomy policy received from Lantern (sent {self._ago(b.created_et)} ago).")
                self.director.check_policy_signed()
            elif b.kind == "policy_answer":
                d = self.autonomy.answer(self.et, P["rule"], bool(P["approve"]))
                if d:
                    self._on_decision(d)
            elif b.kind == "ping":
                self.pings += 1
                self.emit(("ship", "spectator"), "ping", count=self.pings, lt_s=b.true_lt_s)
            elif b.kind == "status":
                self.emit("ship", "receipt", ref=P.get("ref"), status=P.get("status"))
        else:
            self.ctrl["last_from_ship_et"] = b.created_et
            if b.kind == "telemetry":
                if self.ctrl["sample_et"] is None or P["sample_et"] > self.ctrl["sample_et"]:
                    self.ctrl.update(frame=P["frame"], sample_et=P["sample_et"], modes=P["modes"], dose=P["dose"],
                                     shelter=P["shelter"], attitude=P["attitude"], anomaly=P["anomaly"])
                    self.ctrl["tele_count"] += 1
                    if "science" in P:             # Phase 3: crew and science, as of this frame
                        self.ctrl.update(science=P["science"], science_sample_et=P["sample_et"],
                                         crew=P.get("crew"), crew_sample_et=P["sample_et"])
            elif b.kind in ("text", "voice", "alert"):
                item = {"id": b.id, "kind": b.kind, "from": P.get("from", "Meridian"), "text": P.get("text", ""),
                        "audio_id": P.get("audio_id"), "created_et": b.created_et, "delivered_et": self.et,
                        "lt_s": b.true_lt_s, "label": P.get("label")}
                self.inbox["control"].append(item)
                self.emit(("control", "spectator"), "message", message=item)
            elif b.kind == "letter":
                rec = {"id": b.id, "text": P.get("text"), "created_et": b.created_et, "delivered_et": self.et,
                       "audio_id": P.get("audio_id")}
                self.home_received.append(rec)
                self.inbox["control"].append({"id": b.id, "kind": "letter", "from": "Meridian (personal)",
                                              "text": "PERSONAL - for the Reyes family. Contents private.",
                                              "created_et": b.created_et, "delivered_et": self.et, "lt_s": b.true_lt_s})
                self.emit("spectator", "letter_home", letter=rec)
                self.director.on_home_letter(rec)
            elif b.kind == "policy_request":
                self.ctrl["requests"][P["rule"]] = {**P["decision"], "received_et": self.et, "bundle": b.id}
                if self.cfg.judge_mode:
                    self.schedule(self.et + 45, "director", {"type": "cue", "cue": {
                        "side": "control", "action": "answer_request", "rule": P["rule"], "unless": None}})
                self.emit(("control", "spectator"), "policy_request", request=self.ctrl["requests"][P["rule"]])
            elif b.kind == "tern_log":
                self.ctrl["tern_log"].append({**P["decision"], "received_et": self.et})
                self.emit(("control", "spectator"), "tern_log", decision=P["decision"])
            elif b.kind == "status":
                self.emit("control", "receipt", ref=P.get("ref"), status=P.get("status"))
        self.science.on_delivered(b)       # Phase 3: science chunks, priority orders, receipts
        self.crew.on_delivered(b)          # Phase 3: crew logs reach Lantern; answers move trust
        if b.dest == EID_CONTROL:          # Phase 4: design callouts reach Lantern; science milestones
            self.callouts.on_delivered(b)
            if b.kind == "science":
                self.callouts.on_science()
        self.emit("spectator", "bundle_rx", bundle={k: pub[k] for k in ("id", "kind", "direction", "true_lt_s", "state")})

    # ======================================================== actions
    def downlink(self, kind: str, payload: dict, priority: int = NORMAL) -> None:
        self.net.submit(make_bundle(EID_SHIP, EID_CONTROL, kind, self.et, payload, priority, lifetime_s=14 * DAY_S))

    def tern_say(self, text: str, level: str = "info", source: str = "tern") -> None:
        self.note("ship", "tern", text, level=level)
        self.emit(("ship", "spectator"), "tern", text=text, level=level, source=source)

    def send(self, role: str, kind: str, text: str = "", priority: int = NORMAL, audio_id: str | None = None,
             audio_bytes: int = 0, to: str | None = None, command: str | None = None, args: dict | None = None,
             label: str | None = None, lifetime_days: float = 30.0) -> dict:
        if self.phase != "mission":
            return {"ok": False, "error": "mission not launched"}
        side = side_of(role) if role != "tern" else "ship"
        if side == "control" and to == "home":
            self.flags.add("family_answered")
            self.note("control", "family", f"Lantern to the Reyes family: {text}")
            self.emit(("control", "spectator"), "family_reply", text=text)
            self.director.objective_done("family_answered")
            return {"ok": True, "local": True}
        if side == "control":
            src, dst = EID_CONTROL, EID_SHIP
            who = "Lantern" if role == "control" else "Lantern Science"
        elif side == "ship":
            src = EID_SHIP
            dst = EID_HOME if (to == "home" or kind == "letter") else EID_CONTROL
            member = self.catalog.crew_member(role) if role in SEATS else None
            who = "TERN" if role == "tern" else (member["name"].split()[0] if member else "Meridian")
        else:
            return {"ok": False, "error": "spectators cannot transmit"}
        payload = {"text": text, "from": who, "label": label or kind.upper(), "audio_id": audio_id,
                   "audio_bytes": audio_bytes}
        if command:
            payload.update(command=command, args=args or {})
            if side == "control":          # Phase 3: what Lantern was looking at when it gave the order
                payload["context"] = self.crew.order_context()
        b = make_bundle(src, dst, kind, self.et, payload, priority, lifetime_s=lifetime_days * DAY_S,
                        report=kind in ("text", "voice", "command", "letter", "alert"))
        self.net.submit(b)
        self.note(role, "send", f"{who} -> {dst}: [{kind}] {text}", bundle=b.id)
        self.director.on_send(role, kind, text, b)
        return {"ok": True, "bundle": b.public(include_payload=False)}

    def send_policy(self, signed_by: list[str], rules: list[dict] | None = None) -> None:
        self.net.submit(make_bundle(EID_CONTROL, EID_SHIP, "policy", self.et,
                                    {"rules": rules or self.autonomy.policy, "signed_by": signed_by, "label": "POLICY"},
                                    EXPEDITED, lifetime_s=40 * DAY_S, report=True))

    def ship_action(self, role: str, action: str, args: dict | None = None) -> dict:
        args = args or {}
        if action == "shelter_enter":
            self.shelter = True
            self.note("ship", "action", "Commander entered the storm shelter.")
            self.director.objective_done("shelter")
            if "protons_ship" not in self.flags:
                self.flags.add("shelter_before_onset")
            return {"ok": True, "text": "In the storm shelter. Water walls around you."}
        if action == "shelter_exit":
            self.shelter = False
            return {"ok": True, "text": "Left the shelter."}
        if self.shelter and action in MANUAL_ACTIONS:
            if role == "bot":
                self.shelter = False
            else:
                return {"ok": False, "text": "You can't do that from inside the shelter. Leave it first."}
        if action == "execute_command":
            cmd = self.shipk["commands"].pop(args.get("id"), None)
            if cmd and cmd.get("command"):
                return self.ship_action(role, cmd["command"], {})
            return {"ok": False, "text": "No such command."}
        msg = self.ship_sys.apply(action, args)
        self.note("ship", "action", f"{role}: {action} -> {msg}")
        if action == "hga_lock":
            self.director.objective_done("hga_point")
        if action == "pump_switch":
            self.director.objective_done("pump_fixed")
        if action == "scrubber_swap_bed":
            self.director.objective_done("scrubber_fixed")
            self.autonomy.release("scrubber_cycle", self.et)
        return {"ok": True, "text": msg}

    def play_letter(self, lid: str) -> dict:
        for L in self.letters:
            if L["id"] == lid and not L["played"]:
                L["played"] = True
                self.letter_order.append(lid)
                self.note("ship", "letter", f"Played letter from {L['from']} ({L.get('created_local')})")
                self.director.on_letter_played(L)
                return {"ok": True}
        return {"ok": False}

    def answer_request(self, rule: str, approve: bool) -> dict:
        req = self.ctrl["requests"].pop(rule, None)
        if req is None:
            return {"ok": False}
        self.net.submit(make_bundle(EID_CONTROL, EID_SHIP, "policy_answer", self.et,
                                    {"rule": rule, "approve": approve, "label": "AUTH" if approve else "DENY"},
                                    EXPEDITED, lifetime_s=2 * DAY_S, report=True))
        self.note("control", "policy_answer", f"Lantern {'approved' if approve else 'denied'} {rule}")
        return {"ok": True}

    SURGE_SETUP_S = 600.0   # time for the DSN to slew and configure an emergency pass

    def dsn_surge(self, hours: float = 4.0) -> dict:
        if self.phase != "mission":
            return {"ok": False}
        start = self.et + self.SURGE_SETUP_S
        self.cp.add_surge(start, hours)
        self._passes_cache = (-1e18, [])
        self.flags.add("dsn_surge")
        self.callouts.on_surge()           # Phase 4
        self.note("control", "dsn", f"Emergency DSN pass requested: {hours:.0f} h starting {et_to_iso(start)}")
        self.emit(("control", "spectator"), "note", text=f"Emergency DSN pass booked. Antenna on point in "
                                                        f"{self.SURGE_SETUP_S / 60:.0f} min.")
        return {"ok": True, "start_et": start}

    def ping(self) -> dict:
        if self.phase != "mission":
            return {"ok": False}
        self.net.submit(make_bundle("dtn://audience", EID_SHIP, "ping", self.et, {"label": "HELLO"}, BULK,
                                    lifetime_s=DAY_S, size_bytes=64))
        return {"ok": True, "lt_s": self.lt("up")}

    # ======================================================== views
    @staticmethod
    def _fmt(s) -> str:
        from .autonomy import fmt
        return fmt(s)

    def _ago(self, et: float) -> str:
        return self._fmt(self.et - et)

    def passes(self) -> list[dict]:
        t, p = self._passes_cache
        if abs(self.et - t) > 600:
            p = self.cp.passes(self.et, 48)
            self._passes_cache = (self.et, p)
        return p

    def geometry(self) -> dict:
        e = self.ephem
        et = self.et
        lt_up, lt_dn = self.lt("up"), self.lt("down")
        pos = {b: e.ecliptic_xy_au(b, et) for b in ("EARTH", "MARS", "SHIP", "VENUS", "MERCURY")}
        rS = e.pos("SHIP", et)
        dist = e.distance_km("EARTH", "SHIP", et)
        ship_phase = e.ship.phase(et) if e.ship else "prelaunch"
        # what each side "sees": light that left the other LT ago
        apparent_earth = e.ecliptic_xy_au("EARTH", et - lt_dn)
        apparent_ship = e.ecliptic_xy_au("SHIP", et - lt_up)

        def ang(body: str, radius: float):
            v = e.pos(body, et) - rS
            d = float(np.linalg.norm(v))
            return {"dir": (v / d).round(6).tolist(), "dist_km": d, "ang_diam_deg": math.degrees(2 * math.atan(radius / d))}
        view = {"sun": ang("SUN", 695_700.0), "earth": ang("EARTH", R_EARTH_KM), "mars": ang("MARS", R_MARS_KM)}
        return {"lt_up_s": lt_up, "lt_down_s": lt_dn, "wait_up_s": lt_up / self.rate if self.rate else 0.0,
                "dist_km": dist, "dist_au": dist / AU_KM, "sep_deg": e.sep_angle_deg(et), "spe_deg": e.spe_angle_deg(et),
                "r_ship_au": float(np.linalg.norm(rS)) / AU_KM, "ship_phase": ship_phase, "pos": pos,
                "apparent_earth": apparent_earth, "apparent_ship": apparent_ship, "viewport": view,
                "mission_day": (et - self.anchors["launch"]) / DAY_S}

    def comms_view(self, role: str) -> dict:
        node = EID_CONTROL if side_of(role) == "control" else EID_SHIP
        q = self.net.queue(node)
        return {"link": self.link.public() if self.link else None, "passes": self.passes(),
                "plan": self.cp.plan, "in_flight": self.net.in_flight_public(self.et), "stats": self.net.stats,
                "queue": [{"id": b.id, "kind": b.kind, "label": b.payload.get("label", b.kind),
                           "priority": b.priority, "created_et": b.created_et, "expires_et": b.expires_et}
                          for b in q if b.kind != "telemetry"][:40],
                "queue_counts": {"control": len(self.net.stores[EID_CONTROL]), "ship": len(self.net.stores[EID_SHIP])}}

    def snapshot(self, role: str) -> dict:
        seat, role = role, side_of(role)          # a seat reads its side's knowledge store
        s = {"type": "state", "role": role, "seat": seat, "seats": self.seats_public(),
             "phase": self.phase, "et": self.et, "utc": et_to_iso(self.et),
             "home_local": self.local_str(self.et), "rate": self.rate, "delay_scale": self.cfg.delay_scale,
             "judge": self.cfg.judge_mode, "ephemeris": self.ephem.describe(), "scenario": {
                 "id": self.scenario["id"], "title": self.scenario["title"], "subtitle": self.scenario.get("subtitle")},
             "director": self.director.public(role)}
        if self.phase in ("design", "review", "scrubbed"):
            s["design"] = self.design_eval()
            s["policy"] = self.autonomy.public()
            s["review"] = self.review_public()
            s["scrub"] = self.scrub
            s["burn"] = self.burn_public()      # Phase 6: the TMI burn, while it plays
            return s
        s["liens"] = self.liens
        s["reality"] = self.reality.public()     # Phase 6: "Meanwhile, in reality"
        s["callouts"] = self.callouts.public(seat)
        s["geometry"] = self.geometry()
        # Read-only, shared design knowledge for render clients joining mid-mission.
        # No telemetry or hidden faults: these are the team's own design choices.
        s["vehicle_design"] = {**self.design, "crew": self.ship_sys.crew,
                               "habitat_m3": self.ship_sys.cabin_m3, "model": True}
        s["anchors"] = self.anchors
        s["windows"] = self.windows
        s["comms"] = self.comms_view(role)
        if role in ("control", "spectator", "observer"):
            age = (self.et - self.ctrl["sample_et"]) if self.ctrl["sample_et"] is not None else None
            s["control"] = {"telemetry": self.ctrl["frame"], "sample_et": self.ctrl["sample_et"], "age_s": age,
                            "stale": age is None or age > s["geometry"]["lt_down_s"] + 120,
                            "modes": self.ctrl["modes"], "dose": self.ctrl["dose"], "anomaly": self.ctrl["anomaly"],
                            "shelter": self.ctrl["shelter"], "attitude": self.ctrl["attitude"],
                            "requests": list(self.ctrl["requests"].values()), "tern_log": self.ctrl["tern_log"][-30:],
                            "space_weather": self.ctrl["space_weather"][-10:], "inbox": list(self.inbox["control"])[-40:],
                            "knowledge_horizon_et": self.ctrl["last_from_ship_et"], "policy": self.autonomy.public(),
                            "dsn_now": self.dsn_now, "storm_label": self.storm_note}
        if role in ("ship", "spectator", "observer"):
            s["ship"] = {"telemetry": self.frame, "alarms": self.alarms, "modes": self.ship_sys.modes(),
                         "dose": self.rad.public(), "anomaly": self.anomaly_state, "shelter": self.shelter,
                         "attitude": self.storm_attitude, "inbox": list(self.inbox["ship"])[-40:],
                         "letters": self.letters[-60:], "pings": self.pings, "policy": self.autonomy.public(),
                         "commands": list(self.shipk["commands"].values()),
                         "knowledge_horizon_et": self.shipk["last_from_earth_et"]}
        s["science"] = self.science.public(seat)      # Phase 3
        s["crew"] = self.crew.public(seat)
        if role in ("spectator", "observer"):
            s["truth"] = {"faults": list(self.ship_sys.faults), "storm": self.storm.public() if self.storm else None}
        return s

    def meta(self) -> dict:
        return {"channels": CHANNELS, "units": UNITS, "labels": LABELS, "limits": LIMITS}

    # ======================================================== debrief
    def after_action(self) -> dict:
        from .scoring import score
        rec = self.design_record
        return {"scenario": self.scenario["id"], "design": rec["eval"] if rec else self.design_eval(), "anchors": self.anchors,
                "windows": self.windows, "dose": self.rad.public(), "comms": self.net.stats if self.net else {},
                "decisions": [d.public() for d in self.autonomy.log], "objectives": self.director.objectives_log,
                "letters_order": [L for i in self.letter_order for L in self.letters if L["id"] == i and L.get("flood")],
                "timeline": list(self.timeline), "storm": self.storm.public() if self.storm else None,
                "score": score(self), "debrief": self._debrief()}

    def _debrief(self) -> dict:
        from .debrief import build
        return build(self)
