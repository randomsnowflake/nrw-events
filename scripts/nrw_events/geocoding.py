"""Geocoding for published events that still lack coordinates.

Follows the public Nominatim policy: one thread, at most one request per
second, an identifying User-Agent and a persistent cache. An address result is
only accepted when postcode, municipality, street and house number agree, so the
pin marks one building. Without a house number only a street or square that is
itself the venue and matches the postcode (``Merler Winkel``, 53340) is accepted.

Events whose address cannot pin them (none, only a postcode, or no point-exact
match) are looked up by venue name and town. That result must pass the venue
research scoring (name and municipality agree, no conflicting postcode, house
number or street, no area), so "Stadtbibliothek Hennef" pins the library while
"Dorfplatz" or a district name stays unpinned. Anything else stays unpinned;
the reviewed venue registry remains the primary source.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from collections.abc import Callable
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import config
from .location import haversine
from .models import CanonicalEvent
from .validation import EventValidationError, validate_event

USER_AGENT = "veranstaltungen-bonn-venue-research/1.0 (https://www.veranstaltungen-bonn.de/kontakt/)"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
MIN_REQUEST_INTERVAL_SECONDS = 1.1
TRANSIENT_RETRY_ATTEMPTS = 3
TRANSIENT_RETRY_BASE_SECONDS = 1.1
# Misses are retried after this; lookups that produced a pin never expire.
CACHE_TTL = timedelta(days=90)
# ponytail: sequential run budget; addresses left over are looked up next run.
RUN_BUDGET_SECONDS = 120.0
LOCATION_SOURCE = "geocoded_address"
VENUE_LOCATION_SOURCE = "geocoded_venue"
POINTS_PATH = Path(__file__).with_name("geocoded_addresses.json")
_UMLAUTS = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})
AREA_TYPES = {"city", "town", "village", "suburb", "administrative", "county", "state", "postcode"}
# A venue name must never resolve to a whole place: its centroid looks exact and is not.
VENUE_AREA_TYPES = AREA_TYPES | {
    "borough", "city_district", "hamlet", "isolated_dwelling", "locality", "municipality",
    "neighbourhood", "quarter", "region", "district",
}
# Features a name can hit by coincidence; only street-like venues may use them.
AMBIGUOUS_VENUE_TYPES = {"bus_stop", "parking", "residential", "unclassified"}
VENUE_MIN_SCORE = 9
VENUE_NAME_MATCHES = {"venue-name-exact", "venue-name-contained"}
VENUE_CONFLICTS = {"city-conflict", "postcode-conflict", "house-number-conflict", "street-conflict", "area-not-venue"}
# One generic word names a kind of place, not one place in a town.
GENERIC_VENUE_WORDS = {
    "aussenspielstaette", "bahnhofsvorplatz", "buero", "dorfplatz", "festplatz", "festwiese",
    "fussgaengerzone", "innenstadt", "kirche", "kirchplatz", "klimabuero", "markt", "marktplatz",
    "parkplatz", "pfarrkirche", "rathausplatz", "schule", "spielplatz", "sportplatz", "stadtmitte",
    "treffpunkt", "wanderparkplatz", "zentrum",
}
NON_PLACE_VENUE_WORDS = {"online", "livestream", "webinar", "zoom", "digital", "virtuell"}


def normalized(value: str) -> str:
    folded = (value or "").casefold().translate(_UMLAUTS)
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


def city_compatible(city: str, result: dict) -> bool:
    expected = tokens(city.replace("Bonn-", "Bonn "))
    address = result.get("address") or {}
    actual = tokens(" ".join([str(result.get("display_name") or ""), *(str(address.get(field) or "") for field in (
        "city", "town", "village", "municipality", "city_district", "suburb", "county", "state_district",
    ))]))
    if "bonn" in expected and "bonn" in actual:
        return True
    return bool(expected & actual)


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


def candidate_score(group: dict, result: dict) -> tuple[int, list[str]]:
    """Score one geocoder result for a venue group (``venue``, ``city``, ``addresses``)."""
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


def street_like(venue: str) -> bool:
    value = venue.casefold()
    return any(word in value for word in (
        "bahnhof", "friedhof", "innenstadt", "markt", "parkplatz", "platz",
        "rheinufer", "straße", "strasse", "treff", "ufer", "wanderparkplatz",
    ))


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


def fetch_with_backoff(fetcher: Callable[[str], list[dict]], query: str) -> list[dict]:
    """Retry transient transport failures without turning them into no-results."""
    for attempt in range(TRANSIENT_RETRY_ATTEMPTS):
        try:
            return fetcher(query)
        except (urllib.error.URLError, TimeoutError):  # noqa: PERF203 - bounded retry loop
            if attempt + 1 >= TRANSIENT_RETRY_ATTEMPTS:
                raise
            time.sleep(TRANSIENT_RETRY_BASE_SECONDS * (2 ** attempt))
    return []


def _city_tokens(event: CanonicalEvent) -> set[str]:
    return tokens(event.city.replace("Bonn-", "Bonn "))


def _street(event: CanonicalEvent) -> tuple[str, bool]:
    """Street tokens text and whether it came from the address.

    Venues named after their street keep only postcode and town as address
    (``Merler Winkel`` / ``53340 Meckenheim``); the venue then names the street.
    """
    street = re.sub(r"\b\d{5}\b|\b\d{1,4}\s*[a-z]?\b", " ", event.venue_address, flags=re.I)
    if tokens(street) - _city_tokens(event):
        return street, True
    return event.venue, False


def query_for(event: CanonicalEvent) -> str:
    """One cacheable query per address; the town is added when no postcode anchors it."""
    city = re.sub(r"^Bonn-.*", "Bonn", event.city.strip())
    street, from_address = _street(event)
    if not city or not tokens(street) - _city_tokens(event):
        return ""
    address = " ".join(event.venue_address.split())
    if not from_address:
        address = ", ".join(part for part in (" ".join(event.venue.split()), address) if part)
    if not postcode(address) and not tokens(city) <= tokens(address):
        address = f"{address}, {city}"
    return f"{address}, Deutschland"


def accepted_point(event: CanonicalEvent, results: list[dict]) -> tuple[float, float] | None:
    """Return the first result that pins the event's own postal address."""
    street_text, from_address = _street(event)
    street = tokens(street_text) - _city_tokens(event)
    expected_postcode = postcode(event.venue_address)
    expected_number = house_number(re.sub(r"\b\d{5}\b", " ", event.venue_address)) if from_address else ""
    for result in results:
        details = result.get("address") or {}
        category = str(result.get("category") or result.get("class") or "")
        if category == "boundary" or str(result.get("type") or "") in AREA_TYPES:
            continue
        if expected_postcode and postcode(str(details.get("postcode") or "")) != expected_postcode:
            continue
        if not city_compatible(event.city, result):
            continue
        road = tokens(str(details.get("road") or details.get("pedestrian") or details.get("square") or result.get("name") or ""))
        if not street or not road or len(street & road) / min(len(street), len(road)) < 0.8:
            continue
        if expected_number:
            if house_number(str(details.get("house_number") or "")) != expected_number:
                continue
        # No house number: only a street or square that is itself the venue,
        # anchored by its postcode (a bare "Rheinufer, Bonn" spans kilometres).
        elif (not expected_postcode or category not in {"highway", "place"}
              or not tokens(event.venue) - _city_tokens(event) <= road):
            continue
        try:
            latitude, longitude = float(result["lat"]), float(result["lon"])
        except (KeyError, TypeError, ValueError):
            continue
        if haversine(config.BONN_LAT, config.BONN_LON, latitude, longitude) > config.MAX_RADIUS_KM:
            continue
        return latitude, longitude
    return None


