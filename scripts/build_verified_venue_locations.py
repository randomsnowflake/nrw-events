#!/usr/bin/env python3
"""Promote reviewed geocoding proposals into the static importer registry."""

from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path


REJECTED = {
    "Brühler Innenstadt": "matched an unrelated restaurant, not the city centre",
    "Bürgersaal im Bergischen Hof": "matched Rathausplatz rather than the hall",
    "Christuskirche": "matched a kindergarten at the source address",
    "Festsaal Haus Wetterstein": "matched a pharmacy rather than the hall",
    "Feuerwehrgerätehaus und Umfeld DGH/Alte Schule": "matched a bakery rather than the venue",
    "Freizeitpark Rheinbach": "matched the adjacent water park",
    "Nachbarschaftszentrum Brüser Berg": "matched the district rather than the centre",
    "Poppelsdorfer": "venue label is incomplete and matched a university office",
    "Spielplatz „Gustav-Stresemann-Ring“, Efferen": "matched Hürth town hall rather than the playground",
    "Treff Max-Ernst-Brunnen": "matched Brühl town hall rather than the fountain",
}

MANUAL = {
    ("Grafschaft", "Mehrzweckhalle Lantershofen"): {
        "latitude": 50.55506,
        "longitude": 7.1031,
        "osmUrl": "https://www.openstreetmap.org/way/254046733",
        "note": "OSM community-centre building, cross-checked against municipal address Graf-Blankard-Straße 25",
    },
    ("Brühl", "Schloss Augustusburg - UNESCO-Welterbe"): {
        "latitude": 50.8284022,
        "longitude": 6.9077385,
        "osmUrl": "https://www.openstreetmap.org/way/25187730",
        "note": "replaced road match with the Schloss Augustusburg building",
    },
    ("Rösrath", "Schloss Eulenbroich - Schlosshof"): {
        "latitude": 50.8997337,
        "longitude": 7.1856549,
        "osmUrl": "https://www.openstreetmap.org/way/110134435",
        "note": "replaced restaurant match with the Schloss Eulenbroich building",
    },
    ("Rösrath", "Außengelände/Park von Schloss Eulenbroich"): {
        "latitude": 50.8997337,
        "longitude": 7.1856549,
        "osmUrl": "https://www.openstreetmap.org/way/110134435",
        "note": "replaced restaurant match with the Schloss Eulenbroich site",
    },
}


def load(path: Path):
    return json.loads(path.read_text())


def street_like(venue: str) -> bool:
    value = venue.casefold()
    return any(word in value for word in (
        "bahnhof", "friedhof", "innenstadt", "markt", "parkplatz", "platz",
        "rheinufer", "straße", "strasse", "treff", "ufer", "wanderparkplatz",
    ))


def decision(proposal: dict) -> tuple[str, str]:
    venue = proposal["venue"]
    if venue in REJECTED:
        return "rejected", REJECTED[venue]
    if (proposal["city"], venue) in MANUAL:
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
    args = parser.parse_args()
    payload = load(args.proposals)
    registry = []
    decisions = []
    for proposal in payload["proposals"]:
        status, note = decision(proposal)
        manual = MANUAL.get((proposal["city"], proposal["venue"]))
        match = manual or proposal.get("match") or {}
        decisions.append({
            "city": proposal["city"], "venue": proposal["venue"], "eventCount": proposal["count"],
            "status": status, "reason": note, "proposalScore": proposal.get("score"),
            "osmUrl": match.get("osmUrl", ""),
        })
        if status != "accepted":
            continue
        samples = proposal.get("samples") or []
        registry.append({
            "city": proposal["city"],
            "venue": proposal["venue"],
            "address": (proposal.get("addresses") or [""])[0],
            "latitude": match["latitude"],
            "longitude": match["longitude"],
            "checkedAt": str(date.today()),
            "evidence": {
                "eventUrl": samples[0].get("link", "") if samples else "",
                "osmUrl": match.get("osmUrl", ""),
                "method": note if not manual else manual["note"],
            },
        })
    registry.sort(key=lambda item: (item["city"].casefold(), item["venue"].casefold()))
    args.registry.parent.mkdir(parents=True, exist_ok=True)
    args.registry.write_text(json.dumps({"locations": registry}, ensure_ascii=False, indent=2) + "\n")
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
    args.decisions.write_text(json.dumps(decision_payload, ensure_ascii=False, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
