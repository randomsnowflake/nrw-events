"""Owning implementation of snapshot publication; core is a compatibility facade."""

from __future__ import annotations

import fcntl
import json
import os
import shutil
import tempfile
from collections import Counter
from datetime import timedelta
from pathlib import Path

from . import (
    config,
    performance,
    report,
)
from . import highlights as highlight_selection
from . import import_contracts as _impl_import_contracts
from . import source_execution as _impl_source_execution
from .category_taxonomy import CATEGORIES
from .health import (
    diagnostic_warning,
    sanitized_warning,
)
from .identity import assign_event_ids
from .quality import quality_gate_warnings, summarize_event_quality
from .runtime import RunContext
from .sources import SOURCE_IDS

SNAPSHOT_GENERATIONS_KEPT = 3


def _validate_output_paths(settings: config.RuntimeConfig) -> None:
    for raw_path in (
        settings.json_out, settings.meta_json_out, settings.highlights_json_out,
        settings.series_ledger_json, settings.log_file, settings.json_log_file,
    ):
        if not raw_path:
            continue
        Path(raw_path).expanduser().parent.mkdir(parents=True, exist_ok=True)


@performance.measured("snapshot.serialize_write")
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


@performance.measured("snapshot.construct")
def build_snapshot(import_result: _impl_import_contracts.ImportResult, context: RunContext) -> _impl_import_contracts.SnapshotPayload:
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
    issues = _impl_source_execution._import_issues(source_results)
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
    if quality_warnings and metadata["run_status"] == "healthy":
        metadata["run_status"] = "degraded"
    return _impl_import_contracts.SnapshotPayload(events, metadata, highlights, import_result.series_ledger)


@performance.measured("snapshot.publish")
def publish_snapshot(snapshot: _impl_import_contracts.SnapshotPayload, settings: config.RuntimeConfig) -> dict[str, str]:
    """Durably publish a prepared snapshot and its commit manifest."""
    return _publish_snapshots(
        settings,
        snapshot.events,
        snapshot.metadata,
        snapshot.metadata["run_id"],
        highlights=snapshot.highlights,
        series_ledger=snapshot.series_ledger,
    )
