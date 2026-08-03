#!/usr/bin/env python3
"""Refresh allowlisted parser fixtures from their canonical public URLs."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
import urllib.request
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = ROOT / "tests" / "fixtures"
MANIFEST = FIXTURE_ROOT / "manifest.json"


def load_manifest(path: Path = MANIFEST) -> dict[str, list[dict[str, Any]]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    sources = payload.get("sources")
    if not isinstance(sources, dict):
        raise ValueError("fixture manifest must contain a sources object")
    return sources


def selected_entries(
    sources: dict[str, list[dict[str, Any]]], selected: tuple[str, ...],
) -> list[tuple[str, dict[str, Any]]]:
    unknown = sorted(set(selected) - sources.keys())
    if unknown:
        raise ValueError(f"unknown fixture source(s): {', '.join(unknown)}")
    names = selected or tuple(sorted(sources))
    return [(name, entry) for name in names for entry in sources[name]]


def fetch_fixture(url: str, timeout: float = 30) -> bytes:
    request = urllib.request.Request(url, headers={
        "User-Agent": "nrw-events-fixture-refresh/1.0 (+https://github.com/randomsnowflake/nrw-events)",
    })
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def write_atomic(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", action="append", default=[], help="source id to refresh")
    parser.add_argument("--dry-run", action="store_true", help="list allowlisted requests only")
    args = parser.parse_args()
    sources = load_manifest()
    for source, entry in selected_entries(sources, tuple(args.source)):
        destination = (FIXTURE_ROOT / entry["path"]).resolve()
        if FIXTURE_ROOT.resolve() not in destination.parents:
            raise ValueError(f"fixture path escapes fixture root: {entry['path']}")
        print(f"{source}: {entry['url']} -> {destination.relative_to(ROOT)}")
        if not args.dry_run:
            write_atomic(destination, fetch_fixture(entry["url"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
