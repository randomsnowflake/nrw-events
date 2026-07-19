"""
Much — Bergisches Land village in the eastern Rhein-Sieg-Kreis (~30 km from Bonn).

Reads:  much.de event listing (TYPO3 tx_news). No feed, but server-rendered with
        machine-readable <time datetime="…"> tags paired to title links.
Yields: rural gems — Bergische Gartentour, Trödelmarkt, village live music.

HTML scraping: fails soft (returns []) if the markup changes.
"""

import re

from .. import common
from . import regional_common as rc

_URL = "https://www.much.de/willkommen/veranstaltungen"
_BASE = "https://www.much.de"


def _comparable_text(value: str) -> str:
    return re.sub(r"\W+", "", value.casefold())


def _parse_detail_description(html: str, title: str = "") -> str:
    structured_candidates = [
        item.get("description", "")
        for item in common.jsonld_event_items(html or "")
        if item.get("description")
    ]

    parser = rc.ClassScopedTextParser({
        "description": lambda _tag, attrs: (
            (attrs.get("itemprop") == "description" and "teaser-text" in (attrs.get("class") or "").split())
            or (attrs.get("itemprop") == "articleBody" and "news-text-wrap" in (attrs.get("class") or "").split())
        ),
    })
    parser.feed(html or "")

    title_key = _comparable_text(title)
    candidates = []
    seen = set()
    raw_candidates = structured_candidates or [parser.text("description")]
    for candidate in raw_candidates:
        text = common.clean_html(candidate)
        key = text.casefold()
        if not text or key in seen or (title_key and _comparable_text(text) == title_key):
            continue
        seen.add(key)
        candidates.append(text)

    # Prefer the structured copy: unlike the visible TYPO3 markup, it contains
    # clean email addresses without anti-scraping text inserted between spans.
    # The longest fallback block avoids repeating Much's title/date teaser.
    return common.concise_description(max(candidates, key=len, default=""))


def _structured_detail_context(html: str, title: str) -> dict[str, str]:
    items = common.jsonld_event_items(html or "")
    item = next((candidate for candidate in items
                 if _comparable_text(candidate.get("name", "")) == _comparable_text(title)),
                items[0] if items else {})
    location = item.get("location") if isinstance(item.get("location"), dict) else {}
    address = location.get("address") if isinstance(location.get("address"), dict) else {}
    venue_parts = [
        location.get("name", ""),
        address.get("streetAddress", ""),
        " ".join(filter(None, [address.get("postalCode", ""),
                                address.get("addressLocality", "")])),
    ]
    venue = ", ".join(part for part in venue_parts if part)
    description = _parse_detail_description(html, title)
    if not description and item:
        start = common.parse_iso_date(item.get("startDate", ""))
        end = common.parse_iso_date(item.get("endDate", ""))
        time_text = start.strftime("%H:%M") if start and start.strftime("%H:%M") != "00:00" else ""
        end_time = end.strftime("%H:%M") if start and end and end > start else ""
        description = common.factual_event_description(
            title, date_value=start,
            end_date_value=end if start and end and end.date() != start.date() else None,
            time_text=time_text, end_time_text=end_time, venue=venue, city="Much",
        )
    return {
        "description": common.concise_description(description),
        "venue": common.normalize_venue_name(venue),
    }


def _fallback_description(event: dict) -> str:
    start = common.parse_iso_date(event.get("start_date") or "")
    return common.factual_event_description(
        event.get("title", ""), date_value=start, time_text=event.get("time", ""),
        venue=event.get("venue", ""), city=event.get("city", "Much"),
    )


def _enrich_missing_descriptions(events: list, source: str) -> list:
    return rc.enrich_descriptions(
        events,
        source=source,
        cache_namespace="much",
        timeout=20,
        extract_context=lambda html, event: _structured_detail_context(
            html, event.get("title") or ""),
        fallback=_fallback_description,
    )


def fetch() -> list:
    source = "Much"
    try:
        html = common.fetch_url(_URL, timeout=20)
        events = common.events_from_time_listing(
            html, source, "Much", "lokal markt kultur outdoor konzert", 0.9, _BASE)
        return _enrich_missing_descriptions(events, source)
    except Exception as e:
        common.log_source_error(source, e)
        return []
