"""
Rheinaue flea market — Bonn's recurring open-air flea market.

Reads:  the official bonn.de Rheinaue-Flohmarkt page, which exposes a schema.org
        Event JSON-LD carrying the live season start/end dates and the venue
        (Rheinaue, Ludwig-Erhard-Allee 10, Bonn).
Yields: the flea market as a live event whenever its season overlaps the window.

This replaces the old hardcoded "third Saturday" recurrence rule: the dates come
straight from the city's own structured data, so they never go stale.
"""

from .. import common

_URL = ("https://www.bonn.de/veranstaltungskalender/veranstaltungen/"
        "hauptkalender/flohmarkt-rheinaue.php")


def fetch() -> list:
    source = "Rheinauen-Flohmarkt"
    try:
        html = common.fetch_url(_URL, timeout=20)
        return common.events_from_jsonld(
            html, source, "Bonn", "markt flohmarkt outdoor", 1.0, _URL)
    except Exception as e:
        common.log_source_error(source, e)
        return []
