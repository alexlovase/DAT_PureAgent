"""Small PDF tools with one open document and lazy, reusable page text.

Page numbers are 1-based throughout. Search is literal, counts all occurrences,
and limits only the returned contexts. This module does not perform OCR.

Examples::

    python -m pureagent.pdf_tools search datasheet.pdf "absolute maximum"
    python -m pureagent.pdf_tools page datasheet.pdf 4
    python -m pureagent.pdf_tools render datasheet.pdf 4 page-4.png
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re

import fitz


class PdfDocument:
    """Open a PDF once; use ``with PdfDocument(path) as pdf`` to close it.

    Text is extracted once per requested page and cached for this instance.
    Image-only pages have empty text; ``render`` can expose their visual content.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser().resolve()
        with self.path.open("rb") as stream:
            signature = stream.read(8)
        if not signature.startswith(b"%PDF-"):
            raise ValueError(f"Not a PDF: missing %PDF- signature in {self.path.name}")
        try:
            document = fitz.open(str(self.path), filetype="pdf")
        except (RuntimeError, ValueError) as exc:
            raise ValueError(f"Cannot open PDF {self.path.name}: {exc}") from exc
        try:
            if not document.is_pdf:
                raise ValueError(f"Not a PDF: {self.path.name}")
            if document.needs_pass:
                raise ValueError(f"Password-protected PDF is not supported: {self.path.name}")
            if document.page_count < 1:
                raise ValueError(f"PDF has no pages: {self.path.name}")
        except Exception:
            document.close()
            raise
        self._document = document
        self._texts: dict[int, str] = {}

    @property
    def page_count(self) -> int:
        self._ensure_open()
        return self._document.page_count

    def _ensure_open(self) -> None:
        if self._document.is_closed:
            raise ValueError("PDF document is closed")

    def _page_index(self, page_number: int) -> int:
        self._ensure_open()
        if isinstance(page_number, bool) or not isinstance(page_number, int):
            raise TypeError("page_number must be a 1-based integer")
        if not 1 <= page_number <= self._document.page_count:
            raise IndexError(
                f"Page {page_number} is out of range; valid pages are "
                f"1..{self._document.page_count}"
            )
        return page_number - 1

    def _page_text(self, page_number: int) -> str:
        index = self._page_index(page_number)
        if page_number not in self._texts:
            self._texts[page_number] = self._document.load_page(index).get_text("text")
        return self._texts[page_number]

    def search(
        self, query: str, case_sensitive: bool = False, limit: int = 20
    ) -> dict:
        """Return page-numbered contexts plus the untruncated occurrence count.

        ``limit=0`` returns only the count. Matches are non-overlapping and do
        not cross page boundaries. PDF extraction preserves line breaks, so a
        phrase split across lines may require shorter search terms.
        """
        self._ensure_open()
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query must be a non-empty string")
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
            raise ValueError("limit must be a non-negative integer")
        pattern = re.compile(re.escape(query), 0 if case_sensitive else re.IGNORECASE)
        matches = []
        total = 0
        for page_number in range(1, self.page_count + 1):
            text = self._page_text(page_number)
            for match in pattern.finditer(text):
                total += 1
                if len(matches) >= limit:
                    continue
                start = max(0, match.start() - 100)
                end = min(len(text), match.end() + 100)
                context = " ".join(text[start:end].split())
                if start:
                    context = "…" + context
                if end < len(text):
                    context += "…"
                matches.append({"page_number": page_number, "context": context})
        return {
            "query": query,
            "matches": matches,
            "total_matches": total,
            "truncated": total > len(matches),
        }

    def page(self, page_number: int) -> dict:
        """Return the full extracted page text with its 1-based page number."""
        return {"page_number": page_number, "text": self._page_text(page_number)}

    def render(self, page_number: int, output_path: str | Path, dpi: int = 120) -> str:
        """Render one page as PNG and return its absolute path."""
        index = self._page_index(page_number)
        if isinstance(dpi, bool) or not isinstance(dpi, int) or dpi <= 0:
            raise ValueError("dpi must be a positive integer")
        output = Path(output_path).expanduser().resolve()
        if output.suffix.lower() != ".png":
            raise ValueError("output_path must have a .png extension")
        output.parent.mkdir(parents=True, exist_ok=True)
        pixmap = self._document.load_page(index).get_pixmap(dpi=dpi, alpha=False)
        pixmap.save(str(output), output="png")
        return str(output)

    def close(self) -> None:
        if not self._document.is_closed:
            self._document.close()
        self._texts.clear()

    def __enter__(self) -> PdfDocument:
        self._ensure_open()
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    search = commands.add_parser("search", help="Find literal text across all pages")
    search.add_argument("file")
    search.add_argument("query")
    search.add_argument("--case-sensitive", action="store_true")
    search.add_argument("--limit", type=int, default=20)
    page = commands.add_parser("page", help="Read the complete text of one page")
    page.add_argument("file")
    page.add_argument("page_number", type=int)
    render = commands.add_parser("render", help="Render one page to a PNG file")
    render.add_argument("file")
    render.add_argument("page_number", type=int)
    render.add_argument("output_path")
    render.add_argument("--dpi", type=int, default=120)
    args = parser.parse_args(argv)
    try:
        with PdfDocument(args.file) as document:
            if args.command == "search":
                result = document.search(args.query, args.case_sensitive, args.limit)
            elif args.command == "page":
                result = document.page(args.page_number)
            else:
                result = {
                    "page_number": args.page_number,
                    "image_path": document.render(args.page_number, args.output_path, args.dpi),
                }
    except (OSError, ValueError, TypeError, IndexError, RuntimeError) as exc:
        parser.exit(2, f"error: {exc}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
