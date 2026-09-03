#!/usr/bin/env python3
"""Run a deterministic local AI-enrichment pilot without printing source prose."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import defaultdict
from contextlib import closing
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from nrw_events import ai_enrichment, config
from nrw_events.identity import event_id

DEFAULT_QUOTAS = {
    "bonn-de-events": 16,
    "bonn-de-sports": 4,
    "marktcom": 8,
    "radio-bonn-rhein-sieg": 8,
}


def _events(path: Path) -> list[dict[str, Any]]:
    document = json.loads(path.read_text(encoding="utf-8"))
    values = document.get("events") if isinstance(document, dict) else document
    if not isinstance(values, list):
        raise ValueError("input must be an event list or an object containing events")
    return [value for value in values if isinstance(value, dict) and ai_enrichment.is_target_event(value)]


def _word_count(event: dict[str, Any]) -> int:
    return len(str(event.get("description") or "").split())


def _sample_source(events: list[dict[str, Any]], count: int) -> list[dict[str, Any]]:
    if len(events) <= count:
        return sorted(events, key=lambda event: event_id(event))
    ordered = sorted(
        events,
        key=lambda event: (
            _word_count(event),
            sha256(event_id(event).encode("utf-8")).hexdigest(),
        ),
    )
    indexes = {
        round(position * (len(ordered) - 1) / max(count - 1, 1))
        for position in range(count)
    }
    return [ordered[index] for index in sorted(indexes)]


def select_pilot(events: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        by_source[str(event.get("source_id") or "")].append(event)
    quotas = dict(DEFAULT_QUOTAS)
    default_total = sum(quotas.values())
    if limit != default_total:
        quotas = {
            source_id: max(1, round(limit * quota / default_total))
            for source_id, quota in quotas.items()
        }
    selected = [
        event
        for source_id, quota in quotas.items()
        for event in _sample_source(by_source[source_id], quota)
    ]
    return selected[:limit]


def select_all(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        events,
        key=lambda event: (str(event.get("source_id") or ""), event_id(event)),
    )


def select_reference_pilot(
    events: list[dict[str, Any]],
    cache_db: Path,
    pipeline_version: str,
) -> list[dict[str, Any]]:
    with closing(sqlite3.connect(cache_db)) as connection:
        keys = {
            str(row[0])
            for row in connection.execute(
                "SELECT event_key FROM ai_event_enrichment WHERE pipeline_version = ?",
                (pipeline_version,),
            )
        }
    by_key = {event_id(event): event for event in events if event_id(event) in keys}
    missing = keys - by_key.keys()
    if missing:
        raise ValueError(f"reference pilot has {len(missing)} events missing from the input")
    return sorted(
        by_key.values(),
        key=lambda event: (str(event.get("source_id") or ""), event_id(event)),
    )


def _usage(path: Path, keys: list[str], pipeline_version: str) -> tuple[int, int, int, float]:
    if not path.exists():
        return 0, 0, 0, 0.0
    with closing(sqlite3.connect(path)) as connection:
        try:
            columns = {
                str(row[1]) for row in connection.execute("PRAGMA table_info(ai_event_enrichment)")
            }
            cost_column = "COALESCE(SUM(cost_usd), 0)" if "cost_usd" in columns else "0"
            if not keys:
                return 0, 0, 0, 0.0
            placeholders = ",".join("?" for _key in keys)
            row = connection.execute(
                f"""SELECT COALESCE(SUM(input_tokens), 0),
                          COALESCE(SUM(cached_input_tokens), 0),
                          COALESCE(SUM(output_tokens), 0),
                          {cost_column}
                   FROM ai_event_enrichment
                  WHERE pipeline_version = ? AND event_key IN ({placeholders})""",
                (pipeline_version, *keys),
            ).fetchone()
        except sqlite3.OperationalError:
            return 0, 0, 0, 0.0
    return int(row[0]), int(row[1]), int(row[2]), float(row[3])


def _cache_outcome(path: Path, key: str, settings: ai_enrichment.AISettings) -> str:
    with closing(sqlite3.connect(path)) as connection:
        row = connection.execute(
            """SELECT source_id, stage1_json, stage2_json FROM ai_event_enrichment
               WHERE event_key = ? AND pipeline_version = ?
               ORDER BY updated_at DESC LIMIT 1""",
            (key, ai_enrichment.cache_pipeline_version(settings)),
        ).fetchone()
    if not row:
        return "rejected"
    try:
        facts = json.loads(row[1]) if row[1] else {}
        result = json.loads(row[2]) if row[2] else {}
    except json.JSONDecodeError:
        return "rejected"
    if result.get("ai_summary"):
        return "summarized"
    if facts.get("is_concrete_event") is False and row[0] == "marktcom":
        return "non_event"
    return "rejected"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path, help="local importer/website event snapshot")
    parser.add_argument("--limit", type=int, default=36)
    parser.add_argument(
        "--all",
        dest="all_events",
        action="store_true",
        help="process every target-source event in the input instead of a pilot sample",
    )
    parser.add_argument(
        "--reference-pipeline-version",
        help="reuse the exact event IDs from a prior cached pilot",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.all_events and args.reference_pipeline_version:
        parser.error("--all and --reference-pipeline-version cannot be combined")
    if not 1 <= args.limit <= 200:
        parser.error("--limit must be between 1 and 200")

    config.load_env_file()
    settings = replace(
        ai_enrichment.settings_from_env(),
        enabled=True,
        max_events=0,
        max_new_cache_rows_per_day=0,
    )
    if not settings.api_key:
        key_name = "OPENROUTER_API_KEY" if settings.provider == "openrouter" else "OPENAI_API_KEY"
        parser.error(f"{key_name} is missing")
    available_events = _events(args.input)
    if args.all_events:
        candidates = select_all(available_events)
    elif args.reference_pipeline_version:
        candidates = select_reference_pilot(
            available_events,
            settings.cache_db,
            args.reference_pipeline_version,
        )
    else:
        candidates = select_pilot(available_events, args.limit)
    if args.reference_pipeline_version and len(candidates) != args.limit:
        parser.error(
            f"reference pilot contains {len(candidates)} events, expected --limit {args.limit}"
        )
    pilot_keys = [event_id(event) for event in candidates]
    pipeline_version = ai_enrichment.cache_pipeline_version(settings)
    before = _usage(settings.cache_db, pilot_keys, pipeline_version)
    results = []
    for index, event in enumerate(candidates, start=1):
        try:
            result = ai_enrichment.enrich_event(dict(event), settings=settings)
        except ai_enrichment.AICacheMissBudgetExceeded:
            result = ai_enrichment.strip_restricted_copy(dict(event))
        results.append(result)
        print(
            f"[{index}/{len(candidates)}] {event.get('source_id', '')}: "
            f"{'summary' if result.get('ai_summary') else 'blank'}",
            flush=True,
        )
    after = _usage(settings.cache_db, pilot_keys, pipeline_version)
    input_tokens = after[0] - before[0]
    cached_tokens = after[1] - before[1]
    output_tokens = after[2] - before[2]
    recorded_cost_usd = after[3] - before[3]
    uncached_tokens = max(input_tokens - cached_tokens, 0)
    estimated_cost_usd = (
        recorded_cost_usd
        if recorded_cost_usd > 0
        else (uncached_tokens + cached_tokens * 0.1 + output_tokens * 6) / 1_000_000
    )

    rows = []
    for original, result in zip(candidates, results, strict=False):
        summary = str(result.get("ai_summary") or "")
        outcome = _cache_outcome(settings.cache_db, event_id(original), settings)
        rows.append({
            "event_id": event_id(original),
            "source_id": original.get("source_id", ""),
            "title": original.get("title", ""),
            "success": outcome == "summarized",
            "outcome": outcome,
            "summary_word_count": len(summary.split()),
            "ai_summary": summary,
            "time": result.get("time", ""),
            "time_note": result.get("time_note", ""),
            "venue": result.get("venue", ""),
            "venue_address": result.get("venue_address", ""),
            "city": result.get("city", ""),
            "organizer": result.get("organizer", ""),
            "price": result.get("price", ""),
            "availability": result.get("availability", ""),
            "category_key": result.get("category_key", ""),
            "series_title": result.get("series_title", ""),
        })
    report = {
        "provider": settings.provider,
        "model": settings.model,
        "facts_reasoning_effort": settings.facts_reasoning_effort,
        "summary_reasoning_effort": settings.summary_reasoning_effort,
        "pipeline_version": ai_enrichment.cache_pipeline_version(settings),
        "reference_pipeline_version": args.reference_pipeline_version,
        "selected": len(rows),
        "successful": sum(row["success"] for row in rows),
        "non_events": sum(row["outcome"] == "non_event" for row in rows),
        "failed_or_blank": sum(row["outcome"] == "rejected" for row in rows),
        "source_counts": {
            source_id: sum(row["source_id"] == source_id for row in rows)
            for source_id in DEFAULT_QUOTAS
        },
        "new_usage": {
            "input_tokens": input_tokens,
            "cached_input_tokens": cached_tokens,
            "output_tokens": output_tokens,
            "recorded_cost_usd": round(recorded_cost_usd, 6),
            "estimated_cost_usd": round(estimated_cost_usd, 6),
            "cost_basis": "provider-reported" if recorded_cost_usd > 0 else "token-price-estimate",
        },
        "events": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"Pilot: {report['successful']}/{report['selected']} summaries, "
        f"estimated new API cost ${estimated_cost_usd:.4f}; report: {args.output}"
    )
    return 0 if report["failed_or_blank"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
