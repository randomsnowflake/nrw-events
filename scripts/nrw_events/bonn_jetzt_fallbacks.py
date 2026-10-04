"""Publish Bonn.jetzt only where no first-party record covers the occurrence.

Bonn.jetzt is a community aggregator. Its cards stay registered as a fallback,
but a same-run publishable record from any non-aggregator source replaces the
card. The replaced card's public ID is inherited so old URLs keep resolving.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from typing import TypeVar

from .dedup_rules import source_authority
from .duplicate_identity import _normalized_city
from .identity import event_id
from .models import CanonicalEvent, normalize_source_id
from .normalization import comparison_text

EventT = TypeVar("EventT", bound=CanonicalEvent)
BONN_JETZT_SOURCE_ID = "bonn-jetzt"
# Bonn.jetzt and organisers disagree by up to an hour on open-door times
# (Datenburg: "ab 18 Uhr" vs. 19:00 on Bonn.jetzt).
_MAX_START_DRIFT_SECONDS = 60 * 60
_GENERIC = frozenset({
    "bonn", "und", "der", "die", "das", "des", "den", "dem", "fuer", "mit", "von", "vom",
    "zum", "zur", "ein", "eine", "im", "in", "am", "an", "auf", "e", "v", "ev", "live",
})


def _tokens(*values: object) -> set[str]:
    words = comparison_text(" ".join(str(value or "") for value in values)).split()
    return {word for word in words if len(word) >= 3 and word not in _GENERIC and not word.isdigit()}


def _title_matches(fallback: set[str], primary: set[str]) -> bool:
    if not fallback or not primary:
        return False
    shared = fallback & primary
    smaller = min(len(fallback), len(primary))
    return len("".join(shared)) >= 8 and len(shared) * 3 >= smaller * 2


def _start(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None
    except ValueError:
        return None


def _covers(fallback: CanonicalEvent, primary: CanonicalEvent) -> bool:
    if _normalized_city(fallback.city) != _normalized_city(primary.city):
        return False
    first_day = fallback.start_date
    last_day = fallback.end_date or first_day
    if not first_day <= primary.start_date <= last_day:
        return False
    if not _title_matches(_tokens(fallback.title), _tokens(primary.title)):
        return False
    city = _tokens(fallback.city, primary.city)
    fallback_place = _tokens(fallback.venue, fallback.venue_address) - city
    primary_place = _tokens(primary.venue, primary.venue_address) - city
    if fallback_place and primary_place and not fallback_place & primary_place:
        return False
    left, right = _start(fallback.start_at), _start(primary.start_at)
    if first_day == last_day and left and right and not fallback.all_day and not primary.all_day:
        return abs((left - right).total_seconds()) <= _MAX_START_DRIFT_SECONDS
    return True


def partition_bonn_jetzt_fallbacks(events: list[EventT]) -> tuple[list[EventT], list[EventT]]:
    """Drop Bonn.jetzt cards covered by a publishable non-aggregator record."""
    primaries = [
        (index, event) for index, event in enumerate(events)
        if normalize_source_id(event.source_id or event.source) != BONN_JETZT_SOURCE_ID
        and source_authority(event.source) >= 2
    ]
    replaced: list[EventT] = []
    replaced_indices: set[int] = set()
    aliases: dict[int, list[str]] = {}
    for index, event in enumerate(events):
        if normalize_source_id(event.source_id or event.source) != BONN_JETZT_SOURCE_ID:
            continue
        match = next((pair for pair in primaries if _covers(event, pair[1])), None)
        if match is None:
            continue
        replaced_indices.add(index)
        replaced.append(event)
        aliases.setdefault(match[0], []).extend(filter(None, (
            event.preserved_event_id,
            event_id(replace(event, preserved_event_id="").to_dict()),
        )))
    kept: list[EventT] = []
    for index, event in enumerate(events):
        if index in replaced_indices:
            continue
        kept.append(replace(event, previous_event_ids=[
            *dict.fromkeys([*event.previous_event_ids, *aliases[index]]),
        ]) if index in aliases else event)
    return kept, replaced
