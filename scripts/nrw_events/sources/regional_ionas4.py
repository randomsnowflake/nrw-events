"""IONAS4 JSON calendars for nearby municipal sources."""

import json
import re
import urllib.parse
from datetime import timedelta

from .. import common
from ..models import normalize_source_id
from . import regional_common as rc

_SOURCE = "ionas4 regional"
_DETAIL_QUERY = {"i4xpath": "69646770502a424b29235b33", "h": "1", "h_": "1"}
_DETAIL_CITIES = frozenset({"Bad Honnef", "Grafschaft", "Sinzig"})

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
]


def fetch() -> list:
    events = []
    for city, url, calendar_url, trust in _CALENDARS:
        source_id = normalize_source_id(f"ionas4-{city}")
        try:
            items = json.loads(common.fetch_url(
                url,
                timeout=25,
                accept="application/json,*/*;q=0.8",
                sec_fetch_mode="cors",
                sec_fetch_dest="empty",
            ))
            if isinstance(items, list):
                detail_fetcher = _detail_fetcher_for_city(city)
                events.extend(_events_from_items(
                    items, city, calendar_url, trust, detail_fetcher=detail_fetcher,
                    source_id=source_id))
        except Exception as e:
            common.log_source_error(f"{_SOURCE} ({city})", e, source_id=source_id)
    return rc.dedupe(events)


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
    return {
        "description": common.concise_description(
            parser.text("description"), max_chars=360),
        "venue": common.normalize_venue_name(parser.text("location")),
        "link": common.normalize_url(link.group(1)) if link else "",
    }


def _time_text(item: dict, start, end) -> str:
    if item.get("allDay") or not start:
        return ""
    if end and end > start:
        return f"{start:%H:%M}–{end:%H:%M}"
    return f"{start:%H:%M}"


def _fallback_description(event: dict) -> str:
    start = common.parse_iso_date(event.get("start_date", ""))
    return common.factual_event_description(
        event.get("title", ""), date_value=start or event.get("date", ""),
        time_text=event.get("time", ""), venue=event.get("venue", ""),
        city=event.get("city", ""), calendar_name=event.get("city", ""),
    )


def _description_is_only_title(description: str, title: str) -> bool:
    normalize = lambda value: re.sub(r"[^\w]+", " ", value or "").strip().casefold()
    return bool(title) and normalize(description) == normalize(title)


def _description_with_context(event: dict) -> str:
    description = (event.get("description") or "").strip()
    fallback = _fallback_description(event)
    if not description or _description_is_only_title(description, event.get("title", "")):
        return fallback
    if len(description) < 40:
        separator = " " if description.endswith((".", "!", "?")) else ". "
        return f"{description}{separator}{fallback}"
    return description


def _events_from_items(items: list, city: str, calendar_url: str, trust: float,
                       detail_fetcher=None, source_id: str = "") -> list:
    events = []
    for item in items:
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
        link = context.get("link") or item.get("website") or calendar_url
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
            event["description"] = _description_with_context(event)
            events.append(event)
    return events
