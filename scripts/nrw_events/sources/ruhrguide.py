"""
Ruhr-Guide — WP Event Manager event listing for Ruhrgebiet / NRW.

Most Ruhrgebiet cities are outside the Bonn-centered 75 km radius, so the parser
keeps only entries whose location resolves to a known in-radius town.
"""

import re

from .. import common
from . import regional_common as rc

_URL = "https://www.ruhr-guide.de/events/"


def fetch() -> list:
    source = "Ruhr-Guide"
    try:
        html = common.fetch_url(_URL, timeout=25)
        events = common.events_from_wp_event_manager_listing(
            html, source, "ruhr-guide nrw ruhrgebiet event konzert kultur ausstellung", 0.65)
        return _enrich_missing_descriptions(
            events,
            detail_fetcher=lambda url: common.fetch_detail_url(
                url, cache_namespace="ruhrguide", timeout=20),
        )
    except Exception as e:
        common.log_source_error(source, e)
        return []


def _detail_description(html: str) -> str:
    descriptions = [
        common.clean_html(item.get("description") or "")
        for item in common.jsonld_event_items(html or "")
        if item.get("description")
    ]
    if descriptions:
        return common.concise_description(max(descriptions, key=len), max_chars=360)
    metadata = re.search(
        r'<meta[^>]+property=["\']og:description["\'][^>]+'
        r'content=["\']([^"\']+)',
        html or "",
        re.S | re.I,
    )
    return common.concise_description(
        common.clean_html(metadata.group(1) if metadata else ""), max_chars=360)


def _fallback_description(event: dict) -> str:
    start = common.parse_iso_date(event.get("start_date") or "")
    return common.factual_event_description(
        event.get("title", ""), date_value=start, time_text=event.get("time", ""),
        venue=event.get("venue", ""), city=event.get("city", ""),
    )


def _enrich_missing_descriptions(events: list, detail_fetcher) -> list:
    return rc.enrich_descriptions(
        events,
        source="Ruhr-Guide detail",
        cache_namespace="ruhrguide",
        extract_context=lambda html, _event: _detail_description(html),
        fallback=_fallback_description,
        detail_fetcher=detail_fetcher,
    )
