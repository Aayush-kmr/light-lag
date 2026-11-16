"""Grounding guardrails, autonomy decisions, eval-check sanity."""
import sys
from pathlib import Path

from lightlag.ai.grounding import check
from lightlag.sim.autonomy import AutonomyEngine

ROOT = Path(__file__).resolve().parents[2]


TOOL = [{"status": "ok", "age_s": 420, "stale": False, "values": {"batt_soc": {"value": 91.24, "unit": "%"}}}]
STALE = [{"status": "stale", "age_s": 420, "stale": True, "values": {"ppco2": {"value": 4.37, "unit": "mmHg"}}}]


def test_invented_number_is_caught():
    r = check("Flight, FIDO. Battery 87.3 percent. Over.", TOOL)
    assert not r.ok and r.violations[0]["number"] == "87.3"


def test_rounding_and_unit_conversion_allowed():
    assert check("Flight, FIDO. Battery 91 percent, as of 7 minutes ago. Over.", TOOL).ok
    assert check("Flight, FIDO. Battery 91.2 percent. Over.", TOOL).ok


def test_stale_value_must_be_flagged():
    assert not check("Flight, FIDO. ppCO2 4.4. Over.", STALE).ok
    assert check("Flight, FIDO. ppCO2 4.4, last good reading 7 min old. Over.", STALE).ok


def test_identifiers_are_not_telemetry():
    assert check("Meridian-1, Lantern. DSS-14 has you. X8.2 flare at S08W88. Over.", []).ok


def test_readback_numbers_come_from_the_prompt():
    assert check("Copy, azimuth 12.5, elevation -3. Over.", [], prompt="Set azimuth 12.5 elevation -3").ok


def test_no_data_means_no_numbers():
    assert not check("Commander, TERN. Probably around 3 mmHg. Over.", [{"status": "no_data"}]).ok


# ----------------------------------------------------------------- autonomy
def test_auto_executes():
    a = AutonomyEngine()
    d = a.decide(0, "scrubber_cycle", "ppCO2 4.2", 3600 * 8, 600, {}, True)
    assert d.outcome == "executed" and d.action == "scrubber_cycle"


def test_ask_with_time_requests_permission():
    a = AutonomyEngine()
    d = a.decide(0, "storm_attitude", "protons", 12 * 3600, 600, {}, True)
    assert d.outcome == "requested" and "storm_attitude" in a.pending


def test_ask_without_time_breaks_policy():
    """The core rule: time-to-harm shorter than the round trip -> act."""
    a = AutonomyEngine()
    d = a.decide(0, "load_shed_science", "battery 30%", 30 * 3600, 10 * 86400, {}, True)
    assert d.outcome == "broke_policy" and d.action == "load_shed_science"


def test_forbid_non_safety_is_held():
    a = AutonomyEngine()
    d = a.decide(0, "execute_uplink", "command arrived", float("inf"), 600, {}, False)
    assert d.outcome == "held" and d.action is None


def test_pending_request_escalates_when_answer_too_late():
    a = AutonomyEngine()
    a.decide(0, "storm_attitude", "protons", 12 * 3600, 600, {}, True)
    d = a.escalate_if_needed(300, "storm_attitude", time_to_harm_s=100, remaining_wait_s=300)
    assert d.outcome == "broke_policy"


def test_lantern_answer_executes():
    a = AutonomyEngine()
    a.decide(0, "storm_attitude", "protons", 12 * 3600, 600, {}, True)
    d = a.answer(700, "storm_attitude", True)
    assert d.outcome == "approved" and "storm_attitude" in a.active


# --------------------------------------------------------- eval checks bite
def test_eval_checks_reject_bad_replies():
    sys.path.insert(0, str(ROOT / "ml" / "finetune" / "eval"))
    import run_eval as E
    p = {"callsign_to": "Flight", "callsign_from": "FIDO", "user": "Battery?", "tool_calls": [
        {"name": "get_telemetry", "args": {}, "result": {"status": "ok", "age_s": 200, "values": {"batt_soc": {"value": 61.0}}}}],
         "expect": {"max_words": 45, "must_mention_values": [61.0]}}
    assert E.score(p, "Flight, FIDO. Battery charge 61 percent. Over.")["pass"]
    assert not E.score(p, "Battery is 61 percent.")["pass"]                     # no callsigns
    assert not E.score(p, "Flight, FIDO. Battery 64 percent. Over.")["pass"]    # invented
    rb = {"callsign_to": "Commander", "callsign_from": "TERN", "user": "TERN, roll 40 pitch 7", "tool_calls": [],
          "expect": {"max_words": 45, "readback_values": [40, 7]}}
    assert not E.score(rb, "Commander, TERN. Copy roll 40. Over.")["pass"]      # incomplete read-back
    go = {"callsign_to": "Flight", "callsign_from": "FIDO", "user": "Go/no-go?", "tool_calls": [], "expect": {"max_words": 45, "go": "NO-GO"}}
    assert not E.score(go, "Flight, FIDO. GO. Over.")["pass"]
