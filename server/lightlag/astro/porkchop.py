"""Porkchop plots and the Mission Design mass trade.

For every (launch date, arrival date) pair we solve Lambert's problem
between Earth and Mars and report:

* C3 = v_inf_departure^2 (km^2/s^2) - the launch-energy figure every
  Mars mission quotes,
* v_inf at arrival,
* delta-v from a 300 km circular parking orbit (TMI) and into a
  400 x 33,000 km ~1-sol Mars capture orbit (MOI),
* propellant mass for the player's chosen payload, via Tsiolkovsky.

The heatmap the players see is computed here, server-side, from the same
ephemeris used for the light-time - one source of truth.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass

import numpy as np

from .constants import DAY_S, G0, MU_EARTH, MU_MARS, MU_SUN, R_EARTH_KM
from .lambert import LambertError, lambert
from .timescale import et_to_iso, utc_to_et
from .trajectory import MarsOrbit

PARKING_ALT_KM = 300.0


@dataclass
class TransferCell:
    launch_et: float
    arrival_et: float
    tof_days: float
    c3: float
    vinf_dep: float
    vinf_arr: float
    dv_tmi: float
    dv_moi: float

    @property
    def dv_total(self) -> float:
        return self.dv_tmi + self.dv_moi


def dv_from_parking(vinf: float, alt_km: float = PARKING_ALT_KM) -> float:
    r = R_EARTH_KM + alt_km
    return math.sqrt(vinf ** 2 + 2 * MU_EARTH / r) - math.sqrt(MU_EARTH / r)


def dv_capture(vinf: float, orbit: MarsOrbit) -> float:
    rp, e = orbit.rp, orbit.e
    return math.sqrt(vinf ** 2 + 2 * MU_MARS / rp) - math.sqrt(MU_MARS * (1 + e) / rp)


def transfer(ephem, launch_et: float, arrival_et: float, orbit: MarsOrbit | None = None) -> TransferCell | None:
    orbit = orbit or MarsOrbit()
    tof = arrival_et - launch_et
    if tof <= 20 * DAY_S:
        return None
    rE, vE = ephem.state("EARTH", launch_et, "SUN")
    rM, vM = ephem.state("MARS", arrival_et, "SUN")
    try:
        v1, v2 = lambert(rE, rM, tof, MU_SUN)
    except LambertError:
        return None
    vinf_d = float(np.linalg.norm(v1 - vE))
    vinf_a = float(np.linalg.norm(v2 - vM))
    return TransferCell(launch_et, arrival_et, tof / DAY_S, vinf_d ** 2, vinf_d, vinf_a,
                        dv_from_parking(vinf_d), dv_capture(vinf_a, orbit))


def porkchop(ephem, launch_start: str, launch_end: str, arr_start: str, arr_end: str,
             n_launch: int = 46, n_arrival: int = 46) -> dict:
    l0, l1 = utc_to_et(launch_start), utc_to_et(launch_end)
    a0, a1 = utc_to_et(arr_start), utc_to_et(arr_end)
    L = np.linspace(l0, l1, n_launch)
    A = np.linspace(a0, a1, n_arrival)
    c3 = np.full((n_arrival, n_launch), np.nan)
    dv = np.full((n_arrival, n_launch), np.nan)
    vinf_a = np.full((n_arrival, n_launch), np.nan)
    best = None
    for j, la in enumerate(L):
        for i, ar in enumerate(A):
            cell = transfer(ephem, float(la), float(ar))
            if cell is None or cell.c3 > 200:
                continue
            c3[i, j], dv[i, j], vinf_a[i, j] = cell.c3, cell.dv_total, cell.vinf_arr
            if best is None or cell.dv_total < best.dv_total:
                best = cell

    def clean(a):
        return [[None if not np.isfinite(x) else round(float(x), 3) for x in row] for row in a]

    return {
        "launch_et": L.tolist(), "arrival_et": A.tolist(),
        "launch_iso": [et_to_iso(x)[:10] for x in L], "arrival_iso": [et_to_iso(x)[:10] for x in A],
        "c3": clean(c3), "dv_total": clean(dv), "vinf_arr": clean(vinf_a),
        "best": asdict(best) | {"dv_total": best.dv_total} if best else None,
    }


# --------------------------------------------------------------------------
# Mass model for the design trade
# --------------------------------------------------------------------------

@dataclass
class MassModel:
    """Very simple single-stage model; all values documented in docs/science.md.

    Consumables follow NASA's BVAD: crew x days x kg per crew member per day
    (data/catalog/crew.yaml). If no rate is given, the fixed `consumables_t`
    is used instead (older scenarios)."""
    isp_s: float = 380.0                 # methane/oxygen class engine
    habitat_dry_t: float = 38.0          # pressurized module, systems, structure
    consumables_t: float = 9.0           # fallback when no per-crew-day rate is set
    tank_fraction: float = 0.08          # tank mass per tonne of propellant
    imleo_budget_t: float = 160.0        # what the launch campaign can assemble in LEO
    shelter_area_m2: float = 14.0        # surface of the storm-shelter water wall
    solar_monitor_t: float = 0.35        # X-ray + particle monitor package
    extra_hull_area_m2: float = 160.0    # whole-hab polyethylene liner area
    crew: int = 1
    stay_days: float = 0.0               # planned days at Mars after arrival
    contingency_days: float = 0.0
    consumables_kg_cm_day: float = 0.0   # 0 = use the fixed consumables_t

    def consumables_for(self, tof_days: float | None) -> float:
        if self.consumables_kg_cm_day <= 0 or tof_days is None:
            return self.consumables_t
        days = tof_days + self.stay_days + self.contingency_days
        return self.crew * days * self.consumables_kg_cm_day / 1000.0

    def payload_t(self, shelter_gcm2: float, hull_gcm2: float, solar_monitor: bool, tof_days: float | None = None) -> float:
        shelter = shelter_gcm2 * 10.0 * self.shelter_area_m2 / 1000.0   # g/cm^2 -> kg/m^2 is x10
        hull = hull_gcm2 * 10.0 * self.extra_hull_area_m2 / 1000.0
        return self.habitat_dry_t + self.consumables_for(tof_days) + shelter + hull + \
            (self.solar_monitor_t if solar_monitor else 0.0)

    def imleo_t(self, dv_tmi: float, dv_moi: float, payload_t: float) -> float:
        ve = self.isp_s * G0 / 1000.0  # km/s
        # back out MOI then TMI, adding tankage for each burn's propellant
        m = payload_t
        for dv in (dv_moi, dv_tmi):
            ratio = math.exp(dv / ve)
            prop = m * (ratio - 1) / (1 - self.tank_fraction * (ratio - 1)) if self.tank_fraction * (ratio - 1) < 1 else float("inf")
            m = m + prop * (1 + self.tank_fraction)
        return m

    def evaluate(self, cell: TransferCell, shelter_gcm2: float, hull_gcm2: float, solar_monitor: bool) -> dict:
        payload = self.payload_t(shelter_gcm2, hull_gcm2, solar_monitor, cell.tof_days)
        imleo = self.imleo_t(cell.dv_tmi, cell.dv_moi, payload)
        return {
            "payload_t": round(payload, 2),
            "consumables_t": round(self.consumables_for(cell.tof_days), 2),
            "crew": self.crew,
            "imleo_t": round(imleo, 1),
            "budget_t": self.imleo_budget_t,
            "margin_t": round(self.imleo_budget_t - imleo, 1),
            "feasible": imleo <= self.imleo_budget_t,
        }
