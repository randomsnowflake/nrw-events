"""Deterministic recurring-event entities and their durable occurrence ledger."""

from __future__ import annotations

from collections import Counter
from datetime import date, timedelta
from hashlib import sha256
import json
from pathlib import Path
import re
from statistics import median
from typing import Any, Iterable, Mapping

from .identity import event_id
from .normalization import comparison_text


LEDGER_SCHEMA_VERSION = 1


def _stem(title: str) -> str:
    text = comparison_text(title)
    text = re.sub(r"\b(?:19|20)\d{2}\b", "", text)
    text = re.sub(r"\b(?:teil|folge|part|episode)\s+(?:\d+|[ivxlcdm]+)\b", "", text)
    text = re.sub(r"\b\d{1,2}[./-]\d{1,2}(?:[./-]\d{2,4})?\b", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _series_key(event: Mapping[str, Any]) -> tuple[str, str, str] | None:
    venue = str(event.get("venue_id") or comparison_text(str(event.get("venue") or ""))).strip()
    title = _stem(str(event.get("series_title") or event.get("title") or ""))
    category = str(event.get("category_key") or "other")
    if not venue or len(title) < 4:
        return None
    return venue, title, category


def _identifier(key: tuple[str, str, str]) -> str:
    digest = sha256("\n".join(key).encode("utf-8")).hexdigest()[:12]
    slug = re.sub(r"[^a-z0-9]+", "-", key[1]).strip("-")[:48]
    return f"{slug}-{digest}"


def load_ledger(path: str) -> dict[str, Any]:
    try:
        payload = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {"schema_version": LEDGER_SCHEMA_VERSION, "series": {}}
    if payload.get("schema_version") != LEDGER_SCHEMA_VERSION or not isinstance(payload.get("series"), dict):
        return {"schema_version": LEDGER_SCHEMA_VERSION, "series": {}}
    return payload


def _parse_dates(values: Iterable[str]) -> list[date]:
    parsed = []
    for value in values:
        try:
            parsed.append(date.fromisoformat(value))
        except (TypeError, ValueError):
            continue
    return sorted(set(parsed))


def _cadence(dates: list[date]) -> tuple[str, str, int | None]:
    if len(dates) < 3:
        return "irregular", "", None
    gaps = [(right - left).days for left, right in zip(dates, dates[1:])]
    typical = round(median(gaps))
    cadence = (
        "weekly" if 6 <= typical <= 8 else
        "biweekly" if 12 <= typical <= 16 else
        "monthly" if 26 <= typical <= 32 else
        "irregular"
    )
    pattern = ""
    if len({value.weekday() for value in dates}) == 1:
        weekday = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")[dates[0].weekday()]
        ordinals = {(value.day - 1) // 7 + 1 for value in dates}
        pattern = f"{('first', 'second', 'third', 'fourth', 'fifth')[next(iter(ordinals)) - 1]}_{weekday}" if len(ordinals) == 1 else weekday
    return cadence, pattern, typical


def _runs(series_id: str, dates: list[date], today: date) -> list[dict[str, Any]]:
    if not dates:
        return []
    _, _, typical = _cadence(dates)
    split_after = max(60, (typical or 30) * 2)
    groups: list[list[date]] = [[dates[0]]]
    for value in dates[1:]:
        if (value - groups[-1][-1]).days > split_after:
            groups.append([value])
        else:
            groups[-1].append(value)
    result = []
    for group in groups:
        cadence, pattern, group_typical = _cadence(group)
        complete = group[-1] < today and bool(group_typical and (today - group[-1]).days > group_typical * 2)
        result.append({
            "run_id": f"{series_id}-{group[0].isoformat()}",
            "start_date": group[0].isoformat(),
            "end_date": group[-1].isoformat(),
            "cadence": cadence,
            "cadence_pattern": pattern,
            "occurrence_count": len(group),
            "is_complete": complete,
        })
    return result


def _season(dates: list[date]) -> tuple[int | None, int | None, str, set[int]]:
    years = {value.year for value in dates}
    counts = Counter(value.month for value in dates)
    ordered = sorted(counts)
    if not ordered or ordered[-1] - ordered[0] >= 11:
        return None, None, "low" if len(years) < 2 else "medium", years
    confidence = "high" if len(years) >= 3 else "medium" if len(years) == 2 else "low"
    return ordered[0], ordered[-1], confidence, years


def enrich_events(
    events: Iterable[Mapping[str, Any]],
    ledger: Mapping[str, Any],
    *,
    today: date,
    generated_at: str,
    announced_events: Iterable[Mapping[str, Any]] = (),
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Attach series/run IDs and return metadata plus an updated durable ledger."""
    rows = [dict(event) for event in events]
    stored = {
        key: dict(value) for key, value in (ledger.get("series") or {}).items()
        if isinstance(value, dict)
    }
    current_groups: dict[str, list[dict[str, Any]]] = {}
    for event in rows:
        key = _series_key(event)
        if not key:
            continue
        series_id = _identifier(key)
        current_groups.setdefault(series_id, []).append(event)
        record = stored.setdefault(series_id, {
            "series_id": series_id,
            "title": str(event.get("series_title") or event.get("title") or ""),
            "venue": str(event.get("venue") or ""),
            "canonical_venue_id": str(event.get("venue_id") or ""),
            "city": str(event.get("city") or ""),
            "category_key": str(event.get("category_key") or "other"),
            "first_seen": generated_at,
            "occurrences": {},
            "announced_dates": [],
        })
        occurrences = record.setdefault("occurrences", {})
        occurrences[event_id(event)] = str(event.get("start_date") or event.get("date") or "")
        record["last_seen"] = generated_at

    for event in announced_events:
        key = _series_key(event)
        announced_date = str(event.get("start_date") or event.get("date") or "")
        try:
            date.fromisoformat(announced_date)
        except (TypeError, ValueError):
            continue
        if not key:
            continue
        series_id = _identifier(key)
        record = stored.setdefault(series_id, {
            "series_id": series_id,
            "title": str(event.get("series_title") or event.get("title") or ""),
            "venue": str(event.get("venue") or ""),
            "canonical_venue_id": str(event.get("venue_id") or ""),
            "city": str(event.get("city") or ""),
            "category_key": str(event.get("category_key") or "other"),
            "first_seen": generated_at,
            "occurrences": {},
            "announced_dates": [],
        })
        announced = record.setdefault("announced_dates", [])
        if announced_date not in announced:
            announced.append(announced_date)
        record["last_seen"] = generated_at

    metadata: list[dict[str, Any]] = []
    event_to_run: dict[tuple[str, str], str] = {}
    for series_id, record in sorted(stored.items()):
        dates = _parse_dates((record.get("occurrences") or {}).values())
        announced = _parse_dates(record.get("announced_dates") or [])
        known_dates = sorted(set(dates + announced))
        if len(known_dates) < 2:
            continue
        runs = _runs(series_id, known_dates, today)
        season_start, season_end, confidence, years = _season(known_dates)
        future = [value for value in known_dates if value >= today]
        if future:
            state = "active"
        elif season_start is not None and len(years) >= 2:
            state = "dormant_seasonal"
        elif known_dates and today - known_dates[-1] > timedelta(days=365):
            state = "concluded"
        else:
            state = "dormant_unknown"
        estimated = None
        if state == "dormant_seasonal" and season_start is not None:
            first_days = [value.day for value in known_dates if value.month == season_start]
            candidate = date(today.year, season_start, round(median(first_days)))
            if candidate <= today:
                candidate = candidate.replace(year=candidate.year + 1)
            estimated = candidate.isoformat()
        item = {
            "series_id": series_id,
            "title": record.get("title", ""),
            "venue": record.get("venue", ""),
            "canonical_venue_id": record.get("canonical_venue_id", ""),
            "city": record.get("city", ""),
            "category_key": record.get("category_key", "other"),
            "series_state": state,
            "season_start_month": season_start,
            "season_end_month": season_end,
            "season_confidence": confidence,
            "next_occurrence": future[0].isoformat() if future else None,
            "next_occurrence_estimated": estimated,
            "runs": runs,
            "occurrence_dates": [value.isoformat() for value in dates],
            "announced_dates": [value.isoformat() for value in announced],
            "first_seen": record.get("first_seen", generated_at),
            "last_seen": record.get("last_seen", generated_at),
            "observed_years": sorted(years),
        }
        metadata.append(item)
        for run in runs:
            for value in known_dates:
                if run["start_date"] <= value.isoformat() <= run["end_date"]:
                    event_to_run[(series_id, value.isoformat())] = run["run_id"]

    known_series = {item["series_id"]: item for item in metadata}
    for event in rows:
        key = _series_key(event)
        if not key:
            continue
        series_id = _identifier(key)
        item = known_series.get(series_id)
        if not item:
            continue
        event["series_id"] = series_id
        event["series_title"] = item["title"]
        event["run_id"] = event_to_run.get((series_id, str(event.get("start_date") or "")), "")

    updated = {"schema_version": LEDGER_SCHEMA_VERSION, "updated_at": generated_at, "series": stored}
    return rows, metadata, updated
