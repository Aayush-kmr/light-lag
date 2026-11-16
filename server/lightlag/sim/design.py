"""The design bay: one pure evaluation of a mission design.

Modelled on a concurrent-engineering session (JPL Team X, ESA CDF): every
choice moves real numbers, and the team must close four budgets (mass,
power, data and cost) with margin before it may launch.

`evaluate()` takes the design and everything it depends on as arguments and
returns plain JSON, so the design UI, the go/no-go vote and the later Review
Board all read the same numbers without recomputing them. Every number comes
from the catalog (with its source keys) or is flagged MODEL.
"""
from __future__ import annotations

import math

from ..astro.conjunction import BLACKOUT_DEG
from ..astro.constants import AU_KM, DAY_S
from ..astro.dsn import COMMS_PLANS
from ..comms.contact_plan import ContactPlan
from .subsystems import Env, ShipSystems

# MODEL: margin bands. Green leaves room for growth (10% is the usual
# concurrent-engineering floor), yellow is closed with no room, red is open.
BAND_GREEN = 0.10
# MODEL: launches the campaign can assemble and dock in LEO before the
# departure window closes.
LAUNCH_LIMIT = 4
# Mars mean distance from the Sun (semimajor axis), NASA Mars Fact Sheet.
MARS_AU = 1.524
# MODEL: science fit. One full instrument's worth of `serves` covers an objective.
COVERS_AT, PARTIAL_AT = 1.0, 0.5

BUDGETS = ("mass", "power", "data", "cost")


def band(margin: float) -> str:
    return "red" if margin < 0 else "yellow" if margin < BAND_GREEN else "green"


def plan_hours(plan: str) -> float:
    """Scheduled antenna hours per day, from the same plan table the DSN scheduler uses."""
    cfg = COMMS_PLANS[plan]
    return min(24.0, cfg["passes_per_day"] * cfg["hours"])


# ------------------------------------------------------------ contact info
def downlink_rates(ephem, arrival_et: float, stay_days: float) -> list[float]:
    """Downlink rate (bit/s) for each day of the stay at Mars, from the contact
    plan's own link model at the true Earth-Mars distance. Days behind the Sun
    (SEP below the blackout threshold) carry nothing."""
    hga = ShipSystems().hga_err
    rates = []
    for k in range(int(stay_days)):
        et = arrival_et + (k + 0.5) * DAY_S
        sep = ephem.sep_angle_deg(et, "MARS")
        if sep < BLACKOUT_DEG:
            rates.append(0.0)
            continue
        d_au = ephem.distance_km("EARTH", "MARS", et) / AU_KM
        rates.append(ContactPlan.data_rate_bps(ContactPlan.link_margin_db(d_au, sep, hga)))
    return rates


def contact_info(ephem, arrival_et: float, stay_days: float) -> dict:
    return {"rates_bps": downlink_rates(ephem, arrival_et, stay_days),
            "hours": {p: plan_hours(p) for p in COMMS_PLANS}, "stay_days": stay_days}


# ------------------------------------------------------------------ pieces
def _mass(design: dict, cell, catalog, mm) -> dict:
    tof = cell.tof_days
    base = mm.payload_t(0.0, 0.0, False, tof)
    instruments = [catalog.get("instruments", i) for i in design["payload"]]
    array, batt = catalog.get("solar_arrays", design["array"]), catalog.get("batteries", design["battery"])
    lines = [
        {"id": "habitat", "label": "Habitat and systems", "t": mm.habitat_dry_t, "model": True},
        {"id": "consumables", "label": f"Consumables, crew of {mm.crew}", "t": mm.consumables_for(tof),
         "sources": ["bvad_rev1", "ascend_2023_logistics"]},
        {"id": "shelter", "label": "Storm shelter water", "t": mm.payload_t(design["shelter_gcm2"], 0.0, False, tof) - base, "model": True},
        {"id": "liner", "label": "Hull liner", "t": mm.payload_t(0.0, design["hull_liner_gcm2"], False, tof) - base, "model": True},
        {"id": "monitor", "label": "Solar flare monitor", "t": mm.solar_monitor_t if design["solar_monitor"] else 0.0, "model": True},
        {"id": "instruments", "label": "Science instruments", "t": sum(i["mass_kg"] for i in instruments) / 1000.0,
         "sources": sorted({s for i in instruments for s in i.get("sources", [])})},
        {"id": "power", "label": "Solar array and battery", "t": array["mass_t"] + batt["mass_t"],
         "sources": ["sst_soa_power"], "model": True},
    ]
    payload = sum(x["t"] for x in lines)
    imleo = mm.imleo_t(cell.dv_tmi, cell.dv_moi, payload)
    lines.append({"id": "propellant", "label": "Propellant and tanks", "t": imleo - payload, "model": True,
                  "note": f"Tsiolkovsky, Isp {mm.isp_s:.0f} s, {mm.tank_fraction:.0%} tankage"})
    for x in lines:
        x["t"] = round(x["t"], 3)
    return {"lines": lines, "payload_t": round(payload, 2), "imleo_t": round(imleo, 1),
            "propellant_t": round(imleo - payload, 2), "consumables_t": round(mm.consumables_for(tof), 2),
            "instruments_kg": round(sum(i["mass_kg"] for i in instruments), 2), "crew": mm.crew}


