"""IONAS4 JSON calendars for nearby municipal sources."""

import json

from .. import common
from . import regional_common as rc

_SOURCE = "ionas4 regional"

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
                events.extend(_events_from_items(items, city, calendar_url, trust))
        except Exception as e:
            common.log_source_error(f"{_SOURCE} ({city})", e)
    return rc.dedupe(events)


def _events_from_items(items: list, city: str, calendar_url: str, trust: float) -> list:
    events = []
    for item in items:
        start = common.parse_iso_date(item.get("start", ""))
        loc = item.get("location") or {}
        cat = item.get("category") or {}
        tag_text = " ".join(t.get("name", "") for t in item.get("tags") or [] if isinstance(t, dict))
        category = " ".join([
            cat.get("name", "") if isinstance(cat, dict) else "",
            tag_text,
            city,
            "kommunal lokal markt kultur",
        ])
        ev = common.make_event(
            item.get("title") or "",
            start,
            common.parse_iso_date(item.get("end", "")) or start,
            loc.get("name") or "",
            city,
            tag_text,
            item.get("website") or calendar_url,
            _SOURCE,
            category,
            trust,
        )
        if ev:
            events.append(ev)
    return events
