"""UTC <-> ephemeris time (ET = TDB seconds past J2000).

When a SPICE leapseconds kernel (naif0012.tls) is loaded we defer to SPICE.
Otherwise we use the IERS leap-second table below (TAI-UTC, from IERS
Bulletin C) and treat TDB ~= TT. The TDB-TT periodic term is under 2 ms,
far below anything a light-time game can show.
"""
from __future__ import annotations

from bisect import bisect_right
from datetime import datetime, timedelta, timezone

# (UTC date the offset takes effect, TAI-UTC seconds)
_LEAP_TABLE = [
    (datetime(1972, 1, 1), 10), (datetime(1972, 7, 1), 11), (datetime(1973, 1, 1), 12),
    (datetime(1974, 1, 1), 13), (datetime(1975, 1, 1), 14), (datetime(1976, 1, 1), 15),
    (datetime(1977, 1, 1), 16), (datetime(1978, 1, 1), 17), (datetime(1979, 1, 1), 18),
    (datetime(1980, 1, 1), 19), (datetime(1981, 7, 1), 20), (datetime(1982, 7, 1), 21),
    (datetime(1983, 7, 1), 22), (datetime(1985, 7, 1), 23), (datetime(1988, 1, 1), 24),
    (datetime(1990, 1, 1), 25), (datetime(1991, 1, 1), 26), (datetime(1992, 7, 1), 27),
    (datetime(1993, 7, 1), 28), (datetime(1994, 7, 1), 29), (datetime(1996, 1, 1), 30),
    (datetime(1997, 7, 1), 31), (datetime(1999, 1, 1), 32), (datetime(2006, 1, 1), 33),
    (datetime(2009, 1, 1), 34), (datetime(2012, 7, 1), 35), (datetime(2015, 7, 1), 36),
    (datetime(2017, 1, 1), 37),
]
_LEAP_DATES = [d for d, _ in _LEAP_TABLE]
_J2000_TT = datetime(2000, 1, 1, 12, 0, 0)
_TT_MINUS_TAI = 32.184

_spice_lsk_loaded = False


def set_spice_lsk_loaded(flag: bool) -> None:
    global _spice_lsk_loaded
    _spice_lsk_loaded = flag


def tai_minus_utc(utc_naive: datetime) -> int:
    i = bisect_right(_LEAP_DATES, utc_naive)
    return _LEAP_TABLE[i - 1][1] if i else 10


def _naive(dt: datetime) -> datetime:
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def utc_to_et(dt: datetime | str) -> float:
    if isinstance(dt, str):
        if _spice_lsk_loaded:
            import spiceypy as sp
            return float(sp.str2et(dt))
        dt = datetime.fromisoformat(dt.replace("Z", "+00:00"))
    dt = _naive(dt)
    if _spice_lsk_loaded:
        import spiceypy as sp
        return float(sp.str2et(dt.strftime("%Y-%m-%dT%H:%M:%S.%f")))
    return (dt - _J2000_TT).total_seconds() + tai_minus_utc(dt) + _TT_MINUS_TAI


def et_to_utc(et: float) -> datetime:
    """Returns a timezone-aware UTC datetime."""
    if _spice_lsk_loaded:
        import spiceypy as sp
        s = sp.et2utc(et, "ISOC", 3)
        return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)
    tai_naive = _J2000_TT + timedelta(seconds=et - _TT_MINUS_TAI)
    guess = tai_naive - timedelta(seconds=37)
    off = tai_minus_utc(guess)
    utc = tai_naive - timedelta(seconds=off)
    off2 = tai_minus_utc(utc)
    if off2 != off:
        utc = tai_naive - timedelta(seconds=off2)
    return utc.replace(tzinfo=timezone.utc)


def et_to_iso(et: float) -> str:
    return et_to_utc(et).strftime("%Y-%m-%dT%H:%M:%SZ")


def et_to_jd_utc(et: float) -> float:
    dt = et_to_utc(et).replace(tzinfo=None)
    return 2451545.0 + (dt - datetime(2000, 1, 1, 12)).total_seconds() / 86400.0
