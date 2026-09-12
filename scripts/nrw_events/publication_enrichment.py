"""Owning implementation of import orchestration; core is a compatibility facade."""

from __future__ import annotations

import time
from collections import Counter
from collections.abc import Sequence
from dataclasses import replace

from . import (
    ai_enrichment,
    performance,
)
from . import retention_policy as _impl_retention_policy
from . import series as series_entities
from .health import (
    SourceResult,
    SourceStatus,
    diagnostic_warning,
    sanitized_warning,
)
from .identity import content_hash, event_id
from .import_phases import PublicationEnrichment, PublicationSelection, SourceBatch
from .models import CanonicalEvent
from .observability import log
from .runtime import RunContext
from .validation import EventValidationError, validate_event


def _publication_ai_input(
    event: CanonicalEvent, results: dict[str, SourceResult],
) -> dict[str, object]:
    """Reattach one dedup winner's private prose without serializing it."""
    raw: dict[str, object] = event.to_dict()
    raw["description"] = ""
    raw["description_html"] = ""
    pre_ai_id = event_id(replace(event, preserved_event_id=""))
    matches = [
        item
        for result in results.values()
        for item in result._ai_source_material
        if item.get("event_id") == pre_ai_id
        and item.get("source_id") == event.source_id
        and item.get("title") == event.title
        and item.get("start_date") == event.start_date
    ]
    # Same-source duplicates can share a stable occurrence ID. The winner's
    # score survives field-wise metadata enrichment, so use it only to narrow
    # such collisions without depending on mutable time or link fields.
    score_matches = [item for item in matches if item.get("score") == event.score]
    if score_matches:
        matches = score_matches
    materials = {str(item["material"]) for item in matches}
    # Conflicting exact records are ambiguous. Structured master data remains a
    # safe AI input, but no arbitrary duplicate's private prose is selected.
    if len(materials) == 1:
        raw["description"] = materials.pop()
    return raw


def _record_publication_ai_metrics(
    events: Sequence[CanonicalEvent],
    source_results: dict[str, SourceResult],
    stats_by_source: dict[str, dict[str, int]],
    duration_ms: int,
    enriched_events: Sequence[CanonicalEvent] = (),
) -> None:
    candidates = Counter(
        event.source_id for event in events if ai_enrichment.is_target_event(event)
    )
    candidate_events = {
        event.source_id: event
        for event in events
        if ai_enrichment.is_target_event(event)
    }
    enriched = Counter(event.source_id for event in enriched_events)
    total = sum(candidates.values())
    for source_id, count in candidates.items():
        result = _impl_retention_policy._source_result_for_identity(source_id, "", source_results)
        if result is None:
            continue
        result.ai_candidate_event_count += count
        result.ai_enriched_event_count += enriched[source_id]
        result.ai_duration_ms += round(duration_ms * count / total) if total else 0
        source_stats = stats_by_source.get(source_id, {})
        result.ai_skipped_event_count += sum(
            value for key, value in source_stats.items()
            if key.endswith("_skipped_event_count")
        )
        result.ai_skipped_without_summary_event_count += sum(
            value for key, value in source_stats.items()
            if key.endswith("_skipped_without_summary_event_count")
        )
        budget_without_summary = sum(
            value for key, value in source_stats.items()
            if key.startswith(("ai_deadline_", "ai_cap_", "ai_cache_budget_"))
            and key.endswith("_skipped_without_summary_event_count")
        )
        failed = int(source_stats.get("ai_failed_event_count", 0))
        if failed / count > 0.5:
            operational_result = (
                _impl_retention_policy._operational_source_result_for_event(
                    candidate_events[source_id],
                    source_results,
                )
                or result
            )
            operational_result.warning(
                candidate_events[source_id].source,
                "AIEnrichmentFailureWarning",
                f"AI enrichment failed for {failed}/{count} final target events",
                source_id=source_id,
            )
            if operational_result.status in {
                SourceStatus.HEALTHY,
                SourceStatus.HEALTHY_EMPTY,
            }:
                operational_result.status = SourceStatus.DEGRADED
        if budget_without_summary:
            operational_result = _impl_retention_policy._operational_source_result_for_event(
                candidate_events[source_id], source_results,
            ) or result
            operational_result.warning(
                candidate_events[source_id].source,
                "AIEnrichmentBudgetWarning",
                f"AI enrichment skipped {budget_without_summary}/"
                f"{count} final target events without a cached summary; those events "
                "publish with master data only",
                source_id=source_id,
            )
            if operational_result.status in {
                SourceStatus.HEALTHY, SourceStatus.HEALTHY_EMPTY,
            }:
                operational_result.status = SourceStatus.DEGRADED


class EnrichmentAlignmentError(ValueError):
    """The optional stage returned rows for a different input batch."""


def require_aligned_rows(inputs: Sequence[dict], outputs: Sequence[dict]) -> None:
    """Optional enrichment must return one corresponding row per input."""
    if len(inputs) != len(outputs):
        raise EnrichmentAlignmentError("enrichment changed row count")
    for original, output in zip(inputs, outputs, strict=True):
        if not isinstance(output, dict) or event_id(original) != event_id(output):
            raise EnrichmentAlignmentError("enrichment changed row identity or order")
        if any(original.get(field) != output.get(field) for field in ("title", "source_id", "start_date")):
            raise EnrichmentAlignmentError("enrichment changed occurrence ownership")


