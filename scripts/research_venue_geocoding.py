#!/usr/bin/env python3
"""Create cached, reviewable geocoding proposals for unresolved event venues.

This is deliberately a research tool, not runtime geocoding. It follows the
public Nominatim policy: one machine, one thread, at most one request per
second, an identifying User-Agent, and a persistent local cache.
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from nrw_events.geocoding import (  # noqa: F401 - shared with runtime geocoding; tests use these names
    MIN_REQUEST_INTERVAL_SECONDS,
    NOMINATIM_URL,
    TRANSIENT_RETRY_ATTEMPTS,
    TRANSIENT_RETRY_BASE_SECONDS,
    USER_AGENT,
    candidate_score,
    city_compatible,
    fetch,
    fetch_with_backoff,
    house_number,
    normalized,
    place_names,
    postcode,
    tokens,
)

PHOTON_URL = "https://photon.komoot.io/api/"


def osm_url(result: dict) -> str:
    osm_type = {"node": "node", "way": "way", "relation": "relation"}.get(result.get("osm_type"), "")
    osm_id = str(result.get("osm_id") or "")
    return f"https://www.openstreetmap.org/{osm_type}/{osm_id}" if osm_type and osm_id else ""


def queries_for(group: dict) -> list[str]:
    addresses = group.get("addresses") or []
    if addresses:
        return [
            ", ".join((group["venue"], addresses[0], "Deutschland")),
            ", ".join((addresses[0], "Deutschland")),
        ]
    return [", ".join((group["venue"], group["city"], "Deutschland"))]


def photon_result(feature: dict) -> dict:
    properties = feature.get("properties") or {}
    coordinates = (feature.get("geometry") or {}).get("coordinates") or [None, None]
    address = {
        "house_number": properties.get("housenumber"),
        "road": properties.get("street"),
        "postcode": properties.get("postcode"),
        "city": properties.get("city"),
        "town": properties.get("town"),
        "village": properties.get("village"),
        "municipality": properties.get("municipality"),
        "city_district": properties.get("district"),
        "suburb": properties.get("locality"),
        "county": properties.get("county"),
        "state": properties.get("state"),
        "country": properties.get("country"),
    }
    osm_type = {"N": "node", "W": "way", "R": "relation"}.get(str(properties.get("osm_type") or "").upper(), "")
    display_parts = [properties.get("name"), properties.get("street"), properties.get("city"), properties.get("district"), properties.get("state"), properties.get("country")]
    return {
        "name": properties.get("name") or "",
        "display_name": ", ".join(str(part) for part in display_parts if part),
        "lat": coordinates[1],
        "lon": coordinates[0],
        "osm_type": osm_type,
        "osm_id": properties.get("osm_id"),
        "class": properties.get("osm_key") or "",
        "type": properties.get("osm_value") or properties.get("type") or "",
        "address": {key: value for key, value in address.items() if value},
        "namedetails": {"name": properties.get("name") or ""},
        "extratags": {},
    }


def fetch_photon(query: str) -> list[dict]:
    params = urllib.parse.urlencode({"q": query, "limit": 5, "lang": "de"})
    request = urllib.request.Request(f"{PHOTON_URL}?{params}", headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.load(response)
    return [photon_result(feature) for feature in payload.get("features", [])]


def load_json(path: Path, default):
    return json.loads(path.read_text()) if path.exists() else default


def atomic_write(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(f"{json.dumps(value, ensure_ascii=False, indent=2)}\n")
    temporary.replace(path)


def cache_buckets(cache: dict) -> tuple[dict, dict]:
    """Return successful queries and errors, migrating legacy cached failures."""
    queries = cache.setdefault("queries", {})
    errors = cache.setdefault("errors", {})
    for query, entry in list(queries.items()):
        if isinstance(entry, dict) and entry.get("error"):
            errors[query] = {
                "failedAt": entry.get("fetchedAt"),
                "error": entry.get("error"),
            }
            del queries[query]
    return queries, errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("audit", type=Path)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--photon-cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=1000)
    args = parser.parse_args()

    audit = load_json(args.audit, {})
    cache = load_json(args.cache, {"queries": {}})
    photon_cache = load_json(args.photon_cache, {"queries": {}})
    cached_queries, query_errors = cache_buckets(cache)
    cached_photon_queries, photon_errors = cache_buckets(photon_cache)
    proposals = []
    last_request_at = 0.0
    candidates = [candidate for candidate in audit.get("candidates", []) if candidate.get("classification") == "candidate"][: args.limit]

    for position, group in enumerate(candidates, 1):
        queries = queries_for(group)
        for query in queries:
            if query in cached_queries:
                continue
            delay = MIN_REQUEST_INTERVAL_SECONDS - (time.monotonic() - last_request_at)
            if delay > 0:
                time.sleep(delay)
            try:
                cached_queries[query] = {
                    "fetchedAt": datetime.now(timezone.utc).isoformat(),
                    "results": fetch_with_backoff(fetch, query),
                }
                query_errors.pop(query, None)
                last_request_at = time.monotonic()
            except (urllib.error.URLError, TimeoutError) as error:
                query_errors[query] = {
                    "failedAt": datetime.now(timezone.utc).isoformat(),
                    "error": str(error),
                }
                last_request_at = time.monotonic()
            atomic_write(args.cache, cache)

        ranked = []
        for query in queries:
            for result in cached_queries.get(query, {}).get("results", []):
                score, reasons = candidate_score(group, result)
                ranked.append((score, reasons, result, query))
        ranked.sort(key=lambda item: item[0], reverse=True)
        best = ranked[0] if ranked else None
        accepted = bool(best and best[0] >= 9 and (not group.get("addresses") or "postcode-conflict" not in best[1]) and "city-conflict" not in best[1])
        photon_query = queries[0]
        if not accepted:
            if photon_query not in cached_photon_queries:
                delay = MIN_REQUEST_INTERVAL_SECONDS - (time.monotonic() - last_request_at)
                if delay > 0:
                    time.sleep(delay)
                try:
                    cached_photon_queries[photon_query] = {
                        "fetchedAt": datetime.now(timezone.utc).isoformat(),
                        "results": fetch_with_backoff(fetch_photon, photon_query),
                    }
                    photon_errors.pop(photon_query, None)
                    last_request_at = time.monotonic()
                except (urllib.error.URLError, TimeoutError) as error:
                    photon_errors[photon_query] = {
                        "failedAt": datetime.now(timezone.utc).isoformat(),
                        "error": str(error),
                    }
                    last_request_at = time.monotonic()
                atomic_write(args.photon_cache, photon_cache)
            for result in cached_photon_queries.get(photon_query, {}).get("results", []):
                score, reasons = candidate_score(group, result)
                ranked.append((score, reasons, result, photon_query))
            ranked.sort(key=lambda item: item[0], reverse=True)
            best = ranked[0] if ranked else None
            accepted = bool(best and best[0] >= 9 and (not group.get("addresses") or "postcode-conflict" not in best[1]) and "city-conflict" not in best[1])
        proposal = {
            **group,
            "queries": queries,
            "matchedQuery": best[3] if best else None,
            "provider": "photon" if best and best[2] in cached_photon_queries.get(photon_query, {}).get("results", []) else "nominatim",
            "status": "strong-candidate" if accepted else "needs-review",
            "score": best[0] if best else None,
            "reasons": best[1] if best else ["no-result"],
        }
        if best:
            result = best[2]
            proposal["match"] = {
                "displayName": result.get("display_name"),
                "latitude": float(result["lat"]),
                "longitude": float(result["lon"]),
                "osmType": result.get("osm_type"),
                "osmId": result.get("osm_id"),
                "osmUrl": osm_url(result),
                "category": result.get("category") or result.get("class"),
                "type": result.get("type"),
                "address": result.get("address") or {},
                "wikidata": (result.get("extratags") or {}).get("wikidata"),
            }
        proposals.append(proposal)
        if position % 25 == 0:
            print(f"processed {position}/{len(candidates)}; strong={sum(item['status'] == 'strong-candidate' for item in proposals)}", flush=True)

    output = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "sourceAuditGeneratedAt": audit.get("generatedAt"),
        "policy": "https://operations.osmfoundation.org/policies/nominatim/",
        "proposalCount": len(proposals),
        "strongCandidateCount": sum(item["status"] == "strong-candidate" for item in proposals),
        "proposals": proposals,
    }
    atomic_write(args.output, output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
