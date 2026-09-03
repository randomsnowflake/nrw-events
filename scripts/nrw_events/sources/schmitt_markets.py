"""Regional flea-market dates from Schmitt Veranstaltungen' official calendar."""

import re
from datetime import datetime

from .. import common
from . import regional_common as rc

_SOURCE = "Schmitt Veranstaltungen"
_SOURCE_ID = "schmitt-veranstaltungen"
_URL = "https://fmarkt.de/"
_LOCATION_URLS = (
    "https://fmarkt.de/Standorte/56170-Bendorf/",
    "https://fmarkt.de/Standorte/56218-Muelheim-Kaerlich/",
    "https://fmarkt.de/Standorte/56564-Neuwied/",
    "https://fmarkt.de/Standorte/56626-Andernach/",
    "https://fmarkt.de/Standorte/56727-Mayen/",
)
_PAGE_SEPARATOR = "<!-- NRW-EVENTS-LOCATIONS -->"
_CALENDAR_HEADING = "Unsere Markttermine - Für weitere Infos einfach Termin anklicken!"


def _events_from_pages(html: str, location_html: str = "", *, strict: bool = False) -> list:
    if _CALENDAR_HEADING not in common.clean_html(html or ""):
        if strict:
            raise rc.ParserEmptyError("Schmitt calendar heading changed")
        return []

    rows = re.findall(
        r">\s*(\d{1,2}\.\d{1,2}\.20\d{2})\s*</div>\s*"
        r"<div[^>]*>\s*<a\s+href=([\"'])(.*?)\2[^>]*>(.*?)</a>",
        html or "",
        re.I | re.S,
    )
    if not rows:
        if strict:
            raise rc.ParserEmptyError("Schmitt dated calendar rows changed")
        return []

    clean_page = common.clean_html(html or "")
    events = []
    for date_text, _quote, href, raw_details in rows:
        details = common.clean_html(raw_details)
        location_match = re.match(r"(\d{5})\s+([^,]+),\s*(.+)", details)
        if not location_match:
            if strict:
                raise rc.ParserEmptyError(f"Schmitt location contract changed for {date_text}")
            return []
        postal, city_text, remainder = location_match.groups()
        city = re.sub(r"\s*-\s*", "-", city_text).strip()
        venue = remainder.split(",", 1)[0].strip()
        if not city or not venue:
            if strict:
                raise rc.ParserEmptyError(f"Schmitt venue contract changed for {date_text}")
            return []

        date_value = common.parse_date(date_text)
        if not date_value:
            if strict:
                raise rc.ParserEmptyError(f"Schmitt date contract changed for {date_text}")
            return []
        row_has_sale_time = bool(re.search(r"Verkauf:\s*ab\s*11[.:]00\s*Uhr", details, re.I))
        is_next_market = bool(re.search(
            rf"Unser\s+nächster\s+Flohmarkt\s+{date_value.day}\.{date_value.month}\.\s*.*?"
            rf"{re.escape(city_text.strip())}.*?Verkauf\s+ab\s+11[.:]00\s*Uhr",
            clean_page,
            re.I | re.S,
        ))
        locations = common.clean_html(location_html or "")
        is_confirmed_location_market = bool(re.search(
            rf"Nächster\s+Flohmarkt\s+{re.escape(postal)}\s+{re.escape(city_text.strip())}"
            rf".*?{re.escape(date_text)}",
            locations,
            re.I | re.S,
        )) and bool(re.search(r"ab\s+11[.:]00\s+Uhr", locations, re.I))
        if not (row_has_sale_time or is_next_market or is_confirmed_location_market):
            if strict:
                raise rc.ParserEmptyError(f"Schmitt visitor start time missing for {date_text} {city}")
            return []

        start = datetime(date_value.year, date_value.month, date_value.day, 11, 0)
        event = common.make_event(
            f"Flohmarkt {city}, {venue}",
            start,
            None,
            venue,
            city,
            f"Flohmarkt von Schmitt Veranstaltungen in {city}; Verkauf ab 11 Uhr.",
            rc.abs_url(_URL, href),
            _SOURCE,
            "flohmarkt trödelmarkt markt",
            1.0,
            "ab 11:00",
            source_id=_SOURCE_ID,
        )
        if event and common.event_in_window(event):
            event["organizer"] = _SOURCE
            events.append(event)
    return rc.dedupe(events)


def _events_from_page(html: str, *, strict: bool = False) -> list:
    return _events_from_pages(html, strict=strict)


def _events_from_combined_page(html: str, *, strict: bool = False) -> list:
    calendar_html, separator, location_html = (html or "").partition(_PAGE_SEPARATOR)
    if not separator:
        if strict:
            raise rc.ParserEmptyError("Schmitt combined-page contract changed")
        return []
    return _events_from_pages(calendar_html, location_html, strict=strict)


def _fetch_combined(_url: str, timeout: int = 25) -> str:
    calendar_html = common.fetch_url(_URL, timeout=timeout)
    location_html = "\n".join(common.fetch_url(url, timeout=timeout) for url in _LOCATION_URLS)
    return f"{calendar_html}{_PAGE_SEPARATOR}{location_html}"


def fetch() -> list:
    return rc.fetch_html_events(
        _SOURCE,
        _URL,
        lambda html: _events_from_combined_page(html, strict=True),
        timeout=25,
        source_id=_SOURCE_ID,
        fetcher=_fetch_combined,
    )
