"""
Naturregion Sieg — tourism calendar for the Sieg valley east of Bonn.

Reads:  naturregion-sieg.de/service/veranstaltungskalender
Yields: Windeck, Eitorf, Hennef, Wissen and Sieg-region cultural/outdoor events.
"""

import os
import time
from zoneinfo import ZoneInfo

from .. import common, richtext
from . import regional_common as rc

_URL = "https://naturregion-sieg.de/service/veranstaltungskalender"
_BASE = "https://naturregion-sieg.de"
_SOURCE = "Naturregion Sieg"
_CATEGORY = "naturregion sieg outdoor kultur markt"
_TRUST = 0.9
_DETAIL_FIELDS = (
    "description", "description_source", "price", "admission_basis", "time", "venue", "city", "distance_km",
    "location_confidence", "location_source", "status", "start_at", "end_at",
    "end_date", "all_day", "timezone",
)


_fallback_description = rc.factual_fallback(calendar_name="Naturregion Sieg")


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
        enriched["description_source"] = "scraped"
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
    matching_items = []
    for item in common.jsonld_event_items(html):
        item_title = common.clean_html(item.get("name", "")).casefold()
        item_start = common.parse_iso_date(item.get("startDate", ""))
        if item_title == title and item_start and item_start.strftime("%Y-%m-%d") == start_date:
            matching_items.append(item)
    exact = [
        candidate for candidate in candidates
        if common.clean_html(candidate.get("title", "")).casefold() == title
        and candidate.get("start_date", "") == start_date
    ]
    if exact:
        enriched = _merge_detail_event(event, exact[0])
    else:
        same_date = [
            candidate for candidate in candidates
            if candidate.get("start_date", "") == start_date
        ]
        if len(same_date) == 1:
            enriched = _merge_detail_event(event, same_date[0])
        else:
            enriched = _merge_raw_jsonld_item(event, matching_items[0]) if matching_items else event

    # Some Feratel detail pages leave JSON-LD ``description`` empty while the
    # reviewed page summary is present in og:description/meta description.
    # Prefer that real source copy over the listing's generic factual fallback.
    matched_copy_is_empty = bool(matching_items) and not any(
        common.concise_description(item.get("description", ""))
        for item in matching_items
    )
    has_usable_description = bool(common.concise_description(enriched.get("description", "")))
    if matched_copy_is_empty and (
        not has_usable_description or enriched.get("description_source") == "generated"
    ):
        meta_description = rc.meta_description(html)
        if meta_description:
            enriched = dict(enriched)
            enriched["description"] = meta_description
            enriched["description_html"] = richtext.from_plain_text(meta_description)
            enriched["description_source"] = "scraped"
    return enriched


def _detail_occurrences(event: dict, html: str) -> list[dict]:
    """Expand an exact same-day JSON-LD schedule into bookable occurrences."""
    title = common.clean_html(event.get("title", "")).casefold()
    start_date = event.get("start_date", "")
    raw_items = []
    seen_bounds = set()
    for item in common.jsonld_event_items(html):
        item_title = common.clean_html(item.get("name", "")).casefold()
        item_start = common.parse_iso_date(item.get("startDate", ""))
        if item_title != title or not item_start or item_start.strftime("%Y-%m-%d") != start_date:
            continue
        bounds = (str(item.get("startDate", "")), str(item.get("endDate", "")))
        if bounds not in seen_bounds:
            seen_bounds.add(bounds)
            raw_items.append(item)
    if len(raw_items) <= 1:
        return [_enrich_from_detail(event, html)]

    # The shared JSON-LD parser intentionally collapses repeated title/date rows.
    # Use its first canonical result for location/copy, then reapply each exact
    # raw schedule bound so every separately bookable start time survives.
    base = _enrich_from_detail(event, html)
    return [_merge_raw_jsonld_item(base, item) for item in raw_items]


def _enrich_listing_events(events: list, detail_fetcher=None) -> list:
    batch_timeout = float(os.environ.get("NRW_EVENTS_DETAIL_BATCH_TIMEOUT_SECONDS", "45"))
    deadline = time.monotonic() + max(batch_timeout, 0.0)
    html_by_link = {}
    failed_links = set()
    enriched_events = []
    for event in events:
        occurrences = [event]
        link = (event.get("link") or "").strip()
        remaining = deadline - time.monotonic()
        if (
            common.event_in_window(event)
            and link
            and link not in html_by_link
            and link not in failed_links
            and remaining >= 3.0
        ):
            try:
                request_timeout = 20.0 if remaining >= 40.0 else max(1.0, remaining / 3.0)
                html_by_link[link] = (
                    detail_fetcher(link) if detail_fetcher
                    else common.fetch_detail_url(
                        link, cache_namespace="naturregion-sieg", timeout=request_timeout,
                    )
                )
            except Exception as exc:
                failed_links.add(link)
                common.log_source_error(f"{_SOURCE} detail", exc)
        if link in html_by_link:
            occurrences = _detail_occurrences(event, html_by_link[link])
        for occurrence in occurrences:
            replacement = occurrence.get("description") or _fallback_description(occurrence)
            if replacement:
                occurrence["description"] = replacement
                occurrence["description_source"] = common.description_source_for(replacement)
            enriched_events.append(occurrence)
    return enriched_events


def fetch() -> list:
    try:
        html = common.fetch_url(_URL, timeout=25)
        events = common.events_from_ecmaps_tiles(
            html, _SOURCE, _SOURCE, _CATEGORY, _TRUST, _BASE,
        )
        return _enrich_listing_events(events)
    except Exception as e:
        common.log_source_error(_SOURCE, e)
        return []
