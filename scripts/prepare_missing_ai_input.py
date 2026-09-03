#!/usr/bin/env python3
"""Prepare restricted events still missing AI copy for cached enrichment."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from nrw_events import ai_enrichment, detail_enrichment
from nrw_events.identity import event_id


def _events(path: Path) -> list[dict[str, Any]]:
    document = json.loads(path.read_text(encoding="utf-8"))
    values = document.get("events") if isinstance(document, dict) else document
    if not isinstance(values, list):
        raise ValueError(f"{path} must contain an events array")
    return [value for value in values if isinstance(value, dict)]


def prepare(
    source_path: Path,
    current_path: Path,
    output_path: Path,
    *,
    enrich_details: bool = True,
) -> dict[str, int]:
    source_events = _events(source_path)
    current_events = _events(current_path)
    missing_ids = {
        str(event.get("event_id") or event_id(event))
        for event in current_events
        if ai_enrichment.is_target_event(event)
        and not str(event.get("ai_summary") or "").strip()
        and (
            str(event.get("description_source") or "").strip().casefold() == "generated"
            or (
                not str(event.get("description") or "").strip()
                and not str(event.get("description_html") or "").strip()
            )
        )
    }
    selected = [
        dict(event)
        for event in source_events
        if str(event.get("event_id") or event_id(event)) in missing_ids
    ]
    selected_ids = {str(event.get("event_id") or event_id(event)) for event in selected}
    absent = missing_ids - selected_ids
    if absent:
        raise ValueError(f"{len(absent)} current missing events are absent from the source snapshot")

    before = sum(
        bool(str(event.get("description") or "").strip() or str(event.get("description_html") or "").strip())
        for event in selected
    )
    if enrich_details:
        blank = [
            event for event in selected
            if not str(event.get("description") or "").strip()
            and not str(event.get("description_html") or "").strip()
        ]
        enriched_by_id = {
            str(event.get("event_id") or event_id(event)): event
            for event in detail_enrichment.enrich_events(
                blank,
                cache_namespace="missing-ai-event-details-v1",
            )
        }
        selected = [
            enriched_by_id.get(str(event.get("event_id") or event_id(event)), event)
            for event in selected
        ]
    after = sum(
        bool(str(event.get("description") or "").strip() or str(event.get("description_html") or "").strip())
        for event in selected
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps({"events": selected}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return {
        "selected": len(selected),
        "with_source_material_before_detail": before,
        "with_source_material_after_detail": after,
        "still_without_source_material": len(selected) - after,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--current", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--skip-detail", action="store_true")
    args = parser.parse_args()
    result = prepare(
        args.source,
        args.current,
        args.output,
        enrich_details=not args.skip_detail,
    )
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