def venue_tokens(event: CanonicalEvent) -> set[str]:
    return tokens(event.venue) - _city_tokens(event)


def venue_query_for(event: CanonicalEvent, rejected: dict[str, str] | None = None) -> str:
    """The research query for a named venue, or "" when the name names no single place."""
    distinctive = venue_tokens(event)
    if not event.city.strip() or not distinctive or distinctive & NON_PLACE_VENUE_WORDS:
        return ""
    if len(distinctive) == 1 and (distinctive <= GENERIC_VENUE_WORDS or next(iter(distinctive)).isdigit()):
        return ""
    if normalized(event.venue) in {normalized(name) for name in (rejected or {})}:
        return ""
    return f"{' '.join(event.venue.split())}, {event.city.strip()}, Deutschland"


def accepted_venue_point(event: CanonicalEvent, results: list[dict]) -> tuple[float, float] | None:
    """Return the best result that the venue research would accept for this name and town."""
    address = " ".join(event.venue_address.split())
    group = {"venue": event.venue, "city": event.city, "addresses": [address] if address else []}
    ranked = []
    for result in results:
        category = str(result.get("category") or result.get("class") or "")
        result_type = str(result.get("type") or "")
        if category == "boundary" or result_type in VENUE_AREA_TYPES:
            continue
        if result_type in AMBIGUOUS_VENUE_TYPES and not street_like(event.venue):
            continue
        score, reasons = candidate_score(group, result)
        if score < VENUE_MIN_SCORE or not VENUE_NAME_MATCHES & set(reasons) or VENUE_CONFLICTS & set(reasons):
            continue
        try:
            latitude, longitude = float(result["lat"]), float(result["lon"])
        except (KeyError, TypeError, ValueError):
            continue
        if haversine(config.BONN_LAT, config.BONN_LON, latitude, longitude) > config.MAX_RADIUS_KM:
            continue
        ranked.append((score, latitude, longitude))
    if not ranked:
        return None
    _, latitude, longitude = max(ranked, key=lambda item: item[0])
    return latitude, longitude


