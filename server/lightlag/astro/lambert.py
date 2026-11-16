"""Lambert's problem: find the conic that connects r1 to r2 in time tof.

Universal-variable formulation with bisection on psi (Vallado, "Fundamentals
of Astrodynamics and Applications", Algorithm 58). Single revolution, prograde
by default. Bisection is slower than Izzo's method but it never diverges,
which matters more for a live porkchop plot than raw speed.
"""
from __future__ import annotations

import math

import numpy as np

from .kepler import stumpff_c2, stumpff_c3


class LambertError(RuntimeError):
    pass


def lambert(r1: np.ndarray, r2: np.ndarray, tof: float, mu: float, prograde: bool = True,
            tol: float = 1e-6, max_iter: int = 300) -> tuple[np.ndarray, np.ndarray]:
    r1 = np.asarray(r1, dtype=float)
    r2 = np.asarray(r2, dtype=float)
    r1n, r2n = float(np.linalg.norm(r1)), float(np.linalg.norm(r2))
    cos_dnu = float(np.dot(r1, r2)) / (r1n * r2n)
    cos_dnu = max(-1.0, min(1.0, cos_dnu))
    cz = float(np.cross(r1, r2)[2])
    dnu = math.acos(cos_dnu)
    if (prograde and cz < 0) or (not prograde and cz >= 0):
        dnu = 2 * math.pi - dnu
    A = math.sin(dnu) * math.sqrt(r1n * r2n / (1 - math.cos(dnu)))
    if abs(A) < 1e-12:
        raise LambertError("transfer angle of 0 or 180 deg is degenerate")
    smu = math.sqrt(mu)

    def y_of(psi: float) -> float:
        c2, c3 = stumpff_c2(psi), stumpff_c3(psi)
        return r1n + r2n + A * (psi * c3 - 1) / math.sqrt(c2)

    psi_lo, psi_hi, psi = -4 * math.pi ** 2, 4 * math.pi ** 2, 0.0
    # make sure y > 0 at the lower bound
    while A > 0 and y_of(psi_lo) < 0:
        psi_lo += 0.1
    for _ in range(max_iter):
        y = y_of(psi)
        if A > 0 and y < 0:
            psi_lo = psi
            psi = 0.5 * (psi_lo + psi_hi)
            continue
        c2, c3 = stumpff_c2(psi), stumpff_c3(psi)
        chi = math.sqrt(y / c2)
        dt = (chi ** 3 * c3 + A * math.sqrt(y)) / smu
        if abs(dt - tof) < tol * max(1.0, tof * 1e-9) or abs(psi_hi - psi_lo) < 1e-12:
            break
        if dt <= tof:
            psi_lo = psi
        else:
            psi_hi = psi
        psi = 0.5 * (psi_lo + psi_hi)
    else:
        raise LambertError("did not converge")
    f = 1 - y / r1n
    g = A * math.sqrt(y / mu)
    gdot = 1 - y / r2n
    v1 = (r2 - f * r1) / g
    v2 = (gdot * r2 - r1) / g
    return v1, v2
