"""Space weather replay: real solar events, real propagation physics, at
in-game dates.

Event sources, in priority order:
1. Raw NASA DONKI responses cached by tools/fetch_donki.py (FLR, CME, SEP).
2. data/reference/sep_events.yaml - values transcribed from peer-reviewed
   papers, each with a citation. Used only when no DONKI cache exists, and
   labelled as such in the UI.
There is no third, made-up source: if neither exists the storm beat is
skipped and the UI says why.

Physics (everything below is computed per event, per ship position):

* Flare light reaches Earth first or the ship first, depending on who is
  closer to the Sun (t = r / c). DONKI times are as observed at Earth, so
  we back out the emission time.
* Protons don't fly straight: they spiral along the interplanetary
  magnetic field (the Parker spiral), so the path is longer than r and
  depends on solar-wind speed. Path length to radius r:
      L(r) = (a/2) [ x sqrt(1+x^2) + asinh(x) ],  a = v_sw / Omega_sun, x = r / a
* Whether protons reach you at all depends on magnetic connectivity: the
  angle between the flare site and your field line's footpoint on the Sun
  (about 60 deg west of you at 1 AU for 400 km/s wind).
* The dose-rate profile (rise, peak, decay) is an illustrative parametric
  model, NOT a transport calculation. Magnitudes are documented in
  docs/science.md and flagged in-game as "MODEL".
"""
from __future__ import annotations

import glob
import json
import math
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

import numpy as np
import yaml

from ..astro.constants import AU_KM, C_KM_S, SUN_OMEGA_RAD_S
from ..astro.kepler import EQ_TO_ECL
from ..astro.timescale import et_to_iso, utc_to_et

PROTON_REST_MEV = 938.272


def beta_for(kinetic_mev: float) -> float:
    g = 1.0 + kinetic_mev / PROTON_REST_MEV
    return math.sqrt(1.0 - 1.0 / (g * g))


def parker_length_au(r_au: float, v_sw_km_s: float = 400.0) -> float:
    a = v_sw_km_s / SUN_OMEGA_RAD_S / AU_KM
    x = r_au / a
    return 0.5 * a * (x * math.sqrt(1 + x * x) + math.asinh(x))


def footpoint_offset_deg(r_au: float, v_sw_km_s: float = 400.0) -> float:
    return math.degrees(SUN_OMEGA_RAD_S * r_au * AU_KM / v_sw_km_s)


def flare_class_flux(cls: str) -> float:
    m = re.match(r"([ABCMX])\s*([0-9.]+)", cls.strip().upper())
    if not m:
        return 1e-5
    base = {"A": 1e-8, "B": 1e-7, "C": 1e-6, "M": 1e-5, "X": 1e-4}[m.group(1)]
    return base * float(m.group(2))


def parse_location(loc: str) -> tuple[float, float]:
    """'S08W88' -> (lat=-8, lon=+88), W positive (Stonyhurst, Earth view)."""
    m = re.match(r"([NS])(\d+)([EW])(\d+)", (loc or "").strip().upper())
    if not m:
        return 0.0, 0.0
    lat = float(m.group(2)) * (1 if m.group(1) == "N" else -1)
    lon = float(m.group(4)) * (1 if m.group(3) == "W" else -1)
    return lat, lon


@dataclass
class SolarEvent:
    id: str
    label: str
    source: str                 # "DONKI cache" | "literature reference"
    citation: str
    flare_start_utc: str
    flare_peak_utc: str
    flare_class: str
    location: str
    cme_speed_km_s: float | None = None
    cme_half_width_deg: float = 45.0
    release_delay_min: float = 18.0
    v_sw_km_s: float = 400.0
    widespread: bool = False
    notes: str = ""


