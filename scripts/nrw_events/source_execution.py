"""Owning implementation of source execution; core is a compatibility facade."""

from __future__ import annotations

import atexit
import threading
import time
import weakref
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import thread as futures_thread
from typing import Any, cast

from . import (
    ai_enrichment,
    common,
    components,
    detail_enrichment,
    early_publication,
    performance,
)
from .health import (
    SourceFetchResult,
    SourceResult,
    SourceStatus,
    bounded_diagnostic_text,
)
from .identity import event_id
from .models import CanonicalEvent, normalize_source_id
from .observability import redact
from .sources import SOURCE_IDS
from .title_normalization import normalize_event_title, title_looks_truncated
from .validation import EventValidationError, validate_event

atexit.register(common.flush_detail_page_caches)


EXIT_SUCCESS = 0


EXIT_DEGRADED = EXIT_SUCCESS


EXIT_FAILED = 2


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


class _DetachedThreadPoolExecutor(ThreadPoolExecutor):
    """Thread pool whose abandoned workers cannot hold CLI shutdown open."""

    def _adjust_thread_count(self) -> None:
        if self._idle_semaphore.acquire(timeout=0):
            return

        def weakref_callback(_reference: object, work_queue: Any = self._work_queue) -> None:
            work_queue.put(None)

        num_threads = len(self._threads)
        if num_threads >= self._max_workers:
            return
        thread_name = f"{self._thread_name_prefix or self}_{num_threads}"
        worker_args: tuple[Any, ...]
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
        cast(set[threading.Thread], self._threads).add(worker)

    def replace_stalled_worker(self, worker: threading.Thread) -> None:
        """Restore queue capacity while an abandoned daemon worker is stuck."""
        cast(set[threading.Thread], self._threads).discard(worker)
        self._adjust_thread_count()


def _sanitize_research_lead(event: Mapping[str, object]) -> dict[str, object]:
    """Keep discovery master data while dropping all publisher and AI copy."""
    lead = {
        field: event[field]
        for field in _RESEARCH_LEAD_MASTER_FIELDS
        if field in event
    }
    lead["reason"] = "needs_primary_source"
    return lead


@performance.measured("source.total")
def _run_source(
    name: str,
    fetch: Callable[[], object],
    timeout_seconds: float | None = None,
    cancel_event: threading.Event | None = None,
) -> tuple[SourceResult, list[CanonicalEvent]]:
    events: list[Any]
    result = SourceResult(
        source=name,
        source_id=SOURCE_IDS.get(name, normalize_source_id(name)),
    )
    started = time.monotonic()
    common.set_source_context(result, timeout_seconds, cancel_event)
    try:
        with common.capture_parser_metrics() as adapter_metrics, performance.span("source.adapter"):
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
            events = cast(list[Any], fetched)
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
            with performance.span("source.universal_details"):
                events = detail_enrichment.enrich_events(
                    events,
                    cache_namespace=f"universal-event-details-{SOURCE_IDS[name]}-v2",
                    **({"parallel_components": True} if name in components.COMPOSITE_SOURCES else {}),
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
        def cancellation_key(item: dict) -> tuple[str, str, str, str]:
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
            except (EventValidationError, TypeError, ValueError) as exc:
                reason = str(exc) if isinstance(exc, EventValidationError) else f"record_invalid:{type(exc).__name__}"
                result.reject(reason, event, in_window=in_window)
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


def _run_status(results: dict[str, SourceResult], event_count: int,
    *,
    previous_event_count: int = 0,
    minimum_snapshot_ratio: float = 0.5,
    max_failed_source_ratio: float = 0.5,
) -> str:
    if event_count <= 0:
        return "failed"
    if results and not any(result.event_source_ids for result in results.values()):
        return "failed"
    if previous_event_count > 0 and event_count < previous_event_count * minimum_snapshot_ratio:
        return "failed"
    attempted = [
        result
        for result in results.values()
        if result.status not in {SourceStatus.SCHEDULED_SKIP, SourceStatus.DISABLED}
    ]
    failed_count = sum(result.status == SourceStatus.FAILED for result in attempted)
    if attempted and failed_count / len(attempted) > max_failed_source_ratio:
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
        issue: dict[str, object] = {"url": url, "attempts": details.get("attempts", 0)}
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
        issue: dict[str, object] = {
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
