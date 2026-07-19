"""
Siegburg — Kreisstadt event calendar (Rhein-Sieg-Kreis, ~10 km from Bonn).

Reads:  siegburg.de combined calendar iCal export (RFC 5545 .ics).
Yields: exhibitions, museum events, town markets, readings and local happenings.
        The feed also carries recurring/historical anniversary entries; those fall
        outside the window and are dropped by the shared make_event guard.
"""

from .. import common
from . import regional_common as rc

_ICS_URL = ("https://siegburg.de/kalender/kombinierter-kalender/"
            "event.ics?weekends=false&tagMode=ANY")


def _parse_detail_description(html: str) -> str:
    parser = rc.ClassScopedTextParser({
        "subtitle": lambda _tag, attrs: attrs.get("id") == "event_subtitle_wrapper",
        "description": lambda _tag, attrs: "dwa_event_description_text" in (attrs.get("class") or "").split(),
    })
    parser.feed(html or "")

    description_parts = []
    normalized_parts = set()
    for target in ("subtitle", "description"):
        text = parser.text(target)
        normalized = text.casefold()
        if text and normalized not in normalized_parts:
            description_parts.append(text)
            normalized_parts.add(normalized)
    return common.concise_description(" ".join(description_parts))


def _fallback_description(event: dict) -> str:
    start = common.parse_iso_date(event.get("start_date") or "")
    return common.factual_event_description(
        event.get("title", ""), date_value=start, time_text=event.get("time", ""),
        venue=event.get("venue", ""), city=event.get("city", "Siegburg"),
    )


def _enrich_missing_descriptions(events: list, source: str) -> list:
    return rc.enrich_descriptions(
        events,
        source=source,
        cache_namespace="siegburg",
        extract_context=lambda html, _event: _parse_detail_description(html),
        fallback=_fallback_description,
    )


def fetch() -> list:
    source = "Siegburg"
    try:
        events = common.fetch_ical(_ICS_URL, source, "Siegburg", "", 1.0)
        return _enrich_missing_descriptions(events, source)
    except Exception as e:
        common.log_source_error(source, e)
        return []
