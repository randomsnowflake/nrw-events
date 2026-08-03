"""Dated Bonn flea markets from Geide Märkte's first-party HTML pages."""

import re
from datetime import datetime

from .. import common
from ..dates import MONTH_DE
from . import regional_common as rc

_SOURCE = "Geide Märkte"
# The organizer's 2026 schedule PDF states a common visitor market time of
# 11:00–18:00:
# https://www.geide-maerkte.de/files/pdf/2026/Termine-2026.pdf
# Fail closed when the linked schedule rolls to another year so those
# sourced-but-configured hours cannot silently become stale.
_CONFIGURED_HOURS_YEAR = 2026
_PAGES = {
    "https://www.geide-maerkte.de/bonn-nord.html": {
        "title": "Trödelmarkt Bonn-Nord",
        "venue": "OBI/EDEKA, Bornheimer Straße 166",
        "address_pattern": r"Bornheimer\s+Str\.\s*166\s*-\s*53119\s+Bonn",
        "hours": None,
        "source_id": "geide-bonn-nord",
        "city": "Bonn",
    },
    "https://www.geide-maerkte.de/bad-godesberg-hit-markt.html": {
        "title": "Trödelmarkt Bad Godesberg am HIT-Markt",
        "venue": "HIT-Markt, Drachenburgstraße 14",
        "address_pattern": r"Drachenburgstra(?:ße|sse)\s*14\s*-\s*53179\s+Bonn",
        "hours": (11, 18),
        "source_id": "geide-bonn-bad-godesberg",
        "city": "Bonn",
    },
    "https://www.geide-maerkte.de/bonn-alfter-oedekoven.html": {
        "title": "Trödelmarkt Alfter-Oedekoven am OBI",
        "venue": "OBI, Alfterer Straße 35–37",
        "address_pattern": r"Alfterer\s+Str\.\s*35\s*-\s*37\s*-\s*53347\s+Alfter[ -]Oedekoven",
        "hours": (11, 18),
        "source_id": "geide-alfter-obi",
        "city": "Alfter",
    },
    "https://www.geide-maerkte.de/bonn-alfter-oedekoven-rewe-markt.html": {
        "title": "Trödelmarkt Alfter-Oedekoven am REWE",
        "venue": "REWE, Ziegelweg 1",
        "address_pattern": r"Ziegelweg\s*1\s*53347\s+Alfter[ -]Oedekoven",
        "hours": (11, 18),
        "source_id": "geide-alfter-rewe",
        "city": "Alfter",
    },
    "https://www.geide-maerkte.de/sankt-augustin-hit-markt.html": {
        "title": "Trödelmarkt Sankt Augustin am HIT-Markt",
        "venue": "HIT-Markt, Alte Heerstraße 53",
        "address_pattern": r"Alte\s+Heerstra(?:ße|sse)\s*53\s*-\s*53757\s+Sankt\s+Augustin",
        "hours": (11, 18),
        "source_id": "geide-sankt-augustin-hit",
        "city": "Sankt Augustin",
    },
    "https://www.geide-maerkte.de/siegburg.html": {
        "title": "Trödelmarkt Siegburg am OBI",
        "venue": "OBI-Baumarkt Siegburg",
        "address_pattern": r"Adresse\s*/\s*-\s*53721\s+Siegburg",
        "venue_pattern": r"Flohmärkte\s+am\s+OBI-Baumarkt\s+in\s+Siegburg",
        "hours": (11, 18),
        "source_id": "geide-siegburg-obi",
        "city": "Siegburg",
    },
    "https://www.geide-maerkte.de/hennef-sieg.html": {
        "title": "Stadtflohmarkt Hennef",
        "venue": "Marktplatz und Frankfurter Straße",
        "address_pattern": r"Frankfurter\s+Stra(?:ße|sse)\s*-\s*Marktplatz\s*-\s*53773\s+Hennef",
        "hours": (11, 18),
        "source_id": "geide-hennef-stadtflohmarkt",
        "city": "Hennef",
    },
}


def _events_from_page(html: str, page_url: str, *, strict: bool = False) -> list:
    page = _PAGES.get(page_url)
    years = {int(value) for value in re.findall(r"(?:files/pdf/|Termine-)(20\d{2})(?:/|-)", html or "", re.I)}
    clean = common.clean_html(html or "")
    address_ok = bool(page and re.search(page["address_pattern"], clean, re.I))
    venue_ok = bool(page and (not page.get("venue_pattern") or re.search(page["venue_pattern"], clean, re.I)))
    time_match = re.search(
        r"Verkauf\s+der\s+Ware.*?von\s+(\d{1,2})\s*Uhr\s+bis\s+(\d{1,2})\s*Uhr",
        clean,
        re.I,
    )
    configured_hours = page.get("hours") if page else None
    configured_hours_ok = bool(configured_hours and years == {_CONFIGURED_HOURS_YEAR})
    if not (page and len(years) == 1 and address_ok and venue_ok and (time_match or configured_hours_ok)):
        if strict:
            raise rc.ParserEmptyError("Geide year, address, venue, or hours contract changed")
        return []

    year = years.pop()
    start_hour, end_hour = tuple(int(value) for value in time_match.groups()) if time_match else configured_hours
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
        except ValueError as exc:
            if strict:
                raise rc.ParserEmptyError("Geide date contract changed") from exc
            return []
        valid_cards += 1
        if not common.window_contains(start, end):
            continue
        event = common.make_event(
            page["title"],
            start,
            end,
            page["venue"],
            page["city"],
            f"Trödel- und Flohmarkt in {page['city']}; Marktzeit von {start_hour:02d}:00 bis {end_hour:02d}:00 Uhr.",
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
        events.extend(
            rc.fetch_html_events(
                _SOURCE,
                url,
                lambda html, page_url=url: _events_from_page(html, page_url, strict=True),
                timeout=20,
                source_id=_PAGES[url]["source_id"],
                empty_is_healthy=True,
            )
        )
    return rc.dedupe(events)
