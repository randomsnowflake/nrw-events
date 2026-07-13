"""IONAS4 JSON calendars for nearby municipal sources."""

import json
import re
import urllib.parse
from datetime import timedelta
from html.parser import HTMLParser

from .. import common
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
                    items, city, calendar_url, trust, detail_fetcher=detail_fetcher))
        except Exception as e:
            common.log_source_error(f"{_SOURCE} ({city})", e)
    return rc.dedupe(events)


def _detail_fetcher_for_city(city: str):
    if city not in _DETAIL_CITIES:
        return None
    return lambda detail_url: common.fetch_detail_url(
        detail_url,
        cache_namespace=f"ionas4-{city}",
        timeout=20,
    )


class _IonasDetailParser(HTMLParser):
    _VOID_TAGS = {
        "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
        "meta", "param", "source", "track", "wbr",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts = {"description": [], "location": []}
        self._target = ""
        self._depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        classes = (dict(attrs).get("class") or "").split()
        target = ""
        if "tvm-event--description" in classes:
            target = "description"
        elif "tvm-event--location" in classes:
            target = "location"

        if not self._target and target:
            self._target = target
            self._depth = 1
        elif self._target and tag not in self._VOID_TAGS:
            self._depth += 1

    def handle_endtag(self, tag: str) -> None:
        if not self._target:
            return
        self._depth -= 1
        if self._depth == 0:
            self._target = ""

    def handle_data(self, data: str) -> None:
        if self._target:
            self.parts[self._target].append(data)


def _detail_url(calendar_url: str, item: dict) -> str:
    query = dict(_DETAIL_QUERY)
    query.update({
        "start": str(item.get("start") or "")[:10],
        "eventId": str(item.get("id") or ""),
    })
    return f"{calendar_url.rstrip('/')}/event-list.html?{urllib.parse.urlencode(query)}"


def _detail_context(html: str) -> dict:
    parser = _IonasDetailParser()
    parser.feed(html or "")
    link = re.search(
        r'navigator\.clipboard\.writeText\(\s*["\']([^"\']+)', html or "", re.S | re.I)
    return {
        "description": common.concise_description(
            " ".join(parser.parts["description"]), max_chars=360),
        "venue": common.normalize_venue_name(" ".join(parser.parts["location"])),
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
    date_label = start.strftime("%d.%m.%Y") if start else event.get("date", "")
    schedule = f" für den {date_label}" if date_label else ""
    if event.get("time"):
        schedule += f" um {event['time'].split('–', 1)[0]} Uhr"
    if event.get("venue"):
        schedule += f" am Veranstaltungsort „{event['venue']}“"
    return (
        f"„{event.get('title', '')}“ ist im Veranstaltungskalender von "
        f"{event.get('city', '')}{schedule} angekündigt."
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
                       detail_fetcher=None) -> list:
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
        base_event = common.make_event(
            item.get("title") or "",
            start,
            end,
            loc.get("name") or "",
            city,
            tag_text,
            item.get("website") or calendar_url,
            _SOURCE,
            category,
            trust,
            _time_text(item, start, end),
            all_day=item_all_day,
        )
        if not base_event:
            continue

        context = {}
        if detail_fetcher and item.get("id"):
            try:
                context = _detail_context(detail_fetcher(_detail_url(calendar_url, item)))
            except Exception as exc:
                common.log_source_error(f"{_SOURCE} ({city}) detail", exc)

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
        )
        if event:
            event["description"] = _description_with_context(event)
            events.append(event)
    return events
