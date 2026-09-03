#!/usr/bin/env python3
"""Promote reviewed geocoding proposals into the static importer registry."""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

DEFAULT_POLICY = Path(__file__).with_name("venue_geocoding_decisions.json")


def load(path: Path):
    return json.loads(path.read_text())


def street_like(venue: str) -> bool:
    value = venue.casefold()
    return any(word in value for word in (
        "bahnhof", "friedhof", "innenstadt", "markt", "parkplatz", "platz",
        "rheinufer", "straße", "strasse", "treff", "ufer", "wanderparkplatz",
    ))


def load_policy(path: Path) -> tuple[dict[str, str], dict[tuple[str, str], dict], str]:
    payload = load(path)
    if payload.get("version") != 1:
        raise ValueError("venue decision policy must use schema version 1")
    rejected = payload.get("rejected")
    manual_entries = payload.get("manual")
    checked_at = payload.get("checkedAt")
    if not isinstance(rejected, dict) or not isinstance(manual_entries, list):
        raise ValueError("venue decision policy must contain rejected and manual collections")
    try:
        date.fromisoformat(checked_at)
    except (TypeError, ValueError):
        raise ValueError("venue decision policy must contain an ISO checkedAt date") from None
    manual = {(entry["city"], entry["venue"]): entry for entry in manual_entries}
    return rejected, manual, checked_at


def decision(
    proposal: dict,
    rejected: dict[str, str],
    manual: dict[tuple[str, str], dict],
) -> tuple[str, str]:
    venue = proposal["venue"]
    if venue in rejected:
        return "rejected", rejected[venue]
    if (proposal["city"], venue) in manual:
        return "accepted", "manually cross-checked override"
    if proposal.get("status") != "strong-candidate":
        return "needs-review", "automated evidence below acceptance threshold"
    reasons = set(proposal.get("reasons") or [])
    match = proposal.get("match") or {}
    category = str(match.get("category") or "")
    result_type = str(match.get("type") or "")
    has_name_match = any(reason.startswith("venue-name-") for reason in reasons)
    if has_name_match:
        if result_type in {"bus_stop", "parking", "residential", "unclassified"} and not street_like(venue):
            return "needs-review", f"name matched only to ambiguous {result_type} feature"
        return "accepted", "venue name and municipality matched"
    # A matching house number, street and municipality is already a point-level
    # postal match; some otherwise exact source addresses omit the postcode.
    exact_address = {"house-number-match", "street-match", "city-match"} <= reasons
    if exact_address and category not in {"highway", "boundary"}:
        return "accepted", "source address matched postcode, house number, street and municipality"
    return "needs-review", "address-only match is not point-exact enough"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("proposals", type=Path)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--decisions", type=Path, required=True)
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    payload = load(args.proposals)
    rejected, manual_overrides, checked_at = load_policy(args.policy)
    registry = []
    decisions = []
    for proposal in payload["proposals"]:
        status, note = decision(proposal, rejected, manual_overrides)
        manual = manual_overrides.get((proposal["city"], proposal["venue"]))
        match = manual or proposal.get("match") or {}
        decisions.append({
            "city": proposal["city"], "venue": proposal["venue"], "eventCount": proposal["count"],
            "status": status, "reason": note, "proposalScore": proposal.get("score"),
            "osmUrl": match.get("osmUrl", ""),
        })
        if status != "accepted":
            continue
        samples = proposal.get("samples") or []
        item = {
            "city": proposal["city"],
            "venue": proposal["venue"],
            "address": (proposal.get("addresses") or [""])[0],
            "latitude": match["latitude"],
            "longitude": match["longitude"],
            "checkedAt": checked_at,
            "evidence": {
                "eventUrl": samples[0].get("link", "") if samples else "",
                "osmUrl": match.get("osmUrl", ""),
                "method": note if not manual else manual["note"],
            },
        }
        if proposal.get("aliases"):
            item["aliases"] = proposal["aliases"]
        if proposal.get("evidence"):
            item["evidence"] = proposal["evidence"]
        registry.append(item)
    registry.sort(key=lambda item: (item["city"].casefold(), item["venue"].casefold()))
    registry_rendered = json.dumps({"locations": registry}, ensure_ascii=False, indent=2) + "\n"
    if args.check:
        if not args.registry.exists() or args.registry.read_text(encoding="utf-8") != registry_rendered:
            raise SystemExit("verified venue registry is stale; regenerate it")
    else:
        args.registry.parent.mkdir(parents=True, exist_ok=True)
        args.registry.write_text(registry_rendered, encoding="utf-8")
    accepted_events = sum(item["eventCount"] for item in decisions if item["status"] == "accepted")
    decision_payload = {
        "generatedAt": str(date.today()),
        "source": str(args.proposals),
        "metrics": {
            "proposalGroups": len(decisions),
            "acceptedGroups": len(registry),
            "acceptedEvents": accepted_events,
            "needsReviewGroups": sum(item["status"] == "needs-review" for item in decisions),
            "rejectedGroups": sum(item["status"] == "rejected" for item in decisions),
        },
        "decisions": decisions,
    }
    if not args.check:
        args.decisions.write_text(json.dumps(decision_payload, ensure_ascii=False, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
