#!/usr/bin/env python3
"""Fetch the pinned Clay R6 input and datasheets into an ignored local cache.

Requires Python 3, curl, and Poppler pdfinfo. Run beside source.json/datasheets.json:
    python fetch_real_board.py
    python fetch_real_board.py --offline --cache-dir /path/to/existing/research

The script reads only the listed public URLs. DAT/license and archived PDF blobs
must match their pinned Git blob IDs. All PDFs must start with %PDF- and be
readable by pdfinfo. Observed page counts are checked because cited page locations
must be revisited when a manufacturer replaces a document.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent

def load_assets():
    source = json.loads((HERE / "source.json").read_text())
    sheets = json.loads((HERE / "datasheets.json").read_text())
    if not re.fullmatch(r"[0-9a-f]{40}", source["commit"]):
        raise ValueError("source.json must pin an actual full Git commit.")
    return source["files"] + sheets["documents"]

def validate(path, item):
    data = path.read_bytes()
    if not data:
        raise ValueError(f"{path.name}: empty file")
    if item.get("git_blob_sha"):
        actual = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
        if actual != item["git_blob_sha"]:
            raise ValueError(f"{path.name}: does not match the pinned repository blob")
    if item.get("content_sha256") and hashlib.sha256(data).hexdigest() != item["content_sha256"]:
        raise ValueError(f"{path.name}: document contents changed; revisit evidence page bindings")
    if "pdf_pages" in item:
        if not data.startswith(b"%PDF-"):
            raise ValueError(f"{path.name}: response is not a PDF")
        result = subprocess.run(["pdfinfo", str(path)], capture_output=True, text=True)
        if result.returncode:
            raise ValueError(f"{path.name}: pdfinfo cannot open the document")
        match = re.search(r"^Pages:\s+(\d+)", result.stdout, re.MULTILINE)
        if not match:
            raise ValueError(f"{path.name}: pdfinfo returned no page count")
        if int(match.group(1)) != item["pdf_pages"]:
            raise ValueError(f"{path.name}: PDF page count changed; update source/page bindings before using it")

def obtain(item, cache, offline):
    relative = Path(item["cache_path"])
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("cache_path must be a relative path within the cache")
    target = cache / relative
    if target.exists():
        validate(target, item)
        return {"file": str(relative), "status": "cached_valid"}
    if offline:
        raise FileNotFoundError(f"Not cached: {relative}")
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(prefix=".download-", dir=target.parent, delete=False) as handle:
        temporary = Path(handle.name)
    try:
        subprocess.run([
            "curl", "--fail", "--location", "--silent", "--show-error",
            "--max-time", "60", "--output", str(temporary), item["url"]
        ], check=True)
        validate(temporary, item)
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return {"file": str(relative), "status": "downloaded_valid"}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-dir", type=Path, default=HERE / ".cache")
    parser.add_argument("--offline", action="store_true", help="Validate existing cache without network")
    args = parser.parse_args()
    for command in ["pdfinfo"] + ([] if args.offline else ["curl"]):
        if not shutil.which(command):
            parser.error(f"Required command not found: {command}")
    args.cache_dir.mkdir(parents=True, exist_ok=True)
    failures = []
    for item in load_assets():
        try:
            print(json.dumps(obtain(item, args.cache_dir, args.offline), ensure_ascii=False))
        except (OSError, ValueError, subprocess.CalledProcessError) as error:
            failures.append(item["cache_path"])
            print(json.dumps({"file": item["cache_path"], "status": "failed", "reason": str(error)}))
    if failures:
        raise SystemExit(1)
    print(json.dumps({"status": "complete", "cache": str(args.cache_dir.resolve())}))

if __name__ == "__main__":
    main()

