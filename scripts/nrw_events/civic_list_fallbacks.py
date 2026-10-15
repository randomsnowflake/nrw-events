"""Let the city's Martinszug list yield to the organiser's own record.

bonn.de lists every reported procession, but some organisers (BV Holzlar,
Förderverein Sankt Martin Beuel) publish the same procession themselves under
another title and start point. The city row is dropped when exactly one
publishable record from another source covers the same day, district and
procession; the organiser record inherits the row's public ID.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TypeVar

from .dedup_rules import source_authority
from .identity import event_id
from .models import CanonicalEvent, normalize_source_id
from .normalization import comparison_text

EventT = TypeVar("EventT", bound=CanonicalEvent)
CITY_LIST_SOURCE_ID = "bonn-martinszuege"
_PROCESSION_WORDS = ("martinszug", "martinsumzug", "martins umzug", "laternenumzug", "sankt martin", "st martin")
_GENERIC = frozenset({
    "martinszug", "martinsumzug", "martins", "umzug", "sankt", "martin", "bonn",
    "und", "der", "die", "das", "des", "mit", "von", "vom", "zum", "zur", "fuer",
})


def _is_procession(event: CanonicalEvent) -> bool:
    title = comparison_text(event.title)
    return any(word in title for word in _PROCESSION_WORDS)


def _tokens(title: str) -> set[str]:
    return {word for word in comparison_text(title).split() if len(word) >= 3 and word not in _GENERIC}


def _covers(row: CanonicalEvent, primary: CanonicalEvent) -> bool:
    return (
        row.start_date == primary.start_date
        and comparison_text(row.city) == comparison_text(primary.city)
        and _is_procession(primary)
        # Never hand the city's row to Bonn.jetzt-style aggregators.
        and source_authority(primary.source) >= 2
        and bool(_tokens(row.title) & _tokens(primary.title))
    )


def partition_city_list_fallbacks(events: list[EventT]) -> tuple[list[EventT], list[EventT]]:
    """Drop city-list rows that exactly one organiser record covers."""
    rows = [i for i, event in enumerate(events) if normalize_source_id(event.source_id or event.source) == CITY_LIST_SOURCE_ID]
    others = [i for i, event in enumerate(events) if normalize_source_id(event.source_id or event.source) != CITY_LIST_SOURCE_ID]
    matches = {row: [i for i in others if _covers(events[row], events[i])] for row in rows}
    claimed = [found[0] for found in matches.values() if len(found) == 1]
    # One organiser record never absorbs two city rows; that would merge two processions.
    targets = {row: found[0] for row, found in matches.items() if len(found) == 1 and claimed.count(found[0]) == 1}
    aliases: dict[int, list[str]] = {}
    for row, winner in targets.items():
        event = events[row]
        aliases.setdefault(winner, []).extend(filter(None, (
            *event.previous_event_ids,
            event.preserved_event_id,
            event_id(replace(event, preserved_event_id="").to_dict()),
        )))
    kept: list[EventT] = []
    for index, event in enumerate(events):
        if index in targets:
            continue
        kept.append(replace(event, previous_event_ids=[
            *dict.fromkeys([*event.previous_event_ids, *aliases[index]]),
        ]) if index in aliases else event)
    return kept, [events[row] for row in targets]
