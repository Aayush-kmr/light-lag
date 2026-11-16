"""Contact plan: when can each side actually transmit?

A contact exists when every one of these is true:

* a DSN pass is scheduled (comms plan) and the station sees the ship;
* the Sun is not in the way (SEP angle above the blackout threshold);
* Mars is not between the ship and Earth (orbital occultation);
* the ship's high-gain antenna is pointed well enough to close the link.

The subtle part, and a lesson the game teaches: **uplink and downlink
windows are not the same.** Earth can transmit while a station is tracking.
The ship must transmit so that its signal *arrives* while a station is
tracking - i.e. it has to start talking one light-time before the pass
begins, into an antenna that is not yet pointed at it.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from ..astro.conjunction import BLACKOUT_DEG, MORATORIUM_DEG
from ..astro.constants import DAY_S, R_MARS_KM
from ..astro.dsn import DSNModel, merge_intervals


@dataclass
class LinkState:
    uplink_open: bool
    downlink_open: bool
    sep_deg: float
    moratorium: bool
    blackout: bool
    occulted: bool
    pass_now: str | None
    margin_db: float
    data_rate_bps: float
    reason: str

    def public(self) -> dict:
        return self.__dict__.copy()


class ContactPlan:
    HORIZON_S = 3 * DAY_S

    def __init__(self, ephem, plan: str = "standard"):
        self.ephem = ephem
        self.dsn = DSNModel(ephem)
        self.plan = plan
        self._passes: list[dict] = []
        self._merged: list[tuple[float, float]] = []
        self._win: tuple[float, float] = (0.0, -1.0)
        self.surges: list[tuple[float, float]] = []   # extra passes Control requested
        self.hga_error_deg = 0.0
        self.force_blackout = False

    # ------------------------------------------------------------ schedule
    def set_plan(self, plan: str) -> None:
        self.plan = plan
        self._win = (0.0, -1.0)

    def add_surge(self, start_et: float, hours: float) -> None:
        self.surges.append((start_et, start_et + hours * 3600))
        self._win = (0.0, -1.0)

    def _ensure(self, et: float) -> None:
        a, b = self._win
        if a <= et <= b - DAY_S:
            return
        start, end = et - 0.5 * DAY_S, et + self.HORIZON_S
        self._passes = self.dsn.schedule(start, end, self.plan)
        allp = self._passes + [{"start": s, "end": e} for s, e in self.surges if e > start and s < end]
        self._merged = merge_intervals(allp)
        self._win = (start, end)

    def passes(self, et: float, hours: float = 48) -> list[dict]:
        self._ensure(et)
        return [p for p in self._passes if p["end"] > et and p["start"] < et + hours * 3600]

    def _in_pass(self, t: float) -> str | None:
        self._ensure(t)
        for s, e in self._merged:
            if s <= t <= e:
                for p in self._passes:
                    if p["start"] <= t <= p["end"]:
                        return p["complex"]
                return "surge"
        return None

    # ------------------------------------------------------------ geometry
    def occulted(self, et: float) -> bool:
        s = self.ephem.ship
        if s is None or et < s.arrival_et:
            return False
        r_ship = self.ephem.pos("SHIP", et)
        r_mars = self.ephem.pos("MARS", et)
        r_earth = self.ephem.pos("EARTH", et)
        d = r_earth - r_ship
        d /= np.linalg.norm(d)
        m = r_mars - r_ship
        t = float(np.dot(m, d))
        if t < 0:
            return False
        closest = float(np.linalg.norm(m - t * d))
        return closest < R_MARS_KM + 100.0  # +100 km of atmosphere

    LINK_FLOOR_DB = -6.0   # below this not even the low-rate mode closes

    def margin_db(self, et: float, sep: float) -> float:
        """Illustrative X-band margin above what the high-rate mode needs.

        base: +14 dB at 1 AU, inverse-square (20 log10 d), capped at +18 dB
        scintillation: (5 - SEP) x 2 dB once SEP < 5 deg
        pointing: Gaussian beam, 12 dB at 0.6 deg off-axis
        """
        d_au = self.ephem.distance_km("EARTH", "SHIP", et) / 1.495978707e8
        return self.link_margin_db(d_au, sep, self.hga_error_deg)

    @staticmethod
    def link_margin_db(d_au: float, sep: float, hga_error_deg: float) -> float:
        """The margin formula on its own, so the design bay can size the downlink
        before there is a ship, with exactly the link the mission will fly."""
        from ..sim.subsystems import pointing_loss_db
        base = min(18.0, 14.0 - 20 * math.log10(max(d_au, 1e-4)))
        scint = 0.0 if sep >= 5 else (5.0 - sep) * 2.0
        return base - scint - pointing_loss_db(hga_error_deg)

    @staticmethod
    def data_rate_bps(margin_db: float) -> float:
        if margin_db < ContactPlan.LINK_FLOOR_DB:
            return 0.0
        return max(500.0, 2.0e6 * 10 ** ((min(margin_db, 18.0) - 18.0) / 10.0))

    # ---------------------------------------------------------------- state
    def state(self, et: float) -> LinkState:
        sep = self.ephem.sep_angle_deg(et)
        launched = self.ephem.ship is None or et >= self.ephem.ship.launch_et
        blackout = sep < BLACKOUT_DEG or self.force_blackout
        moratorium = sep < MORATORIUM_DEG
        occ = self.occulted(et)
        pass_now = self._in_pass(et)
        lt_down = self.ephem.light_time("SHIP", "EARTH", et, "transmit")
        pass_at_rx = self._in_pass(et + lt_down)
        margin = self.margin_db(et, sep)
        reasons = []
        if blackout:
            reasons.append("solar conjunction blackout")
        if occ:
            reasons.append("Mars occultation")
        if margin < self.LINK_FLOOR_DB:
            reasons.append("link does not close (HGA pointing or solar scintillation)")
        elif margin < 0:
            reasons.append("degraded: low-rate mode only")
        physical = not blackout and not occ and margin >= self.LINK_FLOOR_DB
        near_earth = launched and self.ephem.distance_km("EARTH", "SHIP", et) < 2.0e6
        up = physical and (pass_now is not None or near_earth)
        down = physical and (pass_at_rx is not None or near_earth)
        if physical and not up:
            reasons.append("no DSN pass scheduled now")
        if physical and up and not down:
            reasons.append("downlink must wait: next pass not yet listening at arrival time")
        rate = self.data_rate_bps(margin) if (up or down) else 0.0
        return LinkState(up, down, sep, moratorium, blackout, occ, pass_now, margin, rate,
                         "; ".join(reasons) or "nominal")

    def next_open(self, et: float, direction: str, horizon_s: float = 2 * DAY_S, step_s: float = 300.0) -> float | None:
        t = et
        while t < et + horizon_s:
            st = self.state(t)
            if (st.uplink_open if direction == "up" else st.downlink_open):
                return t
            t += step_s
        return None
