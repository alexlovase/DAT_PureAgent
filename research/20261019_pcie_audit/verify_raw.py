#!/usr/bin/env python3
"""Independent raw-PST inventory for the two fixed PCIe research fixtures.

This deliberately separate, line-oriented scanner does not import PureAgent when
building its expected facts.  It is a research cross-check for the observed input
dialect, not a second production parser.  Unconsumed declarations fail the scan.
PIN_NUMBER columns are indexed by SECTION_NUMBER; '0' columns inside a multi-slot
list are absent section entries.  A singleton '(0)' is retained as a literal pad.
Original pin strings and occurrence paths are retained.  The source netlist's
exact NC/NC/C_SIGNAL=NC group is nonconductive.

Usage from the repository root:
  python research/20261019_pcie_audit/verify_raw.py --input-root ../pcie-research
  python research/20261019_pcie_audit/verify_raw.py --input-root ../pcie-research --compare

--compare imports the production parser only after independently generating the
expected inventory; it never uses production parsing to construct that inventory.
The default outputs are cache files; --output can record an archival JSON summary.
Complete independently generated facts remain in --full-output (ignored cache).
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import re
import sys


BOARDS = ("j401-fixture", "opencellular-breakout")
FILE_NAMES = ("pstchip.dat", "pstxprt.dat", "pstxnet.dat")
ATTR = re.compile(r"\b([A-Za-z_][A-Za-z_0-9]*)\s*=\s*'([^']*)'", re.S)


def attributes(text):
    result = {}
    for key, value in ATTR.findall(text):
        if key in result and result[key] != value:
            raise ValueError(f"conflicting research-input attribute {key!r}")
        result[key] = value
    return result


def line_number(text, offset):
    return text.count("\n", 0, offset) + 1


def git_blob(data):
    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()


def scan_primitives(text):
    primitive_pattern = re.compile(r"^primitive '([^']+)';\n(.*?)^end_primitive;", re.M | re.S)
    pin_pattern = re.compile(r"^    '([^']+)':\n(.*?)(?=^    '[^']+':|\Z)", re.M | re.S)
    result = {}
    for match in primitive_pattern.finditer(text):
        name, block = match.groups()
        if name in result:
            raise ValueError(f"duplicate primitive {name}")
        pin_block = re.search(r"^  pin\n(.*?)^  end_pin;", block, re.M | re.S)
        body_block = re.search(r"^  body\n(.*?)^  end_body;", block, re.M | re.S)
        if not pin_block or not body_block:
            raise ValueError(f"unrecognized primitive body {name}")
        pins = []
        consumed = []
        for pin_match in pin_pattern.finditer(pin_block.group(1)):
            logical, values = pin_match.groups()
            attrs = attributes(values)
            number = attrs.get("PIN_NUMBER")
            if number is None or not re.fullmatch(r"\([^()]+\)", number):
                raise ValueError(f"unrecognized PIN_NUMBER {name}/{logical}: {number!r}")
            columns = number[1:-1].split(",")
            if any(not x or x != x.strip() or re.search(r"\s", x) for x in columns):
                raise ValueError(f"unrecognized physical pin token {number!r}")
            pins.append({"logical_name": logical, "columns": columns, "attributes": attrs})
            consumed.append(pin_match.span())
        remainder = pin_block.group(1)
        for start, end in reversed(consumed):
            remainder = remainder[:start] + remainder[end:]
        if remainder.strip():
            raise ValueError(f"unconsumed pin declaration in {name}: {remainder[:80]!r}")
        body = attributes(body_block.group(1))
        if "POWER_PINS" in body:
            raise ValueError("POWER_PINS is not present in the audited fixtures; add a separately checked scanner if needed")
        nc_pins = re.findall(r"[^(),\s]+", body.get("NC_PINS", ""))
        result[name] = {"line": line_number(text, match.start()), "body": body,
                        "pins": pins, "nc_pins": nc_pins}
    if len(result) != len(re.findall(r"^primitive\b", text, re.M)):
        raise ValueError("not all primitive declarations were scanned")
    return result


def scan_components(text):
    part_pattern = re.compile(r"^PART_NAME\s*\n(.*?)(?=^PART_NAME\b|^END\.|\Z)", re.M | re.S)
    section_pattern = re.compile(r"^SECTION_NUMBER\s+(\d+)\n\s*'([^']*)':\n(.*?)(?=^SECTION_NUMBER\b|\Z)", re.M | re.S)
    result = {}
    for match in part_pattern.finditer(text):
        block = match.group(1)
        header = re.match(r"\s*(\S+)\s+'([^']+)':([^\n]*)\n", block)
        if not header:
            raise ValueError(f"unrecognized PART_NAME at {line_number(text, match.start())}")
        ref, primitive, raw_attributes = header.groups()
        if ref in result:
            raise ValueError(f"duplicate PART_NAME {ref}")
        sections = {}
        section_body = block[header.end():]
        consumed = []
        for section in section_pattern.finditer(section_body):
            number, path, metadata = section.groups()
            if number in sections:
                raise ValueError(f"duplicate section {ref}/{number}")
            sections[number] = {"path": path, "attributes": attributes(metadata)}
            consumed.append(section.span())
        remainder = section_body
        for start, end in reversed(consumed):
            remainder = remainder[:start] + remainder[end:]
        if remainder.strip():
            raise ValueError(f"unconsumed section declaration for {ref}")
        result[ref] = {"primitive": primitive, "line": line_number(text, match.start()),
                       "attributes": attributes(raw_attributes), "sections": sections}
    if len(result) != len(re.findall(r"^PART_NAME\b", text, re.M)):
        raise ValueError("not all PART_NAME records were scanned")
    if sum(len(c["sections"]) for c in result.values()) != len(re.findall(r"^SECTION_NUMBER\b", text, re.M)):
        raise ValueError("not all SECTION_NUMBER records were scanned")
    return result


def scan_networks(text):
    net_pattern = re.compile(r"^NET_NAME\n(.*?)(?=^NET_NAME\b|^END\.|\Z)", re.M | re.S)
    node_pattern = re.compile(r"^NODE_NAME[ \t]+(\S+)[ \t]+(\S+)\n\s*'([^']*)':\n\s*'([^']*)':(.*?)(?=^NODE_NAME\b|\Z)", re.M | re.S)
    result = []
    for match in net_pattern.finditer(text):
        block = match.group(1)
        header = re.match(r"'([^']*)'\n\s*'([^']*)':\n(.*?)(?=^NODE_NAME\b|\Z)", block, re.M | re.S)
        if not header:
            raise ValueError(f"unrecognized NET_NAME at line {line_number(text, match.start())}")
        name, path, metadata = header.groups()
        attrs = attributes(metadata)
        is_nc_group = name == "NC" and path == "NC" and attrs.get("C_SIGNAL") == "NC"
        nodes = []
        node_body = block[header.end():]
        consumed = []
        for node in node_pattern.finditer(node_body):
            ref, number, occurrence, logical, properties = node.groups()
            # These two source files have no node property that changes connectivity.
            nodes.append({"ref": ref, "number": number, "path": occurrence,
                          "logical_name": logical, "attributes": attributes(properties),
                          "line": line_number(text, match.start(1) + header.end() + node.start())})
            consumed.append(node.span())
        remainder = node_body
        for start, end in reversed(consumed):
            remainder = remainder[:start] + remainder[end:]
        if remainder.strip():
            raise ValueError(f"unconsumed NODE_NAME record in {path}")
        result.append({"name": name, "path": path, "attributes": attrs,
                       "line": line_number(text, match.start()), "nc_group": is_nc_group, "nodes": nodes})
    if len(result) != len(re.findall(r"^NET_NAME\b", text, re.M)):
        raise ValueError("not all NET_NAME records were scanned")
    if sum(len(net["nodes"]) for net in result) != len(re.findall(r"^NODE_NAME\b", text, re.M)):
        raise ValueError("not all NODE_NAME records were scanned")
    return result


def scan_board(directory):
    directory = Path(directory)
    files = {name: (directory / name).read_bytes() for name in FILE_NAMES}
    texts = {name: data.decode("utf-8") for name, data in files.items()}
    primitives = scan_primitives(texts["pstchip.dat"])
    components = scan_components(texts["pstxprt.dat"])
    networks = scan_networks(texts["pstxnet.dat"])
    pins = {}
    problems = []
    for ref, comp in components.items():
        primitive = primitives.get(comp["primitive"])
        if primitive is None:
            problems.append({"kind": "missing_primitive", "ref": ref})
            continue
        physical = {}
        for row in primitive["pins"]:
            for section, number in enumerate(row["columns"], 1):
                if number == "0" and len(row["columns"]) > 1:
                    continue
                pin = physical.setdefault(number, {"declarations": [], "memberships": []})
                pin["declarations"].append({"section": str(section), "logical_name": row["logical_name"]})
                if str(section) not in comp["sections"]:
                    problems.append({"kind": "uninstantiated_declared_section", "ref": ref,
                                     "number": number, "section": section})
        for number in primitive["nc_pins"]:
            physical.setdefault(number, {"declarations": [], "memberships": []})["nc_pins"] = True
        pins[ref] = physical
    seen = Counter()
    matched_nodes = 0
    nc_nodes = []
    conductive = []
    for net in networks:
        simple_nodes = []
        for node in net["nodes"]:
            ref, number = node["ref"], node["number"]
            comp = components.get(ref)
            section_matches = [] if comp is None else [s for s, detail in comp["sections"].items() if detail["path"] == node["path"]]
            pin = pins.get(ref, {}).get(number)
            mapping_matches = [] if pin is None else [d for d in pin["declarations"] if d["section"] in section_matches and d["logical_name"] == node["logical_name"]]
            node["matching_sections"] = section_matches
            node["exact_mapping_match"] = bool(mapping_matches)
            if len(section_matches) != 1 or not mapping_matches:
                problems.append({"kind": "node_section_pin_mapping_mismatch", "node": node.copy(), "net_path": net["path"]})
            else:
                matched_nodes += 1
            if pin is None:
                problems.append({"kind": "undeclared_physical_endpoint", "ref": ref, "number": number})
                pin = pins.setdefault(ref, {}).setdefault(number, {"declarations": [], "memberships": []})
            membership = {"net_path": net["path"], "nc_group": net["nc_group"], "line": node["line"]}
            pin["memberships"].append(membership)
            seen[(ref, number, net["path"])] += 1
            simple_nodes.append([ref, number])
            if net["nc_group"]:
                nc_nodes.append([ref, number])
        if not net["nc_group"]:
            conductive.append({"name": net["name"], "path": net["path"], "endpoints": simple_nodes})
    for key, count in seen.items():
        if count > 1:
            problems.append({"kind": "duplicate_node_record", "endpoint_and_net": list(key), "count": count})
    explicit_nc = set(map(tuple, nc_nodes))
    unspecified = []
    for ref, physical in pins.items():
        for number, pin in physical.items():
            conductive_memberships = {m["net_path"] for m in pin["memberships"] if not m["nc_group"]}
            if pin.get("nc_pins"):
                explicit_nc.add((ref, number))
            if len(conductive_memberships) > 1 or (conductive_memberships and (ref, number) in explicit_nc):
                problems.append({"kind": "conflicting_pin_membership", "ref": ref, "number": number})
            if not pin["memberships"] and not pin.get("nc_pins"):
                unspecified.append([ref, number])
    stats = {
        "primitives": len(primitives), "primitive_logical_pin_rows": sum(len(x["pins"]) for x in primitives.values()),
        "primitive_nonzero_column_entries": sum(sum(n != "0" for n in r["columns"]) for p in primitives.values() for r in p["pins"]),
        "components": len(components), "section_records": sum(len(x["sections"]) for x in components.values()),
        "physical_pins": sum(len(x) for x in pins.values()), "net_name_records": len(networks),
        "ordinary_nets": len(conductive), "ordinary_endpoint_records": sum(len(n["endpoints"]) for n in conductive),
        "node_name_records": sum(len(n["nodes"]) for n in networks), "nc_group_records": sum(n["nc_group"] for n in networks),
        "nc_group_endpoint_records": len(nc_nodes), "explicit_nc_pins": len(explicit_nc),
        "nc_pins_property_instantiated": sum(bool(p.get("nc_pins")) for ps in pins.values() for p in ps.values()),
        "connection_unspecified_pins": len(unspecified), "single_member_nets": sum(len(n["endpoints"]) == 1 for n in conductive),
        "nodes_with_exact_section_path_and_pin_label_match": matched_nodes, "raw_consistency_problems": len(problems),
    }
    return {"board": directory.name,
            "files": {name: {"bytes": len(data), "git_blob": git_blob(data)} for name, data in files.items()},
            "stats": stats, "primitives": primitives, "components": components, "physical_pins": pins,
            "networks": networks, "ordinary_net_endpoint_sets": conductive,
            "explicit_nc_pin_set": sorted([list(x) for x in explicit_nc]),
            "connection_unspecified_pin_set": sorted(unspecified),
            "single_member_net_set": [n for n in conductive if len(n["endpoints"]) == 1],
            "raw_consistency_problems": problems}


def compare_production(expected, directory):
    """Compare complete fact multisets, preserving multiplicity and raw names."""
    # The expected inventory is already complete before this function is called.
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from pureagent.dat import DatGraph
    graph = DatGraph(directory)
    checks = {}

    def encode(value):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    def compare(name, wanted, observed):
        wanted = Counter(encode(row) for row in wanted)
        observed = Counter(encode(row) for row in observed)
        missing, unexpected = wanted - observed, observed - wanted
        checks[name] = {"equal": wanted == observed,
                        "expected_records": sum(wanted.values()), "actual_records": sum(observed.values()),
                        "missing_records": sum(missing.values()), "unexpected_records": sum(unexpected.values())}
        if missing:
            checks[name]["missing_examples"] = [json.loads(x) for x in list(missing)[:3]]
        if unexpected:
            checks[name]["unexpected_examples"] = [json.loads(x) for x in list(unexpected)[:3]]

    compare("raw_input_content", expected["files"].items(),
            ((name, {"bytes": len(raw.encode("utf-8")), "git_blob": git_blob(raw.encode("utf-8"))})
             for name, raw in graph.raw.items()))
    compare("primitive_ids", expected["primitives"], graph.primitives)
    compare("primitive_body_attributes", ((name, p["body"]) for name, p in expected["primitives"].items()),
            ((name, p["attributes"]) for name, p in graph.primitives.items()))
    compare("primitive_physical_pin_section_name_entries",
            ((name, number, str(section), row["logical_name"])
             for name, p in expected["primitives"].items() for row in p["pins"]
             for section, number in enumerate(row["columns"], 1) if number != "0" or len(row["columns"]) == 1),
            ((name, number, section, label)
             for name, p in graph.primitives.items() for number, pin in p["pins"].items()
             for section in pin["sections"] for label in pin["names"]))
    compare("component_primitive_binding", ((ref, c["primitive"]) for ref, c in expected["components"].items()),
            ((ref, c["primitive"]) for ref, c in graph.components.items()))
    compare("component_sections_and_raw_occurrence_paths",
            ((ref, number, detail["path"], detail["attributes"])
             for ref, c in expected["components"].items() for number, detail in c["sections"].items()),
            ((ref, section["number"], section["path"], section["attributes"])
             for ref, c in graph.components.items() for section in c["sections"]))
    compare("component_effective_attributes",
            ((ref, {**expected["primitives"][c["primitive"]]["body"], **c["attributes"]})
             for ref, c in expected["components"].items()),
            ((ref, c["attributes"]) for ref, c in graph.components.items()))
    compare("physical_pin_set", ((ref, number) for ref, pins in expected["physical_pins"].items() for number in pins),
            ((ref, number) for ref, c in graph.components.items() for number in c["pins"]))
    compare("physical_pin_library_labels",
            ((ref, number, label) for ref, pins in expected["physical_pins"].items()
             for number, pin in pins.items() for label in {d["logical_name"] for d in pin["declarations"]}),
            ((ref, number, label) for ref, c in graph.components.items()
             for number, pin in c["pins"].items() for label in pin["names"]))
    compare("physical_pin_section_membership",
            ((ref, number, section) for ref, pins in expected["physical_pins"].items()
             for number, pin in pins.items() for section in {d["section"] for d in pin["declarations"]}),
            ((ref, number, section) for ref, c in graph.components.items()
             for number, pin in c["pins"].items() for section in pin["sections"]))
    for nc, prefix, actual_nets in ((False, "ordinary", list(graph.nets.values())),
                                   (True, "nc", graph.nc_records)):
        wanted_nets = [n for n in expected["networks"] if n["nc_group"] == nc]
        compare(prefix + "_network_names_paths_attributes",
                ((n["name"], n["path"], n["attributes"]) for n in wanted_nets),
                ((n["name"], n["path"], n["attributes"]) for n in actual_nets))
        compare(prefix + "_complete_endpoint_records",
                ((net["path"], n["ref"], n["number"], n["path"], n["logical_name"], n["line"])
                 for net in wanted_nets for n in net["nodes"]),
                ((net["path"], n["ref"], n["pin"], n["path"], n["name"], n["source"]["start_line"])
                 for net in actual_nets for n in net["members"]))
    compare("explicit_nc_physical_pin_set", expected["explicit_nc_pin_set"],
            ((ref, number) for ref, c in graph.components.items() for number, pin in c["pins"].items()
             if pin["nc_declared"] or pin["nc_exported"]))
    compare("connection_unspecified_pin_set", expected["connection_unspecified_pin_set"],
            ((ref, number) for ref, c in graph.components.items() for number, pin in c["pins"].items()
             if not pin["nets"] and not pin["nc_declared"] and not pin["nc_exported"]))
    compare("ordinary_pin_net_memberships",
            ((ref, number, m["net_path"]) for ref, pins in expected["physical_pins"].items()
             for number, pin in pins.items() for m in pin["memberships"] if not m["nc_group"]),
            ((ref, number, path) for ref, c in graph.components.items()
             for number, pin in c["pins"].items() for path in pin["nets"]))
    compare("single_member_network_set",
            ((n["path"], n["name"], n["endpoints"][0]) for n in expected["single_member_net_set"]),
            ((n["path"], n["name"], [n["members"][0]["ref"], n["members"][0]["pin"]])
             for n in graph.nets.values() if len(n["members"]) == 1))

    def pages(get_page):
        rows, offset, calls = [], 0, 0
        while True:
            page = get_page(offset)
            calls += 1
            if page["offset"] != offset:
                raise AssertionError("wrong page offset")
            rows.extend(page["items"])
            if page["next_offset"] is None:
                if len(rows) != page["total"]:
                    raise AssertionError("pagination total mismatch")
                return rows, calls
            if page["next_offset"] <= offset:
                raise AssertionError("pagination did not advance")
            offset = page["next_offset"]

    inventory_rows, inventory_calls = pages(lambda offset: graph.inventory(offset=offset))
    compare("public_inventory_all_pages", expected["components"], (r["ref"] for r in inventory_rows))
    public_pins, component_calls = [], 0
    for ref in expected["components"]:
        rows, count = pages(lambda offset: graph.component(ref, offset=offset, detail=True)["pins"])
        component_calls += count
        public_pins.extend((ref, p["pin"]) for p in rows)
    compare("public_component_pin_all_pages",
            ((ref, number) for ref, pins in expected["physical_pins"].items() for number in pins), public_pins)
    public_members, net_calls = [], 0
    for net in expected["ordinary_net_endpoint_sets"]:
        rows, count = pages(lambda offset: graph.net(net["path"], offset=offset)["members"])
        net_calls += count
        public_members.extend((net["path"], n["ref"], n["pin"], n["name"]) for n in rows)
    compare("public_net_members_all_pages",
            ((net["path"], n["ref"], n["number"], n["logical_name"])
             for net in expected["networks"] if not net["nc_group"] for n in net["nodes"]), public_members)
    compare("clean_fixture_diagnostics", [], graph.diagnostics)
    return {"overview": graph.overview(), "all_complete_sets_equal": all(c["equal"] for c in checks.values()),
            "comparison_count": len(checks), "checks": checks,
            "public_query_page_counts": {"inventory": inventory_calls, "component": component_calls, "net": net_calls},
            "production_dat_py_git_blob": git_blob(Path(sys.modules["pureagent.dat"].__file__).read_bytes())}


def summarize(board):
    examples = []
    wanted = {"PCIE1_TX0_P", "PCIE1_M2_TX0_P", "PCIE0_RX0_P", "PCIE_WAKE",
              "PCIE0_TXP", "PCIE0_RXP", "PCIE0_GBE_CLKP"}
    for net in board["networks"]:
        if net["name"] in wanted:
            examples.append({"name": net["name"], "path": net["path"], "attributes": net["attributes"],
                             "line": net["line"], "nodes": [{k: v for k, v in n.items() if k != "path"} for n in net["nodes"]]})
    return {"files": board["files"], "stats": board["stats"],
            "multi_section_instances": {ref: c["sections"] for ref, c in board["components"].items() if len(c["sections"]) > 1},
            "raw_consistency_problems": board["raw_consistency_problems"], "connection_examples": examples,
            "single_member_net_set": board["single_member_net_set"],
            "meaning": "Statistics and examples summarize complete independently constructed fact sets; production comparisons below compare every record, not just these counts."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path(".cache/pcie-raw-inventory-summary.json"))
    parser.add_argument("--full-output", type=Path, default=Path(".cache/pcie-full-raw-inventory.json"))
    parser.add_argument("--compare", action="store_true")
    args = parser.parse_args()
    # Construct all expected facts before any production parser import.
    boards = {board: scan_board(args.input_root / board) for board in BOARDS}
    complete = {"method": "Independent line-oriented raw PST scanner; all physical pin strings and occurrence paths retained.",
                "scope": "These two pinned fixtures; complete raw connectivity inventory, not electrical correctness or generic PST support.",
                "boards": boards}
    args.full_output.parent.mkdir(parents=True, exist_ok=True)
    args.full_output.write_text(json.dumps(complete, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    result = {"method": complete["method"], "scope": complete["scope"],
              "full_fact_inventory": "Generated by verify_raw.py into --full-output (ignored cache); excluded from the archive to avoid repeated paths.",
              "boards": {key: summarize(value) for key, value in boards.items()}}
    if args.compare:
        result["production_comparison"] = {}
        for board, expected in boards.items():
            try:
                result["production_comparison"][board] = compare_production(expected, args.input_root / board)
            except Exception as exc:
                result["production_comparison"][board] = {"error": f"{type(exc).__name__}: {exc}"}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "stats": {k: v["stats"] for k, v in boards.items()},
                      "production_comparison": {k: {n: v.get(n) for n in ("all_complete_sets_equal", "comparison_count", "error")}
                                                for k, v in result.get("production_comparison", {}).items()}}, ensure_ascii=False, indent=2))
    if any(board["raw_consistency_problems"] for board in boards.values()):
        raise SystemExit(1)
    if args.compare and any(not value.get("all_complete_sets_equal", False)
                            for value in result["production_comparison"].values()):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
