"""
Hennef (Sieg) — town event calendar (Rhein-Sieg-Kreis, ~14 km from Bonn).

Reads:  hennef.de /veranstaltungen/ (WordPress, schema.org JSON-LD Events).
Yields: small local happenings — readings, workshops, Interkult activities,
        community meetups — exactly the under-the-radar stuff bigger aggregators
        miss.
"""

from .. import common

_URL = "https://www.hennef.de/veranstaltungen/"


def fetch() -> list:
    source = "Hennef"
    try:
        html = common.fetch_url(_URL, timeout=20)
        return common.events_from_jsonld(
            html, source, "Hennef", "lokal veranstaltung markt kultur outdoor", 0.95, _URL)
    except Exception as e:
        common.log_source_error(source, e)
        return []
