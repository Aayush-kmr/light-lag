"""The design catalog: launch vehicles, instruments, power hardware, crew and
objectives, every number tied to a source or labelled MODEL.

Files live in data/catalog/*.yaml. `Catalog.check()` refuses a catalog whose
entries cite a source key that sources.yaml doesn't define, so a broken
citation fails the tests instead of reaching a judge.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

from .config import DATA

CATALOG_DIR = DATA / "catalog"
SEATS = ("control", "scientist", "ship", "engineer")
SIDE = {"control": "control", "scientist": "control", "ship": "ship", "engineer": "ship",
        "spectator": "spectator", "observer": "observer"}


def side_of(role: str) -> str:
    """Which knowledge store a seat reads: Earth seats see what Lantern knows,
    ship seats see what Meridian knows."""
    return SIDE.get(role, role)


@dataclass
class Catalog:
    sources: dict = field(default_factory=dict)
    vehicles: list = field(default_factory=list)
    instruments: list = field(default_factory=list)
    objectives: list = field(default_factory=list)
    solar_arrays: list = field(default_factory=list)
    batteries: list = field(default_factory=list)
    crew: dict = field(default_factory=dict)
    costs: dict = field(default_factory=dict)

    @classmethod
    def load(cls, root: Path = CATALOG_DIR) -> "Catalog":
        def y(name):
            return yaml.safe_load((root / name).read_text(encoding="utf-8")) or {}
        power = y("power.yaml")
        return cls(sources=y("sources.yaml"), vehicles=y("launch_vehicles.yaml")["vehicles"],
                   instruments=y("instruments.yaml")["instruments"], objectives=y("objectives.yaml")["objectives"],
                   solar_arrays=power["solar_arrays"], batteries=power["batteries"], crew=y("crew.yaml"),
                   costs=y("costs.yaml"))

    # ------------------------------------------------------------- lookups
    def get(self, kind: str, item_id: str) -> dict | None:
        return next((x for x in getattr(self, kind) if x["id"] == item_id), None)

    def crew_member(self, seat: str) -> dict | None:
        return next((c for c in self.crew["crew"] if c.get("seat") == seat), None)

    def cost(self, key: str) -> float:
        return float(self.costs[key]["value"])

    def value(self, *path):
        node = self.crew
        for p in path:
            node = node[p]
        return node["value"] if isinstance(node, dict) and "value" in node else node

    # ------------------------------------------------------- consumables
    def consumables_kg_per_cm_day(self) -> float:
        """Mass carried per crew member per day: everything eaten or used up,
        plus the make-up for what the life-support loop can't recover."""
        r = self.crew["consumables_per_cm_day"]
        v = lambda k: float(r[k]["value"])  # noqa: E731
        expendables = v("food_as_shipped") + v("wipes_gloves") + v("waste_hygiene") + v("ops_health_rec")
        water = (v("potable_water") + v("hygiene_water")) * (1.0 - float(self.value("water_recovery")))
        oxygen = v("oxygen") * (1.0 - float(self.value("oxygen_closure")))
        return expendables + water + oxygen

    def consumables_t(self, crew: int, days: float) -> float:
        days = days + float(self.value("contingency_days"))
        return crew * days * self.consumables_kg_per_cm_day() / 1000.0

    # -------------------------------------------------------------- checks
    def check(self) -> list[str]:
        """Every cited source must exist. Returns a list of problems (empty = OK)."""
        problems = []

        def walk(node, where):
            if isinstance(node, dict):
                for s in node.get("sources", []) or []:
                    if s not in self.sources:
                        problems.append(f"{where}: unknown source '{s}'")
                for k, v in node.items():
                    if k != "sources":
                        walk(v, f"{where}.{k}")
            elif isinstance(node, list):
                for i, v in enumerate(node):
                    walk(v, f"{where}[{node[i].get('id', i) if isinstance(v, dict) else i}]")
        for name in ("vehicles", "instruments", "objectives", "solar_arrays", "batteries", "crew", "costs"):
            walk(getattr(self, name), name)
        return problems

    def public(self) -> dict:
        return {"sources": self.sources, "vehicles": self.vehicles, "instruments": self.instruments,
                "objectives": self.objectives, "solar_arrays": self.solar_arrays, "batteries": self.batteries,
                "seats": self.crew["seats"], "crew": self.crew["crew"], "costs": self.costs,
                "consumables_kg_per_cm_day": round(self.consumables_kg_per_cm_day(), 3)}


@lru_cache(maxsize=1)
def catalog() -> Catalog:
    return Catalog.load()
