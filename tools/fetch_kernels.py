"""Download the NAIF SPICE kernels LIGHT-LAG uses into data/kernels/.

    python -m tools.fetch_kernels            # run once, with internet, before the event

Exact filenames (confirmed from NAIF kernel comments where noted):
  lsk/naif0012.tls                          leapseconds
  pck/pck00011.tpc                          planetary constants
  spk/planets/de440s.bsp                    JPL DE440 (short), ~32 MB
  spk/stations/earthstns_itrf93_201023.bsp  DSN station positions (confirmed: NAIF .cmt)
  fk/stations/earth_topo_201023.tf          DSN topocentric frames (confirmed: NAIF .cmt)
  pck/earth_latest_high_prec.bpc            Earth orientation - name NOT confirmed here;
                                            if it 404s, the script lists pck/ and picks the
                                            newest earth_*_*.bpc instead.
"""
from __future__ import annotations

import re
import sys
import urllib.request
from pathlib import Path

BASE = "https://naif.jpl.nasa.gov/pub/naif/generic_kernels/"
FILES = ["lsk/naif0012.tls", "pck/pck00011.tpc", "spk/planets/de440s.bsp",
         "spk/stations/earthstns_itrf93_201023.bsp", "fk/stations/earth_topo_201023.tf"]
OPTIONAL_BPC = "pck/earth_latest_high_prec.bpc"
DEST = Path(__file__).resolve().parents[1] / "data" / "kernels"


def get(rel: str) -> bool:
    """Fetch one kernel. NAIF moves superseded kernels into a_old_versions/,
    so try that next. A failed download never leaves a partial file behind
    (a broken kernel would make SPICE refuse to load)."""
    dst = DEST / Path(rel).name
    if dst.exists() and dst.stat().st_size > 0:
        print("have", dst.name)
        return True
    folder, name = rel.rsplit("/", 1)
    for url in (BASE + rel, f"{BASE}{folder}/a_old_versions/{name}"):
        try:
            print("fetch", url)
            urllib.request.urlretrieve(url, dst)
            if dst.stat().st_size > 0:
                return True
        except Exception as e:
            print("  failed:", e)
        if dst.exists():
            dst.unlink()
    return False


def newest_bpc() -> None:
    try:
        html = urllib.request.urlopen(BASE + "pck/").read().decode()
    except Exception as e:
        print("could not list pck/:", e)
        return
    names = sorted(set(re.findall(r'href="(earth_\d{6}_\d{6}_\d{6}\.bpc)"', html)))
    if names:
        get("pck/" + names[-1])


def main() -> int:
    DEST.mkdir(parents=True, exist_ok=True)
    results = [get(f) for f in FILES]          # try every file, even after a failure
    ok = all(results[:3])                     # LSK, PCK, DE440s are required; station kernels are optional
    if not get(OPTIONAL_BPC):
        newest_bpc()
    print("\nkernels in", DEST)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
