#!/usr/bin/env python3
"""Generate registry and module inventories embedded in project documentation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "scripts" / "nrw_events" / "sources" / "registry.json"
DOCUMENTS = (ROOT / "docs/sources.md", ROOT / "docs/modules.md")


def source_inventory() -> str:
    payload = json.loads(REGISTRY.read_text(encoding="utf-8"))
    rows = sorted(
        payload["sources"],
        key=lambda row: (str(row["region"]).casefold(), str(row["display_name"]).casefold()),
    )
    lines = ["| Region | Quelle | ID | Adapter |", "|---|---|---|---|"]
    lines.extend(
        f"| {row['region']} | {row['display_name']} | `{row['id']}` | `{row['adapter']}` |"
        for row in rows
    )
    return "\n".join(lines)


def module_inventory() -> str:
    package = ROOT / "scripts" / "nrw_events"
    modules = sorted(path.name for path in package.glob("*.py"))
    sources = sorted(path.name for path in (package / "sources").glob("*.py"))
    lines = ["```text", "scripts/nrw_events/"]
    lines.extend(f"  {name}" for name in modules)
    lines.extend(("  sources/", "    registry.json"))
    lines.extend(f"    {name}" for name in sources)
    lines.append("```")
    return "\n".join(lines)


def generated_document(path: Path) -> str:
    if path.name == "sources.md":
        return "# Source inventory\n\nGenerated from `scripts/nrw_events/sources/registry.json`; do not edit by hand.\n\n" + source_inventory() + "\n"
    return "# Module inventory\n\nGenerated from `scripts/nrw_events`; do not edit by hand.\n\n" + module_inventory() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="fail instead of updating drifted docs")
    args = parser.parse_args()
    generated_documents = {path: generated_document(path) for path in DOCUMENTS}
    drifted = []
    for path, generated in generated_documents.items():
        if path.exists() and generated == path.read_text(encoding="utf-8"):
            continue
        drifted.append(path.relative_to(ROOT))
        if not args.check:
            path.write_text(generated, encoding="utf-8")
    if args.check and drifted:
        print("Generated documentation is stale: " + ", ".join(map(str, drifted)))
        print("Run: python3 scripts/generate_docs.py")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
