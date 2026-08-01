"""
Rheinaue flea market — Bonn's recurring open-air flea market.

Reads:  the official bonn.de Rheinaue-Flohmarkt page, which exposes a schema.org
        Event JSON-LD carrying the live season start/end dates and the venue
        (Rheinaue, Ludwig-Erhard-Allee 10, Bonn).
Yields: the flea market as a live event whenever its season overlaps the window.

This replaces the old hardcoded "third Saturday" recurrence rule: the dates come
straight from the city's own structured data, so they never go stale.
"""

import re

from .. import common

_URL = ("https://www.bonn.de/veranstaltungskalender/veranstaltungen/"
        "hauptkalender/flohmarkt-rheinaue.php")


def _visible_description(html: str) -> str:
    """Extract the fuller editorial copy omitted by the page's JSON-LD."""
    paragraphs = re.findall(
        r'<div[^>]+class="[^"]*\bSP-Paragraph\b[^"]*"[^>]*>(.*?)</div>',
        html or "",
        re.S | re.I,
    )
    return common.concise_description(" ".join(paragraphs))


def fetch() -> list:
    source = "Rheinauen-Flohmarkt"
    try:
        html = common.fetch_url(_URL, timeout=20)
        events = common.events_from_jsonld(
            html, source, "Bonn", "markt flohmarkt outdoor", 1.0, _URL)
        description = _visible_description(html)
        if description:
            for event in events:
                event["description"] = description
        return events
    except Exception as e:
        common.log_source_error(source, e)
        return []
