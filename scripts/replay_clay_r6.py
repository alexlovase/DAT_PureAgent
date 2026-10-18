"""Replay recorded arithmetic and presentation; this does NOT rerun any LLM.

Requires the committed investigation/review/coverage records. Generated DAT
variants stay in .cache; fresh output goes to .cache/replay by default. Source
interpretation and independent review are historical human/Agent evidence,
not re-proven merely by successfully replaying these records.
"""
from pathlib import Path
import argparse
import json
import math
import sys

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from pureagent import DatGraph
from pureagent.calculations import evaluate
from pureagent.report import build_report
from examples.clay_r6.make_variants import generate


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def numeric_equal(a, b):
    if a is None or b is None:
        return a is b
    return math.isclose(a, b, rel_tol=1e-12, abs_tol=1e-12)


def replay(output):
    generate(REPO / ".cache/variants")
    root = REPO / "runs/20261018_clay_r6"
    results = {}
    for case in ("original", "case_b", "case_c"):
        saved = root / case
        investigation = read(saved / "investigation.json")
        reviews = read(saved / "reviews.json")
        coverage = read(saved / "coverage.json")
        dat = REPO / ("examples/clay_r6/clay-r6" if case == "original" else f".cache/variants/{case}")
        graph = DatGraph(dat)
        report = build_report(investigation["candidates"], reviews, coverage)
        calculations = []
        for record in investigation["calculations"]:
            actual = evaluate(record)
            expected = record.get("tool_result", record)
            equal = all(numeric_equal(actual.get(k), expected.get(k)) for k in ("result", "trial_result"))
            equal = equal and actual["usable"] == expected["usable"]
            calculations.append({"id": record["id"], "result": actual["result"],
                "trial_result": actual.get("trial_result"), "usable": actual["usable"],
                "status": actual["status"], "matches_recorded_calculation": equal})
        if not all(c["matches_recorded_calculation"] for c in calculations):
            raise ValueError(f"{case}: calculation differs from the recorded result")
        write(output / case / "report.json", report)
        write(output / case / "calculations.json", calculations)
        results[case] = {"graph": graph.overview(), "run_status": report["run_status"],
            "confirmed_count": len(report["confirmed_errors"]),
            "confirmed_ids": [e["id"] for e in report["confirmed_errors"]],
            "replayed_calculations": len(calculations), "all_calculations_match": True}
    summary = {"execution": "deterministic replay of recorded calculations and independent decisions",
               "new_llm_investigations": 0, "new_independent_source_reviews": 0, "cases": results}
    write(output / "replay_summary.json", summary)
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=REPO / ".cache/replay")
    args = parser.parse_args()
    print(json.dumps(replay(args.output), ensure_ascii=False, indent=2))
