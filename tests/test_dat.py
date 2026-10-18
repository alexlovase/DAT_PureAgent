"""Contract regressions anchored in the original native DAT, not UI strings."""
from pathlib import Path
from tempfile import TemporaryDirectory
import re
import shutil
import unittest

from pureagent import DatGraph, DatError

FIXTURE = Path(__file__).resolve().parents[1] / "examples/clay_r6/clay-r6"


class NativeDatContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.graph = DatGraph(FIXTURE)

    def copy_fixture(self, directory):
        for path in FIXTURE.glob("*.dat"):
            shutil.copyfile(path, Path(directory) / path.name)

    def test_every_raw_endpoint_and_nc_declaration(self):
        # Independent scan of native records; swapping two networks with the
        # same endpoint count must still fail this comparison.
        raw = (FIXTURE / "pstxnet.dat").read_text()
        connected, nc = set(), set()
        for block in re.split(r"^NET_NAME\s*$", raw, flags=re.M)[1:]:
            head = re.match(r"\s*'([^']*)'\s*'([^']*)':", block)
            name, path = head.groups()
            pairs = re.findall(r"^NODE_NAME\s+(\S+)\s+(\S+)", block, re.M)
            if name == path == "NC":
                nc.update(pairs)
            else:
                connected.update((path, ref, pin) for ref, pin in pairs)
        actual = {(path, m["ref"], m["pin"]) for path, net in self.graph.nets.items() for m in net["members"]}
        actual_nc = {(c["ref"], p["number"]) for c in self.graph.components.values() for p in c["pins"].values() if p["nc_exported"]}
        declared_nc = {(c["ref"], p["number"]) for c in self.graph.components.values() for p in c["pins"].values() if p["nc_declared"]}
        self.assertEqual(actual, connected)
        self.assertEqual(len(actual), 537)
        self.assertEqual(actual_nc, nc)
        self.assertEqual(len(nc), 9)
        self.assertEqual(declared_nc, {("AE1", "2"), ("AE2", "2")})
        with self.assertRaises(KeyError):
            self.graph.net("NC")

    def test_pin_states_do_not_confuse_absence_or_singletons(self):
        self.assertEqual(self.graph.pin("AE1", "2")["state"], "unconnected")
        self.assertEqual(self.graph.pin("U7", "9")["state"], "unconnected")
        self.assertEqual(self.graph.pin("U7", "999")["state"], "pin_not_declared")
        lone = next(n for n in self.graph.nets.values() if len(n["members"]) == 1)
        m = lone["members"][0]
        self.assertEqual(self.graph.pin(m["ref"], m["pin"])["state"], "connected")

    def test_paging_recovers_tail_and_all_instances(self):
        rows, offset = [], 0
        while offset is not None:
            page = self.graph.net("GND", offset=offset, limit=17)["members"]
            rows.extend(page["items"])
            offset = page["next_offset"]
        self.assertEqual(len(rows), 103)
        self.assertEqual(len({(m["ref"], m["pin"]) for m in rows}), 103)
        refs = [r["ref"] for o in range(0, 133, 13) for r in self.graph.inventory(o, 13)["items"]]
        self.assertEqual(set(refs), set(self.graph.components))
        self.assertEqual(len(refs), 133)

    def test_optional_identity_removed_without_changing_wiring(self):
        with TemporaryDirectory() as tmp:
            self.copy_fixture(tmp)
            chip = Path(tmp) / "pstchip.dat"
            chip.write_text(re.sub(r"^\s*(JEDEC_TYPE|MPN)='[^']*';\n", "", chip.read_text(), flags=re.M))
            graph = DatGraph(tmp)
            def endpoints(g):
                return {(path, m["ref"], m["pin"]) for path, n in g.nets.items() for m in n["members"]}
            self.assertEqual(endpoints(graph), endpoints(self.graph))
            self.assertEqual(graph.pin("D1", "2"), self.graph.pin("D1", "2"))
            self.assertEqual(graph.component("U1")["value"], "LM3658")

    def test_missing_library_metadata_keeps_connected_stub_and_unknown_load(self):
        with TemporaryDirectory() as tmp:
            self.copy_fixture(tmp)
            parts = Path(tmp) / "pstxprt.dat"
            text = parts.read_text().replace("R1 'RESISTOR_R0402_30'", "R1 'UNIDENTIFIED_PART'")
            self.assertNotEqual(text, parts.read_text())
            parts.write_text(text)
            graph = DatGraph(tmp)
            self.assertEqual(graph.pin("R1", "1")["state"], "connected")
            boundary = graph.boundary(["D1", "U1"])
            outside = {(p["ref"], p["pin"]) for n in boundary for p in n["outside"]}
            self.assertIn(("R1", "2"), outside)
            self.assertEqual(graph.component("R1")["value"], None)

    def test_known_pin_omitted_without_nc_is_unspecified(self):
        with TemporaryDirectory() as tmp:
            self.copy_fixture(tmp)
            file = Path(tmp) / "pstxnet.dat"
            text, n = re.subn(r"^NODE_NAME\s+U1 8\s*\n[^\n]*\n[^\n]*\n", "", file.read_text(), flags=re.M)
            self.assertEqual(n, 1)
            file.write_text(text)
            self.assertEqual(DatGraph(tmp).pin("U1", "8")["state"], "connection_unspecified")

    def test_unsupported_physical_mapping_and_truncated_file_fail(self):
        with TemporaryDirectory() as tmp:
            self.copy_fixture(tmp)
            file = Path(tmp) / "pstchip.dat"
            text = file.read_text()
            file.write_text(text.replace("PIN_NUMBER='(1)'", "PIN_NUMBER='(1,3)'", 1))
            with self.assertRaises(DatError):
                DatGraph(tmp)
            file.write_text(text.removesuffix("END."))
            with self.assertRaises(DatError):
                DatGraph(tmp)

    def test_no_name_based_rewiring_or_dnp_deletion(self):
        self.assertEqual(self.graph.pin("U1", "4")["nets"], ["EN_B"])
        self.assertEqual(self.graph.pin("U1", "5")["nets"], ["USB_SEL"])
        self.assertEqual(self.graph.pin("D1", "2")["names"], ["A"])
        self.assertEqual(self.graph.component("L6")["value"], "DNP")
        self.assertEqual(self.graph.component("L6")["assembly"], "unspecified")
        self.assertEqual(self.graph.overview()["physical_pins"], 548)

    def test_conflicting_declarations_duplicate_members_and_mixed_occurrences(self):
        with TemporaryDirectory() as tmp:
            self.copy_fixture(tmp)
            chip = Path(tmp) / "pstchip.dat"
            text = chip.read_text()
            chip.write_text(text.replace("PIN_NUMBER='(1)';", "PIN_NUMBER='(1)';\nPIN_NUMBER='(2)';", 1))
            with self.assertRaisesRegex(DatError, "conflicting property PIN_NUMBER"):
                DatGraph(tmp)
            chip.write_text(text)
            nets = Path(tmp) / "pstxnet.dat"
            original = nets.read_text()
            node = re.search(r"^NODE_NAME\s+U1 8\s*\n[^\n]*\n[^\n]*\n", original, re.M).group()
            nets.write_text(original.replace(node, node + node, 1))
            with self.assertRaisesRegex(DatError, "duplicate endpoint"):
                DatGraph(tmp)
            nets.write_text(original.replace("INS541", "INS999999", 1))
            with self.assertRaisesRegex(DatError, "occurrence path"):
                DatGraph(tmp)


if __name__ == "__main__":
    unittest.main()
