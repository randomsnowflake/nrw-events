"""Kindersachen flea-market dates from Krewelshof's first-party page."""

import re
from datetime import datetime

from .. import common
from ..dates import MONTH_ALL
from . import regional_common as rc
from .fixed_markets import FixedMarketSpec, MarketOccurrence, events_from_occurrences, fetch_market

_SOURCE = "Krewelshof Kindersachen-Flohmarkt"
_SOURCE_ID = "krewelshof-lohmar"
_URL = "https://krewelshof.de/kinder-familie/flohmarkt/"
_SPEC = FixedMarketSpec(
    title="Kindersachen-Flohmarkt Krewelshof Lohmar",
    venue="Krewelshof Lohmar, Krewelshof 1", city="Lohmar",
    source=_SOURCE, source_id=_SOURCE_ID, url=_URL, trust=0.98,
    description="Flohmarkt für gebrauchte Kinder- und Jugendkleidung, Spielzeug und Bücher.",
    category_hint="kinderflohmarkt kindersachen flohmarkt markt",
)


def _events_from_page(html: str, *, strict: bool = False) -> list:
    clean = common.clean_html(html or "")
    next_date_match = re.search(
        r"Nächster\s+Flohmarkttermin:\s*\d{1,2}\.\s*[A-Za-zäöüÄÖÜ.]+\s*(20\d{2})",
        clean,
        re.I,
    )
    location_ok = bool(re.search(r"Krewelshof\s+in\s+(?:Köln/)?Lohmar", clean, re.I))
    time_match = re.search(
        r"(?:zwischen\s+)?(\d{1,2}):([0-5]\d)\s+(?:Uhr\s+)?(?:und|bis)\s+(\d{1,2}):([0-5]\d)",
        clean,
        re.I,
    )
    schedule_match = re.search(
        r"(Sonntag\s+\d{1,2}\..*?Im\s+Dezember\s+keine\s+Termine)",
        clean,
        re.S | re.I,
    )
    if not (next_date_match and location_ok and time_match and schedule_match):
        if strict:
            raise rc.ParserEmptyError("Krewelshof year, location, time, or schedule contract changed")
        return []

    year = int(next_date_match.group(1))
    start_hour, start_minute, end_hour, end_minute = (int(value) for value in time_match.groups())
    date_matches = re.findall(
        r"(?:Sonntag|Samstag)\s+(\d{1,2})\.\s*([A-Za-zäöüÄÖÜ.]+)",
        schedule_match.group(1),
        re.I,
    )
    if not date_matches:
        if strict:
            raise rc.ParserEmptyError("Krewelshof dated schedule contract changed")
        return []

    parsed = []
    for day_text, month_text in date_matches:
        month = MONTH_ALL.get(month_text.casefold().rstrip("."))
        if not month:
            if strict:
                raise rc.ParserEmptyError("Krewelshof month contract changed")
            return []
        try:
            start = datetime(year, month, int(day_text), start_hour, start_minute)
            end = datetime(year, month, int(day_text), end_hour, end_minute)
        except ValueError as exc:
            if strict:
                raise rc.ParserEmptyError("Krewelshof date contract changed") from exc
            return []
        parsed.append(MarketOccurrence(
            start, end,
            f"{start_hour:02d}:{start_minute:02d}–{end_hour:02d}:{end_minute:02d}",
        ))
    return events_from_occurrences(_SPEC, parsed)


def fetch() -> list:
    return fetch_market(_SPEC, lambda html: _events_from_page(html, strict=True))
