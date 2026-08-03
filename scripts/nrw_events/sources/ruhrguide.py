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
        events = common.events_from_wp_event_manager_listing(
            html, source, "ruhr-guide nrw ruhrgebiet event konzert kultur ausstellung", 0.65)
        return _keep_only_master_data(events)
    except Exception as e:
        common.log_source_error(source, e)
        return []


def _keep_only_master_data(events: list) -> list:
    """Keep Ruhr-Guide dates while discarding all editorial description copy."""
    return [common.keep_only_event_master_data(event) for event in events]