def _campaign(imleo_t: float, vehicle: dict) -> dict:
    n = max(1, math.ceil(imleo_t / vehicle["leo_t"] - 1e-9))
    return {"vehicle": vehicle["id"], "name": vehicle["name"], "leo_t": vehicle["leo_t"], "price_musd": vehicle["price_musd"],
            "launches": n, "limit": LAUNCH_LIMIT, "cost_musd": n * vehicle["price_musd"],
            "capacity_t": round(LAUNCH_LIMIT * vehicle["leo_t"], 1), "sources": vehicle.get("sources", []),
            "text": f"{n} × {vehicle['name']}, ${n * vehicle['price_musd']:,} M"}


def _power(design: dict, catalog) -> dict:
    """Supply and load at Mars, measured by running the flight model itself one
    step at Mars distance, so the design numbers can never drift from the sim."""
    array, batt = catalog.get("solar_arrays", design["array"]), catalog.get("batteries", design["battery"])
    instruments_w = sum(catalog.get("instruments", i)["power_w"] for i in design["payload"])

    frac = float(array.get("string_loss_frac", 0.18))

    def probe(*faults, science_on=True):
        s = ShipSystems(array_kw_1au=float(array["kw_at_1au"]), batt_kwh=float(batt["kwh"]), string_loss_frac=frac,
                        science_on=science_on)
        for f in faults:
            s.inject(f)
        s.step(1.0, Env(r_au=MARS_AU))
        return s._solar, s._load
    supply, ship_load = probe()
    degraded, _ = probe("array_string_loss")
    # The review board's degraded case: one string lost and the payload shed. What is
    # left is life support, heaters and systems.
    _, essential = probe("array_string_loss", science_on=False)
    load = ship_load + instruments_w / 1000.0
    margin = (supply - load) / supply
    return {"supply_kw": round(supply, 2), "ship_load_kw": round(ship_load, 2), "instruments_w": round(instruments_w, 1),
            "load_kw": round(load, 2), "margin": round(margin, 4), "short_kw": round(max(0.0, load - supply), 2),
            "string_loss_kw": round(degraded - load, 2), "batt_kwh": float(batt["kwh"]),
            "degraded_kw": round(degraded, 2), "essential_kw": round(essential, 2), "string_loss_frac": frac,
            "life_support_kw": round(degraded - essential, 2),
            "reserve_h": round(float(batt["kwh"]) / load, 1), "r_au": MARS_AU, "array": array["id"], "battery": batt["id"],
            "sources": ["nssdc_mars_fact", "sst_soa_power"]}


def _data(design: dict, catalog, info: dict) -> dict:
    rates = info["rates_bps"]
    days = max(1, len(rates))
    demand = sum(catalog.get("instruments", i)["data_mbit_day"] for i in design["payload"])

    def capacity(plan):          # Mbit per day, averaged over the stay
        return sum(rates) / days * info["hours"][plan] * 3600.0 / 1e6
    by_plan = {}
    for p in info["hours"]:
        c = capacity(p)
        m = (c - demand) / c if c > 0 else -1.0
        by_plan[p] = {"capacity_mbit_day": round(c), "margin": round(m, 4), "band": band(m), "hours": info["hours"][p]}
    plan = design["comms_plan"]
    h = info["hours"][plan] * 3600.0 / 1e6
    returned = sum(min(demand, r * h) for r in rates) / 1000.0          # Gbit over the stay
    return {"demand_mbit_day": round(demand), "capacity_mbit_day": by_plan[plan]["capacity_mbit_day"],
            "margin": by_plan[plan]["margin"], "plan": plan, "hours": info["hours"][plan], "by_plan": by_plan,
            "rate_kbps_mean": round(sum(rates) / days / 1000.0, 1), "stay_days": info["stay_days"],
            "returned_gbit": round(returned, 1), "dark_days": sum(1 for r in rates if r == 0.0)}


