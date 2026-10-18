#!/usr/bin/env python3
"""Fetch and verify the two pinned PCIe netlist samples and their source assets.

Default cache: <repository>/.cache/pcie-inputs, independent of the working directory.
Every sources.json asset keeps its local_path relative to that cache. Valid cached
assets are reused. Online acquisition also writes each fixture's complete source
entry to <cache_directory>/source.json for electrical_audit.py provenance.

--offline performs validation only: no network requests or file writes.
--with-reference-docs adds the two NVIDIA-authored PDFs in electrical_audit.sources.json
under reference-docs/. One is a historical design guide hosted by a distributor;
its original source_kind and revision remain in the reference source manifest.

Only Python's standard library and curl are required. PDF validation here checks
the %PDF- header, not page rendering, textual completeness, or document currency.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import subprocess
import tempfile


HERE = Path(__file__).resolve().parent
REPOSITORY_ROOT = HERE.parents[1]
REFERENCE_FILES = {
    "orin_design_guide_v11": "NVIDIA_OrinNXNano_DesignGuide_v1.1_mouser.pdf",
    "orin_carrier_spec": "NVIDIA_OrinNano_CarrierSpec.pdf",
}


def cache_path(cache, relative):
    """Resolve manifest POSIX paths without allowing paths outside the cache."""
    relative = PurePosixPath(relative)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"Expected a relative cache path: {relative}")
    target = cache.joinpath(*relative.parts).resolve()
    if not target.is_relative_to(cache):
        raise ValueError(f"Cache path escapes its directory: {relative}")
    return target


def verify(path, asset):
    data = path.read_bytes()
    if len(data) != asset["bytes"]:
        raise ValueError(f"Byte count mismatch: expected {asset['bytes']}, got {len(data)}")
    if "git_blob" in asset:
        actual = hashlib.sha1(b"blob " + str(len(data)).encode("ascii") + b"\0" + data).hexdigest()
        if actual != asset["git_blob"]:
            raise ValueError("Git blob SHA mismatch")
    elif "sha256" in asset:
        if hashlib.sha256(data).hexdigest() != asset["sha256"]:
            raise ValueError("SHA256 mismatch")
    else:
        raise ValueError("Source manifest has no expected content digest")
    # The temporary download filename ends in .part, so use the asset's real path.
    if PurePosixPath(asset["local_path"]).suffix.lower() == ".pdf" and not data.startswith(b"%PDF-"):
        raise ValueError("Expected PDF header; received non-PDF content")
    return len(data)


def obtain(cache, asset, offline):
    target = cache_path(cache, asset["local_path"])
    if target.exists():
        try:
            size = verify(target, asset)
            return {"path": asset["local_path"], "status": "verified_cached", "bytes": size}
        except (OSError, ValueError):
            if offline:
                raise
    elif offline:
        raise FileNotFoundError("Asset is missing from the offline cache")

    target.parent.mkdir(parents=True, exist_ok=True)
    # A failed or changed download cannot replace an existing asset until verified.
    with tempfile.NamedTemporaryFile(dir=target.parent, prefix="." + target.name + ".", suffix=".part", delete=False) as handle:
        temporary = Path(handle.name)
    try:
        subprocess.run([
            "curl", "--fail", "--location", "--silent", "--show-error",
            "--retry", "2", "--retry-delay", "1", "--retry-max-time", "45",
            "--connect-timeout", "15", "--max-time", "60",
            "--output", str(temporary), asset["url"],
        ], check=True)
        size = verify(temporary, asset)
        temporary.replace(target)
        return {"path": asset["local_path"], "status": "downloaded_verified", "bytes": size}
    finally:
        temporary.unlink(missing_ok=True)


def reference_assets():
    document_manifest = json.loads((HERE / "electrical_audit.sources.json").read_text(encoding="utf-8"))
    documents = {document["id"]: document for document in document_manifest["documents"]}
    return [{**documents[document_id], "local_path": "reference-docs/" + filename}
            for document_id, filename in REFERENCE_FILES.items()]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-dir", type=Path, default=REPOSITORY_ROOT / ".cache" / "pcie-inputs")
    parser.add_argument("--offline", action="store_true", help="Validate existing assets only; no network or file writes.")
    parser.add_argument("--with-reference-docs", action="store_true", help="Also acquire and verify the two NVIDIA-authored reference PDFs.")
    args = parser.parse_args()
    cache = args.cache_dir.expanduser().resolve()
    manifest = json.loads((HERE / "sources.json").read_text(encoding="utf-8"))
    fixtures = manifest["fixtures"]
    assets = [asset for fixture in fixtures for asset in fixture["assets"]]
    references = reference_assets() if args.with_reference_docs else []
    results, errors = [], []
    for asset in assets + references:
        try:
            results.append(obtain(cache, asset, args.offline))
        except (OSError, ValueError, subprocess.CalledProcessError) as exc:
            errors.append({"path": asset["local_path"], "error": f"{type(exc).__name__}: {exc}"})

    written_manifests = []
    failed_paths = {item["path"] for item in errors}
    if not args.offline:
        for fixture in fixtures:
            if any(asset["local_path"] in failed_paths for asset in fixture["assets"]):
                continue
            target = cache_path(cache, fixture["cache_directory"] + "/source.json")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(fixture, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            written_manifests.append(str(target.relative_to(cache)))
        if references and not any(asset["local_path"] in failed_paths for asset in references):
            target = cache_path(cache, "reference-docs/source.json")
            target.write_text(json.dumps({"documents": references}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            written_manifests.append(str(target.relative_to(cache)))

    summary = {
        "cache_directory": str(cache), "offline": args.offline,
        "requested_assets": len(assets) + len(references), "sample_assets": len(assets),
        "reference_assets": len(references), "verified_assets": len(results),
        "downloaded_assets": sum(item["status"] == "downloaded_verified" for item in results),
        "source_manifests_written": written_manifests, "assets": results, "errors": errors,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
