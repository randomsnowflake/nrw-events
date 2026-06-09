"""
Meckenheim — Voreifel town calendar (~13 km SW of Bonn).

Reads:  meckenheim.de event listing (Govconnect municipal CMS). No iCal/JSON-LD,
        but the calendar is server-rendered: each item has a `result-list_object-
        title` link and a machine-readable <time datetime="…">.
Yields: small local happenings — weekly market, guided hikes, book flea markets,
        nature days, town tours — the kind of thing aggregators never see.

HTML scraping via the shared <time>-listing pairer, scoped to the CMS title
wrapper. Fails soft (returns []) if the markup changes.
"""

from .. import common

_URL = "https://www.meckenheim.de/Leben-in-Meckenheim/Veranstaltungen/"
_BASE = "https://www.meckenheim.de"
_TITLE = r'result-list_object-title[^>]*>\s*<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>'


def fetch() -> list:
    source = "Meckenheim"
    try:
        html = common.fetch_url(_URL, timeout=20)
        return common.events_from_time_listing(
            html, source, "Meckenheim", "lokal veranstaltung markt kultur outdoor", 0.9,
            _BASE, min_title=3, max_chars=1500, anchor_pattern=_TITLE)
    except Exception as e:
        common.log_source_error(source, e)
        return []
