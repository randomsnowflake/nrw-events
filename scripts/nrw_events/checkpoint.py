"""Portable, checksummed import outputs for retries without another source fetch.

Identity is supplied by the caller and must cover code, configuration and baseline.
Only JSON outputs are stored; environment variables and credentials are never copied.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def capture(directory: Path, identity: str, files: dict[str, Path]) -> None:
    if not identity or set(files) != {"events", "metadata", "highlights", "series"}:
        raise ValueError("checkpoint needs an identity and all four import outputs")
    events = json.loads(files["events"].read_text())
    metadata = json.loads(files["metadata"].read_text())
    if not isinstance(events, list) or not isinstance(metadata, dict):
        raise ValueError("invalid import output shape")
    if metadata.get("run_status") not in ("healthy", "degraded"):
        raise ValueError("cannot checkpoint an unsuccessful import")
    directory.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".checkpoint-", dir=directory.parent))
    try:
        for name, source in files.items():
            json.loads(source.read_text())
            shutil.copyfile(source, staging / f"{name}.json")
        manifest = {
            "version": 1, "identity": identity,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "run_id": metadata.get("run_id"),
            "files": {f"{name}.json": digest(staging / f"{name}.json") for name in files},
        }
        (staging / "manifest.json").write_text(json.dumps(manifest, sort_keys=True) + "\n")
        # Immutable directories: retries must use a new identity or remove an
        # explicitly rejected checkpoint. Never replace a readable checkpoint.
        os.rename(staging, directory)
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def validate(directory: Path, identity: str, *, max_age_hours: float = 36) -> dict[str, Any]:
    manifest = json.loads((directory / "manifest.json").read_text())
    if manifest.get("version") != 1 or manifest.get("identity") != identity:
        raise ValueError("checkpoint code, configuration or baseline changed")
    age = (datetime.now(timezone.utc) - datetime.fromisoformat(manifest["created_at"])).total_seconds()
    if not 0 <= age <= max_age_hours * 3600:
        raise ValueError("checkpoint expired or timestamp is in the future")
    expected = {f"{name}.json" for name in ("events", "metadata", "highlights", "series")}
    if set(manifest["files"]) != expected:
        raise ValueError("checkpoint output inventory is incomplete")
    for name, checksum in manifest["files"].items():
        path = directory / name
        if path.is_symlink() or digest(path) != checksum:
            raise ValueError(f"checkpoint checksum mismatch: {name}")
    return manifest


def restore(directory: Path, identity: str, files: dict[str, Path]) -> None:
    validate(directory, identity)
    for name, target in files.items():
        if name not in ("events", "metadata", "highlights", "series"):
            raise ValueError("unknown checkpoint output")
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write((directory / f"{name}.json").read_bytes())
        os.replace(temporary, target)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("capture", "restore", "inspect"))
    parser.add_argument("directory", type=Path)
    parser.add_argument("--identity", required=True)
    for name in ("events", "metadata", "highlights", "series"):
        parser.add_argument(f"--{name}", type=Path)
    args = parser.parse_args()
    files = {name: getattr(args, name) for name in ("events", "metadata", "highlights", "series") if getattr(args, name)}
    try:
        if args.command == "capture":
            capture(args.directory, args.identity, files)
        elif args.command == "restore":
            restore(args.directory, args.identity, files)
        else:
            print(json.dumps(validate(args.directory, args.identity), indent=2))
    except (OSError, ValueError, KeyError) as error:
        parser.exit(2, f"checkpoint: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
