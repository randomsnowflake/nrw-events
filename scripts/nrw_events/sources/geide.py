"""Dated Bonn flea markets from Geide Märkte's first-party HTML pages."""

import re
from datetime import datetime

from .. import common
from ..dates import MONTH_DE
from . import regional_common as rc


_SOURCE = "Geide Märkte"
_PAGES = {
    "https://www.geide-maerkte.de/bonn-nord.html": {
        "title": "Trödelmarkt Bonn-Nord",
        "venue": "OBI/EDEKA, Bornheimer Straße 166",
        "address_pattern": r"Bornheimer\s+Str\.\s*166\s*-\s*53119\s+Bonn",
        "hours": None,
        "source_id": "geide-bonn-nord",
    },
    "https://www.geide-maerkte.de/bad-godesberg-hit-markt.html": {
        "title": "Trödelmarkt Bad Godesberg am HIT-Markt",
        "venue": "HIT-Markt, Drachenburgstraße 14",
        "address_pattern": r"Drachenburgstra(?:ße|sse)\s*14\s*-\s*53179\s+Bonn",
        "hours": (11, 18),
        "source_id": "geide-bonn-bad-godesberg",
    },
}


def _events_from_page(html: str, page_url: str, *, strict: bool = False) -> list:
    page = _PAGES.get(page_url)
    years = {
        int(value)
        for value in re.findall(r"(?:files/pdf/|Termine-)(20\d{2})(?:/|-)", html or "", re.I)
    }
    clean = common.clean_html(html or "")
    address_ok = bool(page and re.search(page["address_pattern"], clean, re.I))
    time_match = re.search(
        r"Verkauf\s+der\s+Ware.*?von\s+(\d{1,2})\s*Uhr\s+bis\s+(\d{1,2})\s*Uhr",
        clean,
        re.I,
    )
    configured_hours = page.get("hours") if page else None
    if not (page and len(years) == 1 and address_ok and (time_match or configured_hours)):
        if strict:
            raise rc.ParserEmptyError("Geide year, address, or hours contract changed")
        return []

    year = years.pop()
    start_hour, end_hour = (
        tuple(int(value) for value in time_match.groups())
        if time_match else configured_hours
    )
    events = []
    valid_cards = 0
    for block in re.findall(r'<div[^>]+class="[^"]*\bevent-itm\b[^"]*"[^>]*>(.*?)</div>\s*</div>', html, re.S | re.I):
        month_match = re.search(r'<div[^>]+class="[^"]*\bheader\b[^"]*"[^>]*>.*?<span>(.*?)</span>', block, re.S | re.I)
        day_match = re.search(r"<strong>\s*(\d{1,2})\s*</strong>", block, re.I)
        link_match = re.search(r'<a[^>]+class="[^"]*\boverlay-lnk\b[^"]*"[^>]+href="([^"]+)"', block, re.S | re.I)
        if not (month_match and day_match and link_match):
            continue
        month = MONTH_DE.get(common.clean_html(month_match.group(1)).casefold().rstrip("."))
        if not month:
            if strict:
                raise rc.ParserEmptyError("Geide month contract changed")
            return []
        try:
            start = datetime(year, month, int(day_match.group(1)), start_hour)
            end = datetime(year, month, int(day_match.group(1)), end_hour)
        except ValueError:
            if strict:
                raise rc.ParserEmptyError("Geide date contract changed")
            return []
        valid_cards += 1
        if not common.window_contains(start, end):
            continue
        event = common.make_event(
            page["title"],
            start,
            end,
            page["venue"],
            "Bonn",
            f"Trödel- und Flohmarkt in Bonn; Verkauf von {start_hour:02d}:00 bis {end_hour:02d}:00 Uhr.",
            rc.abs_url(page_url, link_match.group(1)),
            _SOURCE,
            "trödelmarkt flohmarkt markt",
            0.98,
            f"{start_hour:02d}:00–{end_hour:02d}:00",
            source_id=page["source_id"],
        )
        if event:
            events.append(event)
    if strict and not valid_cards:
        raise rc.ParserEmptyError("Geide event-card contract changed")
    return rc.dedupe(events)


def fetch() -> list:
    events = []
    for url in _PAGES:
        events.extend(rc.fetch_html_events(
            _SOURCE,
            url,
            lambda html, page_url=url: _events_from_page(html, page_url, strict=True),
            timeout=20,
            source_id=_PAGES[url]["source_id"],
            empty_is_healthy=True,
        ))
    return rc.dedupe(events)
