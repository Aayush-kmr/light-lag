"""Design-to-flight callouts: when an event in flight turned on a design choice,
the game says so, with the number and a counterfactual.

Rules
* Every number comes from the design record frozen at launch
  (`Universe.design_record`), from the flight itself, or from re-evaluating the
  same model formula with the other choice. Counterfactuals never re-run the
  simulation and are always labelled MODEL.
* Causality: a callout about something aboard is created in the ship's
  knowledge at the event time. Earth learns it only when the callout's own
  downlink bundle arrives (kind `callout`, through the normal network, so it
  waits for passes and never crosses the Sun). The spectator screen is
  omniscient and sees it at once.
* No spam: at most one callout per scene on any one screen (ship seats,
  Earth seats, spectator). Every callout stays in the ledger for the debrief.
* Templates live in data/story/callouts.yaml.
"""
from __future__ import annotations

import string

import yaml

from ..astro.constants import DAY_S
from ..astro.timescale import et_to_iso
from ..comms.bundle import NORMAL
from ..config import DATA
from .autonomy import fmt

SCREENS = {"ship": ("ship",), "control": ("control",), "spectator": ("spectator", "observer")}
ACTION = {"storm_attitude": "turn the propellant tanks to the Sun", "scrubber_cycle": "swap the CO₂ scrubber beds",
          "pump_failover": "switch coolant loops", "load_shed_science": "power down the science payload",
          "hga_repoint": "re-point the antenna"}
RULE = {"shelter_alert": "Storm alarm", "storm_attitude": "Tanks to the Sun", "scrubber_cycle": "CO₂ scrubber swap",
        "pump_failover": "Coolant loop switch", "load_shed_science": "Science load-shed", "hga_repoint": "Antenna re-point",
        "execute_uplink": "Run uplinked commands"}
PERM = {"auto": "Acts alone", "ask": "Asks first", "forbid": "Never"}


class _Safe(dict):
    def __missing__(self, k):
        return "{" + k + "}"


def _fmt(tpl: str | None, v: dict) -> str:
    return string.Formatter().vformat(tpl or "", (), _Safe(v))


def hours(s: float) -> str:
    h = s / 3600.0
    return f"{h:.0f} h" if h >= 10 else f"{h:.1f} h"


def pct(x: float) -> str:
    return f"{round(x * 100)}%"


