"""Compact references to recorded decisions, never inferred explanations."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


def build_evidence_index(
    events: Sequence[Mapping[str, Any]], *, run_id: str, events_path: str
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for event in events:
        identity = event.get("event_id")
        if not isinstance(identity, str) or not identity or identity in seen:
            raise ValueError("Evidence requires unique, nonempty event IDs")
        seen.add(identity)
        row: dict[str, Any] = {
            "eventId": identity,
            "runId": run_id,
            "rawPaths": [events_path],
        }
        if event.get("merged_event_ids"):
            row["mergedIds"] = list(event["merged_event_ids"])
        if event.get("source_id"):
            row["sourceId"] = event["source_id"]
        decisions = []
        for field, kind in (
            ("admission_basis", "admission"),
            ("category_reason", "category"),
        ):
            if event.get(field):
                decisions.append({"kind": kind, "reason": event[field], "recordField": field})
        if event.get("quality_warnings"):
            decisions.append({
                "kind": "publication_invariants",
                "warnings": event["quality_warnings"],
                "recordField": "quality_warnings",
            })
        if decisions:
            row["decisions"] = decisions
        provenance = {
            name: event[name]
            for name in ("description_source", "location_source", "cancellation_source")
            if event.get(name)
        }
        if provenance:
            row["fieldProvenance"] = provenance
        # previous_event_ids are identity aliases, not proof of a merge decision.
        # Import publication also cannot prove a later website route/generation.
        rows.append(row)
    return {"schemaVersion": 1, "runId": run_id, "events": rows}