def lookups_for(event: CanonicalEvent, rejected: dict[str, str] | None = None) -> list[tuple[str, str]]:
    """Queries to try in order, each with the location source its accepted pin gets.

    The address goes first. A query built from the venue name because the
    address names no street may also pin the venue itself.
    """
    lookups: list[tuple[str, str]] = []
    if address_query := query_for(event):
        lookups.append((address_query, LOCATION_SOURCE))
    if venue_query := venue_query_for(event, rejected):
        if address_query and not _street(event)[1]:
            # Same name-based query; its results are also judged as a venue.
            lookups.append((address_query, VENUE_LOCATION_SOURCE))
        if venue_query != address_query:
            lookups.append((venue_query, VENUE_LOCATION_SOURCE))
    return lookups


def _accept(event: CanonicalEvent, results: list[dict], source: str) -> tuple[float, float] | None:
    return accepted_venue_point(event, results) if source == VENUE_LOCATION_SOURCE else accepted_point(event, results)


def _known_point(known: dict[str, dict], query: str, source: str) -> tuple[float, float] | None:
    entry = known.get(query)
    if not isinstance(entry, dict) or (entry.get("method") == "venue") != (source == VENUE_LOCATION_SOURCE):
        return None
    return float(entry["latitude"]), float(entry["longitude"])


def enabled() -> bool:
    return os.environ.get("NRW_EVENTS_GEOCODING", "1").strip() != "0"


def run_budget_seconds() -> float:
    """Lookup time per import; a catch-up run may raise it, lookups stay at <= 1 per second."""
    try:
        budget = float(os.environ.get("NRW_EVENTS_GEOCODING_BUDGET_SECONDS", "").strip() or RUN_BUDGET_SECONDS)
    except ValueError:
        return RUN_BUDGET_SECONDS
    return max(0.0, budget)


def cache_path() -> Path:
    configured = os.environ.get("NRW_EVENTS_CACHE_DIR", "").strip()
    cache_dir = Path(configured).expanduser() if configured else config.default_state_dir()
    return cache_dir / "geocoding-v1.sqlite3"


def _load_pin_file(path: Path | None = None) -> dict:
    path = path or POINTS_PATH
    if not path.exists():
        return {"version": 1, "points": {}}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("version") != 1 or not isinstance(payload.get("points"), dict):
        raise ValueError(f"{path.name} must use schema version 1 with a points map")
    if not isinstance(payload.get("rejectedVenues", {}), dict):
        raise ValueError(f"{path.name} rejectedVenues must map venue names to reasons")
    return payload


def load_points(path: Path | None = None) -> dict[str, dict]:
    """Versioned accepted pins; they ship with the importer and never expire.

    A pin found by venue name carries ``"method": "venue"``.
    """
    return _load_pin_file(path)["points"]


def load_rejected_venues(path: Path | None = None) -> dict[str, str]:
    """Venue names whose name lookup matched the wrong place; they are never looked up by name."""
    return _load_pin_file(path).get("rejectedVenues", {})


def _cached(connection: sqlite3.Connection, event: CanonicalEvent, query: str, source: str) -> list[dict] | None:
    row = connection.execute("SELECT fetched_at, results FROM nominatim WHERE query = ?", (query,)).fetchone()
    if row is None:
        return None
    results = json.loads(row[1])
    # A lookup that produced a pin stays; only misses are retried after the TTL.
    if datetime.fromisoformat(row[0]) < datetime.now(timezone.utc) - CACHE_TTL and _accept(event, results, source) is None:
        return None
    return results


