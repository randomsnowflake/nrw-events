"""Official iCal calendar for Kleines Theater Bad Godesberg."""

import re
from datetime import datetime
from urllib.parse import urlencode

from .. import category_taxonomy, common


_SOURCE = "Kleines Theater Bad Godesberg"
_BASE = "https://kleinestheater.eu/events/"
_CATEGORY = "theater bühne schauspiel musical comedy show"
_TRUST = 1.0
_CONCERT_PATTERN = re.compile(
    r"\b(?:musik unter der zeder|konzert|in concert|band|pink floyd|singsause)\b",
    re.I,
)


def _month_starts() -> list[datetime]:
    current = common.TODAY.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    end = common.END_DATE.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    months = []
    while current <= end:
        months.append(current)
        current = current.replace(
            year=current.year + (current.month == 12),
            month=1 if current.month == 12 else current.month + 1,
        )
    return months


def _feed_url(month: datetime) -> str:
    query = urlencode({"ical": "1", "tribe-bar-date": month.strftime("%Y-%m-01")})
    return f"{_BASE}?{query}"


def _dedupe(events: list[dict]) -> list[dict]:
    seen, result = set(), []
    for event in events:
        key = (
            event.get("title", "").casefold(),
            event.get("start_at") or event.get("date"),
            event.get("venue", "").casefold(),
        )
        if key not in seen:
            seen.add(key)
            result.append(event)
    return result


def _correct_stage_formats(events: list[dict]) -> list[dict]:
    for event in events:
        text = f"{event.get('title', '')} {event.get('description', '')}"
        key = "concert" if _CONCERT_PATTERN.search(text) else "stage"
        category = category_taxonomy.CATEGORY_BY_KEY[key]
        context = (
            "Konzert im Kleinen Theater."
            if key == "concert" else
            "Theateraufführung auf der Bühne."
        )
        description = common.concise_description(
            f"{context} {event.get('description', '')}"
        )
        event["category"] = "konzert musik" if key == "concert" else _CATEGORY
        event["description"] = description
        event["category_key"] = category["key"]
        event["category_label"] = category["label"]
        event["category_confidence"] = 1.0
        event["category_reason"] = f"source:{key}"
    return events


def fetch() -> list[dict]:
    events = []
    for month in _month_starts():
        url = _feed_url(month)
        try:
            events.extend(common.fetch_ical(
                url, _SOURCE, "Bonn", _CATEGORY, _TRUST,
                source_id="kleines-theater",
            ))
        except Exception as exc:
            common.log_source_error(
                f"{_SOURCE} {month:%Y-%m}", exc,
                source_id="kleines-theater",
            )
    return _correct_stage_formats(_dedupe(events))
