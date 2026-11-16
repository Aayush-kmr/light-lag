"""Space-weather physics, radiation model, voice filter, and a full Judge Mode run."""
import math
import os

import numpy as np
import pytest

from lightlag.astro.timescale import utc_to_et
from lightlag.sim.radiation import GCR_MSV_PER_DAY, Radiation
from lightlag.sim.space_weather import beta_for, footpoint_offset_deg, parker_length_au
from conftest import through_review


def test_parker_spiral_at_1au():
    """400 km/s wind: field line ~1.17 AU long, footpoint ~60 deg west."""
    assert 1.12 < parker_length_au(1.0, 400) < 1.22
    assert 55 < footpoint_offset_deg(1.0, 400) < 66


def test_proton_speeds():
    assert 0.75 < beta_for(500) < 0.77 and 0.27 < beta_for(40) < 0.29


def test_gle72_onset_at_earth(kepler):
    """Model check vs observation: SOL2017-09-10 protons reached 1 AU at
    16:06-16:08 UT (Kurt et al.). Our model, at the real date, should land within 10 minutes."""
    from lightlag.config import DATA
    from lightlag.sim.space_weather import SpaceWeather
    sw = SpaceWeather(DATA, DATA / "cache")
    ev = sw.find("2017-09-10-X8.2")
    tl = sw.timeline(kepler, ev)
    observed = utc_to_et("2017-09-10T16:07:00")
    assert abs(tl.t_onset_earth - observed) < 600


def test_gcr_baseline_and_shielding_asymmetry():
    r0 = Radiation(hull_liner_gcm2=0, shelter_gcm2=20)
    r1 = Radiation(hull_liner_gcm2=10, shelter_gcm2=20)
    r0.coast(1.0)
    assert math.isclose(r0.gcr_msv, GCR_MSV_PER_DAY, rel_tol=1e-9)        # Zeitlin et al. 2013
    assert 0.85 < r1.gcr_factor < 0.9                                     # GCR barely cares
    assert r0.sep_factor(True) < 0.25                                     # shelter cuts SEP several-fold


def test_radio_filter_adds_quindar():
    from lightlag.voice.radio import SR, radio_filter
    x = np.random.default_rng(0).standard_normal(SR).astype(np.float32) * 0.1
    y = radio_filter(x)
    assert len(y) > len(x) + int(0.5 * SR)                               # two 250 ms tones added
    spec = np.abs(np.fft.rfft(y[int(0.3 * SR):-int(0.3 * SR)]))
    f = np.fft.rfftfreq(len(y) - int(0.6 * SR), 1 / SR)
    assert spec[f > 5000].mean() < 0.2 * spec[(f > 500) & (f < 2500)].mean()


@pytest.mark.slow
@pytest.mark.parametrize("crew_ai", ["1", "0"])
def test_judge_mode_end_to_end(monkeypatch, crew_ai):
    monkeypatch.setenv("LL_JUDGE", "1")
    monkeypatch.setenv("LL_CREW_AI", crew_ai)                              # Phase 5: with and without crew AI
    from lightlag.config import Config
    from lightlag.sim.universe import Universe
    u = Universe(Config())
    u.vote("control", "GO")
    u.vote("ship", "GO")
    assert u.phase == "review"                                             # Phase 4: the board first
    wall = 0.0
    while u.phase == "review" and wall < 60:                               # the reveal counts toward the 8 minutes
        u.tick(0.1)
        wall += 0.1
        u.drain()
    assert u.phase == "mission"
    while u.phase == "mission" and wall < 900:
        u.tick(0.1)
        wall += 0.1
        u.drain()
    assert u.phase == "debrief"
    s = u.after_action()["score"]
    assert s["objectives_pct"] == 100.0
    assert s["shelter_before_onset"]
    assert s["policy_breaks"] >= 1                                         # the blackout forces a decision
    assert len([L for L in u.letters if L.get("flood")]) >= 11            # the flood
    assert wall < 8 * 60                                                   # fits the 8-minute Judge Mode
    assert u.science.objective_status()["met"]                             # the retuned need is still met
