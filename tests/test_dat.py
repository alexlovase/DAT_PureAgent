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

    def test_unresolved_library_mapping_is_local_but_truncation_stops(self):
        with TemporaryDirectory() as tmp:
            self.copy_fixture(tmp)
            file = Path(tmp) / "pstchip.dat"
            text = file.read_text()
            file.write_text(text.replace("PIN_NUMBER='(1)'", "PIN_NUMBER='(1:3)'", 1))
            graph = DatGraph(tmp)
            self.assertFalse(graph.overview()["pin_inventory_complete"])
            self.assertEqual(graph.pin("AE1", "1")["declaration"], "absent")
            self.assertEqual(graph.pin("AE1", "1")["nets"], self.graph.pin("AE1", "1")["nets"])
            self.assertEqual(graph.net("UVLO"), self.graph.net("UVLO"))
            self.assertEqual(graph.overview()["connected_endpoints"], 537)
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

    def test_conflicts_are_visible_locally_and_identical_nodes_are_deduplicated(self):
        with TemporaryDirectory() as tmp:
            self.copy_fixture(tmp)
            chip = Path(tmp) / "pstchip.dat"
            text = chip.read_text()
            chip.write_text(text.replace("PIN_NUMBER='(1)';", "PIN_NUMBER='(1)';\nPIN_NUMBER='(2)';", 1))
            graph = DatGraph(tmp)
            self.assertEqual(graph.pin("AE1", "1")["state"], "conflicting_records")
            self.assertEqual(graph.net("UVLO"), self.graph.net("UVLO"))
            chip.write_text(text)
            nets = Path(tmp) / "pstxnet.dat"
            original = nets.read_text()
            node = re.search(r"^NODE_NAME\s+U1 8\s*\n[^\n]*\n[^\n]*\n", original, re.M).group()
            nets.write_text(original.replace(node, node + node, 1))
            graph = DatGraph(tmp)
            self.assertEqual(graph.overview()["connected_endpoints"], 537)
            self.assertEqual(graph.net("N00337"), self.graph.net("N00337"))
            self.assertTrue(any(d["kind"] == "duplicate_endpoint_record" for d in graph.diagnostics))
            nets.write_text(original.replace("INS541", "INS999999", 1))
            graph = DatGraph(tmp)
            disputed = [(c["ref"], p["number"]) for c in graph.components.values()
                        for p in c["pins"].values() if p["mapping_issues"]]
            self.assertTrue(disputed)
            for ref, number in disputed:
                pin = graph.pin(ref, number)
                self.assertEqual(pin["state"], "conflicting_records")
                for name in pin["nets"]:
                    row = next(m for m in graph.net(name)["members"]["items"]
                               if (m["ref"], m["pin"]) == (ref, number))
                    self.assertEqual(row["state"], "conflicting_records")

    def test_two_net_conflict_reaches_net_and_boundary_queries(self):
        with TemporaryDirectory() as tmp:
            self.copy_fixture(tmp)
            file = Path(tmp) / "pstxnet.dat"
            original = file.read_text()
            node = re.search(r"^NODE_NAME\s+U1 8\s*\n[^\n]*\n[^\n]*\n", original, re.M).group()
            marker = re.search(r"^NODE_NAME\s+U6 7\s*\n", original, re.M).start()
            file.write_text(original[:marker] + node + original[marker:])
            graph = DatGraph(tmp)
            row = next(m for m in graph.net("UVLO")["members"]["items"] if m["ref"] == "U1")
            self.assertEqual(row["state"], "conflicting_records")
            external = next(n for n in graph.boundary(["U6"]) if n["net"] == "UVLO")
            self.assertEqual(external["outside"][0]["state"], "conflicting_records")
            self.assertEqual(graph.overview()["electrical_nets"], 164)

    def test_ambiguous_nc_group_never_becomes_a_conductive_net(self):
        with TemporaryDirectory() as tmp:
            self.copy_fixture(tmp)
            file = Path(tmp) / "pstxnet.dat"
            file.write_text(file.read_text().replace("C_SIGNAL='NC';", "C_SIGNAL='NC', C_SIGNAL='OTHER';"))
            graph = DatGraph(tmp)
            self.assertEqual(graph.overview()["electrical_nets"], 164)
            self.assertEqual(graph.overview()["connected_endpoints"], 537)
            self.assertEqual(graph.pin("U7", "9")["state"], "conflicting_records")
            self.assertEqual(graph.pin("U7", "9")["nets"], [])
            self.assertIn("ambiguous_nc_group", graph.pin("U7", "9")["mapping_issues"])

    def test_optional_attribute_disagreement_does_not_change_connections(self):
        with TemporaryDirectory() as tmp:
            self.copy_fixture(tmp)
            file = Path(tmp) / "pstchip.dat"
            file.write_text(file.read_text().replace("JEDEC_TYPE='ANT_1206';", "JEDEC_TYPE='ANT_1206'; JEDEC_TYPE='OTHER';"))
            graph = DatGraph(tmp)
            self.assertEqual(graph.pin("AE1", "1"), self.graph.pin("AE1", "1"))
            self.assertEqual(graph.component("AE1", detail=True)["raw_attributes"]["JEDEC_TYPE"], ["ANT_1206", "OTHER"])

    def test_multisection_alphanumeric_pins_and_zero_placeholders(self):
        # Reduced form of the actual multi-section connector exports. A bad
        # section path stays disputed, while unrelated endpoints remain usable.
        chips = """FILE_TYPE=LIBRARY_PARTS;
primitive 'CONNECTOR'; pin
'TX_P': PIN_NUMBER='(A1,0)';
'TX_N': PIN_NUMBER='(A2,0)';
'CTRL': PIN_NUMBER='(0,B1)';
end_pin; body VALUE='CONN'; end_body; end_primitive; END."""
        parts = """FILE_TYPE=EXPANDEDPARTLIST;
PART_NAME J1 'CONNECTOR':;
SECTION_NUMBER 1 '@BOARD:J1A': SECTION='A';
SECTION_NUMBER 2 '@BOARD:J1B': SECTION='B';
END."""
        nets = """FILE_TYPE=EXPANDEDNETLIST;
NET_NAME 'TX_P' '@BOARD:TX_P': C_SIGNAL='TX_P';
NODE_NAME J1 A1 '@BOARD:J1A': 'TX_P':;
NET_NAME 'TX_N' '@BOARD:TX_N': C_SIGNAL='TX_N';
NODE_NAME J1 A2 '@BOARD:J1A': 'TX_N':;
NET_NAME 'CTRL' '@BOARD:CTRL': C_SIGNAL='CTRL';
NODE_NAME J1 B1 '@BOARD:J1B': 'CTRL':;
END."""
        with TemporaryDirectory() as tmp:
            for filename, text in [("pstchip.dat", chips), ("pstxprt.dat", parts), ("pstxnet.dat", nets)]:
                (Path(tmp) / filename).write_text(text)
            graph = DatGraph(tmp)
            self.assertEqual(set(graph.components["J1"]["pins"]), {"A1", "A2", "B1"})
            self.assertEqual(graph.pin("J1", "B1", detail=True)["sections"], ["2"])
            self.assertEqual(graph.pin("J1", "B1")["singleton_nets"], ["CTRL"])
            self.assertEqual(graph.diagnostics, [])
            (Path(tmp) / "pstxnet.dat").write_text(nets.replace("J1 B1 '@BOARD:J1B'", "J1 B1 '@BOARD:J1A'"))
            graph = DatGraph(tmp)
            self.assertEqual(graph.pin("J1", "B1")["state"], "conflicting_records")
            self.assertEqual(graph.pin("J1", "A1")["state"], "connected")

    def test_power_pin_declarations_preserve_physical_pins_without_guessing_nets(self):
        with TemporaryDirectory() as tmp:
            self.copy_fixture(tmp)
            file = Path(tmp) / "pstchip.dat"
            file.write_text(file.read_text().replace("PART_NAME='ANTENNA';", "PART_NAME='ANTENNA'; POWER_PINS='(VCC:3)'; POWER_PINS='(GND:4)';", 1))
            graph = DatGraph(tmp)
            self.assertEqual(graph.pin("AE1", "3")["state"], "connection_unspecified")
            self.assertEqual(graph.pin("AE1", "3", detail=True)["power_declarations"], ["VCC"])
            self.assertEqual(graph.pin("AE1", "4", detail=True)["power_declarations"], ["GND"])
            self.assertEqual(graph.overview()["connected_endpoints"], 537)


if __name__ == "__main__":
    unittest.main()
