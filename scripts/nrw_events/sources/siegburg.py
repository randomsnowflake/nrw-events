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
        text = parser.block_text(target)
        # Dedupe on the flattened form: a subtitle repeated as the opening
        # paragraph differs only in its breaks.
        normalized = " ".join(text.casefold().split())
        if text and normalized not in normalized_parts:
            description_parts.append(text)
            normalized_parts.add(normalized)
    description = common.concise_description("\n\n".join(description_parts), max_chars=0)
    return description.replace("ent scheiden", "entscheiden")


_fallback_description = rc.factual_fallback("Siegburg")


def _enrich_missing_descriptions(events: list, source: str) -> list:
    return rc.enrich_descriptions(
        events,
        source=source,
        cache_namespace="siegburg",
        extract_context=lambda html, _event: _parse_detail_description(html),
        fallback=_fallback_description,
        needs_enrichment=lambda event: (
            not event.get("description")
            or "[…]" in event.get("description", "")
            or "[...]" in event.get("description", "")
            or event.get("description", "").rstrip().endswith(("…", "..."))
        ),
    )


def _normalize_obvious_source_typos(events: list) -> list:
    for event in events:
        description = event.get("description", "").replace("ent scheiden", "entscheiden")
        if description != event.get("description", ""):
            event["description"] = description
            event["description_html"] = event.get("description_html", "").replace(
                "ent scheiden", "entscheiden"
            )
    return events


def fetch() -> list:
    source = "Siegburg"
    try:
        events = common.fetch_ical(
            _ICS_URL, source, "Siegburg", "", 1.0,
            description_max_chars=0,
        )
        return _enrich_missing_descriptions(_normalize_obvious_source_typos(events), source)
    except Exception as e:
        common.log_source_error(source, e)
        return []
