"""Owning implementation of import contracts; core is a compatibility facade."""

from __future__ import annotations

from dataclasses import dataclass, field

from .health import (
    SourceResult,
)
from .models import CanonicalEvent


@dataclass(frozen=True, slots=True)
class ImportResult:
    events: tuple[CanonicalEvent, ...]
    source_results: dict[str, SourceResult]
    pre_dedup_count: int
    run_status: str
    retention: dict[str, object] = field(default_factory=dict)
    series: tuple[dict, ...] = ()
    series_ledger: dict[str, object] = field(default_factory=dict)
    warnings: tuple[dict[str, str], ...] = ()
    timings: dict[str, int] = field(default_factory=dict)
    early_announcements: tuple[CanonicalEvent, ...] = ()
    generated_at: str = ""


@dataclass(frozen=True, slots=True)
class SnapshotPayload:
    events: list[dict]
    metadata: dict
    highlights: dict[str, object] = field(default_factory=dict)
    series_ledger: dict[str, object] = field(default_factory=dict)
