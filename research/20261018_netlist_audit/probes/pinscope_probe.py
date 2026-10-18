"""Targeted local probes of unmodified Pinscope pure functions.

Loads the original data models and query module. AST-extracts the original
build_component_context function and its ground-summary constant to avoid importing the paid model client and
its environment loader. These are synthetic interface cases, not E2E checks.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent / "_vendor" / "pinscope"
sys.path.insert(0, str(ROOT))

from backend.pinscopex.models import (
    Component, ComponentConstraints, ComponentType, DesignGraph,
    Net, NetType, PackageInfo, Pin, PinConnection,
)
from backend.pinscopex import validation_tools as vt
from backend.pinscopex.pin_function_tokens import parse_net_token

source_path = ROOT / "backend/pinscopex/validate.py"
tree = ast.parse(source_path.read_text())
context_node = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                    and n.name == "build_component_context")
ground_limit_node = next(n for n in tree.body if isinstance(n, ast.Assign)
                         and any(isinstance(t, ast.Name) and t.id == "_GROUND_NET_MAX_COMPONENTS"
                                 for t in n.targets))
module = ast.Module(body=[ground_limit_node, context_node], type_ignores=[])
namespace = {
    "DesignGraph": DesignGraph,
    "ConstraintsMap": dict[str, ComponentConstraints],
    "ComponentType": ComponentType,
    "NetType": NetType,
    "_pin_sort_key": vt._pin_sort_key,
    "_format_specs": vt._format_specs,
    "_is_thermal_pad_pin": vt._is_thermal_pad_pin,
    "_reviewer_voltage_str": vt._reviewer_voltage_str,
    "parse_net_token": parse_net_token,
}
exec(compile(module, str(source_path), "exec"), namespace)
build_context = namespace["build_component_context"]


def component(ref, kind, value, pins, mpn=None):
    return Component(reference=ref, component_type=kind, value=value,
                     footprint="", mpn=mpn, pins=pins)


def make_graph(extra_load=False):
    comps = {
        "U1": component("U1", ComponentType.IC, "SYNTHETIC_AMP",
                        {"1": "VCC", "2": "SUM", "3": "OUT", "4": "GND", "5": ""},
                        "SYNTHETIC_AMP"),
        "RF": component("RF", ComponentType.RESISTOR, "10k", {"1": "OUT", "2": "SUM"}),
        "RG": component("RG", ComponentType.RESISTOR, "10k", {"1": "SUM", "2": "GND"}),
        "UX": component("UX", ComponentType.UNKNOWN, "", {"1": "SUM", "2": "AUX"}),
    }
    for i in range(1, 9):
        ref = f"C{i:02}"
        comps[ref] = component(ref, ComponentType.CAPACITOR, "100n",
                               {"1": "VCC", "2": "GND"})
    if extra_load:
        comps["RLOAD"] = component("RLOAD", ComponentType.RESISTOR, "10k",
                                   {"1": "OUT", "2": "EXTRA_N"})
    nets = {}
    for comp in comps.values():
        for pin, name in comp.pins.items():
            if not name:
                continue
            if name not in nets:
                kind = NetType.POWER if name == "VCC" else NetType.GROUND if name == "GND" else NetType.SIGNAL
                nets[name] = Net(name=name, net_type=kind)
            nets[name].pins.append(PinConnection(component_ref=comp.reference, pin_number=pin))
    return DesignGraph(components=comps, nets=nets)


def facts(graph):
    return sorted((c.reference, pin, net) for c in graph.components.values()
                  for pin, net in c.pins.items())


graph = make_graph()
full = ComponentConstraints(
    mpn="SYNTHETIC_AMP", package_info=PackageInfo(base_family="DUT", package="EXAMPLE", pin_count=5),
    pintable=[Pin(number=n, name=name) for n, name in
              [(1, "VCC"), (2, "IN-"), (3, "OUT"), (4, "GND"), (5, "UNUSED")]],
    absolute_maximum_ratings=[], rules=[],
)
reports = []

# A: An irrelevant package attribute can be removed without changing pin/neighborhood text.
before = build_context(graph, {full.mpn: full}, "U1")
no_package = full.model_copy(update={"package_info": None})
after = build_context(graph, {full.mpn: no_package}, "U1")
stripped_before = "\n".join(line for line in before.split("\n") if not line.startswith("Package:"))
assert stripped_before == after
assert "Pin 5 (UNUSED) → [unconnected]" in after
reports.append({"probe": "optional_package", "all_other_context_equal": True,
                "unconnected_pin_retained": True})

# B: A missing MPN does not destroy the graph; netlist fallback still exposes its pins.
no_mpn = graph.model_copy(deep=True)
no_mpn.components["U1"].mpn = None
no_mpn_context = build_context(no_mpn, {}, "U1")
assert facts(graph) == facts(no_mpn)
assert "Pin 5 → [unconnected]" in no_mpn_context
assert "UX:" in no_mpn_context
reports.append({"probe": "missing_mpn_local_context", "wiring_equal": True,
                "fallback_keeps_unconnected_pin": True, "unknown_neighbor_visible": True,
                "does_not_test_main_reviewer_task_selection": True})

# C: Shared-rail summary does not destroy the detailed query path; signal branches remain visible.
rail_query = vt.find_connected_components(graph, {}, "U1", "1")
assert all(f"C{i:02}:" not in before for i in range(1, 9))
assert all(f"C{i:02}:" in rail_query for i in range(1, 9))
assert rail_query.count("pins: 1->VCC, 2->GND") == 8
extra = make_graph(extra_load=True)
extra_query = vt.find_connected_components(extra, {}, "U1", "3")
extra_context = build_context(extra, {full.mpn: full}, "U1")
assert "RLOAD" not in before and "RLOAD:" in extra_context
assert "RLOAD:" in extra_query and "pins: 1->OUT, 2->EXTRA_N" in extra_query
assert "RF: 10k — bridges" in before
reports.append({"probe": "summary_and_expansion", "capacitors_summarized": 8,
                "capacitors_recovered": 8, "both_ends_recovered": True,
                "extra_load_and_other_endpoint_visible": True,
                "feedback_bridge_visible": True})

# D: Counterexamples to relying on presentation tools as exact physical-pin facts.
partial = full.model_copy(update={"pintable": full.pintable[:-1]})
partial_context = build_context(graph, {full.mpn: partial}, "U1")
fallback_table = vt.get_pintable(graph, {}, "U1")
pin5_line = next(line.strip() for line in fallback_table.splitlines() if "Pin 5:" in line)
absent_pin_query = vt.get_net_for_pin(graph, {}, "U1", "99")
assert "5" in graph.components["U1"].pins and graph.components["U1"].pins["5"] == ""
assert "Pin 5" not in partial_context
assert "[connected]" in pin5_line
assert "99" not in graph.components["U1"].pins and "is not connected" in absent_pin_query
reports.append({"probe": "physical_pin_status_counterexamples",
                "declared_unconnected_pin_missing_from_partial_pintable_context": True,
                "fallback_pintable_empty_net_line": pin5_line,
                "undeclared_pin_query": absent_pin_query,
                "scope": "valid model input at function boundary; not exporter-to-report E2E"})

print(json.dumps({"scope": "unchanged source pure functions, actual Pydantic models, synthetic inputs, no LLM",
                  "results": reports}, ensure_ascii=False, indent=2))
