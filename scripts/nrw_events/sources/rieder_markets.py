"""Solingen REWE flea-market dates from Rieder Märkte's first-party pages."""

import re
from datetime import datetime

from .. import common
from . import regional_common as rc


_SOURCE = "Rieder Märkte"
_SOURCE_ID = "rieder-solingen-rewe"
_TERMS_URL = "https://www.rieder-maerkte.de/termine/"
_LOCATION_URL = "https://www.rieder-maerkte.de/standort/solingen-rewe-ihr-kaufpark/"
_PAGE_SEPARATOR = "<!-- NRW-EVENTS-LOCATION -->"
_TITLE_SUFFIX = "Solingen-Aufderhöhe, REWE Ihr Kaufpark"


def _schedule_confirms_no_target(terms_html: str) -> bool:
    has_dated_cards = bool(re.search(
        r'<h2[^>]*woocommerce-loop-product__title[^>]*>\s*\d{2}\.\d{2}\.20\d{2}\b',
        terms_html or "",
        re.I | re.S,
    ))
    target_still_present = bool(re.search(r"Solingen-Aufderhöhe", terms_html or "", re.I))
    return has_dated_cards and not target_still_present


def _events_from_pages(terms_html: str, location_html: str, *, strict: bool = False) -> list:
    location = common.clean_html(location_html or "")
    address_ok = bool(re.search(r"Friedenstraße\s*96,?\s*42699\s+Solingen", location, re.I))
    hours_ok = bool(re.search(
        r"Verkaufszeiten\s+sind\s+an\s+Sonn[‐-]\s*&\s*Feiertagen\s+von\s+11\s+bis\s+18\s+Uhr",
        location,
        re.I,
    ))
    if not (address_ok and hours_ok):
        if strict:
            raise rc.ParserEmptyError("Rieder Solingen address or hours contract changed")
        return []

    cards = re.findall(
        r'<a\s+href=(["\'])([^"\']+)\1[^>]*>\s*<h2[^>]*>\s*'
        r'(\d{2}\.\d{2}\.20\d{2})\s+Solingen-Aufderhöhe,\s*REWE\s+Ihr\s+Kaufpark\s*'
        r'</h2>',
        terms_html or "",
        re.I | re.S,
    )
    if not cards:
        if _schedule_confirms_no_target(terms_html):
            return []
        if strict:
            raise rc.ParserEmptyError("Rieder Solingen dated cards changed")
        return []

    events = []
    for _quote, href, date_text in cards:
        date_value = common.parse_date(date_text)
        if not date_value:
            if strict:
                raise rc.ParserEmptyError("Rieder Solingen date contract changed")
            return []
        start = datetime(date_value.year, date_value.month, date_value.day, 11, 0)
        end = datetime(date_value.year, date_value.month, date_value.day, 18, 0)
        event = common.make_event(
            "Trödelmarkt Solingen-Aufderhöhe, REWE Ihr Kaufpark",
            start,
            end,
            "REWE Ihr Kaufpark, Friedenstraße 96",
            "Solingen",
            "Regelmäßig stattfindender Trödelmarkt mit hohem Trödelanteil und wenigen Neuwarenständen.",
            rc.abs_url(_TERMS_URL, href),
            _SOURCE,
            "flohmarkt trödelmarkt markt",
            1.0,
            "11:00–18:00",
            source_id=_SOURCE_ID,
        )
        if event and common.event_in_window(event):
            event["organizer"] = _SOURCE
            events.append(event)
    return rc.dedupe(events)


def _events_from_combined_page(html: str, *, strict: bool = False) -> list:
    terms_html, separator, location_html = (html or "").partition(_PAGE_SEPARATOR)
    if not separator:
        if strict:
            raise rc.ParserEmptyError("Rieder combined-page contract changed")
        return []
    return _events_from_pages(terms_html, location_html, strict=strict)


def _fetch_combined(_url: str, timeout: int = 25) -> str:
    terms_html = common.fetch_url(_TERMS_URL, timeout=timeout)
    location_html = common.fetch_url(_LOCATION_URL, timeout=timeout)
    return f"{terms_html}{_PAGE_SEPARATOR}{location_html}"


def _combined_page_confirms_no_target(html: str) -> bool:
    terms_html, separator, _location_html = (html or "").partition(_PAGE_SEPARATOR)
    return bool(separator) and _schedule_confirms_no_target(terms_html)


def fetch() -> list:
    return rc.fetch_html_events(
        _SOURCE,
        _TERMS_URL,
        lambda html: _events_from_combined_page(html, strict=True),
        timeout=25,
        source_id=_SOURCE_ID,
        fetcher=_fetch_combined,
        empty_is_healthy=_combined_page_confirms_no_target,
    )
