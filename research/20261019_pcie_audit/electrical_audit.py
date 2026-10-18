#!/usr/bin/env python3
"""Independent raw-record replay for the two pinned PCIe samples.

This script checks manually reviewed endpoint expectations, not PCIe compliance.
It does not import PureAgent's parser, infer assembly, or simulate a PCIe link.
PDF interpretation is recorded separately in electrical_audit.md.
"""

import argparse
import json
import re
from pathlib import Path


EXPECTED = {
    "j401-fixture": {
        "PCIE1_TX0_P": ["J11.174", "C66.2"],
        "PCIE1_M2_TX0_P": ["C66.1", "J20.35"],
        "PCIE1_TX0_N": ["J11.172", "C65.2"],
        "PCIE1_M2_TX0_N": ["C65.1", "J20.37"],
        "PCIE1_RX0_P": ["J11.169", "J20.41"],
        "PCIE1_RX0_N": ["J11.167", "J20.43"],
        "PCIE1_CLK_P": ["J11.175", "J20.47"],
        "PCIE1_CLK_N": ["J11.173", "J20.49"],
        "PCIE1_CLKREQ": ["J11.182", "J20.53", "R99.2"],
        "PCIE1_RST": ["J11.183", "J20.52", "R153.2"],
        "PCIE_WAKE": ["J11.179", "J18.54", "J20.55", "R100.2"],
        "PCIE0_TX0_P": ["J11.136", "C172.2"],
        "PCIE0_TX0_M2_P": ["C172.1", "J18.49"],
        "PCIE0_TX0_N": ["J11.134", "C170.2"],
        "PCIE0_TX0_M2_N": ["C170.1", "J18.47"],
        "PCIE0_RX0_P": ["J11.133", "J18.43"],
        "PCIE0_RX0_N": ["J11.131", "J18.41"],
        "PCIE0_CLKREQ": ["J11.180", "J18.52"],
        "PCIE0_RST": ["J11.181", "J18.50", "R72.2"],
    },
    "opencellular-breakout": {
        "PCIE0_TXP": ["J113.L4", "J3313.2"],
        "PCIE0_TXN": ["J113.M4", "J3313.3"],
        "PCIE0_RXP": ["J113.L6", "J3314.3"],
        "PCIE0_RXN": ["J113.M6", "J3314.2"],
        "PCIE0_GBE_CLKP": ["J113.L8", "J3313.6"],
        "PCIE0_GBE_CLKN": ["J113.M8", "J3313.5"],
        "UART_RX": ["J113.K3", "J3314.7"],
        "SYNC_INT#": ["J113.E11", "J3314.6"],
        "IRQ_INT": ["J113.J4", "J3314.5"],
    },
}

SERIES_PATHS = [
    {"interface": "J401 PCIe1 P", "nets": ["PCIE1_TX0_P", "PCIE1_M2_TX0_P"],
     "capacitor": "C66", "endpoints": ["J11.174", "C66.2", "C66.1", "J20.35"],
     "raw_value": "100nF"},
    {"interface": "J401 PCIe1 N", "nets": ["PCIE1_TX0_N", "PCIE1_M2_TX0_N"],
     "capacitor": "C65", "endpoints": ["J11.172", "C65.2", "C65.1", "J20.37"],
     "raw_value": "100nF"},
    {"interface": "J401 PCIe0 lane0 P", "nets": ["PCIE0_TX0_P", "PCIE0_TX0_M2_P"],
     "capacitor": "C172", "endpoints": ["J11.136", "C172.2", "C172.1", "J18.49"],
     "raw_value": "220nF"},
    {"interface": "J401 PCIe0 lane0 N", "nets": ["PCIE0_TX0_N", "PCIE0_TX0_M2_N"],
     "capacitor": "C170", "endpoints": ["J11.134", "C170.2", "C170.1", "J18.47"],
     "raw_value": "220nF"},
]


def read_net_records(path):
    text = path.read_text(encoding="utf-8")
    starts = list(re.finditer(r"(?m)^NET_NAME[ \t]*$", text))
    records = {}
    for index, start in enumerate(starts):
        end = starts[index + 1].start() if index + 1 < len(starts) else len(text)
        block = text[start.end():end]
        header = re.match(r"\s*'([^']*)'\s*\n\s*'([^']*)':", block)
        if not header:
            raise ValueError("Unrecognized sample NET_NAME header")
        name, occurrence = header.groups()
        if name in records:
            raise ValueError("Ambiguous short net name in these pinned expectations")
        nodes = []
        pattern = r"(?m)^NODE_NAME[ \t]+(\S+)[ \t]+(\S+)[ \t]*\n\s*'([^']*)':\s*\n\s*'([^']*)':"
        for node in re.finditer(pattern, block):
            ref, pin, inst, label = node.groups()
            nodes.append({"endpoint": f"{ref}.{pin}", "raw_pin_label": label,
                          "occurrence": inst,
                          "source_line": text.count("\n", 0, start.end() + node.start()) + 1})
        if len(nodes) != len(re.findall(r"(?m)^NODE_NAME\b", block)):
            raise ValueError("Unrecognized NODE_NAME in pinned sample")
        records[name] = {"raw_name": name, "raw_path": occurrence,
                         "source_line": text.count("\n", 0, start.start()) + 1,
                         "complete_raw_members": nodes}
    return records


