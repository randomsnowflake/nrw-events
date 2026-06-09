"""
Ruhr-Guide — WP Event Manager event listing for Ruhrgebiet / NRW.

Most Ruhrgebiet cities are outside the Bonn-centered 75 km radius, so the parser
keeps only entries whose location resolves to a known in-radius town.
"""

from .. import common

_URL = "https://www.ruhr-guide.de/events/"


def fetch() -> list:
    source = "Ruhr-Guide"
    try:
        html = common.fetch_url(_URL, timeout=25)
        return common.events_from_wp_event_manager_listing(
            html, source, "ruhr-guide nrw ruhrgebiet event konzert kultur ausstellung", 0.65)
    except Exception as e:
        common.log_source_error(source, e)
        return []
