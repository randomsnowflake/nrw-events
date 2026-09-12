"""Owning implementation of import orchestration; core is a compatibility facade."""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor

from . import (
    common,
    components,
    performance,
    report,
)
from . import import_contracts as _impl_import_contracts
from . import retention_policy as _impl_retention_policy
from . import source_execution as _impl_source_execution
from .health import (
    sanitized_warning,
)
from .identity import event_id
from .import_phases import PublicationEnrichment, PublicationSelection, SourceBatch
from .models import normalize_source_id
from .publication_enrichment import enrich_publication
from .publication_selection import select_publication
from .runtime import RunContext
from .source_batch import collect_sources


@performance.measured("import.total")
def run_import(context: RunContext, sources: Mapping[str, Callable[[], object]],
               executor_factory: Callable[..., ThreadPoolExecutor] = _impl_source_execution._DetachedThreadPoolExecutor) -> _impl_import_contracts.ImportResult:
    """Execute one import with runtime settings isolated to its context."""
    token = common.configure_context(context)
    try:
        component_workers = max(1, min(int(os.environ.get("NRW_EVENTS_COMPONENT_WORKERS", "3")), 4))
        with components.pool_scope(component_workers, executor_factory= _impl_source_execution._DetachedThreadPoolExecutor):
            return _run_import_configured(context, sources, executor_factory)
    finally:
        common.reset_runtime(token)


def _run_import_configured(context: RunContext, sources: Mapping[str, Callable[[], object]],
                           executor_factory: Callable[..., ThreadPoolExecutor]) -> _impl_import_contracts.ImportResult:
    import_started = time.monotonic()
    settings = context.settings
    previous_snapshot_warnings: list[dict[str, str]] = []
    previous = _impl_retention_policy._previous_snapshot(
        settings.previous_meta_json or settings.meta_json_out, previous_snapshot_warnings,
    )
    batch = collect_sources(context, sources, executor_factory, import_started)
    selected = select_publication(context, batch, previous)
    enriched = enrich_publication(context, batch, selected, previous_snapshot_warnings)
    return finish_import(context, previous, batch, selected, enriched, import_started)


def finish_import(context: RunContext, previous: dict, batch: SourceBatch,
                  selected: PublicationSelection, enriched: PublicationEnrichment,
                  import_started: float) -> _impl_import_contracts.ImportResult:
    settings = context.settings
    deduped, source_results = enriched.events, batch.results
    retention, early_candidates = selected.retention, selected.early_candidates
    publication_boundary_warnings = selected.boundary_warnings
    import_warnings = enriched.warnings
    series_metadata, series_ledger = enriched.series, enriched.series_ledger
    generated_at = selected.generated_at
    actual_by_source = _impl_retention_policy._retained_event_counts_by_source(
        selected.retained_events,
        selected.published_event_ids,
    )
    retained_sources = retention.get("retained_sources")
    if isinstance(retained_sources, list):
        for item in retained_sources:
            if isinstance(item, dict):
                source_id = normalize_source_id(item.get("source_id") or item.get("source"))
                item["retained_event_count"] = actual_by_source.get(source_id, 0)
    retained_count = sum(actual_by_source.values())
    retention["retained_event_count"] = retained_count
    retention["fresh_event_count"] = max(len(deduped) - retained_count, 0)

    # A large individual source may own most of the feed. Its proven day-seven
    # removals are expected, but every unrelated missing row stays guarded.
    expired_ids = retention.pop("_outage_expired_event_ids", [])
    expected_removals = set(expired_ids if isinstance(expired_ids, list) else []) - {
        event_id(event) for event in deduped
    }
    guarded_previous_count = max(int(previous.get("event_count") or 0) - len(expected_removals), 0)
    run_status = _impl_source_execution._run_status(source_results, len(deduped),
        previous_event_count=guarded_previous_count,
        minimum_snapshot_ratio=settings.minimum_snapshot_ratio,
        max_failed_source_ratio=settings.max_failed_source_ratio,
    )
    if import_warnings and run_status != "failed":
        run_status = "degraded"
    boundary_warning_count = len(publication_boundary_warnings)
    with performance.span("dedup.early"):
        performance.count("dedup_early_input", len(early_candidates))
        early_unique = report.deduplicate(early_candidates)
        performance.count("dedup_early_output", len(early_unique))
    early_deduped = _impl_retention_policy._enforce_restricted_publication_boundary(
        early_unique,
        publication_boundary_warnings,
    )
    import_warnings = (
        *import_warnings,
        *(sanitized_warning(warning) for warning in publication_boundary_warnings[boundary_warning_count:]),
    )
    return _impl_import_contracts.ImportResult(
        events=tuple(deduped),
        source_results=source_results,
        pre_dedup_count=selected.pre_dedup_count,
        run_status=run_status,
        retention=retention,
        series=tuple(series_metadata),
        series_ledger=series_ledger,
        warnings=import_warnings,
        timings={
            "source_import_duration_ms": batch.duration_ms,
            "ai_processing_duration_ms": enriched.duration_ms,
            "total_import_duration_ms": round((time.monotonic() - import_started) * 1000),
        },
        early_announcements=tuple(early_deduped),
        generated_at=generated_at,
    )