def geocode_missing(
    events: list[CanonicalEvent],
    *,
    budget_seconds: float | None = RUN_BUDGET_SECONDS,
    seed: dict[str, dict] | None = None,
    points: dict[str, dict] | None = None,
    rejected: dict[str, str] | None = None,
    matched: dict[str, dict] | None = None,
) -> Counter[str]:
    """Pin events without coordinates in place; unresolved events stay unchanged.

    Lookup order per query: versioned pins, the persistent cache, an optional
    seed in the research-cache format, then Nominatim within the run budget.
    The earliest events are looked up first, so a short budget is spent on the
    days visitors see next. Accepted pins are collected in ``matched``.
    """
    outcomes: Counter[str] = Counter()
    if not enabled():
        return outcomes
    known = load_points() if points is None else points
    rejected = load_rejected_venues() if rejected is None else rejected
    deadline = None if budget_seconds is None else time.monotonic() + budget_seconds
    last_request_at = 0.0
    online = True
    path = cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    order = sorted(range(len(events)), key=lambda index: (events[index].start_date or events[index].date or "9999", index))
    with closing(sqlite3.connect(path, timeout=30)) as connection:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS nominatim (query TEXT PRIMARY KEY, fetched_at TEXT NOT NULL, results TEXT NOT NULL)"
        )
        for index in order:
            event = events[index]
            if event.venue_latitude is not None:
                continue
            lookups = lookups_for(event, rejected)
            if not lookups:
                continue
            # Versioned pins answer before any cache read or request.
            point, location_source = next(
                ((pin, source) for query, source in lookups if (pin := _known_point(known, query, source))),
                (None, ""),
            )
            outcome = "unmatched"
            for query, source in lookups if point is None else ():
                results = _cached(connection, event, query, source)
                fetched_at = ""
                if results is None and seed and isinstance(seed.get(query), dict):
                    results = seed[query].get("results") or []
                    fetched_at = str(seed[query].get("fetchedAt") or "") or datetime.now(timezone.utc).isoformat()
                if results is None:
                    if not online or (deadline is not None and time.monotonic() >= deadline):
                        outcome = "deferred"
                        break
                    time.sleep(max(0.0, MIN_REQUEST_INTERVAL_SECONDS - (time.monotonic() - last_request_at)))
                    try:
                        results = fetch_with_backoff(fetch, query)
                    except (urllib.error.URLError, TimeoutError, ValueError):
                        # Rate limits and outages end lookups for this run instead of hammering the service.
                        online = False
                        outcome = "failed"
                        break
                    finally:
                        last_request_at = time.monotonic()
                    fetched_at = datetime.now(timezone.utc).isoformat()
                if fetched_at:
                    with connection:
                        connection.execute(
                            "INSERT OR REPLACE INTO nominatim VALUES (?, ?, ?)",
                            (query, fetched_at, json.dumps(results, ensure_ascii=False)),
                        )
                point = _accept(event, results, source)
                if point is not None:
                    location_source = source
                    if matched is not None:
                        matched[query] = {
                            "latitude": point[0], "longitude": point[1],
                            **({"method": "venue"} if source == VENUE_LOCATION_SOURCE else {}),
                        }
                    break
            if point is None:
                outcomes[outcome] += 1
                continue
            try:
                events[index] = validate_event({
                    **event.to_dict(),
                    "venue_latitude": point[0],
                    "venue_longitude": point[1],
                    "location_source": location_source,
                })
            except EventValidationError:
                outcomes["rejected"] += 1
            else:
                outcomes["geocoded" if location_source == LOCATION_SOURCE else "geocoded_venue"] += 1
    return outcomes


def backfill(feeds: list[Path], seed_path: Path | None = None, points_path: Path | None = None) -> Counter[str]:
    """Geocode every unpinned event in feed snapshots and keep the pins in the versioned file."""
    from .validation import canonicalize_event

    points_path = points_path or POINTS_PATH
    pin_file = _load_pin_file(points_path)
    points = pin_file["points"]
    seed = json.loads(seed_path.read_text(encoding="utf-8")).get("queries", {}) if seed_path else None
    events = []
    for feed in feeds:
        payload = json.loads(feed.read_text(encoding="utf-8"))
        for raw in payload.get("events", []) if isinstance(payload, dict) else payload:
            try:
                events.append(canonicalize_event(raw))
            except EventValidationError:  # noqa: PERF203 - one stale archive row must not stop the backfill
                continue
    matched: dict[str, dict] = {}
    outcomes = geocode_missing(
        events, budget_seconds=None, seed=seed, points=points,
        rejected=pin_file.get("rejectedVenues", {}), matched=matched,
    )
    checked_at = datetime.now(timezone.utc).date().isoformat()
    for query, pin in matched.items():
        points.setdefault(query, {**pin, "checkedAt": checked_at})
    pin_file["points"] = dict(sorted(points.items()))
    points_path.write_text(json.dumps(pin_file, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    outcomes["stored_points"] = len(points)
    return outcomes


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=backfill.__doc__)
    parser.add_argument("feeds", nargs="+", type=Path, help="feed or archive JSON with an events list")
    parser.add_argument("--seed", type=Path, help="research Nominatim cache ({queries: {q: {fetchedAt, results}}})")
    parser.add_argument("--points", type=Path)
    args = parser.parse_args(argv)
    print(json.dumps(dict(sorted(backfill(args.feeds, args.seed, args.points).items()))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
