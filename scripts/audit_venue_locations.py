#!/usr/bin/env python3
"""Build the reviewable venue audit consumed by geocoding research."""

from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path

from nrw_events.normalization import comparison_text, resolve_venue


def audit_payload(payload: object) -> dict:
    events = payload if isinstance(payload, list) else payload.get("events", [])
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for event in events:
        city = str(event.get("city") or "").strip()
        venue = str(event.get("venue") or "").strip()
        if not venue:
            continue
        groups[(comparison_text(city), comparison_text(venue))].append(event)

    candidates = []
    for grouped_events in groups.values():
        first = grouped_events[0]
        city = str(first.get("city") or "").strip()
        venue = str(first.get("venue") or "").strip()
        resolution = resolve_venue(venue, city, explicit_id=str(first.get("venue_id") or ""))
        addresses = sorted({
            str(event.get("venue_address") or "").strip()
            for event in grouped_events
            if event.get("venue_address")
        })
        candidates.append({
            "city": city,
            "venue": venue,
            "count": len(grouped_events),
            "addresses": addresses,
            "classification": (
                "verified" if resolution.venue_latitude is not None and resolution.venue_longitude is not None
                else "candidate"
            ),
            "samples": [
                {"title": event.get("title", ""), "link": event.get("link", "")}
                for event in grouped_events[:3]
            ],
        })
    candidates.sort(key=lambda item: (item["classification"], item["city"].casefold(), item["venue"].casefold()))
    return {
        "version": 1,
        "metrics": {
            "venueGroups": len(candidates),
            "candidateGroups": sum(item["classification"] == "candidate" for item in candidates),
        },
        "candidates": candidates,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("feed", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payload = audit_payload(json.loads(args.feed.read_text(encoding="utf-8")))
    rendered = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
