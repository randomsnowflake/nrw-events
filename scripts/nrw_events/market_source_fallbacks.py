"""Prefer first-party market records while retaining marktcom as a live fallback."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import TypeVar

from .identity import event_id
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
        "wachtberg",
        "",
        "flohmarkt niederbachem",
        "wachtberg",
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


_GENERIC_MARKET_TOKENS = frozenset({
    "flohmarkt", "trödelmarkt", "trodelmarkt", "markt", "veranstaltung",
})


def _market_tokens(event: CanonicalEvent) -> set[str]:
    city_tokens = set(_identity_text(event.city).split())
    return {
        token
        for token in _identity_text(f"{event.title} {event.venue}").split()
        if token not in _GENERIC_MARKET_TOKENS
        and token not in city_tokens
        and not token.isdigit()
    }


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


def _primary_matches_rule(event: CanonicalEvent, rule: MarketReplacement) -> bool:
    if normalize_source_id(event.source_id or event.source) != rule.primary_source_id:
        return False
    if not rule.title_marker:
        return True
    identity_tokens = set(
        _identity_text(" ".join((event.title, event.venue, event.organizer))).split()
    )
    marker_tokens = set(rule.title_marker.split())
    return marker_tokens <= identity_tokens


def _same_market(
    fallback: CanonicalEvent,
    primary: CanonicalEvent,
    rule: MarketReplacement,
) -> bool:
    if rule.title_marker:
        return _primary_matches_rule(primary, rule)
    # Organizer-only replacement rules can cover several markets on one day in
    # one city. Require a real venue/title token shared by both occurrences.
    return bool(_market_tokens(fallback) & _market_tokens(primary))


def partition_directory_fallbacks(
    events: list[EventT],
) -> tuple[list[EventT], list[EventT]]:
    """Drop a directory copy only when its matching publishable primary exists."""
    primary_by_occurrence: dict[tuple[str, str, str], list[tuple[int, EventT]]] = {}
    for index, event in enumerate(events):
        source_id = normalize_source_id(event.source_id or event.source)
        if source_id == _MARKTCOM_SOURCE_ID:
            continue
        key = (source_id, *_occurrence_key(event))
        primary_by_occurrence.setdefault(key, []).append((index, event))

    replaced_indices: set[int] = set()
    replaced_events: list[EventT] = []
    aliases_by_primary_index: dict[int, list[str]] = {}
    for index, event in enumerate(events):
        rule = _matching_rule(event)
        if not rule:
            continue
        primary_key = (rule.primary_source_id, *_occurrence_key(event))
        matching_primaries = [
            (primary_index, primary)
            for primary_index, primary in primary_by_occurrence.get(primary_key, [])
            if _same_market(event, primary, rule)
        ]
        if len(matching_primaries) != 1:
            continue
        primary_index, primary = matching_primaries[0]
        aliases = list(dict.fromkeys(filter(None, (
            event.preserved_event_id,
            event_id(replace(event, preserved_event_id="").to_dict()),
        ))))
        existing_aliases = list(dict.fromkeys(primary.previous_event_ids))
        pending_aliases = aliases_by_primary_index.get(primary_index, [])
        new_aliases = [
            alias
            for alias in aliases
            if alias not in existing_aliases and alias not in pending_aliases
        ]
        if len(existing_aliases) + len(pending_aliases) + len(new_aliases) > 20:
            continue
        replaced_indices.add(index)
        replaced_events.append(event)
        aliases_by_primary_index.setdefault(primary_index, []).extend(aliases)

    kept: list[EventT] = []
    for index, event in enumerate(events):
        if index in replaced_indices:
            continue
        aliases = aliases_by_primary_index.get(index, [])
        kept_event = event
        if aliases:
            kept_event = replace(
                event,
                previous_event_ids=[
                    *dict.fromkeys([*event.previous_event_ids, *aliases]),
                ][:20],
            )
        kept.append(kept_event)
    return kept, replaced_events
