"""Point-exact address geocoding for published events that still lack coordinates.

Follows the public Nominatim policy: one thread, at most one request per
second, an identifying User-Agent and a persistent cache. A result is only
accepted when postcode, municipality, street and house number agree, so the pin
marks one building. Without a house number only a street or square that is
itself the venue and matches the postcode (``Merler Winkel``, 53340) is accepted. Anything
else stays unpinned; the reviewed venue registry remains the primary source.
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
RUN_BUDGET_SECONDS = 120
LOCATION_SOURCE = "geocoded_address"
POINTS_PATH = Path(__file__).with_name("geocoded_addresses.json")
_UMLAUTS = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})
AREA_TYPES = {"city", "town", "village", "suburb", "administrative", "county", "state", "postcode"}


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


def enabled() -> bool:
    return os.environ.get("NRW_EVENTS_GEOCODING", "1").strip() != "0"


def cache_path() -> Path:
    configured = os.environ.get("NRW_EVENTS_CACHE_DIR", "").strip()
    cache_dir = Path(configured).expanduser() if configured else config.default_state_dir()
    return cache_dir / "geocoding-v1.sqlite3"


def load_points(path: Path | None = None) -> dict[str, dict]:
    """Versioned accepted pins; they ship with the importer and never expire."""
    path = path or POINTS_PATH
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("version") != 1 or not isinstance(payload.get("points"), dict):
        raise ValueError(f"{path.name} must use schema version 1 with a points map")
    return payload["points"]


def _cached(connection: sqlite3.Connection, event: CanonicalEvent, query: str) -> list[dict] | None:
    row = connection.execute("SELECT fetched_at, results FROM nominatim WHERE query = ?", (query,)).fetchone()
    if row is None:
        return None
    results = json.loads(row[1])
    # A lookup that produced a pin stays; only misses are retried after the TTL.
    if datetime.fromisoformat(row[0]) < datetime.now(timezone.utc) - CACHE_TTL and accepted_point(event, results) is None:
        return None
    return results


def geocode_missing(
    events: list[CanonicalEvent],
    *,
    budget_seconds: float | None = RUN_BUDGET_SECONDS,
    seed: dict[str, dict] | None = None,
    points: dict[str, dict] | None = None,
) -> Counter[str]:
    """Pin events without coordinates in place; unresolved events stay unchanged.

    Lookup order: versioned pins, the persistent cache, an optional seed in the
    research-cache format, then Nominatim within the run budget.
    """
    outcomes: Counter[str] = Counter()
    if not enabled():
        return outcomes
    known = load_points() if points is None else points
    deadline = None if budget_seconds is None else time.monotonic() + budget_seconds
    last_request_at = 0.0
    online = True
    path = cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path, timeout=30)) as connection:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS nominatim (query TEXT PRIMARY KEY, fetched_at TEXT NOT NULL, results TEXT NOT NULL)"
        )
        for index, event in enumerate(events):
            if event.venue_latitude is not None:
                continue
            query = query_for(event)
            if not query:
                continue
            point: tuple[float, float] | None = None
            if query in known:
                point = (float(known[query]["latitude"]), float(known[query]["longitude"]))
            else:
                results = _cached(connection, event, query)
                fetched_at = ""
                if results is None and seed and isinstance(seed.get(query), dict):
                    results = seed[query].get("results") or []
                    fetched_at = str(seed[query].get("fetchedAt") or "") or datetime.now(timezone.utc).isoformat()
                if results is None:
                    if not online or (deadline is not None and time.monotonic() >= deadline):
                        outcomes["deferred"] += 1
                        continue
                    time.sleep(max(0.0, MIN_REQUEST_INTERVAL_SECONDS - (time.monotonic() - last_request_at)))
                    try:
                        results = fetch_with_backoff(fetch, query)
                    except (urllib.error.URLError, TimeoutError, ValueError):
                        # Rate limits and outages end lookups for this run instead of hammering the service.
                        online = False
                        outcomes["failed"] += 1
                        continue
                    finally:
                        last_request_at = time.monotonic()
                    fetched_at = datetime.now(timezone.utc).isoformat()
                if fetched_at:
                    with connection:
                        connection.execute(
                            "INSERT OR REPLACE INTO nominatim VALUES (?, ?, ?)",
                            (query, fetched_at, json.dumps(results, ensure_ascii=False)),
                        )
                point = accepted_point(event, results)
            if point is None:
                outcomes["unmatched"] += 1
                continue
            try:
                events[index] = validate_event({
                    **event.to_dict(),
                    "venue_latitude": point[0],
                    "venue_longitude": point[1],
                    "location_source": LOCATION_SOURCE,
                })
            except EventValidationError:
                outcomes["rejected"] += 1
            else:
                outcomes["geocoded"] += 1
    return outcomes


def backfill(feeds: list[Path], seed_path: Path | None = None, points_path: Path | None = None) -> Counter[str]:
    """Geocode every unpinned event in feed snapshots and keep the pins in the versioned file."""
    from .validation import canonicalize_event

    points_path = points_path or POINTS_PATH
    points = load_points(points_path)
    seed = json.loads(seed_path.read_text(encoding="utf-8")).get("queries", {}) if seed_path else None
    events = []
    for feed in feeds:
        payload = json.loads(feed.read_text(encoding="utf-8"))
        for raw in payload.get("events", []) if isinstance(payload, dict) else payload:
            try:
                events.append(canonicalize_event(raw))
            except EventValidationError:  # noqa: PERF203 - one stale archive row must not stop the backfill
                continue
    outcomes = geocode_missing(events, budget_seconds=None, seed=seed, points=points)
    checked_at = datetime.now(timezone.utc).date().isoformat()
    for event in events:
        if event.location_source == LOCATION_SOURCE and (query := query_for(event)) not in points:
            points[query] = {"latitude": event.venue_latitude, "longitude": event.venue_longitude, "checkedAt": checked_at}
    points_path.write_text(
        json.dumps({"version": 1, "points": dict(sorted(points.items()))}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
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
