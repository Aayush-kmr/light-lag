"""Ephemeris & light-time service.

Two interchangeable backends behind one interface:

* **SPICE** (preferred): NAIF kernels via SpiceyPy. Planet states from the
  JPL DE ephemeris (de440s.bsp), ship states from a generated SPK, one-way
  light time straight from `spkezr` (aberration correction 'XCN' for a
  signal we transmit, 'CN' for one we receive).
* **Kepler** (offline fallback): JPL approximate planetary elements plus
  our own converged Newtonian light-time iteration.

All positions are km in the J2000 (EME2000) frame unless a function says
"ecliptic".
"""
from __future__ import annotations

import glob
import logging
import os
import itertools
import threading
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from . import timescale
from .constants import C_KM_S
from .kepler import EQ_TO_ECL, planet_state_j2000
from .trajectory import SHIP_NAIF_ID, SHIP_NAME, ShipTrajectory

log = logging.getLogger("lightlag.ephem")

# SPICE's kernel pool is global to the process. With several rooms (one universe
# each) the planetary kernels are loaded once and shared, and every universe's ship
# gets its own NAIF id and SPK file, so one room's ship never moves another's.
_SPICE_SET: tuple | None = None
SPICE_LOCK = threading.RLock()        # CSPICE is not thread-safe; rooms tick and answer tools on several threads
_SHIP_IDS = itertools.count(0)


def _next_ship_id() -> int:
    return SHIP_NAIF_ID - next(_SHIP_IDS)          # -926, -927, ...

SPICE_NAMES = {
    "SUN": "SUN",
    "MERCURY": "MERCURY BARYCENTER",
    "VENUS": "VENUS BARYCENTER",
    "EARTH": "EARTH",
    "MOON": "MOON",
    "MARS": "MARS BARYCENTER",
    "JUPITER": "JUPITER BARYCENTER",
    "SHIP": SHIP_NAME,
}

# Kernel filename patterns, in load order. The exact NAIF filenames we
# target are listed in docs/data-provenance.md; patterns let newer
# versions drop in without code changes.
KERNEL_PATTERNS = [
    ("lsk", ["naif0012.tls", "naif*.tls"]),
    ("pck", ["pck00011.tpc", "pck*.tpc"]),
    ("spk", ["de440s.bsp", "de44*.bsp", "de43*.bsp", "de42*.bsp"]),
    ("earth_bpc", ["earth_latest_high_prec.bpc", "earth_*.bpc"]),
    ("stations_spk", ["earthstns_itrf93_*.bsp"]),
    ("stations_fk", ["earth_topo_*.tf"]),
]


