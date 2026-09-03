"""Run source imports, publish snapshots, and expose machine-readable health data."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import shutil
import sys
import tempfile
import threading
import time
import uuid
import weakref
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from concurrent.futures import thread as futures_thread
from contextvars import copy_context
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, cast

from . import (
    ai_enrichment,
    common,
    config,
    detail_enrichment,
    early_publication,
    radio_primary_resolution,
    report,
    reviewed_summaries,
)
from . import highlights as highlight_selection
from . import series as series_entities
from .category_taxonomy import CATEGORIES
from .health import (
    SourceFetchResult,
    SourceResult,
    SourceStatus,
    bounded_diagnostic_text,
    diagnostic_warning,
    sanitized_warning,
)
from .identity import assign_event_ids, content_hash, event_id
from .market_source_fallbacks import partition_directory_fallbacks
from .models import MAX_DISCOVERY_PROVENANCE_SOURCES, CanonicalEvent, normalize_source_id
from .normalization import comparison_text
from .observability import configure_logging, log, redact
from .quality import quality_gate_warnings, summarize_event_quality
from .runtime import EventWindow, RunContext
from .sources import SOURCE_FETCHERS, SOURCE_IDS
from .title_normalization import normalize_event_title, title_looks_truncated
from .validation import EventValidationError, validate_event

# Keep the historical module-level name as the injection seam used by callers
# and tests, while making the typed facade the production default.
SOURCES = SOURCE_FETCHERS

EXIT_SUCCESS = 0
# Historical name retained for callers/tests: a degraded import is still a
# usable import, so it must not break unattended wrappers that use `set -e`.
EXIT_DEGRADED = EXIT_SUCCESS
EXIT_FAILED = 2
SNAPSHOT_GENERATIONS_KEPT = 3
_DISCOVERY_ONLY_SOURCE_IDS = frozenset({"radio-bonn-rhein-sieg"})
_BONN_FALLBACK_SOURCE_IDS = frozenset({"bonn-de-events", "bonn-de-sports"})
_RADIO_RUNNER_SOURCE = "Radio Bonn/Rhein-Sieg"
_LEGACY_DATED_RANGE_TITLE = re.compile(
    r"^\s*\d{1,2}\.\d{1,2}\.20\d{2}\s*[-–]\s*"
    r"\d{1,2}\.\d{1,2}\.20\d{2}\s+(.+?)"
    r"(?:\s*[-–]\s*täglich\s+ab\s+Mittagszeit)?\s*$",
    re.IGNORECASE,
)

_RESEARCH_LEAD_MASTER_FIELDS = (
    "title", "source", "source_id", "source_role", "discovered_via",
    "date", "time", "time_note", "start_date", "end_date", "start_at",
    "end_at", "all_day", "ongoing", "timezone", "status", "venue",
    "venue_id", "venue_address", "venue_district", "venue_type",
    "venue_latitude", "venue_longitude", "city", "link", "link_kind",
    "organizer", "price", "admission_basis", "availability", "category",
    "category_key", "category_label", "distance_km", "location_confidence",
    "location_source", "score",
)

VERBS = ("heute", "heute-abend", "wochenende")
_CATEGORY_ALIASES = {
    "aktivitaeten": "activities", "aktivitäten": "activities", "ausstellung": "exhibition",
    "familie": "kids", "festival": "festival", "food": "food", "fuehrung": "outdoor",
    "führung": "outdoor", "kino": "cinema", "konzert": "concert", "kurs": "workshop",
    "markt": "market", "nachtleben": "nightlife", "party": "nightlife", "sonstiges": "other",
    "sport": "sports", "theater": "stage", "treffen": "activities", "vortrag": "talk",
    "workshop": "workshop",
}


class _DetachedThreadPoolExecutor(ThreadPoolExecutor):
    """Thread pool whose abandoned workers cannot hold CLI shutdown open."""

    def _adjust_thread_count(self) -> None:
        if self._idle_semaphore.acquire(timeout=0):
            return

        def weakref_callback(_reference, work_queue=self._work_queue):
            work_queue.put(None)

        num_threads = len(self._threads)
        if num_threads >= self._max_workers:
            return
        thread_name = f"{self._thread_name_prefix or self}_{num_threads}"
        if hasattr(self, "_create_worker_context"):
            worker_args = (
                weakref.ref(self, weakref_callback),
                self._create_worker_context(),
                self._work_queue,
            )
        else:
            worker_args = (
                weakref.ref(self, weakref_callback),
                self._work_queue,
                getattr(self, "_initializer", None),
                getattr(self, "_initargs", ()),
            )
        worker = threading.Thread(
            name=thread_name, target=futures_thread._worker,
            args=worker_args, daemon=True,
        )
        worker.start()
        self._threads.add(worker)

    def replace_stalled_worker(self, worker: threading.Thread) -> None:
        """Restore queue capacity while an abandoned daemon worker is stuck."""
        self._threads.discard(worker)
        self._adjust_thread_count()


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


def _sanitize_research_lead(event: Mapping[str, object]) -> dict[str, object]:
    """Keep discovery master data while dropping all publisher and AI copy."""
    lead = {
        field: event[field]
        for field in _RESEARCH_LEAD_MASTER_FIELDS
        if field in event
    }
    lead["reason"] = "needs_primary_source"
    return lead


def _run_source(
    name: str,
    fetch: Callable[[], list],
    timeout_seconds: float | None = None,
    cancel_event: threading.Event | None = None,
) -> tuple[SourceResult, list[CanonicalEvent]]:
    result = SourceResult(
        source=name,
        source_id=SOURCE_IDS.get(name, normalize_source_id(name)),
    )
    started = time.monotonic()
    common.set_source_context(result, timeout_seconds, cancel_event)
    try:
        with common.capture_parser_metrics() as adapter_metrics:
            fetched = fetch()
        if isinstance(fetched, SourceFetchResult):
            events = list(fetched.events)
            result.status = fetched.status
            result.status_reason = fetched.disabled_reason
            for warning in fetched.warnings:
                result.warning(name, "SourceWarning", warning)
            for endpoint in fetched.endpoints:
                details = {key: value for key, value in {
                    "status": endpoint.status, "error_type": endpoint.error_type,
                    "error": endpoint.error,
                }.items() if value not in (None, "")}
                result.endpoint(redact(endpoint.url), **details)
        else:
            events = fetched
        if not isinstance(events, list):
            raise TypeError(f"source returned {type(events).__name__}, expected list")
        if not any(
            endpoint.get("parser_type")
            for endpoint in result.endpoints.values()
        ):
            result.endpoint(
                f"adapter://{result.source_id}",
                parser_type="adapter",
                candidate_count=max(adapter_metrics["candidate_count"], len(events)),
                out_of_window_count=adapter_metrics["out_of_window_count"],
                parsed_event_count=len(events),
                parser_empty=(
                    not isinstance(fetched, SourceFetchResult)
                    and not events
                    and adapter_metrics["out_of_window_count"] == 0
                    and not result.warnings
                    and not any(
                        "error_type" in endpoint
                        for endpoint in result.endpoints.values()
                    )
                ),
            )
        health_events = events
        discovery_events = [
            event for event in events
            if isinstance(event, dict) and event.get("source_role") == "discovery"
        ]
        if discovery_events:
            result.research_leads = [
                _sanitize_research_lead(event) for event in discovery_events
            ]
            result.research_lead_count = len(result.research_leads)
            result.research_lead_reasons = {
                "needs_primary_source": result.research_lead_count,
            }
            events = cast(list[dict], [
                event for event in events if event not in discovery_events
            ])
        events = cast(list[dict], events)
        # Feed/listing payloads are commonly teasers.  Every registered source
        # gets the same cached detail-page second pass before canonical fields
        # are validated, classified and stored.  Ad-hoc embedded/test sources
        # remain side-effect free unless they opt into the helper directly.
        if events and name in SOURCE_IDS:
            events = detail_enrichment.enrich_events(
                events,
                cache_namespace=f"universal-event-details-{SOURCE_IDS[name]}-v2",
            )
        typed_status = result.status if isinstance(fetched, SourceFetchResult) else None
        # Discovery records prove that the parser is healthy and contribute to
        # raw source counts, even though the publication gate excludes them.
        result.finish(health_events)
        explicit_parser_empty = any(
            endpoint.get("parser_empty") is True
            for endpoint in result.endpoints.values()
        )
        endpoint_errors = any(
            "error_type" in endpoint
            for endpoint in result.endpoints.values()
        )
        if typed_status in {
            SourceStatus.DISABLED, SourceStatus.SCHEDULED_SKIP,
            SourceStatus.PARSER_EMPTY, SourceStatus.DEGRADED,
        } or (
            typed_status == SourceStatus.HEALTHY_EMPTY
            and not explicit_parser_empty
            and not endpoint_errors
        ):
            result.status = typed_status
        # A warning means an authoritative empty result is not trustworthy. Keep
        # the source degraded so cross-run retention protects prior records.
        if result.status == SourceStatus.HEALTHY_EMPTY and result.warnings:
            result.status = SourceStatus.DEGRADED
        accepted = []
        def cancellation_key(item) -> tuple[str, str, str, str]:
            raw_start_date = str(item.get("start_date") or item.get("date") or "")
            raw_end_date = str(item.get("end_date") or raw_start_date)
            start = common.parse_iso_date(raw_start_date) or common.parse_date(raw_start_date)
            end = common.parse_iso_date(raw_end_date) or common.parse_date(raw_end_date)
            start_date = start.strftime("%Y-%m-%d") if start else raw_start_date
            return (
                normalize_source_id(item.get("source_id") or item.get("source")),
                normalize_event_title(
                    str(item.get("title") or ""),
                    start=start,
                    end=end,
                    source=str(item.get("source") or ""),
                ),
                start_date,
                str(item.get("status") or ""),
            )

        known_cancellation_keys = {
            cancellation_key(item) for item in result.cancelled_events
        }
        for event in events:
            if not isinstance(event, dict):
                result.reject("record_not_object", event)
                continue
            # Adapter feeds often include archives or future listings. Their
            # structural defects are irrelevant to the published window and
            # must not degrade an otherwise healthy current import.
            try:
                in_window = common.event_in_window(event)
            except (AttributeError, TypeError):
                # Malformed date types are structural defects, not source-wide
                # failures. Let canonical validation reject just this record.
                in_window = True
            if not in_window and not early_publication.is_eligible(event):
                result.announced_events.append(event)
                continue
            if title_looks_truncated(
                str(event.get("title") or ""),
                source=result.source,
            ):
                result.warning(
                    result.source,
                    "TitleTruncationWarning",
                    f"title may be truncated: {event.get('title', '')}",
                    source_id=result.source_id,
                )
            try:
                private_ai_material = (
                    ai_enrichment._source_material(event)
                    if ai_enrichment.is_target_event(event)
                    else ""
                )
                canonical_event = validate_event(event)
                if not common.event_in_window(canonical_event) and not early_publication.is_eligible(canonical_event):
                    continue
                if canonical_event.status in {"cancelled", "postponed"}:
                    canonical_cancellation_key = cancellation_key(canonical_event)
                    if canonical_cancellation_key not in known_cancellation_keys:
                        result.cancelled_events.append(canonical_event.to_dict())
                        known_cancellation_keys.add(canonical_cancellation_key)
                accepted.append(canonical_event)
                if private_ai_material:
                    result._ai_source_material.append({
                        "event_id": event_id(canonical_event),
                        "source_id": canonical_event.source_id,
                        "title": canonical_event.title,
                        "start_date": canonical_event.start_date,
                        "score": canonical_event.score,
                        "material": private_ai_material,
                    })
            except EventValidationError as exc:
                result.reject(str(exc), event, in_window=in_window)
        result.accepted_event_count = len(accepted)
        result.event_sources = sorted({event["source"] for event in accepted})
        result.event_source_ids = sorted({event.source_id for event in accepted})
        # Editorial quality drops are expected filtering decisions, not source
        # health failures. Keep their counts for diagnostics, but only degrade
        # the source when a record fails structural validation.
        if any(
            not reason.startswith(("quality:", "filter:"))
            for reason in result.rejection_reasons
        ):
            result.status = SourceStatus.DEGRADED
        return result, accepted
    except Exception as exc:
        result.error = {"error_type": type(exc).__name__, "error": redact(exc)}
        result.finish([])
        return result, []
    finally:
        result.duration_ms = round((time.monotonic() - started) * 1000)
        common.set_source_context(None)


def _run_status(results: dict[str, SourceResult], event_count: int) -> str:
    if event_count <= 0:
        return "failed"
    if results and not any(result.event_source_ids for result in results.values()):
        return "failed"
    if any(result.status in {SourceStatus.FAILED, SourceStatus.DEGRADED, SourceStatus.PARSER_EMPTY}
           for result in results.values()):
        return "degraded"
    if any(result.anomalies for result in results.values()):
        return "degraded"
    return "healthy"


def _exit_code(run_status: str) -> int:
    return {"healthy": EXIT_SUCCESS, "degraded": EXIT_DEGRADED, "failed": EXIT_FAILED}[run_status]


def _endpoint_issues(result: SourceResult) -> list[dict[str, object]]:
    issues: list[dict[str, object]] = []
    for url, details in result.endpoints.items():
        status = details.get("status")
        has_bad_status = isinstance(status, int) and status >= 400
        if not (
            has_bad_status
            or details.get("error")
            or details.get("error_type")
            or details.get("parser_empty") is True
        ):
            continue
        issue = {"url": url, "attempts": details.get("attempts", 0)}
        for key in ("status", "error_type", "error"):
            if key in details:
                issue[key] = details[key]
        if details.get("parser_empty") is True:
            issue["error_type"] = issue.get("error_type") or "ParserEmptyError"
            issue["error"] = issue.get("error") or "parser returned no event records"
        issues.append(issue)
    return issues


def _source_issue_message(result: SourceResult, endpoint_issues: list[dict[str, object]]) -> str:
    parts: list[str] = []
    if result.error:
        parts.append(f"source raised {result.error['error_type']}: {result.error['error']}")
    if result.rejection_reasons:
        reasons = ", ".join(
            f"{reason}={count}" for reason, count in sorted(result.rejection_reasons.items())
        )
        parts.append(f"rejected {result.rejected_event_count} event record(s): {reasons}")
        sample_text = "; ".join(
            f"{reason}: title={sample.get('title', '<unavailable>')!r}, "
            f"source={sample.get('source', result.source)!r}, "
            f"in_window={sample.get('in_window', 'unknown')}"
            for reason, sample in sorted(result.rejection_samples.items())[:3]
        )
        if sample_text:
            parts.append(
                "representative samples: "
                + bounded_diagnostic_text(sample_text, 1024)
            )
    if result.warnings:
        warning_text = "; ".join(
            f"{warning.get('source', result.source)}: {warning.get('error', warning)}"
            for warning in result.warnings[:3]
        )
        parts.append(f"warnings: {warning_text}")
    if endpoint_issues:
        endpoint_text = "; ".join(
            f"{issue.get('url')}: {issue.get('error_type') or issue.get('status') or 'endpoint issue'}"
            f" {issue.get('error', '')}".rstrip()
            for issue in endpoint_issues[:3]
        )
        parts.append(f"endpoint issues: {endpoint_text}")
    if result.anomalies:
        parts.append("anomalies: " + ", ".join(result.anomalies))
    message = "; ".join(parts) or f"source status is {result.status.value}"
    return bounded_diagnostic_text(message, 2048)


def _import_issues(results: dict[str, SourceResult]) -> list[dict[str, object]]:
    issues: list[dict[str, object]] = []
    for name, result in sorted(results.items()):
        if (
            result.status not in {SourceStatus.FAILED, SourceStatus.DEGRADED, SourceStatus.PARSER_EMPTY}
            and not result.anomalies
        ):
            continue
        endpoint_issues = _endpoint_issues(result)
        issue = {
            "source": name,
            "status": result.status.value,
            "severity": "error" if result.status == SourceStatus.FAILED else "warning",
            "raw_event_count": result.raw_event_count,
            "accepted_event_count": result.accepted_event_count,
            "rejected_event_count": result.rejected_event_count,
            "message": _source_issue_message(result, endpoint_issues),
        }
        if result.error:
            issue["error"] = result.error
        if result.rejection_reasons:
            issue["rejection_reasons"] = result.rejection_reasons
        if result.rejection_samples:
            issue["rejection_samples"] = result.rejection_samples
        if endpoint_issues:
            issue["endpoint_issues"] = endpoint_issues[:10]
        if result.warnings:
            issue["warnings"] = result.warnings
        if result.anomalies:
            issue["anomalies"] = result.anomalies
        issues.append(issue)
    return issues


def _validate_output_paths(settings: config.RuntimeConfig) -> None:
    for raw_path in (
        settings.json_out, settings.meta_json_out, settings.highlights_json_out,
        settings.series_ledger_json, settings.log_file, settings.json_log_file,
    ):
        if not raw_path:
            continue
        Path(raw_path).expanduser().parent.mkdir(parents=True, exist_ok=True)


def _previous_snapshot(path: str) -> dict:
    metadata_path = Path(path).expanduser()
    try:
        with metadata_path.open(encoding="utf-8") as handle:
            payload = json.load(handle)
        if not isinstance(payload, dict):
            return {}
        if "events" not in payload and payload.get("events_path"):
            events_path = Path(str(payload["events_path"])).expanduser()
            if not events_path.is_absolute():
                events_path = metadata_path.parent / events_path
            try:
                events = json.loads(events_path.read_text(encoding="utf-8"))
                if isinstance(events, list):
                    payload["events"] = events
            except (OSError, ValueError, TypeError):
                pass
        return payload
    except (OSError, ValueError, AttributeError):
        return {}


def _event_source_id(event: dict) -> str:
    explicit = normalize_source_id(event.get("source_id"))
    if explicit:
        return explicit
    source = normalize_source_id(event.get("source"))
    # Migration path for snapshots written before grouped adapters emitted
    # child IDs. These adapters carry the stable municipality in ``city``.
    if source == "ionas4-regional" and event.get("city"):
        return normalize_source_id(f"ionas4-{event['city']}")
    if source == "sitekit-regional" and event.get("city"):
        city = str(event["city"]).casefold()
        city = city.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")
        return normalize_source_id(f"sitekit-{city}")
    return source


def _is_discovery_only_event(event: dict) -> bool:
    """Reject tagged discovery records and legacy rows from discovery-only sources."""
    return (
        event.get("source_role") == "discovery"
        or _event_source_id(event) in _DISCOVERY_ONLY_SOURCE_IDS
    )


def _retention_labels(
    results: dict[str, SourceResult],
    previous: dict,
    unpublished_fallback_source_ids: frozenset[str] = frozenset(),
) -> set[str]:
    """Return stable logical source IDs whose fresh data cannot be trusted."""
    previous_results = previous.get("source_results") or {}
    previous_event_ids = {
        _event_source_id(event)
        for event in previous.get("events") or []
        if isinstance(event, dict) and event.get("source")
    }
    previous_retained = [
        item for item in previous.get("retained_sources") or []
        if isinstance(item, dict)
    ]
    previous_retained_ids = {
        normalize_source_id(item.get("source_id") or item.get("source"))
        for item in previous_retained
        if item.get("source_id") or item.get("source")
    }
    labels: set[str] = set()
    for runner_source, result in results.items():
        prior = previous_results.get(runner_source) or {}
        prior_labels = {
            normalize_source_id(label)
            for label in (
                prior.get("event_source_ids")
                or prior.get("event_sources")
                or []
            )
            if str(label).strip()
        }
        prior_labels.update(
            normalize_source_id(item.get("source_id") or item.get("source"))
            for item in previous_retained
            if item.get("runner_source") == runner_source
            and (item.get("source_id") or item.get("source"))
        )
        runner_source_id = normalize_source_id(runner_source)
        if not prior_labels and runner_source_id in previous_event_ids:
            # Bootstrap snapshots predate per-runner source metadata. Most
            # standalone adapters use the runner name as their event source.
            prior_labels.add(runner_source_id)
        fresh_labels = set(result.event_source_ids)
        unavailable = (
            result.status in {SourceStatus.FAILED, SourceStatus.PARSER_EMPTY}
            or (result.status == SourceStatus.DEGRADED and not fresh_labels)
            or result.status == SourceStatus.SCHEDULED_SKIP
            or "zero_after_recent_nonempty" in result.anomalies
        )
        if unavailable:
            labels.update(prior_labels)

        for warning in result.warnings:
            warning_source = normalize_source_id(
                warning.get("source_id") or warning.get("source")
            )
            # Grouped adapters report the concrete municipality/venue that
            # failed. Retain that logical child only when it produced no fresh
            # records; a zero-event retained child remains tracked across
            # consecutive failures.
            if (
                warning_source
                and warning_source in (previous_event_ids | previous_retained_ids)
            ):
                labels.add(warning_source)
            elif (
                warning_source.startswith("meetup-")
                and "meetup" in previous_event_ids
            ):
                # A pre-source-ID snapshot cannot map Meetup records back to
                # individual groups. Preserve the legacy group conservatively
                # for this one migration run; new snapshots use child IDs.
                labels.add("meetup")

    # An audited Radio fallback is promoted from the manifest and owns no runner
    # source, so nothing above can ever name it. Its whole publication depends on
    # a lead the editors rotate in and out weekly: the run after the paragraph
    # disappears, the event would leave the feed while it is still running.
    # Only a source that actually published last night is worth naming here, and
    # a real adapter that meanwhile owns that ID stays authoritative.
    fresh_source_ids = {
        source_id for result in results.values() for source_id in result.event_source_ids
    }
    labels.update(
        (unpublished_fallback_source_ids & previous_event_ids) - fresh_source_ids
    )
    return labels


def _retain_previous_events(
    results: dict[str, SourceResult], previous: dict, context: RunContext,
    unpublished_fallback_source_ids: frozenset[str] = frozenset(),
) -> tuple[list[CanonicalEvent], dict[str, object]]:
    labels = _retention_labels(results, previous, unpublished_fallback_source_ids)
    empty_summary: dict[str, object] = {
        "fresh_event_count": 0,
        "retained_event_count": 0,
        "expired_retained_event_count": 0,
        "retained_sources": [],
    }
    if not labels:
        return [], empty_summary

    previous_retention = {
        normalize_source_id(item.get("source_id") or item.get("source")): item
        for item in previous.get("retained_sources") or []
        if isinstance(item, dict) and item.get("source")
    }
    source_names: dict[str, str] = {
        label: str(item.get("source") or label)
        for label, item in previous_retention.items()
        if label in labels
    }
    runner_sources: dict[str, str] = {
        label: str(item.get("runner_source"))
        for label, item in previous_retention.items()
        if label in labels and item.get("runner_source")
    }
    previous_results = previous.get("source_results") or {}
    for runner_source, result in results.items():
        prior = previous_results.get(runner_source) or {}
        prior_ids = {
            normalize_source_id(value)
            for value in (prior.get("event_source_ids") or prior.get("event_sources") or [])
            if str(value).strip()
        }
        runner_source_id = normalize_source_id(runner_source)
        if not prior_ids and runner_source_id in labels:
            prior_ids.add(runner_source_id)
        for source_id in prior_ids & labels:
            runner_sources.setdefault(source_id, runner_source)
        for warning in result.warnings:
            source_id = normalize_source_id(warning.get("source_id") or warning.get("source"))
            if source_id in labels and warning.get("source"):
                source_names[source_id] = str(warning["source"])
                runner_sources[source_id] = runner_source
    # A promoted fallback has no runner source, yet the Radio adapter is what
    # feeds it. Booking it there keeps the summary readable and lets the next run
    # recover the label from the snapshot when Radio itself is unavailable.
    for label in unpublished_fallback_source_ids & labels:
        runner_sources.setdefault(label, _RADIO_RUNNER_SOURCE)
    retained: list[CanonicalEvent] = []
    expired_counts = dict.fromkeys(labels, 0)
    candidate_counts = dict.fromkeys(labels, 0)
    window_start = context.window.start.strftime("%Y-%m-%d")
    window_end = context.window.end.strftime("%Y-%m-%d")
    for raw_event in previous.get("events") or []:
        if not isinstance(raw_event, dict):
            continue
        if _is_discovery_only_event(raw_event):
            continue
        label = _event_source_id(raw_event)
        if label not in labels:
            continue
        source_names.setdefault(label, str(raw_event.get("source") or label))
        raw_end = str(raw_event.get("end_date") or raw_event.get("date") or "")
        if "ongoing until " in raw_end:
            raw_end = raw_end.rsplit("ongoing until ", 1)[-1]
        elif "–" in raw_end:
            raw_end = raw_end.rsplit("–", 1)[-1]
        parsed_end = common.parse_date(raw_end)
        if parsed_end and parsed_end.strftime("%Y-%m-%d") < window_start:
            expired_counts[label] += 1
            continue
        try:
            retained_raw = {**raw_event, "source_id": label}
            event = validate_event(retained_raw)
        except EventValidationError:
            continue
        published_event_id = str(raw_event.get("event_id") or "").strip()
        if published_event_id:
            event = replace(event, preserved_event_id=published_event_id)
        if event.end_date < window_start:
            expired_counts[label] += 1
            continue
        if event.start_date > window_end:
            continue
        retained.append(event)
        candidate_counts[label] += 1

    prior_generated_at = str(previous.get("generated_at") or "")
    retained_sources = []
    for label in sorted(labels):
        prior = previous_retention.get(label) or {}
        runner_source = runner_sources.get(label) or prior.get("runner_source") or ""
        scheduled_skip = (
            runner_source in results
            and results[runner_source].status == SourceStatus.SCHEDULED_SKIP
        )
        retained_sources.append({
            "source": source_names.get(label, label),
            "source_id": label,
            "runner_source": runner_source,
            "retained_event_count": candidate_counts[label],
            "expired_event_count": expired_counts[label],
            "last_success_at": prior.get("last_success_at") or prior_generated_at,
            "consecutive_failures": (
                int(prior.get("consecutive_failures") or 0)
                if scheduled_skip
                else int(prior.get("consecutive_failures") or 0) + 1
            ),
        })
    return retained, {
        **empty_summary,
        "retained_event_count": len(retained),
        "expired_retained_event_count": sum(expired_counts.values()),
        "retained_sources": retained_sources,
    }


def _attach_baselines(results: dict[str, SourceResult], previous: dict, minimum_count: int) -> None:
    """Expose count changes without treating seasonal empty calendars as failures."""
    for name, result in results.items():
        if result.status == SourceStatus.SCHEDULED_SKIP:
            continue
        prior = previous.get(name, {})
        prior_count = prior.get("raw_event_count")
        if not isinstance(prior_count, int):
            continue
        result.baseline = {"previous_raw_event_count": prior_count}
        if prior_count >= minimum_count and result.raw_event_count == 0:
            result.anomalies.append("zero_after_recent_nonempty")
        elif prior_count >= minimum_count and result.raw_event_count * 2 < prior_count:
            result.anomalies.append("large_drop_after_recent_nonempty")


def _source_result_for_identity(
    source_id: str, source: str, results: dict[str, SourceResult],
) -> SourceResult | None:
    """Resolve an exact source owner before considering aggregate membership."""
    for result in results.values():
        if source_id == result.source_id or (source and source == result.source):
            return result
    for result in results.values():
        if source_id in result.event_source_ids or source in result.event_sources:
            return result
    return results.get(source)


def _source_result_for_event(
    event: CanonicalEvent, results: dict[str, SourceResult],
) -> SourceResult | None:
    """Resolve a canonical child source back to its runner result."""
    return _source_result_for_identity(event.source_id, event.source, results)


def _operational_source_result_for_event(
    event: CanonicalEvent, results: dict[str, SourceResult],
) -> SourceResult | None:
    """Prefer an active aggregate producer over a scheduled exact owner."""
    result = _source_result_for_event(event, results)
    if result is None or result.status != SourceStatus.SCHEDULED_SKIP:
        return result
    for candidate in results.values():
        if (
            candidate.status != SourceStatus.SCHEDULED_SKIP
            and (
                event.source_id in candidate.event_source_ids
                or event.source in candidate.event_sources
            )
        ):
            return candidate
    return result


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
        result = _source_result_for_identity(source_id, "", source_results)
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
        if budget_without_summary:
            operational_result = _operational_source_result_for_event(
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


def _publication_filter_reason(
    event: CanonicalEvent, settings: config.RuntimeConfig,
) -> str:
    """Return the final publication rejection reason without mutating health data."""
    if not common.event_in_window(event):
        return "filter:window"
    if event.distance_km is not None and event.distance_km > settings.radius_km:
        return "filter:radius"
    if event.score < settings.score_floor and event.status == "scheduled":
        return "filter:score_floor"
    return ""


def _attach_cross_run_fields(
    events: Sequence[CanonicalEvent | dict], previous: dict, generated_at: str,
) -> list[CanonicalEvent]:
    """Carry first-seen timestamps and expose a content-change fingerprint."""
    previous_by_id = {
        str(record.get("event_id") or event_id(record)): record
        for record in previous.get("events") or []
        if isinstance(record, dict)
    }
    enriched: list[CanonicalEvent] = []
    for event in events:
        canonical_event = event
        if isinstance(event, dict):
            canonical_event = CanonicalEvent(**{
                name: event[name]
                for name in CanonicalEvent.__dataclass_fields__
                if name in event
            })
        identifier = event_id(canonical_event)
        prior = previous_by_id.get(identifier, {})
        first_seen = str(
            prior.get("first_seen_at")
            or prior.get("generated_at")
            or previous.get("generated_at")
            or generated_at
        )
        current_event = canonical_event
        cancelled_at = current_event.cancelled_at
        if current_event.status == "cancelled":
            cancelled_at = str(prior.get("cancelled_at") or cancelled_at or generated_at)
        candidate = replace(
            current_event,
            first_seen_at=first_seen,
            cancelled_at=cancelled_at,
            cancellation_source=(
                current_event.cancellation_source
                or (
                    current_event.source
                    if current_event.status in {"cancelled", "postponed"}
                    else ""
                )
            ),
        )
        enriched.append(replace(candidate, content_hash=content_hash(candidate)))
    return enriched


def _cross_run_match_score(current: CanonicalEvent | dict, prior: dict) -> int:
    """Score corroborating fields for a same-title/date cross-run match."""
    def start_time(value: object, start_at: object) -> str:
        if match := re.match(r"\s*(\d{1,2}):(\d{2})", str(value or "")):
            return f"{int(match.group(1)):02d}:{match.group(2)}"
        iso_value = str(start_at or "")
        return iso_value[11:16] if len(iso_value) >= 16 else ""

    score = 0
    def source_links(event: CanonicalEvent | dict) -> set[str]:
        links = {
            report._normalized_link_key(str(value))
            for value in (event.get("source_links") or [])
            if str(value or "").strip()
        }
        if event.get("link_kind") == "detail" and event.get("link"):
            links.add(report._normalized_link_key(str(event.get("link"))))
        return links

    if source_links(current) & source_links(prior):
        score += 8
    if current.get("source_id") and current.get("source_id") == _event_source_id(prior):
        score += 4
    current_time = start_time(current.get("time"), current.get("start_at"))
    prior_time = start_time(prior.get("time"), prior.get("start_at"))
    if current_time and prior_time and current_time != prior_time:
        return -1
    if current_time and prior_time and current_time == prior_time:
        score += 3
    if current.get("venue_id") and current.get("venue_id") == str(prior.get("venue_id") or ""):
        score += 3
    elif (
        current.get("venue")
        and prior.get("venue")
        and comparison_text(str(current.get("venue"))) == comparison_text(str(prior["venue"]))
    ):
        score += 2
    if (
        current.get("city")
        and prior.get("city")
        and comparison_text(str(current.get("city"))) == comparison_text(str(prior["city"]))
    ):
        score += 2
    return score


def _uniquely_disambiguates_occurrence(
    current: CanonicalEvent | dict,
    prior: dict,
    current_group: Sequence[CanonicalEvent | dict],
    prior_group: Sequence[dict],
) -> bool:
    """Require a pair-specific signal when title/date groups are ambiguous."""
    def start_time(event: CanonicalEvent | dict) -> str:
        if match := re.match(r"\s*(\d{1,2}):(\d{2})", str(event.get("time") or "")):
            return f"{int(match.group(1)):02d}:{match.group(2)}"
        start_at = str(event.get("start_at") or "")
        return start_at[11:16] if len(start_at) >= 16 else ""

    def normalized_link(event: CanonicalEvent | dict) -> str:
        return str(event.get("link") or "").rstrip("/")

    def normalized_source_links(event: CanonicalEvent | dict) -> set[str]:
        links = {
            report._normalized_link_key(str(value))
            for value in (event.get("source_links") or [])
            if str(value or "").strip()
        }
        if event.get("link_kind") == "detail" and event.get("link"):
            links.add(report._normalized_link_key(str(event.get("link"))))
        return links

    def normalized_venue(event: CanonicalEvent | dict) -> str:
        return str(event.get("venue_id") or "") or comparison_text(str(event.get("venue") or ""))

    for getter in (start_time, normalized_link, normalized_venue):
        value = getter(current)
        if not value or value != getter(prior):
            continue
        if (
            sum(getter(event) == value for event in current_group) == 1
            and sum(getter(event) == value for event in prior_group) == 1
        ):
            return True
    shared_source_links = normalized_source_links(current) & normalized_source_links(prior)
    return bool(shared_source_links and any(sum(link in normalized_source_links(event) for event in prior_group) == 1 for link in shared_source_links))


def _uniquely_matches_renamed_occurrence(
    current: CanonicalEvent | dict, prior: dict,
) -> bool:
    """Match a conservative upstream title expansion to its published record."""
    current_title = comparison_text(str(current.get("title") or ""))
    prior_title = comparison_text(str(prior.get("title") or ""))
    if (
        min(len(current_title.replace(" ", "")), len(prior_title.replace(" ", ""))) < 12
        or (current_title not in prior_title and prior_title not in current_title)
    ):
        return False
    current_venue = comparison_text(str(current.get("venue") or ""))
    prior_venue = comparison_text(str(prior.get("venue") or ""))
    return bool(
        min(len(current_venue.replace(" ", "")), len(prior_venue.replace(" ", ""))) >= 8
        and (current_venue in prior_venue or prior_venue in current_venue)
        and normalize_source_id(current.get("source_id") or current.get("source"))
        == _event_source_id(prior)
    )


def _reconcile_published_ids(
    events: Sequence[CanonicalEvent | dict], previous: dict,
) -> list[CanonicalEvent | dict]:
    """Carry a published URL across safe metadata and source-winner changes.

    Identity fields can improve between healthy refreshes, so retained-source
    handling alone is insufficient. Candidate pairs must share title and start
    date, then at least one strong or two independent occurrence signals. A
    greedy one-to-one assignment avoids giving one historical URL to multiple
    same-day performances.
    """
    prior_groups: dict[tuple[str, str], list[dict]] = {}
    prior_date_source_groups: dict[tuple[str, str], list[dict]] = {}
    for prior in previous.get("events") or []:
        if not isinstance(prior, dict) or not str(prior.get("event_id") or "").strip():
            continue
        key = (
            comparison_text(str(prior.get("title") or "")),
            str(prior.get("start_date") or prior.get("date") or ""),
        )
        prior_groups.setdefault(key, []).append(prior)
        prior_date_source_groups.setdefault((key[1], _event_source_id(prior)), []).append(prior)

    reconciled = list(events)
    current_groups: dict[tuple[str, str], list[CanonicalEvent | dict]] = {}
    for current in reconciled:
        key = (
            comparison_text(str(current.get("title") or "")),
            str(current.get("start_date") or current.get("date") or ""),
        )
        current_groups.setdefault(key, []).append(current)
    candidate_pairs: list[tuple[int, int, dict]] = []
    fallback_pairs: list[tuple[int, int, dict]] = []
    for index, current in enumerate(reconciled):
        key = (
            comparison_text(str(current.get("title") or "")),
            str(current.get("start_date") or current.get("date") or ""),
        )
        current_group = current_groups[key]
        prior_group = prior_groups.get(key, [])
        for prior in prior_group:
            score = _cross_run_match_score(current, prior)
            unambiguous_group = len(current_group) == len(prior_group) == 1
            if score >= 4 and (
                unambiguous_group
                or _uniquely_disambiguates_occurrence(current, prior, current_group, prior_group)
            ):
                candidate_pairs.append((score, index, prior))
        if prior_group:
            continue
        date_source_key = (
            key[1],
            normalize_source_id(current.get("source_id") or current.get("source")),
        )
        for prior in prior_date_source_groups.get(date_source_key, []):
            score = _cross_run_match_score(current, prior)
            if score >= 4 and _uniquely_matches_renamed_occurrence(current, prior):
                fallback_pairs.append((score, index, prior))

    fallback_current_counts = Counter(index for _score, index, _prior in fallback_pairs)
    fallback_prior_counts = Counter(id(prior) for _score, _index, prior in fallback_pairs)
    candidate_pairs.extend(
        pair for pair in fallback_pairs
        if fallback_current_counts[pair[1]] == 1 and fallback_prior_counts[id(pair[2])] == 1
    )

    used_current: set[int] = set()
    used_prior_ids: set[str] = set()
    candidates_by_current: dict[int, list[tuple[int, dict]]] = {}
    for score, index, prior in candidate_pairs:
        candidates_by_current.setdefault(index, []).append((score, prior))
    for index, candidates in sorted(candidates_by_current.items()):
        current = reconciled[index]
        natural_id = event_id(current)
        candidates.sort(
            key=lambda item: (item[0], str(item[1].get("event_id")) == natural_id),
            reverse=True,
        )
        _score, prior = candidates[0]
        prior_id = str(prior["event_id"])
        if index in used_current or prior_id in used_prior_ids:
            continue
        inherited_ids = list(dict.fromkeys([
            *(current.get("previous_event_ids") or []),
            *(
                str(candidate.get("event_id") or "").strip()
                for _candidate_score, candidate in candidates
                if str(candidate.get("event_id") or "").strip() != prior_id
            ),
            *(prior.get("previous_event_ids") or []),
        ]))[:20]
        updates = {
            "preserved_event_id": prior_id,
            "previous_event_ids": inherited_ids,
        }
        reconciled[index] = (
            {**current, **updates}
            if isinstance(current, dict)
            else replace(current, **updates)
        )
        used_current.add(index)
        used_prior_ids.add(prior_id)
    return reconciled


def _atomic_json(path: Path, payload: object) -> None:
    """Write a complete JSON document before atomically replacing its target."""
    temp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, dir=path.parent,
                                         prefix=f".{path.name}.", suffix=".tmp") as handle:
            temp_name = handle.name
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except Exception:
        if temp_name is not None:
            Path(temp_name).unlink(missing_ok=True)
        raise


def _publish_snapshots(
    settings: config.RuntimeConfig,
    events: list,
    metadata: dict,
    run_id: str,
    *,
    highlights: dict[str, object] | None = None,
    series_ledger: dict[str, object] | None = None,
) -> dict[str, str]:
    """Publish immutable run artifacts and atomically commit their manifest."""
    event_path = Path(settings.json_out).expanduser()
    meta_path = Path(settings.meta_json_out).expanduser()
    highlights_path = Path(settings.highlights_json_out).expanduser()
    series_ledger_path = Path(settings.series_ledger_json).expanduser()
    manifest_path = meta_path.with_suffix(meta_path.suffix + ".manifest.json")
    generations_dir = meta_path.parent / f".{meta_path.name}.generations"
    lock_path = manifest_path.with_suffix(manifest_path.suffix + ".lock")
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    event_path.parent.mkdir(parents=True, exist_ok=True)
    highlights_path.parent.mkdir(parents=True, exist_ok=True)
    series_ledger_path.parent.mkdir(parents=True, exist_ok=True)

    # The website serializes refreshes, but nrw-events is also a standalone
    # package. Lock its complete publication transaction so overlapping CLI
    # runs cannot prune a generation that another publisher is committing.
    with lock_path.open("a+") as lock_handle:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
        generation_dir = generations_dir / run_id
        generation_dir.mkdir(parents=True, exist_ok=False)
        immutable_events = generation_dir / "events.json"
        immutable_metadata = generation_dir / "metadata.json"
        immutable_highlights = generation_dir / "highlights.json"

        metadata["events_path"] = str(immutable_events)
        _atomic_json(immutable_events, events)
        _atomic_json(immutable_metadata, metadata)
        _atomic_json(immutable_highlights, highlights or {})

        # Preserve the historical fixed outputs for existing callers. The manifest
        # is the commit record and always points at the immutable matching pair.
        _atomic_json(event_path, events)
        _atomic_json(meta_path, metadata)
        _atomic_json(highlights_path, highlights or {})
        if series_ledger:
            _atomic_json(series_ledger_path, series_ledger)
        _atomic_json(manifest_path, {
            "run_id": run_id,
            "generated_at": metadata["generated_at"],
            "events_path": str(immutable_events),
            "metadata_path": str(immutable_metadata),
            "highlights_path": str(immutable_highlights),
            "event_count": len(events),
            "run_status": metadata["run_status"],
        })

        generations = sorted(
            (path for path in generations_dir.iterdir() if path.is_dir()),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        for obsolete in generations[SNAPSHOT_GENERATIONS_KEPT:]:
            shutil.rmtree(obsolete)
        return {
            "events": str(event_path),
            "metadata": str(meta_path),
            "manifest": str(manifest_path),
            "immutable_events": str(immutable_events),
            "immutable_metadata": str(immutable_metadata),
            "highlights": str(highlights_path),
            "immutable_highlights": str(immutable_highlights),
            "series_ledger": str(series_ledger_path),
        }


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ValueError(message)


@dataclass(frozen=True, slots=True)
class CliQuery:
    verb: str = ""
    source_ids: tuple[str, ...] = ()


def _parser() -> argparse.ArgumentParser:
    parser = _ArgumentParser(
        prog="nrw-events",
        description="Import and query public NRW events.",
    )
    parser.add_argument("target", nargs="?", help="days_ahead or one of: " + ", ".join(VERBS))
    parser.add_argument("--days", type=int, help="number of days to import (1-90)")
    parser.add_argument("--json", action="store_true", help="write only the filtered event list as JSON to stdout")
    parser.add_argument("--umkreis", metavar="KM", help="maximum distance from Bonn, e.g. 15km")
    parser.add_argument("--kostenlos", action="store_true", help="return only events with explicit free admission")
    parser.add_argument("--kategorie", metavar="KEYS", help="comma-separated category keys or German names")
    parser.add_argument("--max-per-section", type=int, metavar="N", help="maximum events per report section")
    parser.add_argument("--max-chars", type=int, metavar="N", help="maximum Markdown report length")
    parser.add_argument(
        "--source",
        action="append",
        metavar="SOURCE_ID",
        help="refresh only this source id; repeat for multiple sources and retain all others from the previous snapshot",
    )
    return parser


def _parse_radius(value: str) -> float:
    normalized = value.strip().casefold().removesuffix("km").strip()
    try:
        radius = float(normalized.replace(",", "."))
    except ValueError as exc:
        raise ValueError("--umkreis must be a distance such as 15km") from exc
    if not 0.1 <= radius <= 500:
        raise ValueError("--umkreis must be between 0.1km and 500km")
    return radius


def _category_keys(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    known = {category["key"] for category in CATEGORIES}
    keys = []
    for raw in value.split(","):
        normalized = raw.strip().casefold()
        key = _CATEGORY_ALIASES.get(normalized, normalized)
        if key not in known:
            raise ValueError(f"unknown category {raw.strip()!r}; use one of: {', '.join(sorted(known))}")
        if key not in keys:
            keys.append(key)
    return tuple(keys)


def _weekend_bounds(today: datetime) -> tuple[datetime, datetime]:
    weekday = today.weekday()
    days_to_friday = 4 - weekday if weekday <= 4 else 0
    start = today + timedelta(days=days_to_friday)
    end = today + timedelta(days=(6 - weekday) % 7)
    return start, end


def _parse_cli(argv: list[str], now: datetime | None = None) -> tuple[int | None, CliQuery, dict[str, object]]:
    args = _parser().parse_args(argv[1:])
    target = args.target or ""
    verb = target if target in VERBS else ""
    positional_days: int | None = None
    if target and not verb:
        try:
            positional_days = int(target)
        except ValueError as exc:
            raise ValueError(f"unknown verb {target!r}; use one of: {', '.join(VERBS)}") from exc
    if positional_days is not None and args.days is not None:
        raise ValueError("days_ahead may be given either positionally or with --days, not both")
    if verb and args.days is not None:
        # A verb *is* the time window. Silently discarding --days would make an
        # agent believe it queried 14 days when it only ever saw one.
        raise ValueError(f"{verb} already defines its own window; drop --days")
    explicit_days = args.days if args.days is not None else positional_days
    current = (now or datetime.now(common.LOCAL_TIMEZONE)).replace(tzinfo=None)
    if verb in {"heute", "heute-abend"}:
        explicit_days = 1
    elif verb == "wochenende":
        _, weekend_end = _weekend_bounds(current.replace(hour=0, minute=0, second=0, microsecond=0))
        explicit_days = (weekend_end.date() - current.date()).days + 1
    overrides: dict[str, object] = {}
    if args.json:
        overrides["json_stdout"] = True
    if args.umkreis:
        overrides["radius_km"] = _parse_radius(args.umkreis)
    if args.kostenlos:
        overrides["free_only"] = True
    if args.kategorie:
        overrides["categories"] = _category_keys(args.kategorie)
    if args.max_per_section is not None:
        if args.max_per_section < 0:
            raise ValueError("--max-per-section must be zero or greater")
        overrides["max_per_section"] = args.max_per_section
    if args.max_chars is not None:
        if args.max_chars < 200:
            raise ValueError("--max-chars must be at least 200")
        overrides["report_max_chars"] = args.max_chars
    requested_source_ids: list[str] = []
    known_source_ids = set(SOURCE_IDS.values())
    for value in args.source or []:
        for raw_source_id in value.split(","):
            source_id = normalize_source_id(raw_source_id)
            if source_id not in known_source_ids:
                raise ValueError(
                    f"unknown source {raw_source_id.strip()!r}; use one of: "
                    + ", ".join(sorted(known_source_ids))
                )
            if source_id not in requested_source_ids:
                requested_source_ids.append(source_id)
    return explicit_days, CliQuery(verb, tuple(requested_source_ids)), overrides


def _targeted_sources(source_ids: tuple[str, ...]) -> Mapping[str, Callable[[], object]]:
    """Fetch selected sources while retaining every unselected source globally."""
    if not source_ids:
        return SOURCES
    selected = set(source_ids)

    def retained_source(source_id: str) -> Callable[[], object]:
        return lambda: SourceFetchResult.scheduled_skip(
            f"targeted refresh preserved {source_id} from the previous snapshot"
        )

    return {
        name: fetcher if SOURCE_IDS[name] in selected else retained_source(SOURCE_IDS[name])
        for name, fetcher in SOURCES.items()
    }


def _validate_targeted_refresh_snapshot(
    settings: config.RuntimeConfig,
    source_ids: tuple[str, ...],
) -> None:
    """Refuse a partial refresh when there is no snapshot to retain."""
    if not source_ids:
        return
    previous_path = settings.previous_meta_json or settings.meta_json_out
    previous = _previous_snapshot(previous_path)
    if not isinstance(previous.get("events"), list):
        raise ValueError(
            "--source requires a readable previous snapshot with events at "
            f"{Path(previous_path).expanduser()}"
        )


def _event_overlaps(event: CanonicalEvent, start: datetime, end: datetime) -> bool:
    event_start = common.parse_iso_date(event.start_date)
    event_end = common.parse_iso_date(event.end_date) or event_start
    return bool(event_start and event_end and event_start.date() <= end.date() and event_end.date() >= start.date())


def _matches_query(event: CanonicalEvent, settings: config.RuntimeConfig, query: CliQuery, today: datetime) -> bool:
    if settings.categories and event.category_key not in settings.categories:
        return False
    if settings.free_only and (event.admission or {}).get("isFree") is not True:
        return False
    if event.distance_km is not None and event.distance_km > settings.radius_km:
        return False
    day = today.replace(hour=0, minute=0, second=0, microsecond=0)
    if query.verb == "wochenende":
        start, end = _weekend_bounds(day)
        if not _event_overlaps(event, start, end):
            return False
    elif query.verb in {"heute", "heute-abend"} and not _event_overlaps(event, day, day):
        return False
    if query.verb == "heute-abend":
        times = [int(hour) * 60 + int(minute) for hour, minute in re.findall(r"(\d{2}):(\d{2})", event.time)]
        if not times or times[0] < 17 * 60:
            return False
    return True


def filter_import_result(
    result: ImportResult,
    settings: config.RuntimeConfig,
    query: CliQuery,
    today: datetime,
) -> ImportResult:
    events = tuple(event for event in result.events if _matches_query(event, settings, query, today))
    return replace(result, events=events)


def run_import(context: RunContext, sources: dict[str, Callable[[], list]],
               executor_factory=_DetachedThreadPoolExecutor) -> ImportResult:
    """Execute one import with runtime settings isolated to its context."""
    token = common.configure_context(context)
    try:
        return _run_import_configured(context, sources, executor_factory)
    finally:
        common.reset_runtime(token)


def _retained_events_without_fresh_duplicate(
    fresh_events: list[CanonicalEvent],
    retained_events: list[CanonicalEvent],
) -> list[CanonicalEvent]:
    """Keep retained occurrences absent from the indexed fresh event set."""
    fresh_ids = {event_id(event) for event in fresh_events}
    blocking_frequencies = Counter(
        key
        for event in fresh_events
        for key in report._dedup_blocking_keys(event)
    )
    candidate_index: dict[tuple[str, ...], set[int]] = {}
    for index, event in enumerate(fresh_events):
        report._index_blocking_keys(event, index, candidate_index)

    def is_legacy_dated_title_twin(fresh: CanonicalEvent, retained: CanonicalEvent) -> bool:
        match = _LEGACY_DATED_RANGE_TITLE.match(str(retained.get("title") or ""))
        return bool(
            match
            and _event_source_id(fresh) == _event_source_id(retained)
            and str(fresh.get("start_date") or "")
            == str(retained.get("start_date") or "")
            and comparison_text(str(fresh.get("city") or ""))
            == comparison_text(str(retained.get("city") or ""))
            and comparison_text(str(fresh.get("title") or ""))
            == comparison_text(match.group(1))
        )

    def is_same_source_refresh(fresh: CanonicalEvent, retained: CanonicalEvent) -> bool:
        """Match a retained occurrence even when its source corrected the venue."""
        return bool(
            _event_source_id(fresh) == _event_source_id(retained)
            and comparison_text(str(fresh.get("title") or ""))
            == comparison_text(str(retained.get("title") or ""))
            and str(fresh.get("start_date") or "")
            == str(retained.get("start_date") or "")
            and comparison_text(str(fresh.get("city") or ""))
            == comparison_text(str(retained.get("city") or ""))
            and (
                not fresh.get("start_at")
                or not retained.get("start_at")
                or report._same_explicit_start(
                    str(fresh.get("start_at")), str(retained.get("start_at"))
                )
            )
        )

    return [
        candidate
        for candidate in retained_events
        if event_id(candidate) not in fresh_ids
        and not any(
            report.events_are_duplicates(fresh_events[index], candidate)
            or is_legacy_dated_title_twin(fresh_events[index], candidate)
            or is_same_source_refresh(fresh_events[index], candidate)
            for index in report._blocking_candidates(
                candidate, candidate_index, blocking_frequencies
            )
        )
    ]


def _enrich_promoted_fallbacks(
    events: Sequence[CanonicalEvent],
    promoted_fallback_event_ids: frozenset[str],
) -> list[CanonicalEvent]:
    """Read the audited primary page of every promoted Radio fallback.

    A promoted fallback carries master data only: the lead's safe fields plus a
    sentence built from them. Its link is the audited first-party URL, which is
    the one page holding the venue, the hours and the programme.

    The per-source detail pass runs while a source is imported, long before the
    manifest resolves that URL, so these records are the only ones that never
    get a second pass. Without it the site publishes its most visited Kirmes
    pages with no venue and no description at all.

    Identity is pinned before the fetch. Filling a blank venue would otherwise
    move a URL that is already public; this mirrors how AI enrichment preserves
    the pre-enrichment occurrence id.
    """
    resolved: list[CanonicalEvent] = list(events)
    indexes = [
        index for index, event in enumerate(resolved)
        if event_id(event) in promoted_fallback_event_ids
    ]
    if not indexes:
        return resolved

    drafts: list[dict] = []
    for index in indexes:
        draft = resolved[index].to_dict()
        draft["preserved_event_id"] = event_id(resolved[index])
        drafts.append(draft)

    # A manifest entry may expand into several occurrences with one audited
    # primary URL. The generic detail pass deliberately rejects duplicate URLs
    # in one batch as likely overview pages. These URLs are explicitly audited,
    # so pass each occurrence separately; the shared cache fetches the document
    # once while title-aware extraction still runs for every occurrence.
    enriched_drafts = [
        detail_enrichment.enrich_events(
            [draft], cache_namespace="radio-primary-fallback-v1",
        )[0]
        for draft in drafts
    ]
    for index, draft in zip(indexes, enriched_drafts, strict=False):
        try:
            resolved[index] = validate_event(draft)
        except EventValidationError as exc:  # noqa: PERF203 - each optional detail result fails soft
            # Enrichment is optional. A page that pushes the record past a
            # quality rule leaves the audited fallback exactly as it was.
            common.log_source_error(
                f"{draft.get('source') or 'radio fallback'} primary detail", exc,
            )
    return resolved


def _prefer_retained_primary_over_radio_fallback(
    fresh_events: list,
    retained_events: list,
    promoted_fallback_event_ids: frozenset[str],
) -> tuple[list, list]:
    """Keep richer retained first-party data when Radio only supplied a fallback."""
    fresh: list = list(fresh_events)
    remaining_retained: list = list(retained_events)
    for fresh_index, fallback in enumerate(fresh):
        if event_id(fallback) not in promoted_fallback_event_ids:
            continue
        duplicate_indexes = [
            index for index, retained in enumerate(remaining_retained)
            if report.events_are_duplicates(fallback, retained)
        ]
        if len(duplicate_indexes) != 1:
            continue
        retained_index = duplicate_indexes[0]
        retained = remaining_retained[retained_index]
        if retained.get("description_source") != "scraped":
            continue
        remaining_retained.pop(retained_index)
        incoming = list(dict.fromkeys(fallback.get("discovered_via", [])))
        existing = [
            source_id for source_id in retained.get("discovered_via", [])
            if source_id not in incoming
        ]
        existing_limit = max(MAX_DISCOVERY_PROVENANCE_SOURCES - len(incoming), 0)
        provenance = [*existing[:existing_limit], *incoming[:MAX_DISCOVERY_PROVENANCE_SOURCES]]
        fresh[fresh_index] = (
            replace(retained, discovered_via=provenance)
            if isinstance(retained, CanonicalEvent)
            else {**retained, "discovered_via": provenance}
        )
    return fresh, remaining_retained


def _prefer_retained_primary_over_bonn_fallback(
    fresh_events: list[CanonicalEvent],
    retained_events: list[CanonicalEvent],
) -> tuple[
    list[CanonicalEvent],
    list[CanonicalEvent],
    list[CanonicalEvent],
]:
    """Keep an unrefreshed primary record ahead of a fresh Bonn fallback.

    Targeted refreshes deduplicate selected sources separately from retained
    sources. The final cross-snapshot filter used to let every fresh record win
    wholesale, which made a Bonn.de calendar copy replace a richer first-party
    record even though the normal same-run deduplicator ranks Bonn lower.

    Only one-to-one, strongly identified pairs are promoted. Broad venue/date
    duplicate matches remain on the existing path because a museum can publish
    several different events in one category on the same day.
    """
    fresh = list(fresh_events)
    retained = list(retained_events)
    blocking_frequencies = Counter(
        key for event in retained for key in report._dedup_blocking_keys(event)
    )
    candidate_index: dict[tuple[str, ...], set[int]] = {}
    for index, event in enumerate(retained):
        report._index_blocking_keys(event, index, candidate_index)

    matches_by_fresh: dict[int, list[int]] = {}
    fresh_indexes_by_retained: dict[int, list[int]] = {}
    for fresh_index, fallback in enumerate(fresh):
        if _event_source_id(fallback) not in _BONN_FALLBACK_SOURCE_IDS:
            continue
        matches = []
        for retained_index in report._blocking_candidates(
            fallback, candidate_index, blocking_frequencies
        ):
            primary = retained[retained_index]
            same_strong_identity = (
                event_id(fallback) == event_id(primary)
                or report._titles_match(fallback, primary)
                or bool(
                    fallback.get("series_id")
                    and fallback.get("series_id") == primary.get("series_id")
                )
            )
            if (
                report.source_authority(primary.get("source", ""))
                > report.source_authority(fallback.get("source", ""))
                and same_strong_identity
                and report.events_are_duplicates(fallback, primary)
            ):
                matches.append(retained_index)
                fresh_indexes_by_retained.setdefault(retained_index, []).append(
                    fresh_index
                )
        if matches:
            matches_by_fresh[fresh_index] = matches

    consumed_retained: set[int] = set()
    promoted_retained: list[CanonicalEvent] = []
    for fresh_index, retained_indexes in matches_by_fresh.items():
        if len(retained_indexes) != 1:
            continue
        retained_index = retained_indexes[0]
        if len(fresh_indexes_by_retained[retained_index]) != 1:
            continue
        promoted = report._merge_duplicate_metadata(
            retained[retained_index],
            fresh[fresh_index],
        )
        fresh[fresh_index] = promoted
        promoted_retained.append(promoted)
        consumed_retained.add(retained_index)

    return (
        fresh,
        [
            event for index, event in enumerate(retained)
            if index not in consumed_retained
        ],
        promoted_retained,
    )


def _enforce_restricted_publication_boundary(events: list) -> list:
    """Reapply the no-source-copy contract after cross-source deduplication."""
    protected = []
    for event in events:
        if not ai_enrichment.is_target_event(event):
            protected.append(event)
            continue
        raw = event.to_dict() if isinstance(event, CanonicalEvent) else dict(event)
        ai_enrichment.strip_restricted_copy(raw)
        protected.append(validate_event(raw))
    return protected


def _run_import_configured(context: RunContext, sources: dict[str, Callable[[], list]],
                           executor_factory=_DetachedThreadPoolExecutor) -> ImportResult:
    """Execute, validate, filter, and deduplicate sources in memory."""
    # Source adapters still read a compatibility facade; embedders must not
    # need to configure that module-global window separately from RunContext.
    import_started = time.monotonic()
    settings, logger, run_id = context.settings, context.logger, context.run_id
    previous_path = settings.previous_meta_json or settings.meta_json_out
    previous = _previous_snapshot(previous_path)
    previous_results = previous.get("source_results") or {}
    log(logger, 20, f"fetching {len(sources)} sources", run_id=run_id, source="runner")
    all_events: list[CanonicalEvent] = []
    source_results: dict[str, SourceResult] = {}
    worker_count = min(settings.source_workers, max(len(sources), 1))
    cache_warnings: list[dict[str, str]] = []
    pool = executor_factory(max_workers=worker_count)
    started: dict[str, tuple[float, threading.Thread, threading.Event, float]] = {}
    started_condition = threading.Condition()
    def run_source(name: str, fetch: Callable[[], list]):
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
        return _run_source(name, fetch, settings.source_timeout_seconds, cancel_event)

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
        all_events.extend(events)

    try:
        futures = {
            pool.submit(
                copy_context().run,
                run_source,
                name,
                fetch,
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
                with started_condition:
                    started_condition.wait_for(
                        lambda names=unstarted_names: any(name in started for name in names),
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
        cache_warnings.extend(common.flush_detail_page_caches())
    source_results = {
        name: source_results[name]
        for name in sources
        if name in source_results
    }
    source_import_duration_ms = round((time.monotonic() - import_started) * 1000)
    radio_result = source_results.get(_RADIO_RUNNER_SOURCE)
    promoted_fallback_event_ids: frozenset[str] = frozenset()
    unpublished_fallback_source_ids: frozenset[str] = frozenset()
    if radio_result is not None:
        matchable_events: list[CanonicalEvent] = []
        filtered_later: list[CanonicalEvent] = []
        for event in all_events:
            if _publication_filter_reason(event, settings):
                filtered_later.append(event)
            else:
                matchable_events.append(event)
        resolution = radio_primary_resolution.resolve_radio_leads(
            radio_result.research_leads,
            matchable_events,
            publication_filter=lambda event: _publication_filter_reason(event, settings),
        )
        promoted_fallback_event_ids = resolution.promoted_fallback_event_ids
        unpublished_fallback_source_ids = resolution.unpublished_fallback_source_ids
        # The audited primary URL is only known now, so the promoted fallbacks
        # get their detail pass here rather than during the source import.
        common.set_source_context(radio_result, settings.source_timeout_seconds)
        try:
            resolved_events = _enrich_promoted_fallbacks(
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
            and not _publication_filter_reason(event, settings)
            for event in resolved_events
        )
        radio_result.cancelled_events.extend(resolution.cancellations)
    _attach_baselines(source_results, previous_results, settings.source_baseline_min_count)
    filtered: list[CanonicalEvent] = []
    early_candidates: list[CanonicalEvent] = []
    for event in all_events:
        rejection_reason = _publication_filter_reason(event, settings)
        if rejection_reason == "filter:window":
            if early_publication.is_eligible(event):
                early_candidates.append(event)
            continue
        if rejection_reason:
            result = _source_result_for_event(event, source_results)
            if result is not None:
                result.reject(rejection_reason, event, in_window=True)
            continue
        filtered.append(event)
    # Directory fallbacks can be replaced by a better first-party record below.
    # Reconcile their already-published IDs before dropping them so the winner
    # can inherit every historical URL, not only today's freshly computed ID.
    filtered = cast(list[CanonicalEvent], _reconcile_published_ids(filtered, previous))
    filtered, replaced_market_fallbacks = partition_directory_fallbacks(filtered)
    for event in replaced_market_fallbacks:
        result = _source_result_for_event(event, source_results)
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
            or _is_discovery_only_event(raw_event)
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
    fresh_deduped = report.deduplicate(
        [*filtered, *previous_cancellations], cancellations=all_cancellations,
    )
    retained, retention = _retain_previous_events(
        source_results, previous, context, unpublished_fallback_source_ids,
    )
    retained_deduped = report.deduplicate(retained, cancellations=all_cancellations)
    fresh_deduped, retained_deduped = _prefer_retained_primary_over_radio_fallback(
        fresh_deduped, retained_deduped, promoted_fallback_event_ids,
    )
    (
        fresh_deduped,
        retained_deduped,
        promoted_bonn_primaries,
    ) = _prefer_retained_primary_over_bonn_fallback(
        fresh_deduped, retained_deduped,
    )
    retained_only = _retained_events_without_fresh_duplicate(
        fresh_deduped, retained_deduped
    )
    # The fresh canonical record wins wholesale. Retained records are only
    # appended when no fresh record represents that occurrence.
    deduped = _enforce_restricted_publication_boundary([
        *fresh_deduped, *retained_only,
    ])
    deduped = report.suppress_redundant_series_umbrellas(deduped)
    published_event_ids = {event_id(event) for event in deduped}
    generated_at = context.clock().isoformat(timespec="seconds")
    deduped = cast(list[CanonicalEvent], _reconcile_published_ids(deduped, previous))
    deduped = _attach_cross_run_fields(deduped, previous, generated_at)
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
                result = _operational_source_result_for_event(event, source_results)
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
        for warning in (*cache_warnings, *reviewed_warnings, *ai_validation_warnings)
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
    deduped = _enforce_restricted_publication_boundary(deduped)
    deduped = [
        replace(event, content_hash=content_hash(replace(event, content_hash="")))
        for event in deduped
    ]

    actual_by_source: dict[str, int] = {}
    for event in [*retained_only, *promoted_bonn_primaries]:
        if event_id(event) not in published_event_ids:
            continue
        actual_by_source[event.source_id] = actual_by_source.get(event.source_id, 0) + 1
    retained_sources = retention.get("retained_sources")
    if isinstance(retained_sources, list):
        for item in retained_sources:
            if isinstance(item, dict):
                source_id = normalize_source_id(item.get("source_id") or item.get("source"))
                item["retained_event_count"] = actual_by_source.get(source_id, 0)
    retained_count = sum(actual_by_source.values())
    retention["retained_event_count"] = retained_count
    retention["fresh_event_count"] = max(len(deduped) - retained_count, 0)

    run_status = _run_status(source_results, len(deduped))
    if import_warnings and run_status != "failed":
        run_status = "degraded"
    early_deduped = _enforce_restricted_publication_boundary(
        report.deduplicate(early_candidates)
    )
    return ImportResult(
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


def build_snapshot(import_result: ImportResult, context: RunContext) -> SnapshotPayload:
    """Build deterministic publication documents without filesystem access."""
    source_results = import_result.source_results
    # Ids are assigned after deduplication and before sorting: they identify the
    # occurrence, so no consumer may see them move when the ranking moves.
    events = assign_event_ids(event.to_dict() for event in import_result.events)
    for event in events:
        features = report.ranking_features(event)
        event["ranking_features"] = features
        event["priority_bonus"] = round(sum(features.values()), 2)
    events.sort(key=lambda event: -(event["score"] + event["priority_bonus"]))
    early_announcements = assign_event_ids(
        event.to_dict() for event in import_result.early_announcements
    )
    for event in early_announcements:
        features = report.ranking_features(event)
        event["ranking_features"] = features
        event["priority_bonus"] = round(sum(features.values()), 2)
    early_announcements.sort(key=lambda event: (event["start_date"], event["title"]))
    issues = _import_issues(source_results)
    quality_metrics = summarize_event_quality(events)
    source_result_payloads = {
        name: result.as_dict() for name, result in source_results.items()
    }
    research_lead_count = sum(
        result.research_lead_count for result in source_results.values()
    )
    research_lead_reasons: Counter[str] = Counter()
    for result in source_results.values():
        research_lead_reasons.update(result.research_lead_reasons)
    quality_warnings = [
        sanitized_warning(warning)
        for warning in quality_gate_warnings(quality_metrics, source_result_payloads)
    ]
    quality_warnings.extend(
        sanitized_warning({
                "source": event.get("source", ""),
                "source_id": event.get("source_id", ""),
                "event_id": event.get("event_id", ""),
                "error_type": "PublicationInvariantWarning",
                "error": warning.get("message", "publication invariant conflict"),
                "rule_id": warning.get("rule_id", "publication.invariant-conflict"),
                "field": warning.get("field", ""),
                "resolution": warning.get("resolution", "unknown"),
            })
        for event in events
        for warning in event.get("quality_warnings", [])
    )
    source_warnings = [
        sanitized_warning(warning)
        for warning in (
            *[warning for result in source_results.values() for warning in result.warnings],
            *import_result.warnings,
            *quality_warnings,
        )
    ]
    start, end = context.window.start, context.window.end
    has_weekend = any((start + timedelta(days=offset)).weekday() >= 5
                      for offset in range((end - start).days + 1))
    generated_at = import_result.generated_at or context.clock().isoformat(timespec="seconds")
    metadata = {
        "snapshot_schema_version": 7,
        "run_id": context.run_id, "run_status": import_result.run_status,
        "generated_at": generated_at,
        "window": {"start": start.strftime("%Y-%m-%d"), "end": end.strftime("%Y-%m-%d"),
                   "label": "this weekend" if has_weekend else "short term"},
        "radius_km_from_bonn": context.settings.radius_km,
        "score_floor": context.settings.score_floor,
        "source_counts_raw": {name: result.raw_event_count for name, result in source_results.items()},
        "source_ids": SOURCE_IDS,
        "source_errors": {name: result.error["error"] for name, result in source_results.items() if result.error},
        "source_warnings": source_warnings,
        "quality_warnings": quality_warnings,
        "import_issues": issues,
        "source_results": source_result_payloads,
        # Discovery records remain available to the in-process resolver, but
        # their titles and links are deliberately absent from public metadata.
        "research_lead_count": research_lead_count,
        "research_lead_reasons": dict(research_lead_reasons),
        "timings": import_result.timings,
        "categories": CATEGORIES, "pre_dedup_count": import_result.pre_dedup_count,
        "fresh_event_count": import_result.retention.get("fresh_event_count", len(events)),
        "retained_event_count": import_result.retention.get("retained_event_count", 0),
        "expired_retained_event_count": import_result.retention.get("expired_retained_event_count", 0),
        "retained_sources": import_result.retention.get("retained_sources", []),
        "event_count": len(events), "quality_metrics": quality_metrics,
        "early_announcement_count": len(early_announcements),
        "early_announcements": early_announcements,
        "series": list(import_result.series),
        "events_path": context.settings.json_out,
    }
    highlights = highlight_selection.build_highlights(
        events, run_id=context.run_id, generated_at=generated_at,
    )
    if not highlight_selection.is_consistent(highlights, context.run_id):
        metadata["run_status"] = "degraded"
        metadata["source_warnings"].append(diagnostic_warning(
            "highlights",
            "HighlightArtifactError",
            "highlight artifact is missing or does not match the snapshot run_id",
        ))
    return SnapshotPayload(events, metadata, highlights, import_result.series_ledger)


def publish_snapshot(snapshot: SnapshotPayload, settings: config.RuntimeConfig) -> dict[str, str]:
    """Durably publish a prepared snapshot and its commit manifest."""
    return _publish_snapshots(
        settings,
        snapshot.events,
        snapshot.metadata,
        snapshot.metadata["run_id"],
        highlights=snapshot.highlights,
        series_ledger=snapshot.series_ledger,
    )


def cli(argv: list[str]) -> int:
    """Translate argv/environment and service results into CLI effects."""
    try:
        config.load_env_file()
        days_ahead, query, overrides = _parse_cli(argv)
        import_settings = config.runtime_config(days_ahead)
        settings = replace(import_settings, **overrides)
        settings = replace(settings, categories=_category_keys(",".join(settings.categories)))
        if not settings.json_stdout:
            _validate_output_paths(settings)
        _validate_targeted_refresh_snapshot(import_settings, query.source_ids)
    except ValueError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return EXIT_FAILED

    run_id = uuid.uuid4().hex
    logger = configure_logging(run_id, settings.log_level, settings.log_file, settings.json_log_file)
    context = RunContext(import_settings, EventWindow.from_days(import_settings.days_ahead), run_id, logger)
    try:
        import_result = run_import(context, _targeted_sources(query.source_ids))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return EXIT_FAILED
    snapshot = build_snapshot(import_result, context)
    presentation_result = filter_import_result(import_result, settings, query, context.window.start)
    if settings.json_stdout:
        presentation_snapshot = build_snapshot(presentation_result, context)
        print(json.dumps(presentation_snapshot.events, ensure_ascii=False, indent=2))
    else:
        report_options: dict[str, Any] = {"radius_km": settings.radius_km}
        if settings.max_per_section:
            report_options["max_per_section"] = settings.max_per_section
        if settings.report_max_chars:
            report_options["max_chars"] = settings.report_max_chars
        print(report.format_report(list(presentation_result.events), **report_options))
    for issue in snapshot.metadata["import_issues"]:
        log(logger, 30 if issue["severity"] == "warning" else 40,
            f"import issue: {issue['message']}", run_id=run_id, source=str(issue["source"]))
    run_status = str(snapshot.metadata["run_status"])
    if run_status == "failed":
        log(logger, 40, "import health gate failed; preserving last-known-good snapshot",
            run_id=run_id, source="runner")
    elif not settings.json_stdout:
        try:
            paths = publish_snapshot(snapshot, settings)
            log(logger, 20, f"published snapshot manifest at {paths['manifest']}", run_id=run_id, source="runner")
        except OSError as exc:
            log(logger, 40, f"snapshot publication failed: {exc}", run_id=run_id, source="runner",
                error_type=type(exc).__name__)
            return EXIT_FAILED
    log(logger, 20 if run_status == "healthy" else 30, f"run finished: {run_status}",
        run_id=run_id, source="runner")
    return _exit_code(run_status)


def main() -> int:
    """Compatibility entry point for existing wrappers."""
    return cli(sys.argv)


if __name__ == "__main__":
    raise SystemExit(main())
