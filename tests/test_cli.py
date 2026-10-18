"""An applicable local PDF must not need invented descriptive metadata."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from pureagent.cli import ToolSession


FIXTURE = Path(__file__).resolve().parents[1] / "examples/clay_r6/clay-r6"


class DocumentIndexTests(unittest.TestCase):
    def test_sparse_local_document_stays_discoverable_without_guessing_binding(self):
        with TemporaryDirectory() as directory:
            manifest = Path(directory) / "documents.json"
            manifest.write_text(json.dumps({"documents": [
                {"cache_path": "local/U6.pdf", "refdes": ["U6"]},
                {"cache_path": "local/unassigned.pdf", "url": None},
            ]}))
            session = ToolSession(FIXTURE, manifest=manifest)
            try:
                matched = session.call({"tool": "datasheets", "args": {"ref": "U6"}})
                self.assertEqual(matched, {"ok": True, "result": [{"file": "U6.pdf", "refdes": ["U6"]}]})
                all_docs = session.call({"tool": "datasheets"})
                self.assertTrue(all_docs["ok"])
                self.assertEqual(all_docs["result"][1], {"file": "unassigned.pdf", "refdes": []})
                self.assertEqual(session.call({"tool": "datasheets", "args": {"ref": "U1"}})["result"], [])
                self.assertTrue(session.call({"tool": "overview"})["ok"])
            finally:
                session.close()


if __name__ == "__main__":
    unittest.main()
