"""Meridian-1's trajectory: a patched-conic Earth->Mars transfer followed by
capture into a ~1-sol elliptical Mars orbit.

The heliocentric leg is the Lambert solution between Earth (at launch) and
Mars (at arrival), propagated as a two-body Sun-centred conic. The Mars leg
is a Keplerian ellipse about the Mars system barycentre. Both legs can be
written into a real SPICE SPK (type 13, Hermite) so that `spkezr` returns
the one-way light time for the ship exactly as it would for a real
spacecraft.

Limitations (documented in README): no Earth escape hyperbola, no Mars
approach hyperbola, no trajectory correction manoeuvres, no planetary
perturbations during cruise. Light-time errors from these are seconds at
most near the planets and negligible in deep cruise.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from .constants import MU_MARS, MU_SUN, R_MARS_KM
from .kepler import elements_to_state, propagate
from .lambert import lambert

SHIP_NAIF_ID = -926
SHIP_NAME = "MERIDIAN-1"


@dataclass
class MarsOrbit:
    periapsis_alt_km: float = 400.0
    apoapsis_alt_km: float = 33_000.0
    inc_deg: float = 25.0
    raan_deg: float = 40.0
    argp_deg: float = 0.0

    @property
    def rp(self) -> float:
        return R_MARS_KM + self.periapsis_alt_km

    @property
    def ra(self) -> float:
        return R_MARS_KM + self.apoapsis_alt_km

    @property
    def a(self) -> float:
        return 0.5 * (self.rp + self.ra)

    @property
    def e(self) -> float:
        return (self.ra - self.rp) / (self.ra + self.rp)

    @property
    def period_s(self) -> float:
        return 2 * math.pi * math.sqrt(self.a ** 3 / MU_MARS)


@dataclass
class ShipTrajectory:
    launch_et: float
    arrival_et: float
    r_launch: np.ndarray            # heliocentric Earth position at launch (km, J2000)
    v_depart: np.ndarray            # heliocentric velocity after TMI (km/s)
    r_arrive: np.ndarray            # heliocentric Mars position at arrival
    v_arrive: np.ndarray            # heliocentric velocity before MOI
    v_earth: np.ndarray
    v_mars: np.ndarray
    orbit: MarsOrbit = field(default_factory=MarsOrbit)
    end_et: float | None = None

    # ---------------------------------------------------------------- build
    @classmethod
    def from_dates(cls, ephem, launch_et: float, arrival_et: float, orbit: MarsOrbit | None = None,
                   end_et: float | None = None) -> "ShipTrajectory":
        rE, vE = ephem.state("EARTH", launch_et, center="SUN")
        rM, vM = ephem.state("MARS", arrival_et, center="SUN")
        v1, v2 = lambert(rE, rM, arrival_et - launch_et, MU_SUN)
        return cls(launch_et, arrival_et, rE, v1, rM, v2, vE, vM, orbit or MarsOrbit(), end_et)

    # --------------------------------------------------------------- derived
    @property
    def vinf_depart(self) -> float:
        return float(np.linalg.norm(self.v_depart - self.v_earth))

    @property
    def vinf_arrive(self) -> float:
        return float(np.linalg.norm(self.v_arrive - self.v_mars))

    @property
    def c3(self) -> float:
        return self.vinf_depart ** 2

    def phase(self, et: float) -> str:
        if et < self.launch_et:
            return "prelaunch"
        if et < self.arrival_et:
            return "cruise"
        return "mars_orbit"

    # --------------------------------------------------------------- states
    def helio_cruise_state(self, et: float) -> tuple[np.ndarray, np.ndarray]:
        return propagate(self.r_launch, self.v_depart, et - self.launch_et, MU_SUN)

    def mars_relative_state(self, et: float) -> tuple[np.ndarray, np.ndarray]:
        o = self.orbit
        n = math.sqrt(MU_MARS / o.a ** 3)
        M = (n * (et - self.arrival_et)) % (2 * math.pi)
        from .kepler import solve_kepler
        E = solve_kepler(M, o.e)
        nu = 2 * math.atan2(math.sqrt(1 + o.e) * math.sin(E / 2), math.sqrt(1 - o.e) * math.cos(E / 2))
        return elements_to_state(o.a, o.e, math.radians(o.inc_deg), math.radians(o.raan_deg),
                                 math.radians(o.argp_deg), nu, MU_MARS)

    def sample(self, start: float, stop: float, step: float):
        n = max(2, int(math.ceil((stop - start) / step)) + 1)
        return np.linspace(start, stop, n)

    # ------------------------------------------------------------ SPK writer
    def write_spk(self, path: str, mission_end_et: float, naif_id: int = SHIP_NAIF_ID) -> str:
        """Write the trajectory as a binary SPK (type 13). Requires SpiceyPy.
        `naif_id` lets several universes in one process (rooms) each fly their own ship."""
        import os
        import spiceypy as sp
        if os.path.exists(path):
            os.remove(path)
        if naif_id == SHIP_NAIF_ID:
            sp.boddef(SHIP_NAME, SHIP_NAIF_ID)
        handle = sp.spkopn(path, "LIGHT-LAG MERIDIAN-1", 2048)
        try:
            # Leg 1: Sun-centred cruise, 2-hour Hermite nodes
            ets = self.sample(self.launch_et, self.arrival_et, 7200.0)
            states = np.array([np.concatenate(self.helio_cruise_state(t)) for t in ets])
            sp.spkw13(handle, naif_id, 10, "J2000", float(ets[0]), float(ets[-1]),
                      "CRUISE", 7, len(ets), states.tolist(), ets.tolist())
            # Leg 2: Mars-centred orbit, 10-minute nodes
            if mission_end_et > self.arrival_et:
                ets = self.sample(self.arrival_et, mission_end_et, 600.0)
                states = np.array([np.concatenate(self.mars_relative_state(t)) for t in ets])
                sp.spkw13(handle, naif_id, 4, "J2000", float(ets[0]), float(ets[-1]),
                          "MARS ORBIT", 7, len(ets), states.tolist(), ets.tolist())
        finally:
            sp.spkcls(handle)
        return path
