"""JSON tools for an actual Agent session, without a bundled LLM provider.

Example: python -m pureagent --dat examples/clay_r6/clay-r6 serve
Each stdin line is {"tool": "component", "args": {"ref": "U1"}}.
The graph is parsed once and PDF handles are reused for the session lifetime.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from time import perf_counter

from .dat import DatGraph


class ToolSession:
    def __init__(self, dat, *, pdf_dir=None, manifest=None, trace=None):
        self.graph = DatGraph(dat)
        self.pdf_dir = Path(pdf_dir).resolve() if pdf_dir else None
        self.documents = json.loads(Path(manifest).read_text())["documents"] if manifest else []
        self.pdfs = {}
        self.calls = 0
        self.trace = Path(trace) if trace else None
        if self.trace:
            self.trace.parent.mkdir(parents=True, exist_ok=True)

    def _pdf(self, filename):
        from .pdf_tools import PdfDocument
        if self.pdf_dir is None:
            raise ValueError("This session has no PDF directory")
        path = (self.pdf_dir / filename).resolve()
        if not path.is_relative_to(self.pdf_dir):
            raise ValueError("PDF filename must stay within the configured directory")
        if str(path) not in self.pdfs:
            self.pdfs[str(path)] = PdfDocument(path)
        return self.pdfs[str(path)]

    def dispatch(self, tool, args):
        graph_tools = ("overview", "inventory", "component", "pin", "net", "boundary", "source")
        if tool in graph_tools:
            return getattr(self.graph, tool)(**args)
        if tool == "datasheets":
            ref = args.get("ref")
            rows = []
            for document in self.documents:
                refs = document.get("refdes") or []
                if ref is not None and ref not in refs:
                    continue
                row = {"file": Path(document["cache_path"]).name, "refdes": refs}
                # Local PDFs remain discoverable without a manufacturer URL,
                # exact model binding, precomputed page count, or made-up text.
                for key, source in (("model", "document_model"),
                                    ("binding_level", "binding_level"),
                                    ("match_basis", "match_basis"),
                                    ("url", "url"), ("pages", "pdf_pages")):
                    if document.get(source) is not None:
                        row[key] = document[source]
                rows.append(row)
            return rows
        if tool in ("pdf_search", "pdf_page", "pdf_render"):
            args = dict(args)
            pdf = self._pdf(args.pop("file"))
            return getattr(pdf, tool[4:])(**args)
        if tool == "calculate":
            from .calculations import evaluate
            return evaluate(**args)
        if tool == "stats":
            return {"graph_loads": 1, "open_pdfs": len(self.pdfs), "tool_calls": self.calls}
        raise ValueError(f"Unknown tool: {tool}")

    def call(self, request):
        self.calls += 1
        start = perf_counter()
        try:
            result = self.dispatch(request["tool"], request.get("args", {}))
            reply = {"ok": True, "result": result}
        except (KeyError, ValueError, TypeError, IndexError, OSError) as exc:
            reply = {"ok": False, "error": str(exc), "error_type": type(exc).__name__}
        if self.trace:
            # Compact audit trail. Do not redistribute entire vendor PDF pages.
            entry = {"call": self.calls, "tool": request.get("tool"), "args": request.get("args", {}),
                     "ok": reply["ok"], "elapsed_ms": round((perf_counter()-start)*1000, 3)}
            if not reply["ok"]:
                entry["error"] = reply["error"]
            with self.trace.open("a", encoding="utf-8") as out:
                out.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return reply

    def close(self):
        for pdf in self.pdfs.values():
            pdf.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dat", required=True, type=Path)
    parser.add_argument("--pdf-dir", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--trace", type=Path)
    sub = parser.add_subparsers(dest="mode", required=True)
    sub.add_parser("serve", help="JSONL stdin/stdout; one cached graph per process")
    one = sub.add_parser("query", help="One JSON request; creates a new process/session")
    one.add_argument("request")
    opts = parser.parse_args()
    try:
        session = ToolSession(opts.dat, pdf_dir=opts.pdf_dir, manifest=opts.manifest, trace=opts.trace)
    except (OSError, UnicodeError, ValueError) as exc:
        print(json.dumps({"run_status": "not_completed", "input_error": str(exc)}))
        raise SystemExit(2)
    try:
        if opts.mode == "query":
            reply = session.call(json.loads(opts.request))
            print(json.dumps(reply, ensure_ascii=False, indent=2))
            return 0 if reply["ok"] else 2
        for line in sys.stdin:
            if not line.strip():
                continue
            try:
                request = json.loads(line)
                if not isinstance(request, dict):
                    raise ValueError("Request must be a JSON object")
                reply = session.call(request)
            except (ValueError, TypeError) as exc:
                reply = {"ok": False, "error": str(exc)}
            print(json.dumps(reply, ensure_ascii=False), flush=True)
    finally:
        session.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
