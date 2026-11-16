"""Two-body tools: Kepler's equation, universal-variable propagation, Stumpff
functions, and the JPL approximate planetary elements used as an offline
fallback when no SPICE kernels are present.

Planet elements: E. M. Standish, "Keplerian Elements for Approximate Positions
of the Major Planets", JPL Solar System Dynamics, Table 1 (valid 1800-2050 AD).
https://ssd.jpl.nasa.gov/planets/approx_pos.html
Errors for Earth-Moon barycenter and Mars over that span are of order
tens of arcseconds in heliocentric longitude, i.e. light-time errors well
under a second - more than good enough for a game, and always flagged in
the UI as "ANALYTIC EPHEMERIS".
"""
from __future__ import annotations

import math

import numpy as np

from .constants import AU_KM, DAY_S, MU_SUN, OBLIQUITY_J2000_DEG

# name: (a[au], e, I[deg], L[deg], varpi[deg], Omega[deg]) and rates per Julian century
STANDISH_1800_2050 = {
    "MERCURY": ((0.38709927, 0.20563593, 7.00497902, 252.25032350, 77.45779628, 48.33076593),
                (0.00000037, 0.00001906, -0.00594749, 149472.67411175, 0.16047689, -0.12534081)),
    "VENUS": ((0.72333566, 0.00677672, 3.39467605, 181.97909950, 131.60246718, 76.67984255),
              (0.00000390, -0.00004107, -0.00078890, 58517.81538729, 0.00268329, -0.27769418)),
    "EARTH": ((1.00000261, 0.01671123, -0.00001531, 100.46457166, 102.93768193, 0.0),
              (0.00000562, -0.00004392, -0.01294668, 35999.37244981, 0.32327364, 0.0)),
    "MARS": ((1.52371034, 0.09339410, 1.84969142, -4.55343205, -23.94362959, 49.55953891),
             (0.00001847, 0.00007882, -0.00813131, 19140.30268499, 0.44441088, -0.29257343)),
    "JUPITER": ((5.20288700, 0.04838624, 1.30439695, 34.39644051, 14.72847983, 100.47390909),
                (-0.00011607, -0.00013253, -0.00183714, 3034.74612775, 0.21252668, 0.20469106)),
}

_EPS = math.radians(OBLIQUITY_J2000_DEG)
ECL_TO_EQ = np.array([[1, 0, 0],
                      [0, math.cos(_EPS), -math.sin(_EPS)],
                      [0, math.sin(_EPS), math.cos(_EPS)]])
EQ_TO_ECL = ECL_TO_EQ.T


def solve_kepler(M: float, e: float, tol: float = 1e-12) -> float:
    E = M if e < 0.8 else math.pi
    for _ in range(50):
        f = E - e * math.sin(E) - M
        d = 1 - e * math.cos(E)
        dE = -f / d
        E += dE
        if abs(dE) < tol:
            break
    return E


def planet_state_ecliptic(name: str, et: float) -> tuple[np.ndarray, np.ndarray]:
    """Heliocentric state in ECLIPJ2000, km and km/s."""
    el0, rate = STANDISH_1800_2050[name]
    T = et / (36525.0 * DAY_S)
    a, e, I, L, varpi, Om = (el0[i] + rate[i] * T for i in range(6))
    a_km = a * AU_KM
    omega = math.radians(varpi - Om)
    M = math.radians((L - varpi) % 360.0)
    I, Om = math.radians(I), math.radians(Om)
    E = solve_kepler(M, e)
    n = math.radians(rate[3]) / (36525.0 * DAY_S)  # rad/s (mean longitude rate)
    xp = a_km * (math.cos(E) - e)
    yp = a_km * math.sqrt(1 - e * e) * math.sin(E)
    Edot = n / (1 - e * math.cos(E))
    vxp = -a_km * math.sin(E) * Edot
    vyp = a_km * math.sqrt(1 - e * e) * math.cos(E) * Edot
    co, so, cO, sO, cI, sI = math.cos(omega), math.sin(omega), math.cos(Om), math.sin(Om), math.cos(I), math.sin(I)
    R = np.array([
        [co * cO - so * sO * cI, -so * cO - co * sO * cI],
        [co * sO + so * cO * cI, -so * sO + co * cO * cI],
        [so * sI, co * sI],
    ])
    r = R @ np.array([xp, yp])
    v = R @ np.array([vxp, vyp])
    return r, v


