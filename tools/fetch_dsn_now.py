"""Save a real DSN Now snapshot so the ambient panel works offline.

    python -m tools.fetch_dsn_now
Writes data/cache/dsn_now/latest.xml (the server also refreshes it live
every 10 s when online). The panel always shows the snapshot's age.
"""
import sys
import time
import urllib.request
from pathlib import Path

URL = "https://eyes.nasa.gov/dsn/data/dsn.xml?r=" + str(int(time.time()))
DEST = Path(__file__).resolve().parents[1] / "data" / "cache" / "dsn_now" / "latest.xml"

if __name__ == "__main__":
    DEST.parent.mkdir(parents=True, exist_ok=True)
    body = urllib.request.urlopen(URL, timeout=15).read()
    DEST.write_bytes(body)
    print(f"saved {len(body)} bytes -> {DEST}")
    sys.exit(0)
