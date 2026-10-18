"""Independent audit probes; mutate temporary fixtures, never production source.

Run from the repository root:
    python research/20261019_pcie_audit/audit_findings.probes.py

Results describe observed behavior. A reproduced limitation is not a positive
electrical result and synthetic changes are not defects on the original board.
"""
from __future__ import annotations

from copy import deepcopy
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from pureagent import DatGraph
from pureagent.calculations import evaluate
from pureagent.report import build_report
from pureagent.cli import ToolSession


FIXTURE = ROOT / "examples/clay_r6/clay-r6"
RAW = {name: (FIXTURE / name).read_text() for name in
       ("pstchip.dat", "pstxprt.dat", "pstxnet.dat")}
BASE = DatGraph(FIXTURE)
RESULTS = []


def facts(graph):
    return sorted((path, member["ref"], member["pin"])
                  for path, net in graph.nets.items()
                  for member in net["members"])


def probe_dat(name, mutation, inspect=None):
    with TemporaryDirectory(prefix="pureagent_flex_audit_") as temp:
        raw = deepcopy(RAW)
        mutation(raw)
        for filename, content in raw.items():
            (Path(temp) / filename).write_text(content)
        try:
            graph = DatGraph(temp)
            result = {"loaded": True, "overview": graph.overview(),
                      "same_ordinary_endpoint_multiset_as_original": facts(graph) == facts(BASE)}
            if inspect:
                result["inspection"] = inspect(graph)
        except Exception as exc:
            result = {"loaded": False, "error_type": type(exc).__name__, "error": str(exc)}
        RESULTS.append({"probe": name, "observed": result})


def replace(name, old, new, count=1):
    def mutation(raw):
        assert old in raw[name]
        raw[name] = raw[name].replace(old, new, count)
    return mutation


NODE = re.search(r"^NODE_NAME\s+U1 8\s*\n[^\n]*\n[^\n]*\n", RAW["pstxnet.dat"], re.M).group()
probe_dat("identical_same_net_endpoint_repetition",
          replace("pstxnet.dat", NODE, NODE + NODE))

probe_dat("same_endpoint_second_logical_name",
          replace("pstxnet.dat", NODE, NODE + NODE.replace("'ISET'", "'ISET_ALIAS'")))

probe_dat("optional_package_conflict",
          replace("pstchip.dat", "JEDEC_TYPE='ANT_1206';",
                  "JEDEC_TYPE='ANT_1206';\n    JEDEC_TYPE='UNKNOWN_PACKAGE';"))

probe_dat("missing_one_library_pin_number_explicit_nodes_survive_in_raw",
          replace("pstchip.dat", "      PIN_NUMBER='(1)';\n", ""))

first_primitive = re.search(r"primitive '[^']+';.*?end_primitive;", RAW["pstchip.dat"], re.S).group()
unused = first_primitive.replace("ANTENNA_ANT_1206_2450AT18A100E", "UNUSED_AUDIT_PRIMITIVE")
unused = unused.replace("    NC_PINS='(2)';", "    POWER_PINS='(VCC:2)';")
probe_dat("unused_library_primitive_with_unsupported_power_attribute",
          replace("pstchip.dat", "END.", unused + "\nEND."))

probe_dat("identical_primitive_repetition",
          replace("pstchip.dat", first_primitive, first_primitive + "\n" + first_primitive))

def conflict(raw):
    marker = "'@CLAYR6.SCHEMATIC1(SCH_1):UVLO':"
    start = raw["pstxnet.dat"].index(marker)
    target = raw["pstxnet.dat"].index("NODE_NAME", start)
    raw["pstxnet.dat"] = raw["pstxnet.dat"][:target] + NODE + raw["pstxnet.dat"][target:]

probe_dat("same_physical_pin_on_two_distinct_nets", conflict,
          lambda g: {"pin": g.pin("U1", "8"), "uvlo_net": g.net("UVLO"),
                     "uvlo_boundary": [n for n in g.boundary(["U6"]) if n["net"] == "UVLO"],
                     "diagnostics": g.diagnostics})

def missing_optional(raw):
    raw["pstchip.dat"] = re.sub(r"^\s*(JEDEC_TYPE|MPN)='[^']*';\n", "", raw["pstchip.dat"], flags=re.M)

probe_dat("optional_identity_missing_control", missing_optional)

