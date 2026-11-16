"""Cache raw NASA DONKI responses (FLR, CME, SEP) for offline storm replay.

    NASA_API_KEY=... python -m tools.fetch_donki 2017-09-01 2017-09-15
    python -m tools.fetch_donki 2020-07-01 2021-11-30      # the whole Meridian-1 mission

Responses are saved VERBATIM to data/cache/donki/{TYPE}_{start}_{end}.json.
The server parses them at startup; cached DONKI events take priority over
the literature reference file. Uses api.nasa.gov (DEMO_KEY works but is
rate-limited); falls back to the CCMC mirror, which needs no key.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path

DEST = Path(__file__).resolve().parents[1] / "data" / "cache" / "donki"
API = "https://api.nasa.gov/DONKI/{t}?startDate={a}&endDate={b}&api_key={k}"
CCMC = "https://kauai.ccmc.gsfc.nasa.gov/DONKI/WS/get/{t}?startDate={a}&endDate={b}"


def fetch(t: str, a: str, b: str) -> list:
    key = os.environ.get("NASA_API_KEY", "DEMO_KEY")
    for url in (API.format(t=t, a=a, b=b, k=urllib.parse.quote(key)), CCMC.format(t=t, a=a, b=b)):
        try:
            with urllib.request.urlopen(url, timeout=30) as r:
                body = r.read().decode()
            return json.loads(body) if body.strip() else []
        except Exception as e:
            print(f"  {t} via {url.split('/')[2]} failed: {e}")
    raise RuntimeError(f"could not fetch {t}")


def main() -> int:
    a, b = (sys.argv[1], sys.argv[2]) if len(sys.argv) > 2 else ("2017-09-01", "2017-09-15")
    DEST.mkdir(parents=True, exist_ok=True)
    for t in ("FLR", "CME", "SEP"):
        data = fetch(t, a, b)
        p = DEST / f"{t}_{a}_{b}.json"
        p.write_text(json.dumps(data, indent=1))
        print(f"{t}: {len(data)} records -> {p.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
