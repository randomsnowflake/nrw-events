#!/usr/bin/env python3
"""Turn importer metadata into an actionable local-canary report."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


BAD_SOURCE_STATUSES = {"degraded", "failed", "parser_empty"}


def canary_problems(metadata: dict[str, Any], importer_exit: int = 0) -> list[str]:
    problems: list[str] = []
    run_status = str(metadata.get("run_status") or "missing")
    if importer_exit:
        problems.append(f"importer exited with status {importer_exit}")
    if run_status != "healthy":
        problems.append(f"run status is `{run_status}`")
    for name, result in sorted((metadata.get("source_results") or {}).items()):
        status = str(result.get("status") or "")
        anomalies = [str(value) for value in result.get("anomalies") or []]
        if status in BAD_SOURCE_STATUSES or anomalies:
            detail = f"{name}: `{status or 'unknown'}`"
            if anomalies:
                detail += f"; {', '.join(anomalies)}"
            error = result.get("error") or {}
            if error.get("error"):
                detail += f"; {error['error']}"
            problems.append(detail)
    return problems


def report_markdown(metadata: dict[str, Any], problems: list[str]) -> str:
    generated_at = metadata.get("generated_at") or "unknown time"
    run_id = metadata.get("run_id") or "unknown"
    event_count = metadata.get("event_count", "unknown")
    lines = [
        "## Local markup-drift canary",
        "",
        f"Run `{run_id}` at {generated_at} produced {event_count} events.",
        "",
    ]
    if problems:
        lines.extend(["Detected problems:", "", *[f"- {problem}" for problem in problems]])
    else:
        lines.append("All measured live sources are healthy.")
    lines.extend(["", "This report was generated from the importer's source health metadata.", ""])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("metadata", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--importer-exit", type=int, default=0)
    args = parser.parse_args()
    try:
        metadata = json.loads(args.metadata.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        metadata = {}
        problems = [f"metadata unavailable: {type(exc).__name__}: {exc}"]
        if args.importer_exit:
            problems.insert(0, f"importer exited with status {args.importer_exit}")
    else:
        problems = canary_problems(metadata, args.importer_exit)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(report_markdown(metadata, problems), encoding="utf-8")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