def _cost(design: dict, cell, catalog, mm, campaign: dict, data: dict, objective: dict) -> dict:
    mission_days = cell.tof_days + mm.stay_days
    instruments = [catalog.get("instruments", i) for i in design["payload"]]
    dsn = data["hours"] * mission_days * catalog.cost("dsn_rate_base_usd_h") * catalog.cost("dsn_aperture_weight_70m") / 1e6
    lines = [
        {"id": "launch", "label": campaign["text"].split(",")[0], "musd": campaign["cost_musd"], "sources": campaign["sources"]},
        {"id": "instruments", "label": "Science instruments", "musd": sum(i["cost_musd"] for i in instruments),
         "sources": sorted({s for i in instruments for s in i.get("sources", [])}),
         "model": any("cost_musd" in i.get("model", []) for i in instruments)},
        {"id": "habitat", "label": "Habitat and systems", "musd": catalog.cost("habitat_systems_musd"), "model": True},
        {"id": "dsn", "label": f"Deep Space Network, {data['hours']:.0f} h a day (2019 rate)", "musd": dsn, "sources": ["scan_mocs_0001"]},
        {"id": "ops", "label": f"Flight operations, {mission_days:.0f} days", "model": True,
         "musd": catalog.cost("flight_ops_musd_per_year") * mission_days / 365.25},
    ]
    for x in lines:
        x["musd"] = round(x["musd"], 1)
    total = sum(x["musd"] for x in lines)
    cap = float(objective["cost_cap_musd"])
    return {"lines": lines, "total_musd": round(total, 1), "cap_musd": cap, "margin": round((cap - total) / cap, 4),
            "mission_days": round(mission_days)}


def _science(design: dict, catalog, objective: dict, data: dict) -> dict:
    cov = sum(catalog.get("instruments", i)["serves"].get(objective["id"], 0.0) for i in design["payload"])
    need = float(objective["min_data_gbit"])
    data_ok = data["returned_gbit"] >= need
    verdict = "covers" if cov >= COVERS_AT and data_ok else "partial" if cov >= PARTIAL_AT else "no"
    if verdict == "covers":
        text = "Covers the objective."
    elif verdict == "no":
        text = "No: these instruments barely serve this objective."
    elif cov < COVERS_AT:
        text = "Partially: add an instrument made for this objective."
    else:
        text = f"Partially: only {data['returned_gbit']:,.0f} of the {need:,.0f} Gbit needed comes home."
    return {"objective": objective["id"], "name": objective["name"], "coverage": round(cov, 2), "verdict": verdict,
            "text": text, "need_gbit": need, "returned_gbit": data["returned_gbit"], "model": True}


# ---------------------------------------------------------------- evaluate
def evaluate(design: dict, cell, catalog, mass_model, contact: dict) -> dict:
    """Close the four budgets for one design. Pure: no side effects."""
    objective = catalog.get("objectives", design["objective"])
    vehicle = catalog.get("vehicles", design["vehicle"])
    mass = _mass(design, cell, catalog, mass_model)
    camp = _campaign(mass["imleo_t"], vehicle)
    power = _power(design, catalog)
    data = _data(design, catalog, contact)
    cost = _cost(design, cell, catalog, mass_model, camp, data, objective)
    science = _science(design, catalog, objective, data)
    # The heaviest trip this rocket can still buy: within the launch limit and
    # what the cost cap leaves after everything that is not a launch.
    other = cost["total_musd"] - camp["cost_musd"]
    affordable = max(0, math.floor((cost["cap_musd"] - other) / vehicle["price_musd"]))
    camp["max_imleo_t"] = round(min(LAUNCH_LIMIT, affordable) * vehicle["leo_t"], 1)

    cap_t = camp["capacity_t"]
    mass.update(budget_t=cap_t, margin_t=round(cap_t - mass["imleo_t"], 1), feasible=camp["launches"] <= LAUNCH_LIMIT)
    budgets = {
        "mass": {"label": "Mass", "value": mass["imleo_t"], "limit": cap_t, "unit": "t",
                 "margin": round((cap_t - mass["imleo_t"]) / cap_t, 4), "limit_text": f"{LAUNCH_LIMIT} launches", "model": True},
        "power": {"label": "Power at Mars", "value": power["load_kw"], "limit": power["supply_kw"], "unit": "kW",
                  "margin": power["margin"], "limit_text": "array output", "sources": power["sources"]},
        "data": {"label": "Data home", "value": data["demand_mbit_day"] / 1000.0, "limit": data["capacity_mbit_day"] / 1000.0,
                 "unit": "Gbit/day", "margin": data["margin"], "limit_text": f"{data['plan']} plan downlink", "model": True},
        "cost": {"label": "Cost", "value": cost["total_musd"], "limit": cost["cap_musd"], "unit": "$M",
                 "margin": cost["margin"], "limit_text": "cost cap", "model": True},
    }
    for b in budgets.values():
        b["band"] = band(b["margin"])
    reasons = _reasons(budgets, mass, camp, power, data, cost, catalog, objective)
    return {"budgets": budgets, "mass": mass, "campaign": camp, "power": power, "data": data, "cost": cost,
            "science": science, "reasons": reasons, "valid": not reasons,
            "constants": {"band_green": BAND_GREEN, "launch_limit": LAUNCH_LIMIT, "mars_au": MARS_AU}}


