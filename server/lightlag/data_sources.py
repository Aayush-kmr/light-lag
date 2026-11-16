"""Live / cached NASA data used at runtime.

* DSN Now (https://eyes.nasa.gov/dsn/data/dsn.xml, ~5 s refresh): which real
  spacecraft each real Deep Space Network dish is talking to right now.
  Shown as an ambient panel in Lantern. Offline -> last cached snapshot,
  clearly marked with its age; never a fabricated one.
"""
from __future__ import annotations

import time
import xml.etree.ElementTree as ET
from pathlib import Path

DSN_NOW_URL = "https://eyes.nasa.gov/dsn/data/dsn.xml"
FRIENDLY = {"gdscc": "Goldstone", "mdscc": "Madrid", "cdscc": "Canberra"}


def parse_dsn_xml(text: str) -> dict:
    """Tolerant parser: <station> elements are siblings followed by their <dish> elements."""
    root = ET.fromstring(text)
    stations, current = [], None
    for el in root:
        if el.tag == "station":
            current = {"name": el.get("name"), "friendly": el.get("friendlyName") or FRIENDLY.get(el.get("name"), el.get("name")),
                       "dishes": []}
            stations.append(current)
        elif el.tag == "dish" and current is not None:
            targets = [t.get("name") for t in el.findall("target") if t.get("name")]
            down = [{"spacecraft": s.get("spacecraft"), "rate_bps": _f(s.get("dataRate")), "band": s.get("band"),
                     "active": s.get("active") == "true"} for s in el.findall("downSignal")]
            up = [{"spacecraft": s.get("spacecraft"), "band": s.get("band"), "active": s.get("active") == "true"}
                  for s in el.findall("upSignal")]
            rtlt = [_f(t.get("rtlt")) for t in el.findall("target")]
            current["dishes"].append({"name": el.get("name"), "az": _f(el.get("azimuthAngle")),
                                      "el": _f(el.get("elevationAngle")), "targets": targets, "down": down, "up": up,
                                      "rtlt_s": [r for r in rtlt if r and r > 0]})
    ts = root.find("timestamp")
    return {"stations": stations, "timestamp_ms": int(ts.text) if ts is not None and ts.text else None}


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


class DSNNow:
    def __init__(self, cache_dir: Path, offline: bool):
        self.cache = Path(cache_dir) / "dsn_now" / "latest.xml"
        self.offline = offline
        self.data: dict | None = None
        self.fetched_at: float | None = None
        self.source = "none"
        self._load_cache()

    def _load_cache(self) -> None:
        if self.cache.exists():
            try:
                self.data = parse_dsn_xml(self.cache.read_text())
                self.fetched_at = self.cache.stat().st_mtime
                self.source = "cached snapshot"
            except ET.ParseError:
                self.data = None

    async def refresh(self) -> None:
        if self.offline:
            return
        try:
            import httpx
            async with httpx.AsyncClient(timeout=4.0) as c:
                r = await c.get(DSN_NOW_URL, params={"r": int(time.time())})
            r.raise_for_status()
            self.data = parse_dsn_xml(r.text)
            self.fetched_at = time.time()
            self.source = "live"
            self.cache.parent.mkdir(parents=True, exist_ok=True)
            self.cache.write_text(r.text)
        except Exception:
            if self.source == "live":
                self.source = "cached snapshot (live feed lost)"

    def public(self) -> dict:
        if not self.data:
            return {"source": "unavailable", "note": "No live feed and no cached snapshot. Run: python -m tools.fetch_dsn_now"}
        age = time.time() - self.fetched_at if self.fetched_at else None
        return {"source": self.source, "age_s": round(age) if age is not None else None, **self.data,
                "attribution": "NASA/JPL-Caltech, DSN Now (eyes.nasa.gov/dsn)"}
