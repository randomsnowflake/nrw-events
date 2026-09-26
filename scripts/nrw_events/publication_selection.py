"""Owning implementation of import orchestration; core is a compatibility facade."""

from __future__ import annotations

from typing import cast

from . import (
    common,
    early_publication,
    exhibition_runs,
    performance,
    radio_primary_resolution,
    report,
    reviewed_summaries,
)
from . import identity_reconciliation as _impl_identity_reconciliation
from . import retention_policy as _impl_retention_policy
from .identity import event_id
from .import_phases import PublicationSelection, SourceBatch
from .market_source_fallbacks import partition_directory_fallbacks
from .models import CanonicalEvent
from .runtime import RunContext
from .validation import EventValidationError, validate_event


def select_publication(context: RunContext, batch: SourceBatch, previous: dict) -> PublicationSelection:
    settings = context.settings
    all_events, source_results, cache_warnings = batch.events, batch.results, batch.cache_warnings
    previous_results = previous.get("source_results") or {}
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
    deduped = exhibition_runs.merge_exhibition_opening_days(deduped, previous)
    published_event_ids = {event_id(event) for event in deduped}
    generated_at = context.clock().isoformat(timespec="seconds")
    deduped = cast(list[CanonicalEvent], _impl_identity_reconciliation._reconcile_published_ids(deduped, previous))
    deduped = _impl_retention_policy._attach_cross_run_fields(deduped, previous, generated_at)
    with performance.span("summaries.reviewed"):
        deduped, reviewed_warnings = reviewed_summaries.apply_reviewed_summaries(deduped)

    return PublicationSelection(deduped, early_candidates, [*retained_only, *promoted_bonn_primaries],
                                published_event_ids, retention, len(filtered) + len(retained),
                                generated_at, reviewed_warnings, publication_boundary_warnings)