@dataclass
class EphemerisService:
    kernel_dir: Path
    mode: str = "auto"
    backend: str = "kepler"
    loaded: dict = field(default_factory=dict)
    ship: ShipTrajectory | None = None
    ship_spk: str | None = None
    ship_id: int = field(default_factory=_next_ship_id)

    # --------------------------------------------------------------- loading
    def load(self) -> "EphemerisService":
        if self.mode in ("auto", "spice"):
            try:
                self._load_spice()
            except Exception as exc:  # pragma: no cover - depends on host
                if self.mode == "spice":
                    raise
                log.warning("SPICE unavailable (%s); using analytic ephemeris", exc)
                self.backend = "kepler"
        return self

    def _load_spice(self) -> None:
        import spiceypy as sp
        kd = Path(self.kernel_dir)
        extra = os.environ.get("LL_EXTRA_SPK")  # e.g. a test kernel
        found = {}
        for kind, pats in KERNEL_PATTERNS:
            for pat in pats:
                hits = sorted(glob.glob(str(kd / pat)))
                if hits:
                    found[kind] = hits[-1]
                    break
        if "spk" not in found and extra:
            found["spk"] = extra
        if "spk" not in found:
            raise FileNotFoundError(f"no planetary SPK in {kd} (run: python -m tools.fetch_kernels)")
        key = tuple(sorted(found.items()))
        with SPICE_LOCK:
            self._furnsh_planets(sp, found, key)
        self.loaded = dict(found)
        self.backend = "spice"
        timescale.set_spice_lsk_loaded("lsk" in found)
        log.info("SPICE kernels loaded: %s", found)

    @staticmethod
    def _furnsh_planets(sp, found: dict, key: tuple) -> None:
        global _SPICE_SET
        if _SPICE_SET != key:          # first load, or the kernel set changed: start clean
            sp.kclear()
            for kind, path in found.items():
                sp.furnsh(path)
            sp.boddef(SHIP_NAME, SHIP_NAIF_ID)
            _SPICE_SET = key

    def attach_ship(self, traj: ShipTrajectory, mission_end_et: float, runtime_dir: Path) -> None:
        self.ship = traj
        if self.backend == "spice":
            runtime_dir.mkdir(parents=True, exist_ok=True)
            self.detach_ship()
            path = str(runtime_dir / f"meridian1_{abs(self.ship_id)}.bsp")
            import spiceypy as sp
            with SPICE_LOCK:
                traj.write_spk(path, mission_end_et, self.ship_id)
                sp.furnsh(path)
            self.ship_spk = path
            self.loaded = {**self.loaded, "ship_spk": path}

    def detach_ship(self, delete: bool = True) -> None:
        """Unload this universe's ship SPK (and delete the file). Called when a room
        is evicted or its universe is replaced."""
        if not self.ship_spk:
            return
        try:
            import spiceypy as sp
            with SPICE_LOCK:
                sp.unload(self.ship_spk)
        except Exception:  # pragma: no cover
            pass
        if delete:
            try:
                Path(self.ship_spk).unlink(missing_ok=True)
            except OSError:  # pragma: no cover
                pass
        self.ship_spk = None

    def _sp(self, body: str) -> str:
        return str(self.ship_id) if body == "SHIP" else SPICE_NAMES[body]

    # ---------------------------------------------------------------- states
    def state(self, body: str, et: float, center: str = "SUN") -> tuple[np.ndarray, np.ndarray]:
        body, center = body.upper(), center.upper()
        if body == "SHIP" and self.ship is not None and et < self.ship.launch_et:
            body = "EARTH"  # still on the pad
        if body == "SHIP" and self.ship is None:
            body = "EARTH"
        if self.backend == "spice":
            import spiceypy as sp
            with SPICE_LOCK:
                st, _ = sp.spkezr(self._sp(body), et, "J2000", "NONE", self._sp(center))
            st = np.asarray(st)
            return st[:3], st[3:]
        r, v = self._kepler_helio(body, et)
        if center != "SUN":
            rc, vc = self._kepler_helio(center, et)
            r, v = r - rc, v - vc
        return r, v

    def _kepler_helio(self, body: str, et: float) -> tuple[np.ndarray, np.ndarray]:
        if body == "SUN":
            return np.zeros(3), np.zeros(3)
        if body == "SHIP":
            s = self.ship
            if et < s.arrival_et:
                return s.helio_cruise_state(et)
            rm, vm = planet_state_j2000("MARS", et)
            ro, vo = s.mars_relative_state(et)
            return rm + ro, vm + vo
        if body == "MOON":
            body = "EARTH"
        return planet_state_j2000(body, et)

    def pos(self, body: str, et: float, center: str = "SUN") -> np.ndarray:
        return self.state(body, et, center)[0]

    # ------------------------------------------------------------ light time
    def light_time(self, src: str, dst: str, et: float, mode: str = "transmit") -> float:
        """One-way light time in seconds.

        mode='transmit': a signal leaves `src` at `et`; returns time until it
        reaches `dst` (SPICE 'XCN'). mode='receive': a signal reaches `dst`
        at `et`; returns how long ago it left `src` (SPICE 'CN').
        """
        src, dst = src.upper(), dst.upper()
        involves_ship = "SHIP" in (src, dst) and self.ship is not None
        if self.backend == "spice" and (not involves_ship or self.ship_spk):
            import spiceypy as sp
            s, d = self._spice_body(src, et), self._spice_body(dst, et)
            with SPICE_LOCK:
                if mode == "transmit":
                    _, lt = sp.spkezr(d, et, "J2000", "XCN", s)
                else:
                    _, lt = sp.spkezr(s, et, "J2000", "CN", d)
            return float(lt)
        return self._newtonian_lt(src, dst, et, mode)

    def _spice_body(self, name: str, et: float) -> str:
        if name == "SHIP" and (self.ship is None or et < self.ship.launch_et):
            return SPICE_NAMES["EARTH"]
        return self._sp(name)

    def _newtonian_lt(self, src: str, dst: str, et: float, mode: str) -> float:
        if mode == "transmit":
            r_src = self.pos(src, et)
            lt = float(np.linalg.norm(self.pos(dst, et) - r_src)) / C_KM_S
            for _ in range(4):
                lt = float(np.linalg.norm(self.pos(dst, et + lt) - r_src)) / C_KM_S
        else:
            r_dst = self.pos(dst, et)
            lt = float(np.linalg.norm(r_dst - self.pos(src, et))) / C_KM_S
            for _ in range(4):
                lt = float(np.linalg.norm(r_dst - self.pos(src, et - lt))) / C_KM_S
        return lt

    # -------------------------------------------------------------- geometry
    def distance_km(self, a: str, b: str, et: float) -> float:
        return float(np.linalg.norm(self.pos(a, et) - self.pos(b, et)))

    def sep_angle_deg(self, et: float, target: str = "SHIP") -> float:
        """Sun-Earth-Probe angle: angle at Earth between the Sun and target."""
        rE = self.pos("EARTH", et)
        to_sun = -rE
        to_t = self.pos(target, et) - rE
        if np.linalg.norm(to_t) < 1.0:          # ship still at Earth (pre-launch)
            return 180.0
        c = float(np.dot(to_sun, to_t) / (np.linalg.norm(to_sun) * np.linalg.norm(to_t)))
        return float(np.degrees(np.arccos(np.clip(c, -1.0, 1.0))))

    def spe_angle_deg(self, et: float, target: str = "SHIP") -> float:
        """Sun-Probe-Earth angle: angle at the probe between Sun and Earth (phase)."""
        rT = self.pos(target, et)
        a = -rT
        b = self.pos("EARTH", et) - rT
        if np.linalg.norm(b) < 1.0:
            return 0.0
        c = float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))
        return float(np.degrees(np.arccos(np.clip(c, -1.0, 1.0))))

    def ecliptic_xy_au(self, body: str, et: float) -> tuple[float, float, float]:
        from .constants import AU_KM
        r = EQ_TO_ECL @ self.pos(body, et)
        return float(r[0] / AU_KM), float(r[1] / AU_KM), float(r[2] / AU_KM)

    def describe(self) -> dict:
        return {
            "backend": self.backend,
            "label": "SPICE (NAIF)" if self.backend == "spice" else "ANALYTIC EPHEMERIS (JPL approx. elements)",
            "kernels": {k: os.path.basename(v) for k, v in self.loaded.items()},
        }
