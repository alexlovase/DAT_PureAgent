#!/usr/bin/env python3
"""Replay independently reviewed PCIe facts through the public tool session.

The expectations come from electrical_audit.py's raw-record/PDF investigation.
This is deterministic query validation, not a new Agent run or PCIe checker.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from electrical_audit import EXPECTED, SERIES_PATHS

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from pureagent.cli import ToolSession


def query(session, tool, **args):
    reply = session.call({"tool": tool, "args": args})
    if not reply["ok"]:
        raise AssertionError((tool, args, reply))
    return reply["result"]


def complete_net(session, name):
    offset, members, identity = 0, [], None
    while True:
        result = query(session, "net", query=name, offset=offset)
        identity = result["path"] if identity is None else identity
        if result["path"] != identity:
            raise AssertionError("Net identity changed between pages")
        page = result["members"]
        members.extend(page["items"])
        if page["next_offset"] is None:
            if len(members) != page["total"]:
                raise AssertionError("Incomplete network query")
            return {"path": identity, "members": members}
        if page["next_offset"] <= offset:
            raise AssertionError("Non-advancing pagination")
        offset = page["next_offset"]


def replay(sample_root):
    result = {
        "execution": "deterministic public ToolSession queries against independently reviewed raw expectations",
        "new_llm_calls": 0,
        "net_expectations_checked": 0,
        "series_paths_checked": 0,
        "samples": {},
    }
    for sample, expectations in EXPECTED.items():
        session = ToolSession(sample_root / sample)
        try:
            board = {"overview": query(session, "overview"), "net_checks": []}
            nets = {}
            for name, expected in expectations.items():
                actual = complete_net(session, name)
                endpoints = sorted(f'{m["ref"]}.{m["pin"]}' for m in actual["members"])
                if endpoints != sorted(expected):
                    raise AssertionError((sample, name, endpoints, expected))
                if any(m.get("state", "connected") != "connected" for m in actual["members"]):
                    raise AssertionError((sample, name, "unexpected endpoint dispute"))
                nets[name] = actual
                board["net_checks"].append({"net": name, "complete_endpoints": endpoints, "equal": True})
                result["net_expectations_checked"] += 1
            if sample == "j401-fixture":
                board["series_paths"] = []
                for path in SERIES_PATHS:
                    left, right = [nets[name] for name in path["nets"]]
                    component = query(session, "component", ref=path["capacitor"])
                    if left["path"] == right["path"] or component["value"] != path["raw_value"]:
                        raise AssertionError((path, component))
                    left_pin = query(session, "pin", ref=path["capacitor"], number="2", detail=True)
                    right_pin = query(session, "pin", ref=path["capacitor"], number="1", detail=True)
                    if left_pin["net_paths"] != [left["path"]] or right_pin["net_paths"] != [right["path"]]:
                        raise AssertionError((path, left_pin, right_pin))
                    board["series_paths"].append({**path, "distinct_copper_nets": True,
                                                  "value_and_capacitor_pins_equal": True})
                    result["series_paths_checked"] += 1
                board["assembly_queries"] = {}
                for ref in ("R99", "R100", "R153", "R72"):
                    component = query(session, "component", ref=ref)
                    if component["assembly"] != "unspecified" or component["value"] != "47K":
                        raise AssertionError((ref, component))
                    board["assembly_queries"][ref] = {"value": component["value"],
                                                       "assembly": component["assembly"]}
                board["assembly_interpretation"] = (
                    "DAT does not establish population. Schematic DNP was reviewed separately; "
                    "this replay does not convert schematic intent into actual assembled-board state."
                )
            board["session_stats"] = query(session, "stats")
            if board["session_stats"]["graph_loads"] != 1:
                raise AssertionError("Expected one graph load per sample session")
            result["samples"][sample] = board
        finally:
            session.close()
    result["limitations"] = [
        "This script verifies query facts, not the physical correctness of an entire board or link.",
        "Cross-capacitor paths are manually reviewed fixture expectations, not automatic path discovery.",
        "The original electrical audit is partial; zero confirmed board errors is not a pass result.",
        "No model cost, hallucination rate, timing benefit, PCIe SI, or configured link generation is measured.",
    ]
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample-root", type=Path, default=REPO / ".cache/pcie-inputs")
    parser.add_argument("--output", type=Path, default=REPO / ".cache/pcie-tool-replay.json")
    args = parser.parse_args()
    result = replay(args.sample_root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "net_expectations_checked": result["net_expectations_checked"],
                      "series_paths_checked": result["series_paths_checked"], "new_llm_calls": 0}, ensure_ascii=False))
