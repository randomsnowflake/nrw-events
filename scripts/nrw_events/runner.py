"""Run source imports, publish snapshots, and expose machine-readable health data."""

from __future__ import annotations

import fcntl
import json
import os
import shutil
import sys
import tempfile
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Callable, Optional

from . import common, config, report
from .category_taxonomy import CATEGORIES
from .health import SourceFetchResult, SourceResult, SourceStatus
from .models import CanonicalEvent, normalize_source_id
from .observability import configure_logging, log, redact
from .quality import summarize_event_quality
from .runtime import EventWindow, RunContext
from .sources import SOURCES, SOURCE_IDS
from .validation import EventValidationError, validate_event


EXIT_SUCCESS = 0
# Historical name retained for callers/tests: a degraded import is still a
# usable import, so it must not break unattended wrappers that use `set -e`.
EXIT_DEGRADED = EXIT_SUCCESS
EXIT_FAILED = 2
SNAPSHOT_GENERATIONS_KEPT = 3


@dataclass(frozen=True, slots=True)
class ImportResult:
    events: tuple[CanonicalEvent, ...]
    source_results: dict[str, SourceResult]
    pre_dedup_count: int
    run_status: str
    retention: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SnapshotPayload:
    events: list[dict]
    metadata: dict


def _run_source(name: str, fetch: Callable[[], list]) -> tuple[SourceResult, list[CanonicalEvent]]:
    result = SourceResult(source=name)
    started = time.monotonic()
    common.set_source_context(result)
    try:
        fetched = fetch()
        if isinstance(fetched, SourceFetchResult):
            events = list(fetched.events)
            result.status = fetched.status
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
        typed_status = result.status if isinstance(fetched, SourceFetchResult) else None
        result.finish(events)
        if typed_status in {SourceStatus.DISABLED, SourceStatus.PARSER_EMPTY, SourceStatus.DEGRADED}:
            result.status = typed_status
        accepted = []
        for event in events:
            if not isinstance(event, dict):
                try:
                    validate_event(event)
                except EventValidationError as exc:
                    result.reject(str(exc))
                continue
            # Adapter feeds often include archives or future listings. Their
            # structural defects are irrelevant to the published window and
            # must not degrade an otherwise healthy current import.
            if not common.event_in_window(event):
                continue
            try:
                canonical_event = validate_event(event)
                if not common.event_in_window(canonical_event):
                    continue
                accepted.append(canonical_event)
            except EventValidationError as exc:
                result.reject(str(exc))
        result.accepted_event_count = len(accepted)
        result.event_sources = sorted({event["source"] for event in accepted})
        result.event_source_ids = sorted({event.source_id for event in accepted})
        # Editorial quality drops are expected filtering decisions, not source
        # health failures. Keep their counts for diagnostics, but only degrade
        # the source when a record fails structural validation.
        if any(not reason.startswith("quality:") for reason in result.rejection_reasons):
            result.status = SourceStatus.DEGRADED
        return result, accepted
    except Exception as exc:
        result.error = {"error_type": type(exc).__name__, "error": redact(exc)}
        result.finish([])
        return result, []
    finally:
        result.duration_ms = round((time.monotonic() - started) * 1000)
        common.flush_detail_page_caches()
        common.set_source_context(None)


def _run_status(results: dict[str, SourceResult], event_count: int) -> str:
    if event_count <= 0:
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
    return "; ".join(parts) or f"source status is {result.status.value}"


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
        if endpoint_issues:
            issue["endpoint_issues"] = endpoint_issues[:10]
        if result.warnings:
            issue["warnings"] = result.warnings
        if result.anomalies:
            issue["anomalies"] = result.anomalies
        issues.append(issue)
    return issues


def _validate_output_paths(settings: config.RuntimeConfig) -> None:
    for raw_path in (settings.json_out, settings.meta_json_out, settings.log_file, settings.json_log_file):
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
                payload["events"] = events if isinstance(events, list) else []
            except (OSError, ValueError, TypeError):
                payload["events"] = []
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


def _retention_labels(results: dict[str, SourceResult], previous: dict) -> set[str]:
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
    return labels


