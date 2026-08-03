#!/usr/bin/env python3
"""Generate registry and module inventories embedded in project documentation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "scripts" / "nrw_events" / "sources" / "registry.json"
DOCUMENTS = (ROOT / "README.md", ROOT / "SKILL.md")


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


def replace_block(text: str, name: str, content: str) -> str:
    begin = f"<!-- BEGIN GENERATED {name} -->"
    end = f"<!-- END GENERATED {name} -->"
    pattern = re.compile(rf"{re.escape(begin)}.*?{re.escape(end)}", re.S)
    replacement = f"{begin}\n{content}\n{end}"
    updated, count = pattern.subn(lambda _match: replacement, text)
    if count != 1:
        raise ValueError(f"expected one {name} block, found {count}")
    return updated


def generated_document(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    text = replace_block(text, "SOURCES", source_inventory())
    return replace_block(text, "MODULES", module_inventory())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="fail instead of updating drifted docs")
    args = parser.parse_args()
    generated_documents = {path: generated_document(path) for path in DOCUMENTS}
    drifted = []
    for path, generated in generated_documents.items():
        if generated == path.read_text(encoding="utf-8"):
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