class Callouts:
    def __init__(self, u):
        self.u = u
        self.defs = {d["id"]: d for d in yaml.safe_load((DATA / "story" / "callouts.yaml").read_text(encoding="utf-8"))["callouts"]}
        self.ledger: list[dict] = []        # truth: every callout, for the debrief
        self.shipk: list[dict] = []         # what Meridian knows
        self.earthk: list[dict] = []        # what Lantern knows (arrived only)
        self.used: dict[str, set] = {k: set() for k in SCREENS}
        self.fired: set[str] = set()
        self._auto_rules: set[str] = set()
        self._sci: set[float] = set()
        self._gap: dict | None = None
        self._seq = 0

    @property
    def enabled(self) -> bool:
        return bool(self.u.cfg.callouts) and self.u.design_record is not None

    @property
    def rec(self) -> dict:
        return self.u.design_record

    def _scene(self) -> str:
        sc = self.u.director.scene
        return sc["id"] if sc else "-"

    # ------------------------------------------------------------ core
    def fire(self, cid: str, variant: str | None = None, extra: str = "", numbers: dict | None = None,
             lien: str | None = None, event_et: float | None = None, **v) -> dict | None:
        if not self.enabled:
            return None
        u, d = self.u, self.defs[cid]
        self._seq += 1
        text = _fmt(d.get(f"template_{variant}") if variant else d["template"], v)
        if extra:
            text = f"{text} {extra}"
        # `day` is when the flight event happened (a storm is told after it has passed);
        # `et` is when the callout was made, which is what causality runs on.
        when = u.et if event_et is None else event_et
        day = (when - u.anchors["launch"]) / DAY_S if "launch" in u.anchors else 0.0
        e = {"id": f"c{self._seq:03d}", "trigger": cid, "side": d["side"], "seat": d.get("seat", "both"),
             "step": d.get("step", ""), "et": u.et, "iso": et_to_iso(u.et), "day": round(day, 1), "scene": self._scene(),
             "text": text, "choice": _fmt(d.get("choice"), v), "counter": _fmt(d.get("counter"), v),
             "model": True, "sources": list(d.get("sources") or []), "numbers": numbers or {}, "lien": lien,
             "show": d.get("show", True) is not False, "shown": {}}
        self.ledger.append(e)
        self.fired.add(cid)
        if d["side"] in ("ship", "both"):
            self.shipk.append(e)
            self._display(e, "ship")
        if d["side"] in ("earth", "both"):
            self.earthk.append({**e, "arrived_et": u.et, "lt_s": 0.0})
            self._display(e, "control")
        self._display(e, "spectator")
        if d["side"] == "ship" and u.net is not None:
            # Earth hears it like anything else from the ship: a bundle, a light time late.
            u.downlink("callout", {"label": "DESIGN TRACE", "entry": self._wire(e)}, NORMAL)
        return e

    @staticmethod
    def _wire(e: dict) -> dict:
        return {k: v for k, v in e.items() if k != "shown"}

    def public_entry(self, e: dict) -> dict:
        return {k: e[k] for k in ("id", "trigger", "side", "seat", "step", "et", "iso", "day", "scene", "text", "choice",
                                  "counter", "model", "sources", "lien") if k in e} | \
            ({"arrived_et": e["arrived_et"], "lt_s": e.get("lt_s")} if "arrived_et" in e else {})

    def _display(self, e: dict, screen: str) -> None:
        if not e["show"]:
            return
        sc = self._scene()
        if sc in self.used[screen]:
            e["shown"][screen] = False
            return
        self.used[screen].add(sc)
        e["shown"][screen] = True
        self.u.emit(SCREENS[screen], "callout", callout={**self.public_entry(e), "shown_scene": sc})

    def on_delivered(self, b) -> None:
        """A ship callout reached Lantern."""
        if b.kind != "callout" or b.state != "delivered":
            return
        entry = dict(b.payload.get("entry") or {})
        entry.update(arrived_et=self.u.et, lt_s=b.true_lt_s)
        entry["shown"] = {}
        self.earthk.append(entry)
        for e in self.ledger:
            if e["id"] == entry["id"]:
                e["earth_et"] = self.u.et
                if entry.get("show", True):
                    sc = self._scene()
                    if sc not in self.used["control"]:
                        self.used["control"].add(sc)
                        e["shown"]["control"] = True
                        self.u.emit(SCREENS["control"], "callout", callout={**self.public_entry(entry), "shown_scene": sc})
                    else:
                        e["shown"]["control"] = False

    def public(self, seat: str) -> list[dict]:
        from ..catalog import side_of
        side = side_of(seat)
        screen = side if side in ("ship", "control") else "spectator"
        src = self.shipk if side == "ship" else self.earthk if side == "control" else self.ledger
        # Only what this screen actually showed: at most one per scene. The rest waits for the debrief.
        shown = {e["id"] for e in self.ledger if e["shown"].get(screen)}
        return [self.public_entry(e) for e in src if e["id"] in shown][-20:]

    # ------------------------------------------------------------ triggers
    def on_decision(self, d) -> None:
        """TERN decided something. Power callouts first, so they take the scene's slot."""
        if not self.enabled:
            return
        u = self.u
        if d.rule == "load_shed_science" and d.action:
            self._power()
        if d.outcome == "broke_policy":
            self.fire("tern_rule_broken", rule=RULE.get(d.rule, d.rule), permission=PERM.get(d.permission, d.permission),
                      rtt=fmt(d.round_trip_s), tth=fmt(d.time_to_harm_s),
                      numbers={"round_trip_s": d.round_trip_s, "time_to_harm_s": d.time_to_harm_s})
        elif d.outcome == "executed" and d.permission == "auto" and d.rule in ACTION and d.rule not in self._auto_rules:
            rtt = d.round_trip_s or 0.0
            if rtt > 60:
                self._auto_rules.add(d.rule)
                self.fire("tern_acted_alone", action=ACTION[d.rule], lead=fmt(u.lt("down")), rule=RULE.get(d.rule, d.rule),
                          rtt=fmt(rtt), numbers={"lt_down_s": u.lt("down"), "round_trip_s": rtt})

    def _power(self) -> None:
        u, rec = self.u, self.rec
        S = u.ship_sys
        p = rec["derived"]["power"]
        names = rec["names"]
        large = u.catalog.get("solar_arrays", "array_large")
        lien = next((L for L in rec["liens"] if L["id"] == "power"), None)
        if lien and "lien_true" in self.fired:
            lien = None                      # the board's line is said once
        extra = "The board flagged this at review." if lien else ""
        if "array_string_loss" not in S.faults:
            if "power_distance" in self.fired:
                return
            # Every system healthy, and still short: Mars is farther than the mean distance.
            import numpy as np
            from ..astro.constants import AU_KM
            r = float(np.linalg.norm(u.ephem.pos("SHIP", u.et))) / AU_KM
            supply = rec["derived"]["array_kw_1au"] / r ** 2
            load = getattr(S, "_load", p["load_kw"])
            alt = float(large["kw_at_1au"]) / r ** 2 - load
            self.fire("power_distance", extra=extra, lien=lien["id"] if lien else None, margin=pct(p["margin"]),
                      r=f"{r:.2f}", supply=f"{supply:.1f}", load=f"{load:.1f}", array=names["array"], alt_spare=f"{alt:.1f}",
                      numbers={"margin": p["margin"], "r_au": round(r, 3), "supply_kw": round(supply, 2), "load_kw": round(load, 2),
                               "alt_spare_kw": round(alt, 2)})
            if lien:
                self.fire("lien_true", lien=lien["id"], lien_text=lien["text"], numbers={"lien": lien["id"]})
            return
        if "power_string" in self.fired:
            return
        alt_spare = self.degraded_supply(large) - p["load_kw"]
        self.fire("power_string", extra=extra, lien=lien["id"] if lien else None, margin=pct(p["margin"]),
                  degraded=f"{p['degraded_kw']:.1f}", load=f"{p['load_kw']:.1f}", array=names["array"],
                  alt_spare=f"{alt_spare:.1f}",
                  numbers={"margin": p["margin"], "degraded_kw": p["degraded_kw"], "load_kw": p["load_kw"], "alt_spare_kw": round(alt_spare, 2)})
        t = S.faults["array_string_loss"]["t"]
        kwh = rec["derived"]["batt_kwh"]
        self.fire("battery_bridge", kwh=f"{kwh:.0f}", hours=hours(t), battery=names["battery"],
                  alt_hours=hours(t * 90.0 / kwh), low_hours=hours(t * 40.0 / kwh),
                  numbers={"batt_kwh": kwh, "bridge_s": t, "alt_90_s": t * 90.0 / kwh, "alt_40_s": t * 40.0 / kwh})
        if lien:
            self.fire("lien_true", lien=lien["id"], lien_text=lien["text"], numbers={"lien": lien["id"]})

    def degraded_supply(self, array: dict) -> float:
        """Array output at Mars one string short (the flight model's own formula)."""
        from .design import MARS_AU
        frac = float(array.get("string_loss_frac", 0.18))
        return float(array["kw_at_1au"]) / MARS_AU ** 2 * (1.0 - frac)

    def on_surge(self) -> None:
        if not self.enabled or "emergency_pass" in self.fired:
            return
        rec = self.rec
        h = float(rec["derived"]["plan_hours"])
        free = max(0.0, 24.0 - h)
        dsn = next((x for x in rec["eval"]["cost"]["lines"] if x["id"] == "dsn"), {"musd": 0.0})
        extra = dsn["musd"] / max(h, 1e-6) * (24.0 - h)
        self.fire("emergency_pass", variant="full" if free <= 0 else None, plan=rec["choices"]["comms_plan"],
                  free=f"{free:.0f}", hours=f"{h:.0f}", dsn_extra=f"{extra:,.0f}",
                  numbers={"plan_hours": h, "free_hours": free, "dsn_extra_musd": round(extra, 1)})

    def on_frame(self) -> None:
        """Called from every telemetry frame: geometry-driven callouts."""
        if not self.enabled:
            return
        u, a = self.u, self.u.anchors
        if "conjunction" not in self.fired and "moratorium_start" in a and u.et >= a["moratorium_start"]:
            self._conjunction()
        if self._gap is None and "blackout_start" in a and a["blackout_start"] <= u.et < a["blackout_end"]:
            t = u.science.totals()
            self._gap = {"gen": t["generated_mbit"], "lost": t["lost_mbit"]}
        if self._gap is not None and "recorder_gap" not in self.fired and u.et >= a.get("blackout_end", 1e30):
            self._recorder()

    def _conjunction(self) -> None:
        from . import design as bay
        u, rec = self.u, self.rec
        stay = u.mass.stay_days
        dark = int(rec["derived"]["dark_days"])
        alt = sum(1 for r in bay.downlink_rates(u.ephem, rec["choices"]["arrival_et"] - 30 * DAY_S, stay) if r == 0.0)
        self.fire("conjunction", dark=dark, arrival=et_to_iso(rec["choices"]["arrival_et"])[:10], stay=f"{stay:.0f}",
                  alt_dark=alt, numbers={"dark_days": dark, "alt_dark_days": alt})

    def _recorder(self) -> None:
        u, rec = self.u, self.rec
        t = u.science.totals()
        gen = (t["generated_mbit"] - self._gap["gen"]) / 1000.0
        lost = (t["lost_mbit"] - self._gap["lost"]) / 1000.0
        C = u.catalog
        insts = [C.get("instruments", i) for i in rec["derived"]["payload"]]
        demand = rec["derived"]["demand_mbit_day"] / 1000.0
        hirise = (C.get("instruments", "hirise_class") or {"data_mbit_day": 8000})["data_mbit_day"] / 1000.0
        payload = ", ".join(rec["names"]["payload"]) or "none"
        if lost > 0.05:
            big = max(insts, key=lambda i: i["data_mbit_day"]) if insts else {"name": "biggest instrument", "data_mbit_day": 0}
            counter = f"Without the {big['name'].lower()} ({big['data_mbit_day'] / 1000:.1f} Gbit a day) the payload would make {demand - big['data_mbit_day'] / 1000:.1f} Gbit a day instead of {demand:.1f} (MODEL)."
            self.fire("recorder_gap", lost=f"{lost:.1f}", biggest=big["name"].lower(), payload=payload, counter=counter,
                      numbers={"lost_gbit": round(lost, 2), "gen_gbit": round(gen, 2)})
        else:
            fill = 160.0 / max(hirise + demand, 1e-6)
            counter = f"With a high-resolution camera added, the payload would make {demand + hirise:.1f} Gbit a day instead of {demand:.1f} (MODEL)."
            self.fire("recorder_gap", variant="held", gen=f"{gen:.1f}", fill_days=f"{fill:.0f}", payload=payload, counter=counter,
                      numbers={"lost_gbit": 0.0, "gen_gbit": round(gen, 2), "fill_days_hirise": round(fill, 1)})

    def on_science(self) -> None:
        """Earth's delivered science crossed a milestone of the objective."""
        if not self.enabled:
            return
        u, rec = self.u, self.rec
        st = u.science.objective_status()
        for m in (0.5, 1.0):
            if m in self._sci or st["progress"] < m:
                continue
            self._sci.add(m)
            obj = rec["choices"]["objective"]
            serves = {i: float((u.catalog.get("instruments", i) or {}).get("serves", {}).get(obj, 0.0)) for i in rec["derived"]["payload"]}
            if not serves:
                return
            lead = max(serves, key=serves.get)
            cov = sum(serves.values())
            name = u.catalog.get("instruments", lead)["name"]
            self.fire("science_milestone", lead_inst=f"The {name.lower()} and the rest of the payload" if len(serves) > 1 else f"The {name.lower()}",
                      pct=round(m * 100), objective=rec["names"]["objective"].lower(), payload=", ".join(rec["names"]["payload"]),
                      alt_cov=f"{cov - serves[lead]:.1f}", cov=f"{cov:.1f}",
                      numbers={"progress": st["progress"], "coverage": cov, "alt_coverage": round(cov - serves[lead], 2)})
            break

    def on_coast_end(self) -> None:
        """Between scenes, after the time skip, before the next scene starts."""
        if not self.enabled:
            return
        u = self.u
        if "storm_shelter" not in self.fired and u.storm is not None and "storm_peak" in u.flags and u.rad.sep_msv > 0.05:
            self._storm()
        self.on_science()

    def _storm(self) -> None:
        u, rec = self.u, self.rec
        R = u.rad
        sh, li = float(rec["choices"]["shelter_gcm2"]), float(rec["choices"]["hull_liner_gcm2"])
        dose = R.sep_msv
        without = R.sep_counterfactual(shelter_gcm2=0.0)
        alt_sh = min(40.0, sh + 10.0)
        alt = R.sep_counterfactual(shelter_gcm2=alt_sh)
        nums = {"dose_msv": round(dose, 2), "without_msv": round(without, 2), "alt_shelter_gcm2": alt_sh, "alt_msv": round(alt, 2),
                "raw_in_msv": round(R.sep_raw_in_msv, 3), "raw_out_msv": round(R.sep_raw_out_msv, 3)}
        common = dict(shelter=f"{sh:.0f}", liner=f"{li:.0f}", alt_shelter=f"{alt_sh:.0f}", alt_dose=f"{alt:.1f}", numbers=nums,
                      event_et=u.storm.t_onset_ship)
        if R.sep_raw_in_msv <= 1e-9:
            import math
            inside = R.sep_raw_out_msv * math.exp(-(li + sh) / 12.0)
            self.fire("storm_shelter", variant="outside", dose=f"{dose:.1f}", inside=f"{inside:.1f}", **common)
        else:
            self.fire("storm_shelter", shelter_t=f"{rec['derived']['shelter_t']:.1f}", dose=f"{dose:.1f}",
                      without=f"{without:.1f}", **common)

    def on_finish(self) -> None:
        if not self.enabled:
            return
        u, rec = self.u, self.rec
        st = u.science.objective_status()
        need = st["min_data_gbit"]
        delivered = st["relevant_gbit"]
        self.fire("science_result", verdict="Objective met" if st["met"] else "Objective missed",
                  delivered=f"{delivered:,.1f}", need=f"{need:,.0f}", objective=rec["names"]["objective"].lower(),
                  expected=f"{rec['eval']['data']['returned_gbit']:,.0f}",
                  numbers={"delivered_gbit": delivered, "need_gbit": need, "met": st["met"]})