@dataclass
class StormTimeline:
    event: SolarEvent
    replay_of: str
    t_flare_earth: float        # flare light reaches Earth (game ET)
    t_emit: float
    t_photons_ship: float
    t_release: float
    t_onset_ship: float
    t_peak_ship: float
    tau_decay_s: float
    peak_msv_h: float           # behind the nominal hull, at the ship
    connectivity_deg: float
    connectivity: float
    path_len_au: float
    r_ship_au: float
    t_onset_earth: float
    t_cme_ship: float | None
    forbush_depth: float

    def dose_rate_msv_h(self, t: float) -> float:
        if t < self.t_onset_ship:
            return 0.0
        if t < self.t_peak_ship:
            x = (t - self.t_onset_ship) / max(self.t_peak_ship - self.t_onset_ship, 1.0)
            return self.peak_msv_h * x * x
        return self.peak_msv_h * math.exp(-(t - self.t_peak_ship) / self.tau_decay_s)

    def forbush_factor(self, t: float) -> float:
        if self.t_cme_ship is None or t < self.t_cme_ship:
            return 1.0
        dt = t - self.t_cme_ship
        return 1.0 - self.forbush_depth * math.exp(-dt / (1.5 * 86400))

    def public(self) -> dict:
        d = asdict(self)
        d["event"] = asdict(self.event)
        for k in ("t_flare_earth", "t_emit", "t_photons_ship", "t_release", "t_onset_ship", "t_peak_ship", "t_onset_earth"):
            d[k + "_iso"] = et_to_iso(getattr(self, k))
        return d


