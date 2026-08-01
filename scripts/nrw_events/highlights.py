"""Reproducible homepage highlights derived from published ranking signals."""

from __future__ import annotations

from collections import Counter
from typing import Any, Iterable, Mapping


SCHEMA_VERSION = "1.0"
MAX_PER_VENUE = 2
MAX_PER_CATEGORY = 3
_BASE36 = "0123456789abcdefghijklmnopqrstuvwxyz"


def _planner_id(index: int) -> str:
    result = ""
    while index:
        index, remainder = divmod(index, 36)
        result = _BASE36[remainder] + result
    return result or "0"


def _rank(event: Mapping[str, Any]) -> tuple[float, float, str, str]:
    return (
        -(float(event.get("score") or 0) + float(event.get("priority_bonus") or 0)),
        float(event.get("distance_km") or 999),
        str(event.get("start_date") or ""),
        str(event.get("event_id") or ""),
    )


def build_highlights(
    events: Iterable[Mapping[str, Any]], *, run_id: str, generated_at: str,
) -> dict[str, Any]:
    """Select diverse events without network or LLM access."""
    all_rows = [dict(event) for event in events]
    rows = [event for event in all_rows if event.get("status") == "scheduled"]
    canonical_index = {str(event.get("event_id")): index for index, event in enumerate(all_rows)}
    ranked = sorted(rows, key=_rank)
    venue_counts: Counter[str] = Counter()
    category_counts: Counter[str] = Counter()
    selected: list[dict[str, Any]] = []
    for event in ranked:
        venue = str(event.get("venue_id") or event.get("venue") or event.get("city") or "unknown")
        category = str(event.get("category_key") or "other")
        if venue_counts[venue] >= MAX_PER_VENUE or category_counts[category] >= MAX_PER_CATEGORY:
            continue
        selected.append(event)
        venue_counts[venue] += 1
        category_counts[category] += 1

    categories = []
    for category in sorted({str(event.get("category_key") or "other") for event in rows}):
        choices = [event for event in selected if event.get("category_key") == category][:3]
        if not choices:
            choices = [event for event in ranked if event.get("category_key") == category][:3]
        categories.append({
            "key": category,
            "selectedEventIds": [_planner_id(canonical_index[event["event_id"]]) for event in choices],
            "selected": [
                {
                    "eventId": event["event_id"],
                    "score": round(float(event.get("score") or 0) + float(event.get("priority_bonus") or 0), 3),
                }
                for event in choices
            ],
        })
    return {
        "schemaVersion": SCHEMA_VERSION,
        "run_id": run_id,
        "generated_at": generated_at,
        "selection": {
            "max_per_venue": MAX_PER_VENUE,
            "max_per_category": MAX_PER_CATEGORY,
            "strategy": "score+ranking_features+venue/category-diversity",
        },
        "categories": categories,
    }


def is_consistent(payload: Mapping[str, Any], run_id: str) -> bool:
    return (
        payload.get("schemaVersion") == SCHEMA_VERSION
        and payload.get("run_id") == run_id
        and isinstance(payload.get("categories"), list)
    )
