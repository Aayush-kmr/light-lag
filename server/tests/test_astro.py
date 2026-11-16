"""Light time, conjunction, Lambert, porkchop and DSN geometry against known values."""
import numpy as np
import pytest

from lightlag.astro.conjunction import BLACKOUT_DEG, MORATORIUM_DEG, find_windows
from lightlag.astro.constants import C_KM_S, DAY_S
from lightlag.astro.dsn import DSS14_ITRF93_M, STATIONS, DSNModel
from lightlag.astro.lambert import lambert
from lightlag.astro.porkchop import transfer
from lightlag.astro.timescale import et_to_iso, utc_to_et
from lightlag.astro.trajectory import ShipTrajectory


# ------------------------------------------------------------- light time
# Published Earth-Mars close approaches (NASA): 2003-08-27 55.76 million km,
# 2018-07-31 57.6 million km, 2020-10-06 62.07 million km.
@pytest.mark.parametrize("utc,km", [("2003-08-27T09:51:00", 55.76e6), ("2018-07-31T07:50:00", 57.59e6),
                                    ("2020-10-06T14:18:00", 62.07e6)])
def test_close_approach_distances(ephem, utc, km):
    d = ephem.distance_km("EARTH", "MARS", utc_to_et(utc))
    assert abs(d - km) / km < 0.003


def test_light_time_matches_distance(ephem):
    et = utc_to_et("2003-08-27T09:51:00")
    lt = ephem.light_time("EARTH", "MARS", et)
    assert 185 < lt < 187.5                       # ~3.1 minutes at the 2003 record close approach


def test_light_time_spans_3_to_22_minutes(ephem):
    ets = np.arange(utc_to_et("2020-01-01"), utc_to_et("2022-03-01"), 5 * DAY_S)
    lts = [ephem.light_time("EARTH", "MARS", float(t)) for t in ets]
    assert 3.0 * 60 < min(lts) < 4.0 * 60
    assert 21.0 * 60 < max(lts) < 22.5 * 60


def test_kepler_and_spice_agree(kepler, spice):
    for utc in ("2020-11-29T15:35:00", "2021-10-08T00:00:00", "2026-01-09T00:00:00"):
        et = utc_to_et(utc)
        assert abs(kepler.light_time("EARTH", "MARS", et) - spice.light_time("EARTH", "MARS", et)) < 1.0


def test_uplink_and_downlink_differ(spice):
    tr = ShipTrajectory.from_dates(spice, utc_to_et("2020-07-30T12:00:00"), utc_to_et("2021-02-18T20:00:00"))
    spice.ship = tr
    spice.attach_ship(tr, utc_to_et("2021-03-01"), __import__("pathlib").Path("/tmp/lltest"))
    et = utc_to_et("2020-11-29T15:35:00")
    up = spice.light_time("EARTH", "SHIP", et)
    dn = spice.light_time("SHIP", "EARTH", et)
    d = spice.distance_km("EARTH", "SHIP", et)
    assert abs(up - d / C_KM_S) < 0.5 and abs(dn - d / C_KM_S) < 0.5
    assert up != dn                                   # the ship moves while light is in flight


# ------------------------------------------------------------- conjunction
def test_2021_mars_conjunction(ephem):
    """NASA's Mars fleet stood down Oct 2-16, 2021; conjunction ~Oct 7-8."""
    a, b = utc_to_et("2021-08-01"), utc_to_et("2021-12-01")
    blk = find_windows(ephem, a, b, BLACKOUT_DEG, target="MARS")
    mor = find_windows(ephem, a, b, MORATORIUM_DEG, target="MARS")
    assert len(blk) == 1 and len(mor) == 1
    w = blk[0].as_dict()
    assert w["min_iso"][:10] in ("2021-10-07", "2021-10-08")
    assert w["min_sep_deg"] < 1.0
    assert w["start_iso"][:10] in ("2021-10-01", "2021-10-02", "2021-10-03")    # NASA moratorium began Oct 2
    assert 10 <= w["duration_days"] <= 14
    assert mor[0].start_et < blk[0].start_et and mor[0].end_et > blk[0].end_et


def test_2026_conjunction_found(ephem):
    w = find_windows(ephem, utc_to_et("2025-11-01"), utc_to_et("2026-03-01"), BLACKOUT_DEG, target="MARS")
    assert len(w) == 1 and w[0].as_dict()["min_iso"].startswith("2026-01-")


# ------------------------------------------------------------- Lambert
def test_lambert_curtis_example_5_2():
    v1, v2 = lambert(np.array([5000.0, 10000, 2100]), np.array([-14600.0, 2500, 7000]), 3600.0, 398600.0)
    assert np.allclose(v1, [-5.9925, 1.9254, 3.2456], atol=2e-3)
    assert np.allclose(v2, [-3.3125, -4.1966, -0.38529], atol=2e-3)


def test_perseverance_like_transfer(ephem):
    """Perseverance: launch 2020-07-30, landing 2021-02-18; C3 ~14.5 km^2/s^2."""
    c = transfer(ephem, utc_to_et("2020-07-30T11:50:00"), utc_to_et("2021-02-18T20:55:00"))
    assert 13.0 < c.c3 < 16.0
    assert 2.0 < c.vinf_arr < 3.2
    assert abs(c.tof_days - 203.4) < 0.5


def test_trajectory_ends_at_mars(ephem):
    tr = ShipTrajectory.from_dates(ephem, utc_to_et("2020-07-30T12:00:00"), utc_to_et("2021-02-18T20:00:00"))
    r, _ = tr.helio_cruise_state(tr.arrival_et)
    assert np.linalg.norm(r - ephem.pos("MARS", tr.arrival_et)) < 50.0       # km


# ------------------------------------------------------------- DSN
def test_dss14_geodetic_matches_naif_itrf93():
    d = STATIONS[0].ecef_km() * 1000 - np.array(DSS14_ITRF93_M)
    assert np.linalg.norm(d) < 50.0                                          # metres


def test_three_complexes_cover_the_sky(kepler):
    tr = ShipTrajectory.from_dates(kepler, utc_to_et("2020-07-30T12:00:00"), utc_to_et("2021-02-18T20:00:00"))
    kepler.ship = tr
    m = DSNModel(kepler)
    t0 = utc_to_et("2020-11-29T00:00:00")
    vis = m.visibility(t0, t0 + DAY_S, step_s=600)
    hours = {cx: sum(iv["end"] - iv["start"] for iv in ivs) / 3600 for cx, ivs in vis.items()}
    assert all(5 < h < 16 for h in hours.values()), hours
    std = m.schedule(t0, t0 + DAY_S, "standard")
    assert 1 <= len(std) <= 4
    assert et_to_iso(std[0]["start"]).startswith("2020-11-")