class SpaceWeather:
    def __init__(self, data_dir: Path, cache_dir: Path):
        self.data_dir = Path(data_dir)
        self.cache_dir = Path(cache_dir)
        self.events: dict[str, SolarEvent] = {}
        self.source_summary = ""
        self._load()

    # ---------------------------------------------------------------- load
    def _load(self) -> None:
        n_donki = self._load_donki()
        ref = self.data_dir / "reference" / "sep_events.yaml"
        n_ref = 0
        if ref.exists():
            for e in yaml.safe_load(ref.read_text())["events"]:
                if e["id"] not in self.events:
                    self.events[e["id"]] = SolarEvent(source="literature reference", **e)
                    n_ref += 1
        self.source_summary = f"{n_donki} events from DONKI cache, {n_ref} literature reference events"

    def _load_donki(self) -> int:
        """Parse cached raw DONKI FLR + CME responses (see tools/fetch_donki.py)."""
        flr_files = sorted(glob.glob(str(self.cache_dir / "donki" / "FLR_*.json")))
        cme_files = sorted(glob.glob(str(self.cache_dir / "donki" / "CME_*.json")))
        cmes = {}
        for f in cme_files:
            for c in json.loads(Path(f).read_text()) or []:
                speeds = [a.get("speed") for a in (c.get("cmeAnalyses") or []) if a.get("speed")]
                best = [a for a in (c.get("cmeAnalyses") or []) if a.get("isMostAccurate")]
                hw = best[0].get("halfAngle") if best and best[0].get("halfAngle") else 45.0
                cmes[c.get("activityID")] = (best[0]["speed"] if best and best[0].get("speed") else (max(speeds) if speeds else None), hw)
        n = 0
        for f in flr_files:
            for fl in json.loads(Path(f).read_text()) or []:
                cls = fl.get("classType") or ""
                if not cls.startswith(("X", "M")):
                    continue
                speed, hw = None, 45.0
                for le in fl.get("linkedEvents") or []:
                    if le.get("activityID") in cmes and cmes[le["activityID"]][0]:
                        speed, hw = cmes[le["activityID"]]
                has_sep = any("SEP" in (le.get("activityID") or "") for le in fl.get("linkedEvents") or [])
                eid = (fl.get("flrID") or fl.get("beginTime", "?"))
                self.events[eid] = SolarEvent(
                    id=eid, label=f"{cls} flare {fl.get('sourceLocation','')} AR{fl.get('activeRegionNum','?')}",
                    source="DONKI cache", citation="NASA CCMC DONKI, " + (fl.get("link") or "api.nasa.gov/DONKI/FLR"),
                    flare_start_utc=fl["beginTime"], flare_peak_utc=fl.get("peakTime") or fl["beginTime"],
                    flare_class=cls, location=fl.get("sourceLocation") or "N00W00",
                    cme_speed_km_s=speed, cme_half_width_deg=hw or 45.0,
                    notes="linked SEP in DONKI" if has_sep else "no linked SEP in DONKI")
                n += 1
        return n

    def find(self, key: str) -> SolarEvent | None:
        ref = self.events.get(key)
        if ref is not None and ref.source != "DONKI cache":
            # prefer the raw DONKI record of the same flare if it has been cached
            t0 = utc_to_et(ref.flare_start_utc.replace("Z", ""))
            for e in self.events.values():
                if e.source == "DONKI cache" and e.flare_class[:1] == ref.flare_class[:1]:
                    if abs(utc_to_et(e.flare_start_utc.replace("Z", "")) - t0) < 1800:
                        if not e.location or e.location == "N00W00":
                            e.location = ref.location
                        return e
        if ref is not None:
            return ref
        for e in self.events.values():
            if e.flare_start_utc.startswith(key) or key in e.id:
                return e
        return None

    # ------------------------------------------------------------ physics
    def timeline(self, ephem, ev: SolarEvent, at_utc: str | None = None) -> StormTimeline:
        """Place `ev` so its flare light reaches Earth at `at_utc` (default: its real date)."""
        t_obs_real = utc_to_et(ev.flare_start_utc.replace("Z", ""))
        t_obs = utc_to_et(at_utc) if at_utc else t_obs_real
        peak_off = utc_to_et(ev.flare_peak_utc.replace("Z", "")) - t_obs_real
        rE = ephem.pos("EARTH", t_obs)
        rS = ephem.pos("SHIP", t_obs)
        r_e_au = float(np.linalg.norm(rE)) / AU_KM
        r_s_au = float(np.linalg.norm(rS)) / AU_KM
        t_emit = t_obs - r_e_au * AU_KM / C_KM_S
        t_ph_ship = t_emit + r_s_au * AU_KM / C_KM_S
        # magnetic connectivity of the ship
        lam = lambda r: math.degrees(math.atan2((EQ_TO_ECL @ r)[1], (EQ_TO_ECL @ r)[0]))  # noqa: E731
        d_lon = ((lam(rS) - lam(rE) + 180) % 360) - 180
        _, flare_lon = parse_location(ev.location)
        foot = d_lon + footpoint_offset_deg(r_s_au, ev.v_sw_km_s)
        conn_deg = abs(((flare_lon - foot) + 180) % 360 - 180)
        sigma = 55.0 if ev.widespread else 38.0
        conn = max(0.08 if ev.widespread else 0.02, math.exp(-conn_deg ** 2 / (2 * sigma ** 2)))
        L = parker_length_au(r_s_au, ev.v_sw_km_s)
        t_rel = t_emit + ev.release_delay_min * 60.0
        # +2 min per 10 deg off-connection. Calibrated so the model's Earth onset for the
        # 2017-09-10 event (~16:03 UT) sits within minutes of the observed GLE 72 onset
        # at 1 AU, 16:06-16:08 UT (Kurt et al., arXiv:1806.00226).
        cross = (conn_deg / 10.0) * 120.0
        onset = t_rel + L * AU_KM / (beta_for(500.0) * C_KM_S) + cross
        peak = t_rel + L * AU_KM / (beta_for(40.0) * C_KM_S) + 1.5 * 3600 * (1 + conn_deg / 60.0) + cross
        flux = flare_class_flux(ev.flare_class)
        f_cls = math.sqrt(flux / 1e-4)
        f_cme = ((ev.cme_speed_km_s or 1000.0) / 2000.0) ** 2
        peak_msv_h = min(50.0, 2.0 * f_cls * f_cme * conn / max(r_s_au, 0.3) ** 2)
        # Earth-side proton onset (for Control's GOES panel)
        foot_e = footpoint_offset_deg(r_e_au, ev.v_sw_km_s)
        conn_e = abs(((flare_lon - foot_e) + 180) % 360 - 180)
        onset_e = t_rel + parker_length_au(r_e_au, ev.v_sw_km_s) * AU_KM / (beta_for(500.0) * C_KM_S) + conn_e / 10.0 * 120
        # CME: does the ship lie inside its cone? (direction = flare longitude, Earth view)
        t_cme, fd = None, 0.0
        if ev.cme_speed_km_s:
            sep_cme = abs(((flare_lon - d_lon) + 180) % 360 - 180)
            if sep_cme <= ev.cme_half_width_deg + 15:
                v_avg = max(400.0, 0.6 * ev.cme_speed_km_s)
                t_cme = t_emit + r_s_au * AU_KM / v_avg
                fd = 0.10
        return StormTimeline(
            event=ev, replay_of=ev.flare_start_utc if at_utc else "",
            t_flare_earth=t_obs, t_emit=t_emit, t_photons_ship=t_ph_ship + 0 * peak_off, t_release=t_rel,
            t_onset_ship=onset, t_peak_ship=peak, tau_decay_s=10 * 3600.0, peak_msv_h=peak_msv_h,
            connectivity_deg=conn_deg, connectivity=conn, path_len_au=L, r_ship_au=r_s_au,
            t_onset_earth=onset_e, t_cme_ship=t_cme, forbush_depth=fd)


def iso_now() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
