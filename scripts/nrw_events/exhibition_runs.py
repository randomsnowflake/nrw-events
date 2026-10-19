"""Publish an exhibition's opening days as one ranged event.

Calendars such as bonn.de list a running exhibition once per opening day. Each
row used to become its own near-identical detail page, so one exhibition
produced up to 28 competing URLs. Here those rows become a single event.

Only opening days qualify: category ``exhibition``, one scheduled single-day
row per date, and the same source, title, venue and city. Each day is either a
slot of at least three hours or of the opening hours its text labels
("Dienstag bis Freitag: 15.00-17.00 Uhr"), carries no clock time at all, or only
says when it opens ("ab 12:00 Uhr"). Guided tours, workshops and performances
start at a clock time and keep one page per date.
When every day has hours, ``daily_schedule`` keeps them; otherwise the run is
an all-day range, because partial hours would present the other days as closed.
Cancelled days inside the run are closing days and fold into it. A source's
labelled final date (``details.final_date``) extends the run beyond the days it
currently lists.

Outside exhibitions the same applies to offers bookable on any day (self-guided
city rallies, tours "täglich"): bonn.de expands such a daily repeat into one
row per day, 28 pages with one text. Only untimed rows qualify, on at least
``MIN_DAILY_DAYS`` consecutive days without a gap; a published run continues on
fewer. A clock time or a skipped day means dated sessions (below).

Dated sessions are the other case: a play, course, guided tour or weekly meetup
held ``MIN_SESSIONS`` or more times at one venue, never more than
``MAX_CLOSED_DAYS`` apart, is one offer, and ten dated pages with one (or a
reworded) text competed for the same searches. Its sessions become
``daily_schedule`` slots (``end_at`` empty when the source names no end) and
``dated_sessions`` tells the website to list them as dates, not opening hours.
Two sessions on one day keep their pages: a slot holds one time per date. So do
series whose dates differ in programme (``dated_page_series.json``). Past
sessions leave the schedule; the anchored start keeps the public ID.

The public ID must survive the daily loss of past dates. The start date is
therefore anchored to the previous snapshot's run, and the replaced published
per-day IDs travel on as ``previous_event_ids`` so the website redirects them.
Day pages that had already expired are redirected by the website archive.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from datetime import date, datetime, timedelta
from itertools import pairwise
from pathlib import Path
from typing import Any

from .identity import event_id
from .models import CanonicalEvent
from .normalization import comparison_text
from .validation import _MAX_SCHEDULE_SLOTS, _daily_schedule_from_prose, validate_event

MIN_OPENING_HOURS = 3
#: A prior run still continues when the new first date follows its end within
#: this many days: exhibitions close on some weekdays or for a short break.
MAX_CLOSED_DAYS = 14
#: Gap-free untimed days before a non-exhibition offer counts as daily.
MIN_DAILY_DAYS = 7
#: Sessions before repeats of one offer count as a run.
MIN_SESSIONS = 3
#: Series with a different programme per date, or curated per date by the website; their dated pages stay.
_DATED_PAGE_SERIES = frozenset(
    (entry["source_id"], comparison_text(entry["title"]))
    for entry in json.loads(Path(__file__).with_name("dated_page_series.json").read_text(encoding="utf-8"))
)
#: "Sonntag ab 12:00 Uhr" names an opening time, not a session start.
_OPENS_FROM = re.compile(r"\bab\s+\d{1,2}(?:[:.]\d{2})?\s*uhr\b", re.IGNORECASE)
_NAMES_A_DATE = re.compile(
    r"\b\d{1,2}\.\s?(?:\d{1,2}\.|januar|februar|märz|april|mai|juni|juli|august|september|oktober|november|dezember)",
    re.IGNORECASE,
)


def _run_key(event: Mapping[str, Any]) -> tuple[str, ...] | None:
    title = comparison_text(str(event.get("title") or ""))
    # A tour start point often has an address but no venue name.
    venue = (
        str(event.get("venue_id") or "").strip()
        or comparison_text(str(event.get("venue") or ""))
        or comparison_text(str(event.get("venue_address") or ""))
    )
    if not title or not venue:
        return None
    return (
        str(event.get("source_id") or ""),
        title,
        venue,
        # Municipality only: bonn.de labels some days of one run "Bonn" and
        # others "Bonn-Beuel"; the venue already pins the place.
        comparison_text(str(event.get("city") or "").split("-", 1)[0]),
        "exhibition" if event.get("category_key") == "exhibition" else "daily",
    )


def _is_run(event: Mapping[str, Any]) -> bool:
    return str(event.get("end_date") or "") > str(event.get("start_date") or "")


def _opening_slot(event: CanonicalEvent) -> dict[str, str] | None:
    try:
        start = datetime.fromisoformat(event.start_at)
        end = datetime.fromisoformat(event.end_at)
    except ValueError:
        return None
    if end - start < timedelta(hours=MIN_OPENING_HOURS) and not _labelled_opening_hours(event, start, end):
        return None
    return {"date": event.start_date, "start_at": event.start_at, "end_at": event.end_at}


def _labelled_opening_hours(event: CanonicalEvent, start: datetime, end: datetime) -> bool:
    """A short slot the text labels as opening hours ("Dienstag bis Freitag: 15.00-17.00 Uhr")."""
    # The prose parser expands weekly hours over a range; a one-day row needs one.
    next_day = (date.fromisoformat(event.start_date) + timedelta(days=1)).isoformat()
    return any(
        slot["date"] == event.start_date
        and (datetime.fromisoformat(slot["start_at"]), datetime.fromisoformat(slot["end_at"])) == (start, end)
        for slot in _daily_schedule_from_prose({**event.to_dict(), "end_date": next_day})
    )


def _is_opening_day(event: CanonicalEvent) -> bool:
    untimed = not event.time and not event.start_at and not event.end_at
    opens_from = not event.end_at and "–" not in event.time and bool(_OPENS_FROM.search(event.time_note))
    return not event.daily_schedule and (untimed or opens_from or _opening_slot(event) is not None)


def _is_daily_offer(rows: Sequence[CanonicalEvent], continues: bool) -> bool:
    """Untimed rows on every day of their span: one offer, not separate sessions."""
    if any(row.time or row.start_at or row.end_at or row.daily_schedule for row in rows):
        return False
    days = sorted(date.fromisoformat(row.start_date) for row in rows)
    gap_free = (days[-1] - days[0]).days == len(days) - 1
    return gap_free and (continues or len(days) >= MIN_DAILY_DAYS)


def _session_slot(event: CanonicalEvent) -> dict[str, str]:
    return {"date": event.start_date, "start_at": event.start_at, "end_at": event.end_at}


def _is_session_run(rows: Sequence[CanonicalEvent], runs: Sequence[CanonicalEvent], continues: bool) -> bool:
    """Dated sessions of one offer at one venue: performances, course dates, tours."""
    # shortcut: stage runs published before ``dated_sessions`` existed lack the
    # flag; drop the category fallback once no snapshot predates it.
    if any(
        not row.start_at or row.daily_schedule
        or (row.source_id, comparison_text(row.title)) in _DATED_PAGE_SERIES
        for row in rows
    ) or any(not (run.dated_sessions or (run.category_key == "stage" and run.daily_schedule)) for run in runs):
        return False
    days = sorted(date.fromisoformat(row.start_date) for row in rows)
    if len(days) > _MAX_SCHEDULE_SLOTS or any((later - earlier).days > MAX_CLOSED_DAYS for earlier, later in pairwise(days)):
        return False
    return continues or len(days) >= MIN_SESSIONS


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
    closed: Sequence[Mapping[str, Any]], previous: Mapping[str, Any], sessions: bool,
) -> CanonicalEvent:
    ordered = sorted(rows, key=lambda row: row.start_date)
    slots = [_session_slot(row) if sessions else _opening_slot(row) for row in ordered]
    timed = all(slots) and all(run.daily_schedule for run in runs)
    by_date: dict[str, dict[str, str]] = {}
    for run in runs:  # an earlier run retained during a source outage
        # Played performances would fill the schedule's slot limit.
        by_date.update((slot["date"], slot) for slot in run.daily_schedule if not sessions or slot["date"] >= ordered[0].start_date)
    by_date.update((slot["date"], slot) for slot in slots if slot)
    base = ordered[0].to_dict()
    start = min([_anchor_start(key, ordered[0].start_date, previous), *(run.start_date for run in runs)])
    listed_end = max([ordered[-1].start_date, *(run.end_date for run in runs)])
    # An exhibition's final date ends its opening days, not a tour's sessions.
    end = listed_end if sessions else max([listed_end, *(str(row.details.get("final_date") or "") for row in ordered)])
    # A day's own note ("Sonntag ab 12:00 Uhr") does not describe the run.
    notes = {row.time_note for row in [*ordered, *runs]}
    # ponytail: the most frequent summary is the generic one; per-day summaries
    # tend to open with "Am 12. September ...". A regenerated range summary
    # would be better once the AI stage can target merged runs.
    summaries = Counter(row.ai_summary for row in ordered if row.ai_summary.strip())
    # A summary naming its own date ("am 19. Oktober") misdescribes the other dates.
    summaries = Counter({text: count for text, count in summaries.items() if not _NAMES_A_DATE.search(text)}) or summaries
    # Only published day pages need a redirect. Future days that enter the
    # window later were never public and would grow the list without bound.
    published = {str(prior.get("event_id") or "") for prior in previous.get("events") or [] if isinstance(prior, dict)}
    day_ids = [identifier for identifier in map(event_id, [*ordered, *closed]) if identifier in published]
    base.update({
        # The district label ("Bonn-Beuel") is more specific than "Bonn".
        "city": max((row.city for row in [*ordered, *runs]), key=len),
        "start_date": start,
        "end_date": end,
        "date": start,
        "time": "",
        "identity_time": "",
        "identity_time_locked": False,
        "start_at": "",
        "end_at": "",
        "all_day": not timed,
        "time_note": base["time_note"] if len(notes) == 1 else "",
        "daily_schedule": [by_date[day] for day in sorted(by_date)] if timed else [],
        "dated_sessions": sessions,
        "preserved_event_id": "",
        "ai_summary": summaries.most_common(1)[0][0] if summaries else base["ai_summary"],
        "source_links": list(dict.fromkeys(link for row in ordered for link in row.source_links)),
        "previous_event_ids": list(dict.fromkeys([
            *(identifier for run in runs for identifier in (event_id(run), *run.previous_event_ids)),
            *day_ids,
        ])),
    })
    # Labelled weekly hours beat the one clock a calendar copies onto every
    # repeat (NAHT: 16–19 daily vs. "Mittwoch bis Freitag, 16 bis 19 Uhr ...").
    # Cancelled days stay closed even when the weekly hours name them.
    closed_dates = {str(day.get("start_date") or "") for day in closed}
    # Performances are dated; weekly hours describe opening days only.
    prose = [] if sessions else [slot for slot in _daily_schedule_from_prose(base) if slot["date"] not in closed_dates]
    if base["daily_schedule"] and prose:
        base["daily_schedule"] = prose
    elif base["daily_schedule"] and end > listed_end and not sessions:
        # Slots exist only for the listed days; the rest of the run would read as closed.
        base.update(daily_schedule=[], all_day=True)
    return validate_event(base)


def merge_exhibition_opening_days(
    events: Sequence[CanonicalEvent], previous: Mapping[str, Any],
) -> list[CanonicalEvent]:
    """Replace each exhibition's or daily offer's per-day rows by one ranged event."""
    rows_by_key: dict[tuple[str, ...], list[CanonicalEvent]] = defaultdict(list)
    runs_by_key: dict[tuple[str, ...], list[CanonicalEvent]] = defaultdict(list)
    closed_by_key: dict[tuple[str, ...], list[Mapping[str, Any]]] = defaultdict(list)
    for event in events:
        key = _run_key(event)
        status = event.get("status", "scheduled")
        if key is None:
            continue
        if status == "cancelled":
            if not _is_run(event):
                closed_by_key[key].append(event)
        # Carried-over cancellations can arrive as plain snapshot records
        # despite the annotation; only validated rows are merged.
        elif status != "scheduled" or not isinstance(event, CanonicalEvent):
            continue
        elif _is_run(event):
            runs_by_key[key].append(event)
        else:
            rows_by_key[key].append(event)

    replaced: dict[int, CanonicalEvent | None] = {}
    for key, rows in rows_by_key.items():
        dates = Counter(row.start_date for row in rows)
        if max(dates.values()) > 1:
            continue
        runs = runs_by_key.get(key, [])
        first_row = min(dates)
        # A published run remains a run on its final opening day. A new
        # standalone day still keeps its original occurrence identity.
        continues = bool(runs) or _anchor_start(key, first_row, previous) != first_row
        # An exhibition's opening days win; its timed tours are sessions.
        opening_days = key[-1] == "exhibition" and all(map(_is_opening_day, rows))
        sessions = not opening_days and _is_session_run(rows, runs, continues)
        if key[-1] == "daily":
            if not (sessions or _is_daily_offer(rows, continues)):
                continue
        elif not sessions and (not opening_days or (len(rows) < 2 and not continues)):
            continue
        first = min([row.start_date for row in rows] + [run.start_date for run in runs])
        last = max([row.start_date for row in rows] + [run.end_date for run in runs])
        closed = [day for day in closed_by_key.get(key, []) if first <= str(day.get("start_date") or "") <= last]
        members = [*rows, *runs, *closed]
        replaced[id(members[0])] = _merged(key, rows, runs, closed, previous, sessions)
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
