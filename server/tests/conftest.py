import glob
import os
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
ROOT = HERE.parents[1]


def _find_spk():
    for p in [os.environ.get("LL_TEST_SPK"), os.environ.get("LL_EXTRA_SPK")] + sorted(glob.glob(str(ROOT / "data" / "kernels" / "de4*.bsp"))):
        if p and os.path.exists(p):
            return p
    try:  # skyfield-data ships de421.bsp (JPL) - handy where NAIF is unreachable
        import skyfield_data
        p = Path(skyfield_data.__file__).parent / "data" / "de421.bsp"
        if p.exists():
            return str(p)
    except ImportError:
        pass
    return None


SPK = _find_spk()


@pytest.fixture(scope="session")
def kepler():
    from lightlag.astro.ephemeris import EphemerisService
    return EphemerisService(Path("/nonexistent"), mode="kepler").load()


@pytest.fixture(scope="session")
def spice():
    if not SPK:
        pytest.skip("no SPICE planetary kernel available (run python -m tools.fetch_kernels or pip install skyfield-data)")
    os.environ["LL_EXTRA_SPK"] = SPK
    from lightlag.astro.ephemeris import EphemerisService
    return EphemerisService(ROOT / "data" / "kernels", mode="spice").load()


@pytest.fixture(params=["kepler", "spice"])
def ephem(request):
    return request.getfixturevalue(request.param)


def through_review(u, max_wall_s: float = 60.0) -> str:
    """Phase 4: all-GO now goes to the Flight Readiness Review first. Tick through
    the reveal (wall time only, no flight physics) and return the phase after it."""
    w = 0.0
    while u.phase == "review" and w < max_wall_s:
        u.tick(0.5)
        w += 0.5
        u.drain()
    return u.phase


@pytest.fixture(scope="session")
def judge_run():
    """One full Judge Mode mission (review included), shared by the Phase 4 tests.
    Returns the universe, every callout event the screens received, and wall time."""
    keep = {k: os.environ.get(k) for k in ("LL_JUDGE", "LL_REVIEW", "LL_CALLOUTS")}
    os.environ.update(LL_JUDGE="1", LL_REVIEW="1", LL_CALLOUTS="1")
    try:
        from lightlag.config import Config
        from lightlag.sim.universe import Universe
        u = Universe(Config())
    finally:
        for k, v in keep.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    u.vote("control", "GO")
    shown, wall, phases = [], 0.0, []
    while u.phase in ("review", "mission") and wall < 900:
        u.tick(0.1)
        wall += 0.1
        if not phases or phases[-1] != u.phase:
            phases.append(u.phase)
        for e in u.drain():
            if e["event"]["kind"] == "callout":
                shown.append({"audience": e["audience"], "wall": wall, **e["event"]["callout"]})
    return {"u": u, "shown": shown, "wall": wall, "phases": phases}


@pytest.fixture(scope="session", autouse=True)
def _clean_room_folders():
    """Rooms made by tests write under data/runtime/rooms/<code>/: remove the new ones."""
    import shutil
    base = ROOT / "data" / "runtime" / "rooms"
    before = set(p.name for p in base.iterdir()) if base.exists() else set()
    main = base / "MAIN"
    spks = set(main.glob("meridian1_*.bsp")) if main.exists() else set()
    yield
    if base.exists():
        for p in base.iterdir():
            if p.name not in before and p.name != "MAIN":
                shutil.rmtree(p, ignore_errors=True)
    for f in (set(main.glob("meridian1_*.bsp")) if main.exists() else set()) - spks:
        try:
            f.unlink()
        except OSError:
            pass
