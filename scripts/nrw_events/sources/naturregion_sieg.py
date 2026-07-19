"""
Naturregion Sieg — tourism calendar for the Sieg valley east of Bonn.

Reads:  naturregion-sieg.de/service/veranstaltungskalender
Yields: Windeck, Eitorf, Hennef, Wissen and Sieg-region cultural/outdoor events.
"""

from zoneinfo import ZoneInfo

from .. import common
from . import regional_common as rc

_URL = "https://naturregion-sieg.de/service/veranstaltungskalender"
_BASE = "https://naturregion-sieg.de"
_SOURCE = "Naturregion Sieg"
_CATEGORY = "naturregion sieg outdoor kultur markt"
_TRUST = 0.9
_DETAIL_FIELDS = (
    "description", "price", "time", "venue", "city", "distance_km",
    "location_confidence", "location_source", "status", "start_at", "end_at",
    "end_date", "all_day", "timezone",
)


def _fallback_description(event: dict) -> str:
    """Build a factual minimum description when a detail page is unavailable."""
    start = common.parse_iso_date(event.get("start_date", ""))
    return common.factual_event_description(
        event.get("title", ""), date_value=start or event.get("date", ""),
        time_text=event.get("time", ""), venue=event.get("venue", ""),
        city=event.get("city", ""), calendar_name="Naturregion Sieg",
    )


def _merge_detail_event(event: dict, detail_event: dict) -> dict:
    """Add authoritative detail fields without changing trusted listing rank/category."""
    enriched = dict(event)
    for field in _DETAIL_FIELDS:
        value = detail_event.get(field)
        if value not in (None, ""):
            enriched[field] = value
    return enriched


def _merge_raw_jsonld_item(event: dict, item: dict) -> dict:
    """Recover detail copy even when policy rejects rebuilding the whole event."""
    enriched = dict(event)
    description = common.concise_description(item.get("description", ""))
    if description:
        enriched["description"] = description
        enriched["price"] = common.infer_free_admission_price(
            enriched.get("title", ""), description, enriched.get("price", ""),
        )

    start = common.parse_iso_date(item.get("startDate", ""))
    end = common.parse_iso_date(item.get("endDate", "")) or start
    if start and "T" in str(item.get("startDate", "")):
        time_text = start.strftime("%H:%M")
        if end and end != start:
            time_text += f"–{end.strftime('%H:%M')}"
        enriched["time"] = common.sanitize_time_text(time_text)
        enriched["all_day"] = False
        local_zone = ZoneInfo("Europe/Berlin")
        enriched["start_at"] = start.replace(tzinfo=local_zone).isoformat(timespec="minutes")
        enriched["end_at"] = end.replace(tzinfo=local_zone).isoformat(timespec="minutes") if end else ""
    return enriched


def _enrich_from_detail(event: dict, html: str) -> dict:
    """Prefer the exact dated JSON-LD occurrence from an event detail page."""
    candidates = common.events_from_jsonld(
        html,
        _SOURCE,
        event.get("city", "") or _SOURCE,
        _CATEGORY,
        _TRUST,
        event.get("link", ""),
    )
    title = common.clean_html(event.get("title", "")).casefold()
    start_date = event.get("start_date", "")
    exact = [
        candidate for candidate in candidates
        if common.clean_html(candidate.get("title", "")).casefold() == title
        and candidate.get("start_date", "") == start_date
    ]
    if exact:
        return _merge_detail_event(event, exact[0])

    same_date = [
        candidate for candidate in candidates
        if candidate.get("start_date", "") == start_date
    ]
    if len(same_date) == 1:
        return _merge_detail_event(event, same_date[0])

    raw_items = []
    for item in common.jsonld_event_items(html):
        item_title = common.clean_html(item.get("name", "")).casefold()
        item_start = common.parse_iso_date(item.get("startDate", ""))
        if item_title == title and item_start and item_start.strftime("%Y-%m-%d") == start_date:
            raw_items.append(item)
    return _merge_raw_jsonld_item(event, raw_items[0]) if raw_items else event


def _enrich_listing_events(events: list, detail_fetcher) -> list:
    return rc.enrich_descriptions(
        events,
        source=f"{_SOURCE} detail",
        cache_namespace="naturregion-sieg",
        extract_context=lambda html, event: _enrich_from_detail(event, html),
        fallback=_fallback_description,
        detail_fetcher=detail_fetcher,
        needs_enrichment=lambda _event: True,
        merge_context=lambda _event, enriched: enriched,
    )


def fetch() -> list:
    try:
        html = common.fetch_url(_URL, timeout=25)
        events = common.events_from_ecmaps_tiles(
            html, _SOURCE, _SOURCE, _CATEGORY, _TRUST, _BASE,
        )
        return _enrich_listing_events(
            events,
            detail_fetcher=lambda url: common.fetch_detail_url(
                url, cache_namespace="naturregion-sieg", timeout=20),
        )
    except Exception as e:
        common.log_source_error(_SOURCE, e)
        return []
