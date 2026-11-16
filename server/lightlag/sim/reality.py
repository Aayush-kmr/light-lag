"""Upgrade B (Phase 6): "Meanwhile, in reality".

The default design flies the real 2020 window on the same days as NASA's
Mars 2020 mission. When the game clock passes the date of a real event
(data/story/reality.yaml, each one checked on a NASA or mission-agency page),
every screen in the room shows it once, as a quiet line. Never scored.

It is a note to the players about the real world, not in-world knowledge, so it
carries no light delay. Delivery follows the game clock, per room, in date
order, at most one line per scene so it never crowds the callouts. Events that
fall inside a coast between scenes arrive at the next scene as one short
"while you were coasting" list. Only events inside the team's own mission window
(launch day to mission end) ever fire.
"""
from __future__ import annotations

from datetime import datetime, timezone

import yaml

from ..astro.timescale import et_to_utc, utc_to_et
from ..config import DATA

AUDIENCE = ("control", "ship", "spectator", "observer")


def load() -> dict:
    return yaml.safe_load((DATA / "story" / "reality.yaml").read_text(encoding="utf-8"))


class Reality:
    def __init__(self, u):
        self.u = u
        self.on = bool(getattr(u.cfg, "reality", True))
        y = load()
        self.events = sorted(({**e, "et": utc_to_et(e["utc"])} for e in y["events"]), key=lambda e: e["et"])
        self.perseverance = y.get("perseverance", {})
        self.window: tuple[float, float] | None = None
        self.queue: list[dict] = []           # in the window, not shown yet, in date order
        self.shown: list[dict] = []           # every line as delivered (items carry their delivery)
        self.scene_id: str | None = None
        self.scene_used = False               # a line already went out in this scene
        self.compare: str | None = None

    # ----------------------------------------------------------- lifecycle
    def start(self) -> None:
        """At launch: the mission window is launch day (UTC) to the mission's end."""
        if not self.on:
            return
        a = self.u.anchors
        day = et_to_utc(a["launch"]).astimezone(timezone.utc).date()
        start = utc_to_et(datetime(day.year, day.month, day.day, tzinfo=timezone.utc))
        self.window = (start, a["mission_end"])
        self.queue = [e for e in self.events if start <= e["et"] <= a["mission_end"]]
        # If the team picked other dates than Perseverance's, keep the comparison visible.
        p = self.perseverance
        d = self.u.design
        same = p and abs(d["launch_et"] - utc_to_et(p["launch"] + "T12:00:00")) < 86400 \
            and abs(d["arrival_et"] - utc_to_et(p["arrival"] + "T12:00:00")) < 86400
        self.compare = None if same or not p else p.get("line")

    def on_scene(self, sid: str, coast_from: float | None) -> None:
        """A scene starts. Anything due that fell inside the coast comes now as one list."""
        if not self.on or self.window is None:
            return
        self.scene_id, self.scene_used = sid, False
        due = self._due()
        if not due:
            return
        coasted = coast_from is not None and any(e["et"] > coast_from for e in due)
        if coasted and len(due) > 1:
            self._deliver(due, coasted=True)
        else:
            self._deliver(due[:1], coasted=coasted)

    def check(self) -> None:
        """Every tick in flight: at most one line per scene, the earliest due first."""
        if not self.on or self.window is None or self.scene_used or self.u.director.scene is None:
            return
        if self.queue and self.queue[0]["et"] <= self.u.et:
            self._deliver(self._due()[:1], coasted=False)

    # ------------------------------------------------------------- helpers
    def _due(self) -> list[dict]:
        return [e for e in self.queue if e["et"] <= self.u.et]

    def _deliver(self, items: list[dict], coasted: bool) -> None:
        if not items:
            return
        ids = {e["id"] for e in items}
        self.queue = [e for e in self.queue if e["id"] not in ids]
        self.scene_used = True
        u = self.u
        pub = [self._item(e) for e in items]
        for p in pub:
            p.update(scene=self.scene_id, shown_et=u.et)
            self.shown.append(p)
        msg = {"items": pub, "coasted": coasted, "batch": len(pub) > 1, "scene": self.scene_id}
        u.note("both", "reality", "Meanwhile, in reality: " + " / ".join(f"{p['date_label']}: {p['text']}" for p in pub))
        u.emit(AUDIENCE, "reality", reality=msg)

    @staticmethod
    def _item(e: dict) -> dict:
        from ..catalog import catalog
        src = catalog().sources.get(e["source"], {})
        return {"id": e["id"], "utc": e["utc"], "date_label": e["date_label"], "text": e["text"],
                "source": e["source"], "source_title": src.get("title"), "url": src.get("url")}

    def public(self) -> dict | None:
        if not self.on:
            return None
        return {"shown": self.shown[-8:], "compare": self.compare, "pending": len(self.queue)}
