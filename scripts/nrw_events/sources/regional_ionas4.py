"""IONAS4 JSON calendars for nearby municipal sources."""

import json
import re
import urllib.parse
from collections import defaultdict
from datetime import timedelta
from functools import partial

from .. import common, components, http
from ..models import normalize_source_id
from . import regional_common as rc

_SOURCE = "ionas4 regional"
_DETAIL_QUERY = {"i4xpath": "69646770502a424b29235b33", "h": "1", "h_": "1"}
_DETAIL_CITIES = frozenset({
    "Bad Honnef", "Grafschaft", "Sinzig", "Rösrath", "Ruppichteroth",
})

_CALENDARS = [
    (
        "Bad Honnef",
        "https://meinbadhonnef.de/kalender/veranstaltungen/events.json",
        "https://meinbadhonnef.de/kalender/veranstaltungen/",
        0.98,
    ),
    (
        "Grafschaft",
        "https://www.gemeinde-grafschaft.de/kalender/kalendergrafschaft/events.json",
        "https://www.gemeinde-grafschaft.de/kalender/kalendergrafschaft/",
        0.9,
    ),
    (
        "Sinzig",
        "https://tourismus.sinzig.de/kalender/events.json?weekends=false&tagMode=ALL",
        "https://tourismus.sinzig.de/kalender/",
        0.82,
    ),
    (
        "Rösrath",
        "https://www.roesrath.de/kalender/events.json",
        "https://www.roesrath.de/kalender/",
        0.95,
    ),
    (
        "Ruppichteroth",
        "https://www.ruppichteroth.de/kalender/events.json",
        "https://www.ruppichteroth.de/kalender/",
        0.95,
    ),
]


def fetch() -> list:
    events = components.run([
        components.Job(calendar[1], partial(_fetch_calendar, *calendar))
        for calendar in _CALENDARS
    ])
    return rc.dedupe(events)


def _fetch_calendar(city: str, url: str, calendar_url: str, trust: float) -> list:
    source_id = normalize_source_id(f"ionas4-{city}")
    try:
        hostname = urllib.parse.urlsplit(url).hostname or ""
        items = json.loads(http.fetch_url_with_brightdata_fallback(
            url,
            timeout=25,
            allowed_hosts=(hostname,),
            fallback_statuses=(408, 429, 500, 502, 503, 504),
            fallback_on_timeout=True,
            accept="application/json,*/*;q=0.8",
            sec_fetch_mode="cors",
            sec_fetch_dest="empty",
        ))
        if isinstance(items, list):
            detail_fetcher = _detail_fetcher_for_city(city)
            return _events_from_items(
                items, city, calendar_url, trust, detail_fetcher=detail_fetcher,
                source_id=source_id)
    except Exception as e:
        common.log_source_error(f"{_SOURCE} ({city})", e, source_id=source_id)
    return []


def _detail_fetcher_for_city(city: str):
    if city not in _DETAIL_CITIES:
        return None
    return lambda detail_url: common.fetch_detail_url(
        detail_url,
        cache_namespace=f"ionas4-{city}",
        timeout=20,
    )


def _detail_url(calendar_url: str, item: dict) -> str:
    query = dict(_DETAIL_QUERY)
    query.update({
        "start": str(item.get("start") or "")[:10],
        "eventId": str(item.get("id") or ""),
    })
    return f"{calendar_url.rstrip('/')}/event-list.html?{urllib.parse.urlencode(query)}"


def _detail_context(html: str) -> dict:
    parser = rc.ClassScopedTextParser({
        "description": lambda _tag, attrs: "tvm-event--description" in (attrs.get("class") or "").split(),
        "location": lambda _tag, attrs: "tvm-event--location" in (attrs.get("class") or "").split(),
    })
    parser.feed(html or "")
    link = re.search(
        r'navigator\.clipboard\.writeText\(\s*["\']([^"\']+)', html or "", re.S | re.I)
    organizer = re.search(
        r'<[^>]+class=["\'][^"\']*\btvm-organiser-name\b[^"\']*["\'][^>]*>(.*?)</[^>]+>',
        html or "",
        re.S | re.I,
    )
    return {
        # Handed over untrimmed, with its paragraphs: ``make_event`` infers
        # admission from the text it is given and only then shortens it for
        # display. Cutting to 360 here dropped the closing "Die Teilnahme ist
        # kostenfrei" sentence these calendars put last — IONAS4 has no price
        # field at all, so that sentence is the only admission evidence the
        # source ever offers.
        "description": parser.block_text("description"),
        # Preserve the source string until the canonical event boundary so its
        # street address can be separated into ``venue_address`` there.
        "venue": parser.text("location"),
        "organizer": common.clean_html(organizer.group(1)) if organizer else "",
        "link": common.normalize_url(link.group(1)) if link else "",
    }


def _time_text(item: dict, start, end) -> str:
    if item.get("allDay") or not start:
        return ""
    if end and end > start:
        return f"{start:%H:%M}–{end:%H:%M}"
    return f"{start:%H:%M}"


_fallback_description = rc.factual_fallback(calendar_name=lambda event: event.get("city", ""))


