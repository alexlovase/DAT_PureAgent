#!/usr/bin/env python3
"""Small counterexamples for tool instructions and report prerequisites.

Arithmetic is synthetic. The report probe reuses already reviewed Clay records;
it performs no new electrical investigation and never changes those records.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from pureagent.calculations import evaluate
from pureagent.report import build_report


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def git_blob(path):
    data = path.read_bytes()
    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()


def probe(phase):
    assumptions = ["Synthetic scenario S: an enabled ideal 5 V source across a 1000 ohm resistor."]
    current = evaluate({
        "id": "scenario_current",
        "inputs": {"v": {"value": 5, "unit": "V", "source": "Synthetic scenario definition"},
                   "r": {"value": 1000, "unit": "ohm", "source": "Synthetic scenario definition"}},
        "expression": "v / r", "result_unit": "A", "assumptions": assumptions,
        "validity": {"status": "conditional", "reason": "Scenario S is hypothetical; no actual board source state is established."},
    })
    # Carry the conditions explicitly. The calculator does not do this for us.
    power = evaluate({
        "id": "scenario_power",
        "inputs": {"i": {"value": current["trial_result"], "unit": "A",
                         "source": "scenario_current trial result under the same synthetic scenario S"},
                   "r": {"value": 1000, "unit": "ohm", "source": "Same synthetic scenario resistor"}},
        "expression": "i * i * r", "result_unit": "W", "assumptions": deepcopy(assumptions),
        "validity": {"status": "conditional", "reason": "The caller explicitly retains scenario S; no unconditional working point is asserted."},
    })
    for value in (current, power):
        assert value["result"] is None and value["usable"] is False and value["status"] == "conditional"
    assert current["trial_result"] == 0.005 and power["trial_result"] == 0.025

    saved = REPO / "runs/20261018_clay_r6/original"
    investigation = read(saved / "investigation.json")
    reviews = read(saved / "reviews.json")
    coverage = read(saved / "coverage.json")
    original = build_report(investigation["candidates"], reviews, coverage)
    without_advice = deepcopy(investigation["candidates"])
    for candidate in without_advice:
        candidate.pop("recommendation", None)
    stripped = build_report(without_advice, reviews, coverage)
    assert len(original["confirmed_errors"]) == 1
    declared = build_report(investigation["candidates"], reviews, {
        "investigated_count": 1, "total_count": 1, "full_board": True,
        "scope": "Synthetic caller declaration; this probe performs no new board investigation.",
    })
    return {
        "phase": phase, "baseline_commit": "7a743e0cab5dedf31230a0c2bc316aa191a0e23d",
        "scope": "Synthetic arithmetic and deterministic re-presentation of historical findings; no new LLM or electrical review.",
        "production_git_blobs": {name: git_blob(REPO / name)
                                  for name in ("pureagent/calculations.py", "pureagent/report.py")},
        "conditional_arithmetic": {
            "current": current, "power": power,
            "conditions_carried_by": "The probe caller explicitly, not an automatic dependency engine.",
        },
        "remediation_prerequisite": {
            "only_change": "Remove recommendation from in-memory candidate copies; all reviews and requirements unchanged.",
            "original_confirmed_count": len(original["confirmed_errors"]),
            "without_recommendation_confirmed_count": len(stripped["confirmed_errors"]),
            "without_recommendation_ids": [row["id"] for row in stripped["confirmed_errors"]],
            "missing_advice_fabricated": any("recommendation" in row for row in stripped["confirmed_errors"]),
        },
        "completion_declaration": {
            "supplied_count": "1/1", "supplied_full_board": True,
            "returned_status": declared["run_status"],
            "meaning": "Acceptance of caller metadata, not independent proof of electrical coverage.",
        },
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", default="replay")
    parser.add_argument("--output", type=Path, default=REPO / ".cache/agent-freedom-probes.json")
    args = parser.parse_args()
    result = probe(args.phase)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"phase": args.phase, "remediation": result["remediation_prerequisite"],
                      "conditional_values": [result["conditional_arithmetic"][name]["trial_result"]
                                             for name in ("current", "power")],
                      "completion_status": result["completion_declaration"]["returned_status"]}, ensure_ascii=False))