def _reasons(budgets, mass, camp, power, data, cost, catalog, objective) -> list[str]:
    out = []
    if budgets["mass"]["band"] == "red":
        out.append(f"Mass: {mass['imleo_t']:.0f} t needs {camp['launches']} launches of {camp['name']}; "
                   f"the campaign can assemble {LAUNCH_LIMIT}")
    if budgets["power"]["band"] == "red":
        name = catalog.get("solar_arrays", power["array"])["name"].lower()
        out.append(f"Power: {power['short_kw']:.1f} kW short at Mars with the {name}")
    if budgets["data"]["band"] == "red":
        out.append(f"Data: the payload makes {data['demand_mbit_day'] / 1000:.1f} Gbit a day, and the {data['plan']} "
                   f"radio plan brings home {data['capacity_mbit_day'] / 1000:.1f}")
    if budgets["cost"]["band"] == "red":
        out.append(f"Cost: ${cost['total_musd']:,.0f} M is ${cost['total_musd'] - cost['cap_musd']:,.0f} M over the "
                   f"${cost['cap_musd']:,.0f} M cap for {objective['name'].lower()}")
    return out


# ------------------------------------------------------------------ ripple
def ripple(prev: dict | None, new: dict) -> list[str]:
    """What moved between two evaluations, in the order a team lead would read
    it out: what was added, what it weighs, what it costs, which margins moved."""
    if not prev or "mass" not in prev or "mass" not in new or "lines" not in prev["mass"]:
        return []
    out = []
    pl = {x["id"]: x["t"] for x in prev["mass"]["lines"]}
    for x in new["mass"]["lines"]:
        d = x["t"] - pl.get(x["id"], 0.0)
        if x["id"] == "propellant":
            continue
        if x["id"] == "instruments" and abs(d) >= 0.0005:
            out.append(f"{d * 1000:+.0f} kg instruments")
        elif abs(d) >= 0.05:
            out.append(f"{d:+.1f} t {x['label'].lower()}")
    dp = new["mass"]["propellant_t"] - prev["mass"]["propellant_t"]
    if abs(dp) >= 0.05:
        out.append(f"{dp:+.1f} t propellant")
    di = new["mass"]["imleo_t"] - prev["mass"]["imleo_t"]
    if abs(di) >= 0.05:
        out.append(f"{di:+.1f} t IMLEO")
    a, b = prev["campaign"], new["campaign"]
    if a["vehicle"] != b["vehicle"]:
        out.append(f"rocket {a['name']} → {b['name']}")
    if a["launches"] != b["launches"]:
        out.append(f"launches {a['launches']} → {b['launches']}")
    dc = new["cost"]["total_musd"] - prev["cost"]["total_musd"]
    if abs(dc) >= 0.5:
        out.append(f"{'+' if dc > 0 else '-'}${abs(dc):,.0f} M")
    for k in BUDGETS:
        m0, m1 = prev["budgets"][k]["margin"], new["budgets"][k]["margin"]
        if round(m0 * 100) != round(m1 * 100) or prev["budgets"][k]["band"] != new["budgets"][k]["band"]:
            out.append(f"{k} margin {m0:.0%} → {m1:.0%} ({new['budgets'][k]['band']})")
    return out
