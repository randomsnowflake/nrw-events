#!/usr/bin/env python3
"""Create cached, reviewable geocoding proposals for unresolved event venues.

This is deliberately a research tool, not runtime geocoding. It follows the
public Nominatim policy: one machine, one thread, at most one request per
second, an identifying User-Agent, and a persistent local cache.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

USER_AGENT = "veranstaltungen-bonn-venue-research/1.0 (https://www.veranstaltungen-bonn.de/kontakt/)"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
PHOTON_URL = "https://photon.komoot.io/api/"
MIN_REQUEST_INTERVAL_SECONDS = 1.1
TRANSIENT_RETRY_ATTEMPTS = 3
TRANSIENT_RETRY_BASE_SECONDS = 1.1


def normalized(value: str) -> str:
    folded = (value or "").casefold().translate(str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"}))
    ascii_text = unicodedata.normalize("NFKD", folded).encode("ascii", "ignore").decode("ascii")
    words = re.sub(r"[^a-z0-9]+", " ", ascii_text).strip()
    words = re.sub(r"\b([a-z]+)str\b", r"\1strasse", words)
    return re.sub(r"\bstr\b", "strasse", words)


def tokens(value: str) -> set[str]:
    ignored = {"am", "an", "auf", "bei", "der", "die", "das", "den", "des", "im", "in", "und", "von", "vor", "zum", "zur"}
    return {part for part in normalized(value).split() if len(part) > 1 and part not in ignored}


def postcode(value: str) -> str:
    match = re.search(r"\b\d{5}\b", value or "")
    return match.group(0) if match else ""


def house_number(value: str) -> str:
    match = re.search(r"\b\d{1,4}\s*[a-z]?\b", value or "", re.I)
    return normalized(match.group(0)).replace(" ", "") if match else ""


def osm_url(result: dict) -> str:
    osm_type = {"node": "node", "way": "way", "relation": "relation"}.get(result.get("osm_type"), "")
    osm_id = str(result.get("osm_id") or "")
    return f"https://www.openstreetmap.org/{osm_type}/{osm_id}" if osm_type and osm_id else ""


def place_names(result: dict) -> list[str]:
    address = result.get("address") or {}
    names = result.get("namedetails") or {}
    return [
        str(result.get("name") or ""),
        str(names.get("name") or ""),
        str(names.get("name:de") or ""),
        str(result.get("display_name") or "").split(",", 1)[0],
        *(str(address.get(field) or "") for field in ("amenity", "building", "tourism", "shop", "leisure")),
    ]


def city_compatible(city: str, result: dict) -> bool:
    expected = tokens(city.replace("Bonn-", "Bonn "))
    address = result.get("address") or {}
    actual = tokens(" ".join([str(result.get("display_name") or ""), *(str(address.get(field) or "") for field in (
        "city", "town", "village", "municipality", "city_district", "suburb", "county", "state_district",
    ))]))
    if "bonn" in expected and "bonn" in actual:
        return True
    return bool(expected & actual)


def candidate_score(group: dict, result: dict) -> tuple[int, list[str]]:
    reasons: list[str] = []
    score = 0
    address = (result.get("address") or {})
    input_addresses = group.get("addresses") or []
    input_postcodes = {postcode(value) for value in input_addresses} - {""}
    result_postcode = postcode(str(address.get("postcode") or ""))
    if input_postcodes:
        if result_postcode in input_postcodes:
            score += 4
            reasons.append("postcode-match")
        else:
            score -= 6
            reasons.append("postcode-conflict")

    input_numbers = {house_number(value) for value in input_addresses} - {""}
    result_number = house_number(str(address.get("house_number") or ""))
    if input_numbers:
        if result_number in input_numbers:
            score += 4
            reasons.append("house-number-match")
        elif result_number:
            score -= 5
            reasons.append("house-number-conflict")
        else:
            score -= 2
            reasons.append("house-number-missing")

    input_street_tokens = set()
    for value in input_addresses:
        input_street_tokens.update(tokens(re.sub(r"\b\d{5}\b|\b\d{1,4}\s*[a-z]?\b", " ", value, flags=re.I)))
    input_street_tokens -= tokens(group["city"])
    result_street_tokens = tokens(str(address.get("road") or ""))
    if input_street_tokens and result_street_tokens:
        street_overlap = len(input_street_tokens & result_street_tokens) / min(len(input_street_tokens), len(result_street_tokens))
        if street_overlap >= 0.8:
            score += 3
            reasons.append("street-match")
        else:
            score -= 3
            reasons.append("street-conflict")

    if city_compatible(group["city"], result):
        score += 3
        reasons.append("city-match")
    else:
        score -= 5
        reasons.append("city-conflict")

    expected_tokens = tokens(group["venue"]) - tokens(group["city"])
    expected_compact = normalized(group["venue"]).replace(" ", "")
    best_overlap = 0.0
    best_containment = 0.0
    best_intersection = 0
    for name in place_names(result):
        actual_tokens = tokens(name)
        if expected_tokens and actual_tokens:
            intersection = len(expected_tokens & actual_tokens)
            best_intersection = max(best_intersection, intersection)
            best_overlap = max(best_overlap, intersection / len(expected_tokens | actual_tokens))
            best_containment = max(best_containment, intersection / min(len(expected_tokens), len(actual_tokens)))
            actual_compact = normalized(name).replace(" ", "")
            if len(expected_tokens) >= 2 and len(actual_tokens) >= 2 and min(len(expected_compact), len(actual_compact)) >= 6 and (
                expected_compact in actual_compact or actual_compact in expected_compact
            ):
                best_containment = 1.0
                best_intersection = max(best_intersection, 2)
    if best_overlap >= 0.8:
        score += 6
        reasons.append("venue-name-exact")
    elif best_containment >= 0.8 and best_intersection >= 2:
        score += 6
        reasons.append("venue-name-contained")
    elif best_overlap >= 0.5:
        score += 3
        reasons.append("venue-name-partial")
    elif input_addresses:
        reasons.append("address-only")
    else:
        score -= 5
        reasons.append("venue-name-mismatch")

    result_type = str(result.get("type") or "")
    result_class = str(result.get("class") or "")
    if result_class == "boundary" or result_type in {"city", "town", "village", "suburb", "administrative"}:
        score -= 8
        reasons.append("area-not-venue")
    return score, reasons


def queries_for(group: dict) -> list[str]:
    addresses = group.get("addresses") or []
    if addresses:
        return [
            ", ".join((group["venue"], addresses[0], "Deutschland")),
            ", ".join((addresses[0], "Deutschland")),
        ]
    return [", ".join((group["venue"], group["city"], "Deutschland"))]


def fetch(query: str) -> list[dict]:
    params = urllib.parse.urlencode({
        "q": query,
        "format": "jsonv2",
        "limit": 5,
        "addressdetails": 1,
        "namedetails": 1,
        "extratags": 1,
        "countrycodes": "de",
    })
    request = urllib.request.Request(f"{NOMINATIM_URL}?{params}", headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


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


def fetch_with_backoff(fetcher, query: str):
    """Retry transient transport failures without turning them into no-results."""
    for attempt in range(TRANSIENT_RETRY_ATTEMPTS):
        try:
            return fetcher(query)
        except (urllib.error.URLError, TimeoutError):
            if attempt + 1 >= TRANSIENT_RETRY_ATTEMPTS:
                raise
            time.sleep(TRANSIENT_RETRY_BASE_SECONDS * (2 ** attempt))


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