def _description_is_only_title(description: str, title: str) -> bool:
    normalize = lambda value: re.sub(r"[^\w]+", " ", value or "").strip().casefold()
    return bool(title) and normalize(description) == normalize(title)


def _description_with_context(event: dict) -> str:
    description = (event.get("description") or "").strip()
    fallback = _fallback_description(event)
    organizer = (event.get("organizer") or "").strip()
    if organizer and (not description or description.casefold() in {"freizeit", "allgemeines", "vereine"}):
        return common.GeneratedDescription(f"{fallback} Veranstalter: {organizer}.")
    if not description or _description_is_only_title(description, event.get("title", "")):
        return fallback
    if len(description) < 40:
        separator = " " if description.endswith((".", "!", "?")) else ". "
        return common.GeneratedDescription(f"{description}{separator}{fallback}")
    return description


def _all_day_series_key(item: dict):
    """Return a conservative key for FullCalendar-generated daily occurrences."""
    item_id = str(item.get("id") or "")
    match = re.fullmatch(r"(.+):\d+", item_id)
    raw_all_day = item.get("allDay")
    all_day = (
        raw_all_day if isinstance(raw_all_day, bool)
        else str(raw_all_day).strip().casefold() == "true"
    )
    start = common.parse_iso_date(item.get("start", ""))
    end = common.parse_iso_date(item.get("end", ""))
    if not (match and all_day and start and end and end - start == timedelta(days=1)):
        return None
    identity = json.dumps(
        {
            "base_id": match.group(1),
            "title": item.get("title") or "",
            "website": item.get("website") or "",
            "location": item.get("location") or {},
            "category": item.get("category") or {},
            "tags": item.get("tags") or [],
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return identity, start, end


def _collapse_consecutive_all_day_items(items: list) -> list:
    """Collapse three or more generated daily rows into one inclusive date run."""
    grouped: dict[str, list[tuple[object, object, dict]]] = defaultdict(list)
    untouched: list[dict] = []
    for item in items:
        series = _all_day_series_key(item)
        if not series:
            untouched.append(item)
            continue
        identity, start, end = series
        grouped[identity].append((start, end, item))

    collapsed = list(untouched)
    for occurrences in grouped.values():
        occurrences.sort(key=lambda value: value[0])
        runs: list[list[tuple[object, object, dict]]] = []
        for occurrence in occurrences:
            if runs and occurrence[0] == runs[-1][-1][1]:
                runs[-1].append(occurrence)
            else:
                runs.append([occurrence])
        for run in runs:
            if len(run) < 3:
                collapsed.extend(occurrence[2] for occurrence in run)
                continue
            merged = dict(run[0][2])
            merged["end"] = run[-1][2].get("end")
            collapsed.append(merged)
    return collapsed


def _events_from_items(items: list, city: str, calendar_url: str, trust: float,
                       detail_fetcher=None, source_id: str = "") -> list:
    events = []
    for item in _collapse_consecutive_all_day_items(items):
        start = common.parse_iso_date(item.get("start", ""))
        end = common.parse_iso_date(item.get("end", "")) or start
        raw_all_day = item.get("allDay")
        item_all_day = (
            raw_all_day if isinstance(raw_all_day, bool)
            else str(raw_all_day).strip().casefold() == "true"
            if raw_all_day is not None else None
        )
        if (item_all_day is True and start and end and end > start
                and not (end.hour or end.minute or end.second)):
            # IONAS/FullCalendar represents all-day end dates exclusively.
            # Without this correction a weekend event ending Sunday appears as
            # "ongoing" on Monday and survives the report window incorrectly.
            end -= timedelta(days=1)
        if start and end and end < start and end.date() == start.date():
            # IONAS uses same-day midnight as an empty end time for some events.
            end = start
        loc = item.get("location") or {}
        cat = item.get("category") or {}
        tag_text = " ".join(t.get("name", "") for t in item.get("tags") or [] if isinstance(t, dict))
        category = " ".join([
            cat.get("name", "") if isinstance(cat, dict) else "",
            tag_text,
            city,
            "kommunal lokal markt kultur",
        ])
        should_enrich = common.event_in_window_and_radius(start, end, city)
        context = {}
        if should_enrich and detail_fetcher and item.get("id"):
            try:
                context = _detail_context(detail_fetcher(_detail_url(calendar_url, item)))
            except Exception as exc:
                common.log_source_error(
                    f"{_SOURCE} ({city}) detail", exc,
                    source_id=f"{source_id}-detail",
                )

        description = context.get("description") or tag_text
        venue = context.get("venue") or loc.get("name") or ""
        raw_link = (context.get("link")
                    or common.normalize_url(item.get("website") or "")
                    or calendar_url)
        link = rc.abs_url(calendar_url, raw_link)
        event = common.make_event(
            item.get("title") or "",
            start,
            end,
            venue,
            city,
            description,
            link,
            _SOURCE,
            category,
            trust,
            _time_text(item, start, end),
            all_day=item_all_day,
            source_id=source_id,
        )
        if event:
            if context.get("organizer"):
                event["organizer"] = context["organizer"]
            event["description"] = _description_with_context(event)
            event["description_source"] = common.description_source_for(event["description"])
            events.append(event)
    return events
