"""Shared construction and fail-closed fetching for fixed-location markets."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from .. import common
from . import regional_common as rc


@dataclass(frozen=True, slots=True)
class MarketOccurrence:
    start: datetime
    end: datetime | None = None
    time_text: str = ""


@dataclass(frozen=True, slots=True)
class FixedMarketSpec:
    title: str
    venue: str
    city: str
    source: str
    source_id: str
    url: str
    trust: float
    description: str
    category_hint: str = "flohmarkt trödelmarkt markt"
    price: str = ""
    admission_basis: str = ""
    timeout: int = 25
    empty_is_healthy: bool = False


def events_from_occurrences(
    spec: FixedMarketSpec,
    occurrences: list[MarketOccurrence],
) -> list:
    events = []
    for occurrence in occurrences:
        event = common.make_event(
            spec.title, occurrence.start, occurrence.end, spec.venue, spec.city,
            spec.description, spec.url, spec.source, spec.category_hint,
            spec.trust, occurrence.time_text, source_id=spec.source_id,
        )
        if event and spec.price:
            event["price"] = spec.price
            event["admission_basis"] = spec.admission_basis
        if event and common.event_in_window(event):
            events.append(event)
    return rc.dedupe(events)


def fetch_market(
    spec: FixedMarketSpec,
    parser: Callable[[str], list],
) -> list:
    return rc.fetch_html_events(
        spec.source, spec.url, parser, timeout=spec.timeout,
        source_id=spec.source_id, empty_is_healthy=spec.empty_is_healthy,
    )
