#!/usr/bin/env python3
"""Apply the static verified-location registry to an existing feed snapshot."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from nrw_events import common
from nrw_events.normalization import resolve_venue


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("feed", type=Path)
    args = parser.parse_args()
    payload = json.loads(args.feed.read_text(encoding="utf-8"))
    enriched = 0
    excluded = 0
    retained_events = []
    for event in payload.get("events", []):
        if event.get("location_confidence") == "exact":
            retained_events.append(event)
            continue
        venue = resolve_venue(
            str(event.get("venue") or ""),
            str(event.get("city") or ""),
            explicit_id=str(event.get("venue_id") or ""),
        )
        if (
            venue.coordinate_source != "verified_venue_locations"
            or venue.venue_latitude is None
            or venue.venue_longitude is None
        ):
            retained_events.append(event)
            continue
        event["venue_latitude"] = venue.venue_latitude
        event["venue_longitude"] = venue.venue_longitude
        if not event.get("venue_address") and venue.venue_address:
            event["venue_address"] = venue.venue_address
        distance_km = round(common.haversine(
            common.BONN_LAT,
            common.BONN_LON,
            venue.venue_latitude,
            venue.venue_longitude,
        ), 2)
        if distance_km > common.MAX_RADIUS_KM:
            excluded += 1
            continue
        event["distance_km"] = distance_km
        event["location_confidence"] = "exact"
        event["location_source"] = "verified_venue_locations"
        enriched += 1
        retained_events.append(event)
    payload["events"] = retained_events
    temporary = args.feed.with_suffix(f"{args.feed.suffix}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(args.feed)
    print(
        f"Enriched {enriched} existing event(s) with verified map locations; "
        f"excluded {excluded} outside the configured radius."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
