"""
Much — Bergisches Land village in the eastern Rhein-Sieg-Kreis (~30 km from Bonn).

Reads:  much.de event listing (TYPO3 tx_news). No feed, but server-rendered with
        machine-readable <time datetime="…"> tags paired to title links.
Yields: rural gems — Bergische Gartentour, Trödelmarkt, village live music.

HTML scraping: fails soft (returns []) if the markup changes.
"""

from .. import common

_URL = "https://www.much.de/willkommen/veranstaltungen"
_BASE = "https://www.much.de"


def fetch() -> list:
    source = "Much"
    try:
        html = common.fetch_url(_URL, timeout=20)
        return common.events_from_time_listing(
            html, source, "Much", "lokal markt kultur outdoor konzert", 0.9, _BASE)
    except Exception as e:
        common.log_source_error(source, e)
        return []
