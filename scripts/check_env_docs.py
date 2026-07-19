#!/usr/bin/env python3
"""Fail when an NRW_EVENTS_* code setting is absent from .env.example."""

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PATTERN = re.compile(r"\bNRW_EVENTS_[A-Z0-9_]+\b")


def main() -> int:
    used = set()
    for path in (ROOT / "scripts" / "nrw_events").rglob("*.py"):
        used.update(PATTERN.findall(path.read_text(encoding="utf-8")))
    documented = set(PATTERN.findall((ROOT / ".env.example").read_text(encoding="utf-8")))
    missing = sorted(used - documented)
    if missing:
        print("Undocumented NRW event settings: " + ", ".join(missing))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
