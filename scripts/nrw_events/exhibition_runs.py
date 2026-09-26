"""Publish an exhibition's opening days as one ranged event.

Calendars such as bonn.de list a running exhibition once per opening day. Each
row used to become its own near-identical detail page, so one exhibition
produced up to 28 competing URLs. Here those rows become a single event.

Only opening days qualify: category ``exhibition``, one scheduled single-day
row per date, and the same source, title, venue and city. Each day is either a
slot of at least three hours or carries no clock time at all. Guided tours,
workshops and performances start at a clock time and keep one page per date.
When every day has hours, ``daily_schedule`` keeps them; otherwise the run is
an all-day range, because partial hours would present the other days as closed.
Cancelled days inside the run are closing days and fold into it.

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
    if event.get("category_key") != "exhibition":
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


def _is_run(event: Mapping[str, Any]) -> bool:
    return str(event.get("end_date") or "") > str(event.get("start_date") or "")


def _opening_slot(event: CanonicalEvent) -> dict[str, str] | None:
    try:
        start = datetime.fromisoformat(event.start_at)
        end = datetime.fromisoformat(event.end_at)
    except ValueError:
        return None
    if end - start < timedelta(hours=MIN_OPENING_HOURS):
        return None
    return {"date": event.start_date, "start_at": event.start_at, "end_at": event.end_at}


def _is_opening_day(event: CanonicalEvent) -> bool:
    untimed = not event.time and not event.start_at and not event.end_at
    return not event.daily_schedule and (untimed or _opening_slot(event) is not None)


def _anchor_start(key: tuple[str, ...], first: str, previous: Mapping[str, Any]) -> str:
    """Return the start of the run published last time, if this continues it."""
    first_day = date.fromisoformat(first)
    for prior in previous.get("events") or []:
        if (
            not isinstance(prior, dict) or not _is_run(prior)
            or prior.get("status", "scheduled") != "scheduled" or _run_key(prior) != key
        ):
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
    closed: Sequence[CanonicalEvent], previous: Mapping[str, Any],
) -> CanonicalEvent:
    ordered = sorted(rows, key=lambda row: row.start_date)
    slots = [_opening_slot(row) for row in ordered]
    timed = all(slots) and all(run.daily_schedule for run in runs)
    by_date: dict[str, dict[str, str]] = {}
    for run in runs:  # an earlier run retained during a source outage
        by_date.update((slot["date"], slot) for slot in run.daily_schedule)
    by_date.update((slot["date"], slot) for slot in slots if slot)
    base = ordered[0].to_dict()
    start = min([_anchor_start(key, ordered[0].start_date, previous), *(run.start_date for run in runs)])
    end = max([ordered[-1].start_date, *(run.end_date for run in runs)])
    # ponytail: the most frequent summary is the generic one; per-day summaries
    # tend to open with "Am 12. September ...". A regenerated range summary
    # would be better once the AI stage can target merged runs.
    summaries = Counter(row.ai_summary for row in ordered if row.ai_summary.strip())
    # Only published day pages need a redirect. Future days that enter the
    # window later were never public and would grow the list without bound.
    published = {str(prior.get("event_id") or "") for prior in previous.get("events") or [] if isinstance(prior, dict)}
    day_ids = [identifier for identifier in map(event_id, [*ordered, *closed]) if identifier in published]
    base.update({
        "start_date": start,
        "end_date": end,
        "date": start,
        "time": "",
        "identity_time": "",
        "identity_time_locked": False,
        "start_at": "",
        "end_at": "",
        "all_day": not timed,
        "daily_schedule": [by_date[day] for day in sorted(by_date)] if timed else [],
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
    closed_by_key: dict[tuple[str, ...], list[CanonicalEvent]] = defaultdict(list)
    for event in events:
        key = _run_key(event)
        if key is None or event.status not in {"scheduled", "cancelled"}:
            continue
        if event.status == "cancelled":
            if not _is_run(event):
                closed_by_key[key].append(event)
        elif _is_run(event):
            runs_by_key[key].append(event)
        else:
            rows_by_key[key].append(event)

    replaced: dict[int, CanonicalEvent | None] = {}
    for key, rows in rows_by_key.items():
        dates = Counter(row.start_date for row in rows)
        if len(rows) < 2 or max(dates.values()) > 1 or not all(map(_is_opening_day, rows)):
            continue
        runs = runs_by_key.get(key, [])
        first = min([row.start_date for row in rows] + [run.start_date for run in runs])
        last = max([row.start_date for row in rows] + [run.end_date for run in runs])
        closed = [day for day in closed_by_key.get(key, []) if first <= day.start_date <= last]
        members = [*rows, *runs, *closed]
        replaced[id(members[0])] = _merged(key, rows, runs, closed, previous)
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
