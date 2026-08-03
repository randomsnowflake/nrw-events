#!/usr/bin/env python3
"""Run a deterministic local AI-enrichment pilot without printing source prose."""

from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from nrw_events import ai_enrichment, config  # noqa: E402
from nrw_events.identity import event_id  # noqa: E402


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


def _usage(path: Path) -> tuple[int, int, int]:
    if not path.exists():
        return 0, 0, 0
    with sqlite3.connect(path) as connection:
        try:
            row = connection.execute(
                """SELECT COALESCE(SUM(input_tokens), 0),
                          COALESCE(SUM(cached_input_tokens), 0),
                          COALESCE(SUM(output_tokens), 0)
                   FROM ai_event_enrichment"""
            ).fetchone()
        except sqlite3.OperationalError:
            return 0, 0, 0
    return int(row[0]), int(row[1]), int(row[2])


def _cache_outcome(path: Path, key: str, model: str) -> str:
    with sqlite3.connect(path) as connection:
        row = connection.execute(
            """SELECT stage1_json, stage2_json FROM ai_event_enrichment
               WHERE event_key = ? AND pipeline_version = ?
               ORDER BY updated_at DESC LIMIT 1""",
            (key, f"{ai_enrichment.PIPELINE_VERSION}:{model}"),
        ).fetchone()
    if not row:
        return "rejected"
    try:
        facts = json.loads(row[0]) if row[0] else {}
        result = json.loads(row[1]) if row[1] else {}
    except json.JSONDecodeError:
        return "rejected"
    if facts.get("is_concrete_event") is False:
        return "non_event"
    return "summarized" if result.get("ai_summary") else "rejected"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path, help="local importer/website event snapshot")
    parser.add_argument("--limit", type=int, default=36)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not 1 <= args.limit <= 200:
        parser.error("--limit must be between 1 and 200")

    config.load_env_file()
    settings = replace(ai_enrichment.settings_from_env(), enabled=True, max_events=0)
    if not settings.api_key:
        parser.error("OPENAI_API_KEY is missing")
    candidates = select_pilot(_events(args.input), args.limit)
    before = _usage(settings.cache_db)
    results = []
    for index, event in enumerate(candidates, start=1):
        result = ai_enrichment.enrich_event(dict(event), settings=settings)
        results.append(result)
        print(
            f"[{index}/{len(candidates)}] {event.get('source_id', '')}: "
            f"{'summary' if result.get('ai_summary') else 'blank'}",
            flush=True,
        )
    after = _usage(settings.cache_db)
    input_tokens, cached_tokens, output_tokens = tuple(end - start for start, end in zip(before, after))
    uncached_tokens = max(input_tokens - cached_tokens, 0)
    estimated_cost_usd = (uncached_tokens + cached_tokens * 0.1 + output_tokens * 6) / 1_000_000

    rows = []
    for original, result in zip(candidates, results):
        summary = str(result.get("ai_summary") or "")
        outcome = _cache_outcome(settings.cache_db, event_id(original), settings.model)
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
        "model": settings.model,
        "pipeline_version": ai_enrichment.PIPELINE_VERSION,
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
            "estimated_cost_usd": round(estimated_cost_usd, 6),
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