def planet_state_j2000(name: str, et: float) -> tuple[np.ndarray, np.ndarray]:
    r, v = planet_state_ecliptic(name, et)
    return ECL_TO_EQ @ r, ECL_TO_EQ @ v


# --------------------------------------------------------------------------
# Universal variables
# --------------------------------------------------------------------------

def stumpff_c2(psi: float) -> float:
    if psi > 1e-6:
        s = math.sqrt(psi)
        return (1 - math.cos(s)) / psi
    if psi < -1e-6:
        s = math.sqrt(-psi)
        return (math.cosh(s) - 1) / (-psi)
    return 0.5 - psi / 24.0


def stumpff_c3(psi: float) -> float:
    if psi > 1e-6:
        s = math.sqrt(psi)
        return (s - math.sin(s)) / (s ** 3)
    if psi < -1e-6:
        s = math.sqrt(-psi)
        return (math.sinh(s) - s) / (s ** 3)
    return 1.0 / 6.0 - psi / 120.0


def propagate(r0: np.ndarray, v0: np.ndarray, dt: float, mu: float = MU_SUN) -> tuple[np.ndarray, np.ndarray]:
    """Universal-variable two-body propagation (Curtis, Algorithm 3.4)."""
    if dt == 0:
        return r0.copy(), v0.copy()
    r0n = float(np.linalg.norm(r0))
    vr0 = float(np.dot(r0, v0)) / r0n
    alpha = 2.0 / r0n - float(np.dot(v0, v0)) / mu
    smu = math.sqrt(mu)
    chi = smu * abs(alpha) * dt if abs(alpha) > 1e-12 else smu * dt / r0n
    for _ in range(100):
        z = alpha * chi * chi
        C, S = stumpff_c2(z), stumpff_c3(z)
        F = (r0n * vr0 / smu) * chi * chi * C + (1 - alpha * r0n) * chi ** 3 * S + r0n * chi - smu * dt
        dF = (r0n * vr0 / smu) * chi * (1 - alpha * chi * chi * S) + (1 - alpha * r0n) * chi * chi * C + r0n
        ratio = F / dF
        chi -= ratio
        if abs(ratio) < 1e-9:
            break
    z = alpha * chi * chi
    C, S = stumpff_c2(z), stumpff_c3(z)
    f = 1 - chi * chi / r0n * C
    g = dt - chi ** 3 / smu * S
    r = f * r0 + g * v0
    rn = float(np.linalg.norm(r))
    fdot = smu / (rn * r0n) * (alpha * chi ** 3 * S - chi)
    gdot = 1 - chi * chi / rn * C
    v = fdot * r0 + gdot * v0
    return r, v


def elements_to_state(a: float, e: float, inc: float, raan: float, argp: float, nu: float, mu: float):
    """Classical elements (radians) -> state in the reference frame of the elements."""
    p = a * (1 - e * e)
    r_pf = np.array([p * math.cos(nu) / (1 + e * math.cos(nu)), p * math.sin(nu) / (1 + e * math.cos(nu)), 0.0])
    v_pf = np.array([-math.sqrt(mu / p) * math.sin(nu), math.sqrt(mu / p) * (e + math.cos(nu)), 0.0])
    cO, sO, ci, si, cw, sw = math.cos(raan), math.sin(raan), math.cos(inc), math.sin(inc), math.cos(argp), math.sin(argp)
    Q = np.array([
        [cO * cw - sO * sw * ci, -cO * sw - sO * cw * ci, sO * si],
        [sO * cw + cO * sw * ci, -sO * sw + cO * cw * ci, -cO * si],
        [sw * si, cw * si, ci],
    ])
    return Q @ r_pf, Q @ v_pf
