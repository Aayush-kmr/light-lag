"""Crew radiation dose: galactic cosmic rays + solar particle events.

Baseline: 1.84 mSv/day dose equivalent from GCR during cruise, measured by
the MSL Radiation Assessment Detector inside the Curiosity cruise stage
(Zeitlin et al., Science 340:1080, 2013). RAD's shielding was judged
similar to the ISS and to what future crewed vehicles assume, so we treat
it as Meridian-1's nominal hull.

Shielding is the great trade of Mission Design, and the model encodes its
key asymmetry:
* GCR (GeV nuclei) barely care: a few g/cm^2 of extra polyethylene removes
  a few percent (Zeitlin: "even an aluminum hull a foot thick wouldn't
  change the dose very much").
* SEP protons are mostly tens of MeV and are stopped well by mass, so a
  water-walled storm shelter cuts their dose by a large factor.

Factors used (illustrative, not a transport calculation):
    GCR factor  = max(0.75, 1 - 0.012 * hull_liner_gcm2)
    SEP factor  = exp(-(liner + shelter_if_inside) / 12 g/cm^2)
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

GCR_MSV_PER_DAY = 1.84
CAREER_LIMIT_MSV = 600.0   # NASA-STD-3001 Vol. 1 Rev. B (2022) career effective dose limit


@dataclass
class Radiation:
    hull_liner_gcm2: float = 0.0
    shelter_gcm2: float = 20.0
    gcr_msv: float = 0.0
    sep_msv: float = 0.0
    storms: list = field(default_factory=list)   # StormTimeline objects
    peak_usv_h: float = 0.0
    # Storm dose before the hull, liner and shelter factors (attitude included),
    # split by whether the crew was in the shelter: a counterfactual shelter
    # thickness is then the same formula (sep_counterfactual).
    sep_raw_in_msv: float = 0.0
    sep_raw_out_msv: float = 0.0

    @property
    def gcr_factor(self) -> float:
        return max(0.75, 1.0 - 0.012 * self.hull_liner_gcm2)

    def sep_factor(self, in_shelter: bool) -> float:
        return math.exp(-(self.hull_liner_gcm2 + (self.shelter_gcm2 if in_shelter else 0.0)) / 12.0)

    ATTITUDE_FACTOR = 0.6   # propellant tanks turned sunward

    def components_usv_h(self, et: float, in_shelter: bool, attitude: bool = False) -> tuple[float, float]:
        fd = 1.0
        sep = 0.0
        for s in self.storms:
            fd = min(fd, s.forbush_factor(et))
            sep += s.dose_rate_msv_h(et) * 1000.0
        gcr = GCR_MSV_PER_DAY / 24.0 * 1000.0 * self.gcr_factor * fd
        return gcr, sep * self.sep_factor(in_shelter) * (self.ATTITUDE_FACTOR if attitude else 1.0)

    def rate_usv_h(self, et: float, in_shelter: bool, attitude: bool = False) -> float:
        g, s = self.components_usv_h(et, in_shelter, attitude)
        return g + s

    def _raw(self, msv: float, in_shelter: bool) -> None:
        if in_shelter:
            self.sep_raw_in_msv += msv
        else:
            self.sep_raw_out_msv += msv

    def sep_counterfactual(self, shelter_gcm2: float | None = None, liner_gcm2: float | None = None) -> float:
        """Storm dose (mSv) this mission would have taken with a different shelter or
        liner, the crew doing exactly what it did. Same factors as sep_factor."""
        sh = self.shelter_gcm2 if shelter_gcm2 is None else shelter_gcm2
        li = self.hull_liner_gcm2 if liner_gcm2 is None else liner_gcm2
        return self.sep_raw_in_msv * math.exp(-(li + sh) / 12.0) + self.sep_raw_out_msv * math.exp(-li / 12.0)

    def accumulate(self, et: float, dt: float, in_shelter: bool, attitude: bool = False) -> None:
        g, s = self.components_usv_h(et, in_shelter, attitude)
        self.gcr_msv += g * dt / 3600.0 / 1000.0
        self.sep_msv += s * dt / 3600.0 / 1000.0
        if s > 0:
            self._raw(s * dt / 3600.0 / 1000.0 / self.sep_factor(in_shelter), in_shelter)
        self.peak_usv_h = max(self.peak_usv_h, g + s)

    def coast(self, days: float) -> None:
        """Analytic GCR accumulation over a time skip with no storms."""
        self.gcr_msv += GCR_MSV_PER_DAY * self.gcr_factor * days

    def coast_storms(self, t0: float, t1: float, in_shelter: bool, attitude: bool = False) -> None:
        """Integrate solar-particle dose across a time skip (10-minute steps)."""
        if not self.storms or t1 <= t0:
            return
        step = 600.0
        t = t0
        f = self.sep_factor(in_shelter) * (self.ATTITUDE_FACTOR if attitude else 1.0)
        while t < t1:
            dt = min(step, t1 - t)
            rate = sum(s.dose_rate_msv_h(t + dt / 2) for s in self.storms)
            if rate > 0:
                self.sep_msv += rate * f * dt / 3600.0
                self._raw(rate * (self.ATTITUDE_FACTOR if attitude else 1.0) * dt / 3600.0, in_shelter)
            elif t > max(s.t_peak_ship for s in self.storms):
                break
            t += dt

    @property
    def total_msv(self) -> float:
        return self.gcr_msv + self.sep_msv

    def public(self) -> dict:
        return {"total_msv": round(self.total_msv, 2), "gcr_msv": round(self.gcr_msv, 2),
                "sep_msv": round(self.sep_msv, 2), "career_limit_msv": CAREER_LIMIT_MSV,
                "fraction_of_limit": round(self.total_msv / CAREER_LIMIT_MSV, 4),
                "gcr_factor": round(self.gcr_factor, 3), "shelter_gcm2": self.shelter_gcm2,
                "hull_liner_gcm2": self.hull_liner_gcm2, "peak_usv_h": round(self.peak_usv_h, 1)}
