#!/usr/bin/env python3
"""Fetch only the pinned upstream files needed by these local research probes.

Uses the Python standard library. Does not install packages or execute sources.
Downloaded third-party files belong in the ignored _vendor directory.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path, PurePosixPath
import re
from urllib.parse import quote
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent


def fetch_file(job):
    project, relative = job
    path = PurePosixPath(relative)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"Invalid source path: {relative}")
    commit = project["commit"]
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("A full immutable commit ID is required")
    url = (f"https://raw.githubusercontent.com/{project['repository']}/"
           f"{commit}/{quote(relative, safe='/')}")
    request = Request(url, headers={"User-Agent": "DAT-netlist-research-probes/1"})
    with urlopen(request, timeout=20) as response:
        body = response.read()
    destination = ROOT / "_vendor" / project["id"] / path
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(body)
    return f"{project['id']}/{relative}"


def main():
    manifest = json.loads((ROOT / "sources.json").read_text(encoding="utf-8"))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", action="append",
                        choices=[p["id"] for p in manifest["projects"]],
                        help="Fetch only this project (repeatable; default: all)")
    args = parser.parse_args()
    projects = [p for p in manifest["projects"]
                if args.project is None or p["id"] in args.project]
    jobs = [(project, name) for project in projects for name in project["files"]]
    with ThreadPoolExecutor(max_workers=4) as executor:
        downloaded = list(executor.map(fetch_file, jobs))
    print(json.dumps({"downloaded_files": len(downloaded), "files": downloaded},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
