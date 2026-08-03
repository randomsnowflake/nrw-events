"""Regional Melan flea markets from the organizer's first-party schedule."""

import re
from datetime import datetime

from .. import common
from . import regional_common as rc

_SOURCE = "Melan Märkte"
_SOURCE_ID = "melan-regional-markets"
_URL = "https://www.melan.de/fuer-alle/markttermine/"
_TARGETS = {
    "Bornheim PORTA": {
        "title": "Trödelmarkt Bornheim PORTA",
        "city": "Bornheim",
        "venue": "PORTA, Alexander-Bell-Straße 2",
        "address_pattern": r"Alexander-Bell-Stra(?:ße|sse)\s*2\s*53332\s+Bornheim",
        "source_id": "melan-bornheim-porta",
    },
    "St. Augustin METRO": {
        "title": "Trödelmarkt Sankt Augustin METRO",
        "city": "Sankt Augustin",
        "venue": "METRO, Einsteinstraße 28",
        "address_pattern": r"Einsteinstra(?:ße|sse)\s*28\s*53757\s+St\.\s*Augustin",
        "source_id": "melan-sankt-augustin-metro",
    },
}


def _events_from_page(html: str, *, strict: bool = False) -> list:
    if not re.search(r'class="[^"]*\bdate-markets\b', html or "", re.I):
        if strict:
            raise rc.ParserEmptyError("Melan dated-listing contract changed")
        return []

    events = []
    matched_targets = set()
    date_blocks = re.split(
        r'(?=<div[^>]+class="[^"]*\bdate-markets\b[^"]*"[^>]*>)',
        html,
        flags=re.I,
    )
    for date_block in date_blocks:
        date_match = re.search(
            r'<h1[^>]+class="[^"]*\bdate\b[^"]*"[^>]*>\s*(?:Sonntag\s+)?(\d{1,2})\.(\d{1,2})\.(20\d{2})\s*</h1>',
            date_block,
            re.I,
        )
        if not date_match:
            continue
        day, month, year = (int(value) for value in date_match.groups())
        card_blocks = re.split(
            r'(?=<a[^>]+class="[^"]*\bmarket-list-item\b[^"]*"[^>]*>)',
            date_block,
            flags=re.I,
        )
        for card in card_blocks:
            title_match = re.search(r"<h2[^>]*>(.*?)</h2>", card, re.S | re.I)
            if not title_match:
                continue
            source_title = common.clean_html(title_match.group(1))
            target = _TARGETS.get(source_title)
            if not target:
                continue
            card_text = common.clean_html(card)
            time_match = re.search(
                r"(\d{1,2})[:.]([0-5]\d)\s*-\s*(\d{1,2})[:.]([0-5]\d)\s*Uhr",
                card_text,
                re.I,
            )
            link_match = re.search(r'<a[^>]+href="([^"]+)"', card, re.I)
            address_ok = bool(re.search(target["address_pattern"], card_text, re.I))
            if not (time_match and link_match and address_ok):
                if strict:
                    raise rc.ParserEmptyError(f"Melan card contract changed for {source_title}")
                return []
            matched_targets.add(source_title)
            start_hour, start_minute, end_hour, end_minute = (int(value) for value in time_match.groups())
            try:
                start = datetime(year, month, day, start_hour, start_minute)
                end = datetime(year, month, day, end_hour, end_minute)
            except ValueError as exc:
                if strict:
                    raise rc.ParserEmptyError("Melan date contract changed") from exc
                return []
            event = common.make_event(
                target["title"],
                start,
                end,
                target["venue"],
                target["city"],
                "Regionaler Trödelmarkt mit privaten und gewerblichen Ständen.",
                rc.abs_url(_URL, link_match.group(1)),
                _SOURCE,
                "trödelmarkt flohmarkt markt",
                0.98,
                f"{start_hour:02d}:{start_minute:02d}–{end_hour:02d}:{end_minute:02d}",
                source_id=target["source_id"],
            )
            if event and common.event_in_window(event):
                events.append(event)
    missing_targets = set(_TARGETS) - matched_targets
    if strict and missing_targets:
        names = ", ".join(sorted(missing_targets))
        raise rc.ParserEmptyError(f"Melan regional market cards missing: {names}")
    return rc.dedupe(events)


def fetch() -> list:
    return rc.fetch_html_events(
        _SOURCE,
        _URL,
        lambda html: _events_from_page(html, strict=True),
        timeout=35,
        source_id=_SOURCE_ID,
    )
