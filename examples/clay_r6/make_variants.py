"""Create clearly labeled experiment copies; never modify the upstream fixture.

These are controlled error injections, not defects attributed to the Clay board.
The investigator receives only the generated DAT folder. This expected-change
record is retained separately by the experiment coordinator until review ends.
"""
from pathlib import Path
import argparse
import json
import re
import shutil


def generate(output):
    here = Path(__file__).resolve().parent
    original = here / "clay-r6"
    output = Path(output).resolve()
    if output == original.resolve() or original.resolve().is_relative_to(output):
        raise ValueError("Output must not contain or overwrite the original fixture")
    text = (original / "pstxnet.dat").read_text()
    pattern = r"^NODE_NAME\s+U6 10\s*\n[^\n]*\n[^\n]*\n"
    matches = list(re.finditer(pattern, text, re.M))
    if len(matches) != 1:
        raise ValueError("Expected exactly one source endpoint U6.10")
    node = matches[0].group()
    # Move one physical pin to the exporter's explicit unconnected group.
    changed = text[:matches[0].start()] + text[matches[0].end():]
    if not re.search(r"NET_NAME\s*'NC'\s*'NC':\s*C_SIGNAL='NC';", changed):
        raise ValueError("Expected the native NC export group at the end")
    changed = re.sub(r"END\.\s*$", lambda _: node + "END.\n", changed)
    output.mkdir(parents=True, exist_ok=True)
    for case in ("case_b", "case_c"):
        dest = output / case
        dest.mkdir(exist_ok=True)
        for file in original.glob("*.dat"):
            shutil.copyfile(file, dest / file.name)
        (dest / "pstxnet.dat").write_text(changed)
    chip = output / "case_c/pstchip.dat"
    text, removed = re.subn(r"^\s*JEDEC_TYPE='[^']*';\n", "", chip.read_text(), flags=re.M)
    chip.write_text(text)
    truth = {"original_changed": False,
             "case_b": {"change": "U6 physical pin 10 FB moved from VCC to the NC export group",
                        "expected_assertion": "Fixed TPS61201 FB must connect to VOUT; supplied netlist leaves it unconnected"},
             "case_c": {"same_electrical_change_as": "case_b", "removed_JEDEC_TYPE_fields": removed,
                        "expected_assertion": "Same topology error remains decidable without unrelated package strings"}}
    (output / "coordinator_truth.json").write_text(json.dumps(truth, indent=2) + "\n")
    return truth


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parent / ".cache/variants")
    args = parser.parse_args()
    print(json.dumps(generate(args.output), indent=2))