def _retain_previous_events(
    results: dict[str, SourceResult], previous: dict, context: RunContext,
) -> tuple[list[CanonicalEvent], dict[str, object]]:
    labels = _retention_labels(results, previous)
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
    retained: list[CanonicalEvent] = []
    expired_counts = {label: 0 for label in labels}
    candidate_counts = {label: 0 for label in labels}
    window_start = context.window.start.strftime("%Y-%m-%d")
    window_end = context.window.end.strftime("%Y-%m-%d")
    for raw_event in previous.get("events") or []:
        if not isinstance(raw_event, dict):
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
        retained_sources.append({
            "source": source_names.get(label, label),
            "source_id": label,
            "runner_source": runner_sources.get(label) or prior.get("runner_source") or "",
            "retained_event_count": candidate_counts[label],
            "expired_event_count": expired_counts[label],
            "last_success_at": prior.get("last_success_at") or prior_generated_at,
            "consecutive_failures": int(prior.get("consecutive_failures") or 0) + 1,
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
        prior = previous.get(name, {})
        prior_count = prior.get("raw_event_count")
        if not isinstance(prior_count, int):
            continue
        result.baseline = {"previous_raw_event_count": prior_count}
        if prior_count >= minimum_count and result.raw_event_count == 0:
            result.anomalies.append("zero_after_recent_nonempty")


def _atomic_json(path: Path, payload: object) -> None:
    """Write a complete JSON document before atomically replacing its target."""
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, dir=path.parent,
                                     prefix=f".{path.name}.", suffix=".tmp") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
        temp_name = handle.name
    try:
        os.replace(temp_name, path)
    except Exception:
        Path(temp_name).unlink(missing_ok=True)
        raise


def _publish_snapshots(settings: config.RuntimeConfig, events: list, metadata: dict, run_id: str) -> dict[str, str]:
    """Publish immutable run artifacts and atomically commit their manifest."""
    event_path = Path(settings.json_out).expanduser()
    meta_path = Path(settings.meta_json_out).expanduser()
    manifest_path = meta_path.with_suffix(meta_path.suffix + ".manifest.json")
    generations_dir = meta_path.parent / f".{meta_path.name}.generations"
    lock_path = manifest_path.with_suffix(manifest_path.suffix + ".lock")
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    event_path.parent.mkdir(parents=True, exist_ok=True)

    # The website serializes refreshes, but nrw-events is also a standalone
    # package. Lock its complete publication transaction so overlapping CLI
    # runs cannot prune a generation that another publisher is committing.
    with lock_path.open("a+") as lock_handle:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
        generation_dir = generations_dir / run_id
        generation_dir.mkdir(parents=True, exist_ok=False)
        immutable_events = generation_dir / "events.json"
        immutable_metadata = generation_dir / "metadata.json"

        _atomic_json(immutable_events, events)
        _atomic_json(immutable_metadata, metadata)

        # Preserve the historical fixed outputs for existing callers. The manifest
        # is the commit record and always points at the immutable matching pair.
        _atomic_json(event_path, events)
        _atomic_json(meta_path, metadata)
        _atomic_json(manifest_path, {
            "run_id": run_id,
            "generated_at": metadata["generated_at"],
            "events_path": str(immutable_events),
            "metadata_path": str(immutable_metadata),
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
        }


def _parse_days(argv: list[str]) -> Optional[int]:
    if len(argv) <= 1:
        return None
    if len(argv) > 2:
        raise ValueError("usage: nrw-events.py [days_ahead]")
    try:
        return int(argv[1])
    except ValueError as exc:
        raise ValueError("days_ahead must be an integer between 1 and 90") from exc


def run_import(context: RunContext, sources: dict[str, Callable[[], list]],
               executor_factory=ThreadPoolExecutor) -> ImportResult:
    """Execute, validate, filter, and deduplicate sources in memory."""
    # Source adapters still read a compatibility facade; embedders must not
    # need to configure that module-global window separately from RunContext.
    common.configure_context(context)
    settings, logger, run_id = context.settings, context.logger, context.run_id
    previous_path = settings.previous_meta_json or settings.meta_json_out
    previous = _previous_snapshot(previous_path)
    previous_results = previous.get("source_results") or {}
    log(logger, 20, f"fetching {len(sources)} sources", run_id=run_id, source="runner")
    all_events: list[CanonicalEvent] = []
    source_results: dict[str, SourceResult] = {}
    with executor_factory(max_workers=6) as pool:
        futures = {pool.submit(_run_source, name, fetch): name for name, fetch in sources.items()}
        for future in as_completed(futures):
            name = futures[future]
            result, events = future.result()
            source_results[name] = result
            if result.error:
                log(logger, 40, result.error["error"], run_id=run_id, source=name,
                    error_type=result.error["error_type"])
            marker = "✓" if result.status in {
                SourceStatus.HEALTHY, SourceStatus.HEALTHY_EMPTY, SourceStatus.DISABLED,
            } else "!"
            log(logger, 20 if marker == "✓" else 30,
                f"{marker} {result.status.value}: {result.accepted_event_count}/{result.raw_event_count} events in {result.duration_ms}ms",
                run_id=run_id, source=name)
            all_events.extend(events)
    _attach_baselines(source_results, previous_results, settings.source_baseline_min_count)
    filtered = [event for event in all_events if event["score"] >= settings.score_floor]
    fresh_deduped = report.deduplicate(filtered)
    retained, retention = _retain_previous_events(source_results, previous, context)
    retained_deduped = report.deduplicate(retained)
    retained_only = [
        candidate
        for candidate in retained_deduped
        if not any(
            report.events_are_duplicates(fresh, candidate)
            for fresh in fresh_deduped
        )
    ]
    # The fresh canonical record wins wholesale. Retained records are only
    # appended when no fresh record represents that occurrence.
    deduped = [*fresh_deduped, *retained_only]

    actual_by_source: dict[str, int] = {}
    for event in retained_only:
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

    return ImportResult(
        tuple(deduped), source_results, len(filtered) + len(retained),
        _run_status(source_results, len(deduped)), retention,
    )


def build_snapshot(import_result: ImportResult, context: RunContext) -> SnapshotPayload:
    """Build deterministic publication documents without filesystem access."""
    source_results = import_result.source_results
    events = sorted((event.to_dict() for event in import_result.events),
                    key=lambda event: -event["score"])
    issues = _import_issues(source_results)
    start, end = context.window.start, context.window.end
    has_weekend = any((start + timedelta(days=offset)).weekday() >= 5
                      for offset in range((end - start).days + 1))
    metadata = {
        "snapshot_schema_version": 2,
        "run_id": context.run_id, "run_status": import_result.run_status,
        "generated_at": context.clock().isoformat(timespec="seconds"),
        "window": {"start": start.strftime("%Y-%m-%d"), "end": end.strftime("%Y-%m-%d"),
                   "label": "this weekend" if has_weekend else "short term"},
        "radius_km_from_bonn": common.MAX_RADIUS_KM,
        "score_floor": context.settings.score_floor,
        "source_counts_raw": {name: result.raw_event_count for name, result in source_results.items()},
        "source_ids": SOURCE_IDS,
        "source_errors": {name: result.error["error"] for name, result in source_results.items() if result.error},
        "source_warnings": [warning for result in source_results.values() for warning in result.warnings],
        "import_issues": issues,
        "source_results": {name: result.as_dict() for name, result in source_results.items()},
        "categories": CATEGORIES, "pre_dedup_count": import_result.pre_dedup_count,
        "fresh_event_count": import_result.retention.get("fresh_event_count", len(events)),
        "retained_event_count": import_result.retention.get("retained_event_count", 0),
        "expired_retained_event_count": import_result.retention.get("expired_retained_event_count", 0),
        "retained_sources": import_result.retention.get("retained_sources", []),
        "event_count": len(events), "quality_metrics": summarize_event_quality(events),
        "events_path": context.settings.json_out,
    }
    return SnapshotPayload(events, metadata)


def publish_snapshot(snapshot: SnapshotPayload, settings: config.RuntimeConfig) -> dict[str, str]:
    """Durably publish a prepared snapshot and its commit manifest."""
    return _publish_snapshots(settings, snapshot.events, snapshot.metadata,
                              snapshot.metadata["run_id"])


def cli(argv: list[str]) -> int:
    """Translate argv/environment and service results into CLI effects."""
    if len(argv) == 2 and argv[1] in {"-h", "--help"}:
        print("Usage: nrw-events [days_ahead]")
        print("Import public NRW events for 1-90 days ahead (default: 3).")
        return EXIT_SUCCESS
    try:
        config.load_env_file()
        settings = config.runtime_config(_parse_days(argv))
        _validate_output_paths(settings)
    except ValueError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return EXIT_FAILED

    run_id = uuid.uuid4().hex
    logger = configure_logging(run_id, settings.log_level, settings.log_file, settings.json_log_file)
    context = RunContext(settings, EventWindow.from_days(settings.days_ahead), run_id, logger)
    common.configure_context(context)
    import_result = run_import(context, SOURCES)
    print(report.format_report(list(import_result.events)))
    snapshot = build_snapshot(import_result, context)
    for issue in snapshot.metadata["import_issues"]:
        log(logger, 30 if issue["severity"] == "warning" else 40,
            f"import issue: {issue['message']}", run_id=run_id, source=str(issue["source"]))
    run_status = import_result.run_status
    if run_status == "failed":
        log(logger, 40, "import health gate failed; preserving last-known-good snapshot",
            run_id=run_id, source="runner")
    else:
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
