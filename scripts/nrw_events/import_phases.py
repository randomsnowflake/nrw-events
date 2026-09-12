"""In-memory results passed between the ordered import phases."""
from dataclasses import dataclass

from .health import SourceResult
from .models import CanonicalEvent


@dataclass(frozen=True)
class SourceBatch:
    events: list[CanonicalEvent]
    results: dict[str, SourceResult]
    cache_warnings: list[dict[str, str]]
    duration_ms: int

@dataclass(frozen=True)
class PublicationSelection:
    events: list[CanonicalEvent]
    early_candidates: list[CanonicalEvent]
    retained_events: list[CanonicalEvent]
    published_event_ids: set[str]
    retention: dict[str, object]
    pre_dedup_count: int
    generated_at: str
    reviewed_warnings: list[dict[str, str]]
    boundary_warnings: list[dict[str, str]]

@dataclass(frozen=True)
class PublicationEnrichment:
    events: list[CanonicalEvent]
    series: list[dict]
    series_ledger: dict[str, object]
    warnings: tuple[dict[str, str], ...]
    duration_ms: int
