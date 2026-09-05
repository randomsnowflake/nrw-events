"""Owning implementation of import orchestration; core is a compatibility facade."""

from __future__ import annotations

import os
import threading
import time
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from contextvars import copy_context
from dataclasses import replace
from typing import cast

from . import (
    ai_enrichment,
    common,
    components,
    early_publication,
    performance,
    radio_primary_resolution,
    report,
    reviewed_summaries,
)
from . import identity_reconciliation as _impl_identity_reconciliation
from . import import_contracts as _impl_import_contracts
from . import retention_policy as _impl_retention_policy
from . import series as series_entities
from . import source_execution as _impl_source_execution
from .health import (
    SourceResult,
    SourceStatus,
    diagnostic_warning,
    sanitized_warning,
)
from .identity import content_hash, event_id
from .market_source_fallbacks import partition_directory_fallbacks
from .models import CanonicalEvent, normalize_source_id
from .observability import log
from .runtime import RunContext
from .sources import SOURCE_IDS
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
                           executor_factory: Callable[..., ThreadPoolExecutor] = _impl_source_execution._DetachedThreadPoolExecutor) -> _impl_import_contracts.ImportResult:
    """Execute, validate, filter, and deduplicate sources in memory."""
    # Source adapters still read a compatibility facade; embedders must not
    # need to configure that module-global window separately from RunContext.
    import_started = time.monotonic()
    settings, logger, run_id = context.settings, context.logger, context.run_id
    previous_path = settings.previous_meta_json or settings.meta_json_out
    previous_snapshot_warnings: list[dict[str, str]] = []
    previous = _impl_retention_policy._previous_snapshot(previous_path, previous_snapshot_warnings)
    previous_results = previous.get("source_results") or {}
    log(logger, 20, f"fetching {len(sources)} sources", run_id=run_id, source="runner")
    all_events: list[CanonicalEvent] = []
    events_by_source: dict[str, list[CanonicalEvent]] = {}
    source_results: dict[str, SourceResult] = {}
    result: SourceResult | None
    worker_count = min(settings.source_workers, max(len(sources), 1))
    cache_warnings: list[dict[str, str]] = []
    pool = executor_factory(max_workers=worker_count)
    started: dict[str, tuple[float, threading.Thread, threading.Event, float]] = {}
    started_condition = threading.Condition()
    def run_source(name: str, fetch: Callable[[], object], queued_at: float | None) -> tuple[SourceResult, list[CanonicalEvent]]:
        cancel_event = threading.Event()
        source_timeout = settings.source_timeout_seconds
        # Network work stays capped by source_timeout_seconds.  The worker gets
        # a short grace period to canonicalize large successful payloads and
        # return partial detail enrichment instead of discarding the source.
        source_timeout += settings.source_processing_grace_seconds
        with started_condition:
            started[name] = (
                time.monotonic(), threading.current_thread(), cancel_event, source_timeout,
            )
            started_condition.notify_all()
        with performance.source_scope(name):
            performance.record_queue_wait(queued_at)
            return _impl_source_execution._run_source(name, fetch, settings.source_timeout_seconds, cancel_event)

    def accept_result(name: str, future: Future) -> None:
        result, events = future.result()
        source_results[name] = result
        if result.error:
            log(logger, 40, result.error["error"], run_id=run_id, source=name,
                error_type=result.error["error_type"])
        marker = "✓" if result.status in {
            SourceStatus.HEALTHY, SourceStatus.HEALTHY_EMPTY,
            SourceStatus.SCHEDULED_SKIP, SourceStatus.DISABLED,
        } else "!"
        log(logger, 20 if marker == "✓" else 30,
            f"{marker} {result.status.value}: {result.accepted_event_count}/{result.raw_event_count} events in {result.duration_ms}ms",
            run_id=run_id, source=name)
        events_by_source[name] = events

    try:
        futures = {
            pool.submit(
                copy_context().run,
                run_source,
                name,
                fetch,
                performance.queued_at(),
            ): name
            for name, fetch in sources.items()
        }
        pending = set(futures)
        while pending:
            unstarted_names = {
                futures[future] for future in pending
                if futures[future] not in started
            }
            if unstarted_names:
                def any_started(names: set[str] = unstarted_names) -> bool:
                    return any(name in started for name in names)
                with started_condition:
                    started_condition.wait_for(
                        any_started,
                        timeout=0.05,
                    )
            now = time.monotonic()
            pending_deadlines = [
                started[name][0] + started[name][3] - now
                for future in pending
                if (name := futures[future]) in started
            ]
            # A queued future has no start timestamp yet. Recheck it promptly
            # without returning to the former 10 ms busy-poll cadence.
            next_deadline = (
                0.05
                if len(pending_deadlines) < len(pending)
                else min(pending_deadlines, default=1.0)
            )
            wait_timeout = max(0.05, min(next_deadline, 1.0))
            completed, _ = wait(
                pending, timeout=wait_timeout, return_when=FIRST_COMPLETED
            )
            for future in completed:
                name = futures[future]
                if (
                    name in started
                    and time.monotonic() - started[name][0] >= started[name][3]
                    and future.result()[0].duration_ms > started[name][3] * 1000
                ):
                    continue
                pending.remove(future)
                accept_result(name, future)

            now = time.monotonic()
            timed_out = [
                future for future in pending
                if futures[future] in started
                and now - started[futures[future]][0] >= started[futures[future]][3]
            ]
            for future in timed_out:
                pending.remove(future)
                name = futures[future]
                if (
                    future.done()
                    and future.result()[0].duration_ms <= started[name][3] * 1000
                ):
                    accept_result(name, future)
                    continue
                worker = started[name][1]
                started[name][2].set()
                future.cancel()
                replace_worker = getattr(pool, "replace_stalled_worker", None)
                if replace_worker is not None:
                    replace_worker(worker)
                result = SourceResult(
                    source=name,
                    source_id=SOURCE_IDS.get(name, normalize_source_id(name)),
                )
                result.error = {
                    "error_type": "TimeoutError",
                    "error": f"source exceeded {started[name][3]:g}s wall-clock budget",
                }
                result.duration_ms = round(started[name][3] * 1000)
                result.finish([])
                source_results[name] = result
                log(
                    logger, 40, result.error["error"], run_id=run_id, source=name,
                    error_type=result.error["error_type"],
                )
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
        # Source workers share the detail-cache lock. Persist dirty namespaces
        # once after all workers finish instead of serializing every source at
        # its boundary while other workers still need cache lookups.
        if components.pending():
            log(logger, 30, "cache flush deferred until component workers finish", run_id=run_id, source="runner")
        else:
            cache_warnings.extend(common.flush_detail_page_caches())
    source_results = {
        name: source_results[name]
        for name in sources
        if name in source_results
    }
    all_events = [event for name in sources for event in events_by_source.get(name, [])]
    source_import_duration_ms = round((time.monotonic() - import_started) * 1000)
    radio_result = source_results.get(_impl_retention_policy._RADIO_RUNNER_SOURCE)
    promoted_fallback_event_ids: frozenset[str] = frozenset()
    unpublished_fallback_source_ids: frozenset[str] = frozenset()
    if radio_result is not None:
        matchable_events: list[CanonicalEvent] = []
        filtered_later: list[CanonicalEvent] = []
        for event in all_events:
            if _impl_retention_policy._publication_filter_reason(event, settings):
                filtered_later.append(event)
            else:
                matchable_events.append(event)
        resolution = radio_primary_resolution.resolve_radio_leads(
            radio_result.research_leads,
            matchable_events,
            publication_filter=lambda event: _impl_retention_policy._publication_filter_reason(event, settings),
        )
        promoted_fallback_event_ids = resolution.promoted_fallback_event_ids
        unpublished_fallback_source_ids = resolution.unpublished_fallback_source_ids
        # The audited primary URL is only known now, so the promoted fallbacks
        # get their detail pass here rather than during the source import.
        common.set_source_context(radio_result, settings.source_timeout_seconds)
        try:
            resolved_events = _impl_retention_policy._enrich_promoted_fallbacks(
                resolution.events, promoted_fallback_event_ids,
            )
        finally:
            common.set_source_context(None)
        # This detail pass occurs after the source-worker cache flush above.
        # Persist its successful responses and failure backoff before exit.
        cache_warnings.extend(common.flush_detail_page_caches(
            "radio-primary-fallback-v1"
        ))
        all_events = [*filtered_later, *resolved_events]
        radio_result.research_leads = list(resolution.research_leads)
        radio_result.research_lead_count = len(radio_result.research_leads)
        radio_result.research_lead_reasons = resolution.research_lead_reasons
        radio_result.accepted_event_count = sum(
            radio_primary_resolution.RADIO_SOURCE_ID in event.discovered_via
            and not _impl_retention_policy._publication_filter_reason(event, settings)
            for event in resolved_events
        )
        radio_result.cancelled_events.extend(resolution.cancellations)
    _impl_retention_policy._attach_baselines(source_results, previous_results, settings.source_baseline_min_count)
    filtered: list[CanonicalEvent] = []
    early_candidates: list[CanonicalEvent] = []
    for event in all_events:
        rejection_reason = _impl_retention_policy._publication_filter_reason(event, settings)
        if rejection_reason == "filter:window":
            if early_publication.is_eligible(event):
                early_candidates.append(event)
            continue
        if rejection_reason:
            result = _impl_retention_policy._source_result_for_event(event, source_results)
            if result is not None:
                result.reject(rejection_reason, event, in_window=True)
            continue
        filtered.append(event)
    # Directory fallbacks can be replaced by a better first-party record below.
    # Reconcile their already-published IDs before dropping them so the winner
    # can inherit every historical URL, not only today's freshly computed ID.
    filtered = cast(list[CanonicalEvent], _impl_identity_reconciliation._reconcile_published_ids(filtered, previous))
    filtered, replaced_market_fallbacks = partition_directory_fallbacks(filtered)
    for event in replaced_market_fallbacks:
        result = _impl_retention_policy._source_result_for_event(event, source_results)
        if result is not None:
            result.reject("filter:first_party_replacement", event, in_window=True)
    cancellations = [
        event
        for result in source_results.values()
        for event in result.cancelled_events
    ]
    previous_cancellations: list[CanonicalEvent] = []
    window_start = context.window.start.strftime("%Y-%m-%d")
    window_end = context.window.end.strftime("%Y-%m-%d")
    for raw_event in previous.get("events") or []:
        if (
            not isinstance(raw_event, dict)
            or _impl_retention_policy._is_discovery_only_event(raw_event)
            or raw_event.get("status") not in {"cancelled", "postponed"}
        ):
            continue
        try:
            cancellation = validate_event(raw_event)
        except EventValidationError:
            continue
        if cancellation.end_date >= window_start and cancellation.start_date <= window_end:
            previous_cancellations.append(cancellation)
    all_cancellations = [*cancellations, *(event.to_dict() for event in previous_cancellations)]
    with performance.span("dedup.fresh"):
        performance.count("dedup_fresh_input", len(filtered) + len(previous_cancellations))
        fresh_deduped = report.deduplicate(
            [*filtered, *previous_cancellations], cancellations=all_cancellations,
        )
        performance.count("dedup_fresh_output", len(fresh_deduped))
    with performance.span("retention.merge"):
        retained, retention = _impl_retention_policy._retain_previous_events(
            source_results, previous, context, unpublished_fallback_source_ids,
        )
    with performance.span("dedup.retained"):
        performance.count("dedup_retained_input", len(retained))
        retained_deduped = report.deduplicate(retained, cancellations=all_cancellations)
        performance.count("dedup_retained_output", len(retained_deduped))
    fresh_deduped, retained_deduped = _impl_retention_policy._prefer_retained_primary_over_radio_fallback(
        fresh_deduped, retained_deduped, promoted_fallback_event_ids,
    )
    (
        fresh_deduped,
        retained_deduped,
        promoted_bonn_primaries,
    ) = _impl_retention_policy._prefer_retained_primary_over_bonn_fallback(
        fresh_deduped, retained_deduped,
    )
    retained_only = _impl_retention_policy._retained_events_without_fresh_duplicate(
        fresh_deduped, retained_deduped
    )
    # The fresh canonical record wins wholesale. Retained records are only
    # appended when no fresh record represents that occurrence.
    publication_boundary_warnings: list[dict[str, str]] = []
    deduped = _impl_retention_policy._enforce_restricted_publication_boundary([
        *fresh_deduped, *retained_only,
    ],
        publication_boundary_warnings,
    )
    deduped = report.suppress_redundant_series_umbrellas(deduped)
    published_event_ids = {event_id(event) for event in deduped}
    generated_at = context.clock().isoformat(timespec="seconds")
    deduped = cast(list[CanonicalEvent], _impl_identity_reconciliation._reconcile_published_ids(deduped, previous))
    deduped = _impl_retention_policy._attach_cross_run_fields(deduped, previous, generated_at)
    with performance.span("summaries.reviewed"):
        deduped, reviewed_warnings = reviewed_summaries.apply_reviewed_summaries(deduped)

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
        except Exception as exc:
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
                target_indexes, ai_candidates, ai_outputs, strict=False,
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
        for event, row in zip(deduped, series_rows, strict=False)
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

    actual_by_source = _impl_retention_policy._retained_event_counts_by_source(
        [*retained_only, *promoted_bonn_primaries],
        published_event_ids,
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

    run_status = _impl_source_execution._run_status(source_results, len(deduped),
        previous_event_count=int(previous.get("event_count") or 0),
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
        pre_dedup_count=len(filtered) + len(retained),
        run_status=run_status,
        retention=retention,
        series=tuple(series_metadata),
        series_ledger=series_ledger,
        warnings=import_warnings,
        timings={
            "source_import_duration_ms": source_import_duration_ms,
            "ai_processing_duration_ms": ai_processing_duration_ms,
            "total_import_duration_ms": round((time.monotonic() - import_started) * 1000),
        },
        early_announcements=tuple(early_deduped),
        generated_at=generated_at,
    )
