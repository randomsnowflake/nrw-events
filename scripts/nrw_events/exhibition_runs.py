"""Publish an exhibition's daily opening hours as one ranged event.

Calendars such as bonn.de list a running exhibition once per opening day. Each
row used to become its own near-identical detail page, so one exhibition
produced up to 28 competing URLs. Here those rows become a single event whose
``daily_schedule`` keeps every opening slot.

Only opening hours qualify: category ``exhibition``, one scheduled single-day
row per date, a slot of at least three hours, and the same source, title, venue
and city. Guided tours, workshops and performances keep one page per date.

The public ID must survive the daily loss of past dates. The start date is
therefore anchored to the previous snapshot's run, and the replaced published
per-day IDs travel on as ``previous_event_ids`` so the website redirects them.
Day pages that had already expired are redirected by the website archive.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from datetime import date, datetime, timedelta
from typing import Any

from .identity import event_id
from .models import CanonicalEvent
from .normalization import comparison_text
from .validation import validate_event

MIN_OPENING_HOURS = 3
#: A prior run still continues when the new first date follows its end within
#: this many days: exhibitions close on some weekdays or for a short break.
MAX_CLOSED_DAYS = 14


def _run_key(event: Mapping[str, Any]) -> tuple[str, ...] | None:
    if event.get("category_key") != "exhibition" or event.get("status", "scheduled") != "scheduled":
        return None
    title = comparison_text(str(event.get("title") or ""))
    venue = str(event.get("venue_id") or "").strip() or comparison_text(str(event.get("venue") or ""))
    if not title or not venue:
        return None
    return (
        str(event.get("source_id") or ""),
        title,
        venue,
        comparison_text(str(event.get("city") or "")),
    )


def _opening_slot(event: CanonicalEvent) -> dict[str, str] | None:
    if event.end_date not in ("", event.start_date) or event.daily_schedule:
        return None
    try:
        start = datetime.fromisoformat(event.start_at)
        end = datetime.fromisoformat(event.end_at)
    except ValueError:
        return None
    if end - start < timedelta(hours=MIN_OPENING_HOURS):
        return None
    return {"date": event.start_date, "start_at": event.start_at, "end_at": event.end_at}


def _anchor_start(key: tuple[str, ...], first: str, previous: Mapping[str, Any]) -> str:
    """Return the start of the run published last time, if this continues it."""
    first_day = date.fromisoformat(first)
    for prior in previous.get("events") or []:
        if not isinstance(prior, dict) or not prior.get("daily_schedule") or _run_key(prior) != key:
            continue
        try:
            prior_start = date.fromisoformat(str(prior.get("start_date") or ""))
            prior_end = date.fromisoformat(str(prior.get("end_date") or ""))
        except ValueError:
            continue
        if prior_start <= first_day <= prior_end + timedelta(days=MAX_CLOSED_DAYS):
            return min(prior_start, first_day).isoformat()
    return first


def _merged(
    key: tuple[str, ...], rows: Sequence[CanonicalEvent], runs: Sequence[CanonicalEvent],
    previous: Mapping[str, Any],
) -> CanonicalEvent:
    by_date: dict[str, dict[str, str]] = {}
    for run in runs:  # an earlier run retained during a source outage
        by_date.update((slot["date"], slot) for slot in run.daily_schedule)
    for row in rows:
        slot = _opening_slot(row)
        if slot:
            by_date[slot["date"]] = slot
    schedule = [by_date[day] for day in sorted(by_date)]
    ordered = sorted(rows, key=lambda row: row.start_date)
    base = ordered[0].to_dict()
    start = min([_anchor_start(key, schedule[0]["date"], previous), *(run.start_date for run in runs)])
    # ponytail: the most frequent summary is the generic one; per-day summaries
    # tend to open with "Am 12. September ...". A regenerated range summary
    # would be better once the AI stage can target merged runs.
    summaries = Counter(row.ai_summary for row in ordered if row.ai_summary.strip())
    # Only published day pages need a redirect. Future days that enter the
    # window later were never public and would grow the list without bound.
    published = {str(prior.get("event_id") or "") for prior in previous.get("events") or [] if isinstance(prior, dict)}
    day_ids = [identifier for identifier in map(event_id, ordered) if identifier in published]
    base.update({
        "start_date": start,
        "end_date": schedule[-1]["date"],
        "date": start,
        "time": "",
        "identity_time": "",
        "identity_time_locked": False,
        "start_at": "",
        "end_at": "",
        "all_day": False,
        "daily_schedule": schedule,
        "preserved_event_id": "",
        "ai_summary": summaries.most_common(1)[0][0] if summaries else base["ai_summary"],
        "source_links": list(dict.fromkeys(link for row in ordered for link in row.source_links)),
        "previous_event_ids": list(dict.fromkeys([
            *(identifier for run in runs for identifier in (event_id(run), *run.previous_event_ids)),
            *day_ids,
        ])),
    })
    return validate_event(base)


def merge_exhibition_opening_days(
    events: Sequence[CanonicalEvent], previous: Mapping[str, Any],
) -> list[CanonicalEvent]:
    """Replace each exhibition's per-day opening rows by one ranged event."""
    rows_by_key: dict[tuple[str, ...], list[CanonicalEvent]] = defaultdict(list)
    runs_by_key: dict[tuple[str, ...], list[CanonicalEvent]] = defaultdict(list)
    for event in events:
        key = _run_key(event)
        if key is None:
            continue
        if event.daily_schedule and event.end_date > event.start_date:
            runs_by_key[key].append(event)
        else:
            rows_by_key[key].append(event)

    replaced: dict[int, CanonicalEvent | None] = {}
    for key, rows in rows_by_key.items():
        dates = Counter(row.start_date for row in rows)
        if len(rows) < 2 or max(dates.values()) > 1 or not all(_opening_slot(row) for row in rows):
            continue
        runs = runs_by_key.get(key, [])
        members = [*rows, *runs]
        merged = _merged(key, rows, runs, previous)
        replaced[id(members[0])] = merged
        for member in members[1:]:
            replaced[id(member)] = None
    if not replaced:
        return list(events)
    result: list[CanonicalEvent] = []
    for event in events:
        replacement = replaced.get(id(event), event)
        if replacement is not None:
            result.append(replacement)
    return result
