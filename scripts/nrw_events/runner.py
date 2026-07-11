"""Run source imports, publish snapshots, and expose machine-readable health data."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Optional

from . import common, config, report
from .category_taxonomy import CATEGORIES, categorize_event
from .health import SourceFetchResult, SourceResult, SourceStatus
from .models import CanonicalEvent
from .observability import configure_logging, log, redact
from .runtime import EventWindow, RunContext
from .sources import SOURCES
from .validation import EventValidationError, validate_event


EXIT_SUCCESS = 0
# Historical name retained for callers/tests: a degraded import is still a
# usable import, so it must not break unattended wrappers that use `set -e`.
EXIT_DEGRADED = EXIT_SUCCESS
EXIT_FAILED = 2


def _with_canonical_category(event: dict) -> dict:
    if event.get("category_key") and event.get("category_label"):
        return event
    canonical = categorize_event(
        event.get("category", ""), event.get("title", ""),
        f"{event.get('description', '')} {event.get('link', '')}",
    )
    return {
        **event,
        "category_key": canonical["key"],
        "category_label": canonical["label"],
        "category_confidence": canonical.get("confidence", 0),
        "category_reason": canonical.get("reason", ""),
    }


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
            try:
                accepted.append(validate_event(event))
            except EventValidationError as exc:
                result.reject(str(exc))
        result.accepted_event_count = len(accepted)
        if result.rejected_event_count:
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
    if any(result.status in {SourceStatus.FAILED, SourceStatus.DEGRADED, SourceStatus.PARSER_EMPTY}
           for result in results.values()):
        return "degraded"
    return "healthy"


def _exit_code(run_status: str) -> int:
    return {"healthy": EXIT_SUCCESS, "degraded": EXIT_DEGRADED, "failed": EXIT_FAILED}[run_status]


def _endpoint_issues(result: SourceResult) -> list[dict[str, object]]:
    issues: list[dict[str, object]] = []
    for url, details in result.endpoints.items():
        status = details.get("status")
        has_bad_status = isinstance(status, int) and status >= 400
        if not (has_bad_status or details.get("error") or details.get("error_type")):
            continue
        issue = {"url": url, "attempts": details.get("attempts", 0)}
        for key in ("status", "error_type", "error"):
            if key in details:
                issue[key] = details[key]
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


def _previous_source_results(path: str) -> dict:
    try:
        with Path(path).expanduser().open(encoding="utf-8") as handle:
            return json.load(handle).get("source_results", {})
    except (OSError, ValueError, AttributeError):
        return {}


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
    """Publish independently atomic artifacts followed by a run-commit manifest."""
    event_path = Path(settings.json_out).expanduser()
    meta_path = Path(settings.meta_json_out).expanduser()
    manifest_path = meta_path.with_suffix(meta_path.suffix + ".manifest.json")
    _atomic_json(event_path, events)
    _atomic_json(meta_path, metadata)
    _atomic_json(manifest_path, {
        "run_id": run_id,
        "generated_at": metadata["generated_at"],
        "events_path": str(event_path),
        "metadata_path": str(meta_path),
        "event_count": len(events),
        "run_status": metadata["run_status"],
    })
    return {"events": str(event_path), "metadata": str(meta_path), "manifest": str(manifest_path)}


def _parse_days(argv: list[str]) -> Optional[int]:
    if len(argv) <= 1:
        return None
    if len(argv) > 2:
        raise ValueError("usage: nrw-events.py [days_ahead]")
    try:
        return int(argv[1])
    except ValueError as exc:
        raise ValueError("days_ahead must be an integer between 1 and 90") from exc


def main() -> int:
    try:
        config.load_env_file()
        settings = config.runtime_config(_parse_days(sys.argv))
        _validate_output_paths(settings)
    except ValueError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return EXIT_FAILED

    run_id = uuid.uuid4().hex
    logger = configure_logging(run_id, settings.log_level, settings.log_file, settings.json_log_file)
    context = RunContext(settings, EventWindow.from_days(settings.days_ahead), run_id, logger)
    common.configure_context(context)
    previous_results = _previous_source_results(settings.meta_json_out)
    log(logger, 20, f"fetching {len(SOURCES)} sources", run_id=run_id, source="runner")

    all_events: list[CanonicalEvent] = []
    source_results: dict[str, SourceResult] = {}
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {pool.submit(_run_source, name, fetch): name for name, fetch in SOURCES.items()}
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

    # Quality already runs when source records cross the canonical boundary.
    filtered = [event for event in all_events if event["score"] >= settings.score_floor]
    deduped = report.deduplicate(filtered)
    print(report.format_report(deduped))
    events_sorted = sorted((event.to_dict() for event in deduped), key=lambda event: -event["score"])
    import_issues = _import_issues(source_results)
    run_status = _run_status(source_results, len(deduped))
    for issue in import_issues:
        log(logger, 30 if issue["severity"] == "warning" else 40,
            f"import issue: {issue['message']}", run_id=run_id, source=str(issue["source"]))
    start, end = context.window.start, context.window.end
    has_weekend = any((start + timedelta(days=offset)).weekday() >= 5 for offset in range((end - start).days + 1))
    payload = {
        "run_id": run_id,
        "run_status": run_status,
        "generated_at": context.clock().isoformat(timespec="seconds"),
        "window": {"start": start.strftime("%Y-%m-%d"), "end": end.strftime("%Y-%m-%d"),
                   "label": "this weekend" if has_weekend else "short term"},
        "radius_km_from_bonn": common.MAX_RADIUS_KM,
        "score_floor": settings.score_floor,
        "source_counts_raw": {name: result.raw_event_count for name, result in source_results.items()},
        "source_errors": {name: result.error["error"] for name, result in source_results.items() if result.error},
        "source_warnings": [warning for result in source_results.values()
                            for warning in result.warnings],
        "import_issues": import_issues,
        "source_results": {name: result.as_dict() for name, result in source_results.items()},
        "categories": CATEGORIES,
        "pre_dedup_count": len(filtered),
        "event_count": len(deduped),
        "events": events_sorted,
    }
    if run_status == "failed":
        log(logger, 40, "import health gate failed; preserving last-known-good snapshot",
            run_id=run_id, source="runner")
    else:
        try:
            paths = _publish_snapshots(settings, events_sorted, payload, run_id)
            log(logger, 20, f"published snapshot manifest at {paths['manifest']}", run_id=run_id, source="runner")
        except OSError as exc:
            log(logger, 40, f"snapshot publication failed: {exc}", run_id=run_id, source="runner",
                error_type=type(exc).__name__)
            return EXIT_FAILED
    log(logger, 20 if run_status == "healthy" else 30, f"run finished: {run_status}",
        run_id=run_id, source="runner")
    return _exit_code(run_status)


if __name__ == "__main__":
    raise SystemExit(main())