probe_dat("missing_library_definition_control",
          replace("pstxprt.dat", "R1 'RESISTOR_R0402_30'", "R1 'UNKNOWN_PRIMITIVE'"),
          lambda g: {"r1": g.component("R1"),
                     "boundary_n33179": [n for n in g.boundary(["DS1"]) if n["net"] == "N33179"]})

def record():
    return {"id": "explicit_ideal_series_resistors",
            "inputs": {"r1": {"value": 1000, "unit": "ohm", "source": "synthetic R1 fixed value"},
                       "r2": {"value": 2000, "unit": "ohm", "source": "synthetic R2 fixed value"}},
            "expression": "r1 + r2", "result_unit": "ohm", "assumptions": [],
            "validity": {"status": "valid", "reason": "Both ideal resistance values are defined by the synthetic fixture."}}

RESULTS.append({"probe": "calculation_baseline", "observed": evaluate(record())})
extra = record()
extra["inputs"]["unrelated_external_current"] = {
    "value": None, "unit": "A", "source": "Not provided; this variable is absent from the expression."}
RESULTS.append({"probe": "unused_unknown_input_in_payload", "observed": evaluate(extra)})
missing = record()
missing["inputs"]["r1"]["source"] = None
RESULTS.append({"probe": "referenced_unsourced_input_control", "observed": evaluate(missing)})
literal = record()
literal["inputs"] = {}
literal["expression"] = "1000 + 2000"
RESULTS.append({"probe": "numeric_literals_with_no_declared_inputs", "observed": evaluate(literal)})

candidate = {"id": "synthetic_supplied_rail_short", "investigator": "a",
             "why": "Synthetic supplied interface contract assigns J1.1 to +12 V and J1.2 to return; both physical contacts are on one net.",
             "recommendation": "Separate the supply and return contacts according to the supplied interface contract."}
review = {"candidate_id": candidate["id"], "reviewer": "b", "verdict": "confirmed",
          "raw_connection_evidence": "Synthetic net N1 has J1.1 and J1.2.",
          "requirement_evidence": "Synthetic user-supplied interface contract: J1.1 is +12 V, J1.2 is return."}
coverage = {"investigated_count": 1, "total_count": 1, "full_board": False, "scope": "Synthetic report-schema probe only."}
RESULTS.append({"probe": "requirement_evidence_without_datasheet",
                "observed": build_report([candidate], [review], coverage)})
placeholder = deepcopy(review)
placeholder["datasheet_evidence"] = "Not applicable: no datasheet was used."
RESULTS.append({"probe": "nonempty_datasheet_placeholder",
                "observed": build_report([candidate], [placeholder], coverage)})

boundary = BASE.boundary(["U6"])
RESULTS.append({"probe": "actual_u6_complete_boundary_size",
                "observed": {"nets": len(boundary), "outside_endpoints": sum(len(n["outside"]) for n in boundary),
                             "by_net": [{"net": n["net"], "outside_endpoints": len(n["outside"])} for n in boundary],
                             "uvlo_complete_net": BASE.net("UVLO")}})

with TemporaryDirectory(prefix="pureagent_manifest_audit_") as temp:
    manifest = Path(temp) / "manifest.json"
    manifest.write_text(json.dumps({"documents": [{"cache_path": "local.pdf", "refdes": ["U6"], "document_model": "TPS61201"}]}))
    session = ToolSession(FIXTURE, manifest=manifest)
    reply = session.call({"tool": "datasheets", "args": {"ref": "U6"}})
    overview = session.call({"tool": "overview"})
    session.close()
    RESULTS.append({"probe": "sparse_document_index",
                    "observed": {"datasheets_reply": reply, "graph_query_still_works": overview["ok"]}})

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--output", type=Path, default=Path(__file__).with_name("audit_findings.after.json"))
parser.add_argument("--phase", default="after audit fixes")
args = parser.parse_args()
out = {"scope": "Independent implementation flexibility audit; all fixture mutations are synthetic; this probe script does not edit production source.",
       "reference_baseline_commit": "3f1a7dcdce7a88cfbdb214eb8a7de48340f4425e",
       "measurement_phase": args.phase,
       "source_sha256": {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                         for p in [ROOT / "pureagent" / f for f in ("dat.py", "calculations.py", "report.py", "cli.py")]},
       "probe_count": len(RESULTS), "results": RESULTS}
target = args.output
target.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n")
print(json.dumps(out, indent=2, ensure_ascii=False))
