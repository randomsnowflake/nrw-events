"""Strict semantic snapshot comparison with path-scoped volatile exclusions."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

# A star matches exactly one path component, never an arbitrary subtree.
# In particular, events/*/run_id identifies a series occurrence and is semantic.
VOLATILE_PATHS = (
    ("run_id",),
    ("generated_at",),
    ("events_path",),  # Local output file, not a public event URL.
    ("timings", "source_import_duration_ms"),
    ("timings", "ai_processing_duration_ms"),
    ("timings", "total_import_duration_ms"),
    ("source_results", "*", "duration_ms"),
    ("source_results", "*", "ai_duration_ms"),
    ("source_results", "*", "endpoints", "*", "duration_ms"),
)


def _volatile(path: tuple[str, ...]) -> bool:
    return any(
        len(path) == len(pattern)
        and all(expected == "*" or actual == expected for actual, expected in zip(path, pattern, strict=True))
        for pattern in VOLATILE_PATHS
    )


def differences(left: Any, right: Any) -> list[str]:
    """Return deterministic JSON-pointer diagnostics, without copying event prose."""
    result: list[str] = []

    def report(path: tuple[str, ...], reason: str) -> None:
        pointer = "/" + "/".join(part.replace("~", "~0").replace("/", "~1") for part in path)
        result.append(f"{pointer}: {reason}")

    def compare(a: Any, b: Any, path: tuple[str, ...]) -> None:
        if _volatile(path):
            return
        if type(a) is not type(b):
            report(path, "type changed")
        elif isinstance(a, dict):
            for key in sorted(a.keys() | b.keys()):
                child = (*path, key)
                if _volatile(child):
                    continue
                if key not in a:
                    report(child, "added")
                elif key not in b:
                    report(child, "removed")
                else:
                    compare(a[key], b[key], child)
        elif isinstance(a, list):
            if len(a) != len(b):
                report(path, f"length changed ({len(a)} -> {len(b)})")
            for index, (first, second) in enumerate(zip(a, b, strict=False)):
                compare(first, second, (*path, str(index)))
        elif a != b:
            report(path, "value changed")

    compare(left, right, ())
    return result


def snapshot_differences(left: Any, right: Any) -> list[str]:
    """Reject incomplete reports before making a public-event equivalence claim."""
    for label, payload in (("left", left), ("right", right)):
        if not isinstance(payload, dict) or not isinstance(payload.get("events"), list):
            return [f"/{label}/events: published event list is missing"]
        if "event_count" in payload and payload["event_count"] != len(payload["events"]):
            return [f"/{label}/events: published event count is inconsistent"]
    return differences(left, right)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline", type=Path)
    parser.add_argument("candidate", type=Path)
    args = parser.parse_args()
    delta = snapshot_differences(
        json.loads(args.baseline.read_text(encoding="utf-8")),
        json.loads(args.candidate.read_text(encoding="utf-8")),
    )
    print(json.dumps({"equal": not delta, "differences": delta}, indent=2))
    return 1 if delta else 0


if __name__ == "__main__":
    raise SystemExit(main())