def raw_component_value(folder, ref):
    parts = (folder / "pstxprt.dat").read_text(encoding="utf-8")
    match = re.search(r"(?m)^PART_NAME\s*\n\s*" + re.escape(ref) + r"\s+'([^']*)'", parts)
    if not match:
        raise ValueError(f"No raw PART_NAME for {ref}")
    primitive = match.group(1)
    chips = (folder / "pstchip.dat").read_text(encoding="utf-8")
    body = re.search(r"(?ms)^primitive\s+'" + re.escape(primitive) + r"';(.*?)^end_primitive;", chips)
    value = re.search(r"\bVALUE='([^']*)'", body.group(1))
    return {"primitive": primitive, "raw_value": value.group(1),
            "part_source_line": parts.count("\n", 0, match.start()) + 1,
            "primitive_source_line": chips.count("\n", 0, body.start()) + 1}


def replay(sample_root):
    result = {"kind": "independent_raw_record_replay", "electrical_check_scope": "partial",
              "confirmed_electrical_errors": [], "net_expectations_checked": 0,
              "series_paths_checked": 0, "samples": {}}
    records = {}
    for sample, expectations in EXPECTED.items():
        folder = sample_root / sample
        records[sample] = read_net_records(folder / "pstxnet.dat")
        selected = {}
        for net, expected in expectations.items():
            actual = records[sample][net]
            assert sorted(n["endpoint"] for n in actual["complete_raw_members"]) == sorted(expected), (sample, net)
            selected[net] = actual
            result["net_expectations_checked"] += 1
        result["samples"][sample] = {"provenance": json.loads((folder / "source.json").read_text()),
                                     "selected_nets": selected}
    j401 = result["samples"]["j401-fixture"]
    j401["series_paths"] = []
    for path in SERIES_PATHS:
        left, right = (records["j401-fixture"][name] for name in path["nets"])
        assert left["raw_path"] != right["raw_path"]
        assert not ({n["endpoint"] for n in left["complete_raw_members"]}
                    & {n["endpoint"] for n in right["complete_raw_members"]})
        value = raw_component_value(sample_root / "j401-fixture", path["capacitor"])
        assert value["raw_value"] == path["raw_value"]
        j401["series_paths"].append({**path, **value,
                                      "interpretation": "capacitor separates copper nets; no DC net merge or proven SI pass"})
        result["series_paths_checked"] += 1
    j401["dnp_token_counts"] = {
        f.name: len(re.findall(r"\b(?:DNP|DNI)\b", f.read_text(), re.I))
        for f in (sample_root / "j401-fixture").glob("*.dat")
    }
    assert all(value == 0 for value in j401["dnp_token_counts"].values())
    j401["schematic_dnp_refs_reviewed"] = ["R99", "R100", "R153", "R72"]
    j401["assembly_limitation"] = "DNP seen on official schematic p16/p17; absent from reviewed raw instance/primitive records. No assembled-board population inferred."
    power = records["j401-fixture"]["VDD_3V3_SYS"]
    j401["upstream_pullup_endpoints"] = {
        "net": power["raw_name"], "total_raw_endpoints": len(power["complete_raw_members"]),
        "selected": [n for n in power["complete_raw_members"] if n["endpoint"] in {"R99.1", "R100.1", "R153.1", "R72.1"}],
        "complete_net_shown": False,
    }
    assert len(j401["upstream_pullup_endpoints"]["selected"]) == 4
    result["limitations"] = [
        "Zero confirmed errors applies only to this local interpretation audit; no board or link pass is claimed.",
        "Endpoint tests replay manually reviewed raw facts; they do not rerun datasheet reasoning or a language model.",
        "No board-specific link generation, module population, power sequence, firmware configuration, PCB SI, or attached-device behavior was established.",
        "The two samples are intentionally different provenance classes; fixture export is not relabeled native upstream DAT.",
    ]
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path(__file__).with_suffix(".json"))
    args = parser.parse_args()
    result = replay(args.sample_root)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({key: result[key] for key in ("net_expectations_checked", "series_paths_checked", "confirmed_electrical_errors")}, ensure_ascii=False))
