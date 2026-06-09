"""
Naturregion Sieg — tourism calendar for the Sieg valley east of Bonn.

Reads:  naturregion-sieg.de/service/veranstaltungskalender
Yields: Windeck, Eitorf, Hennef, Wissen and Sieg-region cultural/outdoor events.
"""

from .. import common

_URL = "https://naturregion-sieg.de/service/veranstaltungskalender"
_BASE = "https://naturregion-sieg.de"


def fetch() -> list:
    source = "Naturregion Sieg"
    try:
        html = common.fetch_url(_URL, timeout=25)
        return common.events_from_ecmaps_tiles(
            html, source, "Naturregion Sieg", "naturregion sieg outdoor kultur markt", 0.9, _BASE)
    except Exception as e:
        common.log_source_error(source, e)
        return []
