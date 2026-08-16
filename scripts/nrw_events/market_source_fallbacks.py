"""Prefer first-party market records while retaining marktcom as a live fallback."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TypeVar

from .models import CanonicalEvent, normalize_source_id


EventT = TypeVar("EventT", bound=CanonicalEvent)
_MARKTCOM_SOURCE_ID = "marktcom"


@dataclass(frozen=True, slots=True)
class MarketReplacement:
    primary_source_id: str
    organizer_marker: str
    title_marker: str = ""
    city_marker: str = ""


_REPLACEMENTS = (
    MarketReplacement(
        "rossel-wilberhofen-dorfflohmarkt",
        "bv wilberhofen rossel",
        "dorf flohmarkt",
        "windeck",
    ),
    MarketReplacement(
        "schmitt-veranstaltungen",
        "schmitt veranstaltungen",
    ),
    MarketReplacement(
        "rieder-solingen-rewe",
        "rieder märkte",
        "rewe ihr kaufpark solingen aufderhöhe",
        "solingen",
    ),
)


def _identity_text(value: object) -> str:
    return " ".join(re.sub(r"[^\w]+", " ", str(value or "").casefold()).split())


def _occurrence_key(event: CanonicalEvent) -> tuple[str, str]:
    return event.start_date, _identity_text(event.city)


def _matching_rule(event: CanonicalEvent) -> MarketReplacement | None:
    if normalize_source_id(event.source_id or event.source) != _MARKTCOM_SOURCE_ID:
        return None
    organizer = _identity_text(event.organizer)
    title = _identity_text(" ".join((event.title, event.venue)))
    city = _identity_text(event.city)
    for rule in _REPLACEMENTS:
        if rule.organizer_marker not in organizer:
            continue
        if rule.title_marker and rule.title_marker not in title:
            continue
        if rule.city_marker and rule.city_marker not in city:
            continue
        return rule
    return None


def partition_directory_fallbacks(
    events: list[EventT],
) -> tuple[list[EventT], list[EventT]]:
    """Drop a directory copy only when its publishable primary occurrence exists."""
    primary_occurrences = {
        (normalize_source_id(event.source_id or event.source), *_occurrence_key(event))
        for event in events
        if normalize_source_id(event.source_id or event.source) != _MARKTCOM_SOURCE_ID
    }
    kept: list[EventT] = []
    replaced: list[EventT] = []
    for event in events:
        rule = _matching_rule(event)
        primary_key = (
            rule.primary_source_id,
            *_occurrence_key(event),
        ) if rule else None
        if primary_key and primary_key in primary_occurrences:
            replaced.append(event)
        else:
            kept.append(event)
    return kept, replaced
