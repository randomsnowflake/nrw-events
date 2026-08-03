"""MSD flea-market dates from the first-party Okken organizer page."""

import re
from datetime import datetime

from .. import common
from .fixed_markets import FixedMarketSpec, MarketOccurrence, events_from_occurrences, fetch_market
from . import regional_common as rc


_URL = "https://okkengmbh.de/flohmarkt-bonn/"
_SOURCE = "Okken Märkte"
_SOURCE_ID = "okken-bonn-puetzchen"
_SPEC = FixedMarketSpec(
    title="Der MSD-Flohmarkt in Bonn-Beuel",
    venue="REWE Center Bonn-Beuel, Am Weidenbach 31",
    city="Bonn", source=_SOURCE, source_id=_SOURCE_ID, url=_URL, trust=1.0,
    description=(
        "Flohmarkt mit privaten Händlerinnen und Händlern aus der Region. "
        "Der Eintritt für Besucher ist kostenlos."
    ),
    timeout=20, empty_is_healthy=True,
)


def _events_from_page(html: str, *, strict: bool = False) -> list:
    clean = common.clean_html(html or "")
    address_match = re.search(
        r"REWE\s+Center\s+Bonn-Beuel,\s*Am\s+Weidenbach\s+31,\s*53229\s+Bonn",
        clean,
        re.I,
    )
    occurrences = re.findall(
        r"(\d{1,2}\.\s+[A-Za-zÄÖÜäöü]+\s+20\d{2})\s+von\s+"
        r"(\d{1,2})(?::(\d{2}))?\s*[-–]\s*(\d{1,2})(?::(\d{2}))?\s*Uhr",
        clean,
        re.I,
    )
    if not address_match or not occurrences:
        if strict:
            raise rc.ParserEmptyError("Okken date or location contract changed")
        return []

    parsed = []
    for date_text, start_hour, start_minute, end_hour, end_minute in occurrences:
        date_value = common.parse_date(date_text)
        if not date_value:
            continue
        start = datetime(
            date_value.year, date_value.month, date_value.day,
            int(start_hour), int(start_minute or 0),
        )
        end = datetime(
            date_value.year, date_value.month, date_value.day,
            int(end_hour), int(end_minute or 0),
        )
        if not common.window_contains(start, end):
            continue
        parsed.append(MarketOccurrence(
            start, end,
            f"{int(start_hour):02d}:{int(start_minute or 0):02d}–"
            f"{int(end_hour):02d}:{int(end_minute or 0):02d}",
        ))
    return events_from_occurrences(_SPEC, parsed)


def fetch() -> list:
    return fetch_market(_SPEC, lambda html: _events_from_page(html, strict=True))
