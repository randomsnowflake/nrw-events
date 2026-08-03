"""Direct market dates from the Rhein Antik organizer schedule.

Rhein Antik runs the antique/art/design markets on Bonn's Friedensplatz, the
Siegburg and Königswinter market squares and the Bad Honnef pedestrian zone. Those
occurrences previously reached the importer only second hand through a Köln market
operator and the Bonn press calendar, so the organizer's own schedule is the
canonical record.

The page is a single server-rendered Elementor listing: each market is a date item
followed by a location item, with optional marketing badges ("NEU!!!", "NEUER
TERMIN") interleaved as further list items. Only the heading carries the year, and
the day numbers of a multi-day market appear before the single trailing month name
("Mi 3. bis So 7. Juni", "Sa 18. & So 19. Juli").
"""

import re
from datetime import datetime

from .. import common
from ..dates import MONTH_DE
from . import regional_common as rc

_URL = "https://rhein-antik.de/termine/"
_SOURCE = "Rhein Antik"
_SOURCE_ID = "rhein-antik"

_ITEM_PATTERN = re.compile(
    r'<span[^>]+class="[^"]*\belementor-icon-list-text\b[^"]*"[^>]*>(.*?)</span>',
    re.S | re.I,
)
_HEADING_YEAR_PATTERN = re.compile(r"Geplante[^<]{0,120}?Märkte\s*(\d{4})", re.I)
_MONTH_NAMES = "|".join(sorted(MONTH_DE, key=len, reverse=True))
_MONTH_IN_TEXT = re.compile(rf"\b({_MONTH_NAMES})\b\.?", re.I)
_DAY_IN_TEXT = re.compile(r"\b(\d{1,2})\.")
# Marketing badges are appended inside the location item itself, not emitted as
# separate list items: "Siegburg - Marktplatz NEUER TERMIN", "Bendorf -
# Industriedenkmal Sayner Hütte NEU!!!". Strip them off the venue instead of
# treating the item as a badge — discarding the item would silently shift the
# following market's location onto this date.
_BADGE_SUFFIX_PATTERN = re.compile(r"(?:\s*(?:\b(?:wieder\s+da|neuer\s+termin|indoor|neu)\b|!+))+\s*$", re.I)


def _is_date_item(text: str) -> bool:
    return bool(_MONTH_IN_TEXT.search(text) and _DAY_IN_TEXT.search(text))


def _strip_badges(text: str) -> str:
    return _BADGE_SUFFIX_PATTERN.sub("", text or "").strip()


def _parse_dates(text: str, year: int):
    """Return (start, end) for single, ``bis`` range and ``&`` pair notations."""
    months = [
        MONTH_DE[m.group(1).casefold()] for m in _MONTH_IN_TEXT.finditer(text) if m.group(1).casefold() in MONTH_DE
    ]
    days = [int(m.group(1)) for m in _DAY_IN_TEXT.finditer(text)]
    if not months or not days:
        return None, None
    try:
        start = datetime(year, months[0], days[0])
    except ValueError:
        return None, None
    if len(days) < 2:
        return start, None
    # A range may cross a month boundary ("Sa 30. Mai bis So 1. Juni"); a single
    # trailing month name applies to both days.
    try:
        end = datetime(year, months[-1], days[-1])
    except ValueError:
        return start, None
    return (start, end) if end > start else (start, None)


def _city_and_venue(text: str) -> tuple[str, str]:
    """Split "Bonn - Friedensplatz" and "Koblenz / Mülheim-Kärlich"."""
    cleaned = _strip_badges(text)
    city, separator, venue = cleaned.partition(" - ")
    if not separator and " / " in cleaned:
        # The organizer uses a regional label before the slash for the
        # Mülheim-Kärlich venue: "Koblenz / Mülheim-Kärlich CORE
        # Eventlocation". Keep the actual municipality and venue rather than
        # publishing it as a venue-less Koblenz market.
        _, local = cleaned.split(" / ", 1)
        venue_suffix = "CORE Eventlocation"
        if local.endswith(venue_suffix):
            city = local.removesuffix(venue_suffix).strip()
            venue = venue_suffix
    city = city.split("/", 1)[0].strip()
    return city, _strip_badges(venue)


def events_from_listing(html: str) -> list:
    """Parse the organizer schedule; skips badges and out-of-radius towns."""
    year_match = _HEADING_YEAR_PATTERN.search(html or "")
    if not year_match:
        return []
    year = int(year_match.group(1))

    items = [rc.clean(m.group(1)) for m in _ITEM_PATTERN.finditer(html or "")]
    items = [item for item in items if item]

    events = []
    for index, item in enumerate(items):
        if not _is_date_item(item):
            continue
        # The listing strictly alternates date item, location item. Take the very
        # next item rather than scanning ahead, so a location this parser fails to
        # recognise can never inherit the following market's town.
        location = items[index + 1] if index + 1 < len(items) else ""
        if not location or _is_date_item(location):
            continue
        start, end = _parse_dates(item, year)
        if start is None:
            continue
        city, venue = _city_and_venue(location)
        if not city:
            continue
        # The organizer also runs markets well outside the reporting radius
        # (Aachen, Bad Schwalbach). Never coerce an unknown town into Bonn.
        resolved_coords, _, _ = common.resolve_location(city)
        if not resolved_coords:
            continue
        # Normalizing to the press calendar's market name lets the existing
        # antique-market dedup rules collapse this with the same occurrence
        # published by the Bonn press calendar and Köln market operators.
        title = f"Antik-, Kunst- & Designmarkt {city}"
        description = common.factual_event_description(
            title,
            date_value=start,
            venue=venue,
            city=city,
        )
        event = common.make_event(
            title,
            start,
            end,
            venue,
            city,
            description,
            _URL,
            _SOURCE,
            "antikmarkt kunstmarkt designmarkt trödelmarkt markt",
            0.95,
            source_id=_SOURCE_ID,
        )
        if event:
            events.append(event)
    return rc.dedupe(events)


def fetch() -> list:
    try:
        parsed = events_from_listing(common.fetch_url(_URL, timeout=20))
    except Exception as exc:
        common.log_source_error(_SOURCE, exc)
        return []
    common._record_endpoint(
        _URL,
        parser_type="html",
        parsed_event_count=len(parsed),
        parser_empty=not bool(parsed),
    )
    return parsed
