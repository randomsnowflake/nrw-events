"""Expiry-checked source corrections backed by reviewed evidence."""

from __future__ import annotations

import json
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

_PATH = Path(__file__).with_name("reviewed_corrections.json")


@lru_cache(maxsize=1)
def _payload() -> dict[str, Any]:
    raw = json.loads(_PATH.read_text(encoding="utf-8"))
    if raw.get("schema_version") != 1 or not isinstance(raw.get("groups"), dict):
        raise ValueError("reviewed corrections manifest has an unsupported schema")
    return raw


def active_entries(group: str, reference: date | datetime) -> tuple[dict[str, Any], ...]:
    """Return reviewed entries that have not expired at *reference*."""
    reference_date = reference.date() if isinstance(reference, datetime) else reference
    entries = _payload()["groups"].get(group, [])
    active: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict) or not {
            "match", "value", "valid_until", "evidence",
        } <= entry.keys():
            raise ValueError(f"invalid reviewed correction in {group}")
        try:
            valid_until = date.fromisoformat(str(entry["valid_until"]))
        except ValueError as exc:
            raise ValueError(f"invalid reviewed correction expiry in {group}") from exc
        if reference_date <= valid_until:
            active.append(entry)
    return tuple(active)