def enrich_publication(context: RunContext, batch: SourceBatch, selected: PublicationSelection,
                       previous_snapshot_warnings: list[dict[str, str]]) -> PublicationEnrichment:
    settings, logger, run_id = context.settings, context.logger, context.run_id
    deduped = list(selected.events)
    source_results, cache_warnings = batch.results, batch.cache_warnings
    generated_at = selected.generated_at
    reviewed_warnings = selected.reviewed_warnings
    publication_boundary_warnings = selected.boundary_warnings
    ai_started = time.monotonic()
    ai_stats_by_source: dict[str, dict[str, int]] = {}
    ai_validation_warnings: list[dict[str, str]] = []
    ai_enriched_candidates: list[CanonicalEvent] = []
    target_indexes = [
        index for index, event in enumerate(deduped)
        if ai_enrichment.is_target_event(event) and not event.ai_summary.strip()
    ]
    ai_candidates = [deduped[index] for index in target_indexes]
    if target_indexes:
        ai_inputs = [
            _publication_ai_input(deduped[index], source_results)
            for index in target_indexes
        ]
        ai_settings = ai_enrichment.settings_from_env()
        try:
            with performance.span("summaries.ai"):
                ai_outputs = ai_enrichment.enrich_events(
                    ai_inputs,
                    settings=ai_settings,
                    stats_by_source=ai_stats_by_source,
                )
            require_aligned_rows(ai_inputs, ai_outputs)
        except Exception as exc:
            if isinstance(exc, EnrichmentAlignmentError):
                ai_validation_warnings.append(diagnostic_warning(
                    "ai-enrichment", "AIEnrichmentValidationWarning", str(exc),
                ))
            for event in ai_candidates:
                source_stats = ai_stats_by_source.setdefault(event.source_id, {})
                source_stats["ai_batch_skipped_event_count"] = (
                    source_stats.get("ai_batch_skipped_event_count", 0) + 1
                )
                source_stats["ai_batch_skipped_without_summary_event_count"] = (
                    source_stats.get("ai_batch_skipped_without_summary_event_count", 0) + 1
                )
            candidates_by_source = {
                event.source_id: event for event in ai_candidates
            }
            for source_id, event in candidates_by_source.items():
                result = _impl_retention_policy._operational_source_result_for_event(event, source_results)
                if result is not None:
                    result.warning(
                        result.source,
                        "AIEnrichmentBatchWarning",
                        f"publication AI batch failed: {type(exc).__name__}",
                        source_id=source_id,
                    )
                    if result.status in {SourceStatus.HEALTHY, SourceStatus.HEALTHY_EMPTY}:
                        result.status = SourceStatus.DEGRADED
            log(
                logger, 40, f"publication AI batch failed: {type(exc).__name__}",
                run_id=run_id, source="ai-enrichment", error_type=type(exc).__name__,
            )
        else:
            for index, candidate, raw_event in zip(
                target_indexes, ai_candidates, ai_outputs, strict=True,
            ):
                try:
                    validated_output = validate_event(raw_event)
                except EventValidationError as exc:  # noqa: PERF203 - each AI result validates independently
                    event = deduped[index]
                    ai_validation_warnings.append(diagnostic_warning(
                        event.source,
                        "AIEnrichmentValidationWarning",
                        f"AI output was ignored for {event_id(event)}: {exc}",
                        source_id=event.source_id,
                    ))
                else:
                    deduped[index] = validated_output
                    if validated_output.ai_summary.strip():
                        ai_enriched_candidates.append(candidate)
    ai_processing_duration_ms = round((time.monotonic() - ai_started) * 1000)
    _record_publication_ai_metrics(
        ai_candidates, source_results, ai_stats_by_source, ai_processing_duration_ms,
        ai_enriched_candidates,
    )
    for result in source_results.values():
        result._ai_source_material.clear()
    loaded_series_ledger = series_entities.load_ledger(settings.series_ledger_json)
    import_warnings: tuple[dict[str, str], ...] = tuple(
        sanitized_warning(warning)
        for warning in (*previous_snapshot_warnings,
            *cache_warnings, *reviewed_warnings, *ai_validation_warnings,
            *publication_boundary_warnings,
        )
    )
    try:
        series_rows, series_metadata, series_ledger = series_entities.enrich_events(
            (event.to_dict() for event in deduped),
            loaded_series_ledger,
            today=context.window.start.date(),
            generated_at=generated_at,
            announced_events=(
                event
                for result in source_results.values()
                for event in result.announced_events
            ),
        )
        require_aligned_rows([event.to_dict() for event in deduped], series_rows)
    except Exception as exc:
        warning = diagnostic_warning(
            "series",
            type(exc).__name__,
            f"series enrichment failed: {exc}",
        )
        import_warnings = (*import_warnings, warning)
        log(
            logger, 40, warning["error"],
            run_id=run_id, source="series", error_type=type(exc).__name__,
        )
        series_rows = [event.to_dict() for event in deduped]
        series_metadata = []
        series_ledger = loaded_series_ledger
    deduped = [
        replace(
            event,
            series_id=row.get("series_id", ""),
            series_title=row.get("series_title", ""),
            run_id=row.get("run_id", ""),
        )
        for event, row in zip(deduped, series_rows, strict=True)
    ]
    boundary_warning_count = len(publication_boundary_warnings)
    deduped = _impl_retention_policy._enforce_restricted_publication_boundary(deduped,
        publication_boundary_warnings,
    )
    import_warnings = (
        *import_warnings,
        *(sanitized_warning(warning) for warning in publication_boundary_warnings[boundary_warning_count:]),
    )
    deduped = [
        replace(event, content_hash=content_hash(replace(event, content_hash="")))
        for event in deduped
    ]

    return PublicationEnrichment(deduped, series_metadata, series_ledger, import_warnings, ai_processing_duration_ms)
