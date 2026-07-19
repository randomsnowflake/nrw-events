"""Official Theater Bonn JSON calendar."""

import json

from .. import category_taxonomy, common
from . import regional_common as rc


_SOURCE = "Theater Bonn"
_API = "https://www.theater-bonn.de/de/api/events/"
_CALENDAR = "https://www.theater-bonn.de/de/?mode=kalender#programm"
_TRUST = 1.0


def _start(item: dict):
    date_text = rc.clean(item.get("date_full", ""))
    time_text = rc.time_text(rc.clean(item.get("date_time", "")))
    start = common.parse_date(date_text)
    return rc.with_time(start, time_text), time_text


def _venue(item: dict) -> str:
    tags = [rc.clean(tag.get("name", "") if isinstance(tag, dict) else str(tag))
            for tag in item.get("tags", [])]
    ignored = {"oper", "schauspiel", "tanz", "quatsch keine oper", "theater bonn"}
    candidates = [tag for tag in tags if tag and tag.casefold() not in ignored]
    return candidates[-1] if candidates else "Theater Bonn"


def _category(item: dict) -> str:
    names = []
    for field in ("categories", "genre_names"):
        value = item.get(field, [])
        if isinstance(value, str):
            names.append(value)
        else:
            names.extend(entry.get("name", "") if isinstance(entry, dict) else str(entry)
                         for entry in value or [])
    text = " ".join(rc.clean(name) for name in names).casefold()
    if any(word in text for word in ("oper", "schauspiel", "tanz", "musical", "quatsch")):
        return "theater bühne schauspiel tanz performance"
    if "konzert" in text:
        return "konzert musik"
    return f"theater bühne {text}".strip()


def _format_label(item: dict) -> str:
    values = item.get("categories", []) or item.get("genre_names", []) or []
    if isinstance(values, str):
        values = [values]
    labels = [rc.clean(value.get("name", "") if isinstance(value, dict) else str(value))
              for value in values]
    return next((label for label in labels if label), "")


def _link(item: dict) -> str:
    ticket = item.get("ticket") or {}
    link = (
        rc.clean(ticket.get("url", "") if isinstance(ticket, dict) else "")
        or rc.clean(item.get("link_to_registration_url", ""))
        or _CALENDAR
    )
    return rc.abs_url("https://www.theater-bonn.de/", link)


def events_from_payload(items: list[dict]) -> list[dict]:
    events = []
    for item in items:
        if not isinstance(item, dict):
            continue
        status = rc.clean(item.get("status", ""))
        if any(word in status.casefold() for word in ("abgesagt", "entfällt", "cancelled")):
            continue
        start, time_text = _start(item)
        if not start:
            continue
        title = rc.clean(item.get("title", ""))
        venue = _venue(item)
        description = common.concise_description(item.get("description", ""))
        if not description:
            description = common.factual_event_description(
                title, date_value=start, time_text=time_text, venue=venue, city="Bonn"
            )
        category_hint = _category(item)
        if category_hint.startswith("theater"):
            format_label = _format_label(item) or "Bühnenaufführung"
            description = common.concise_description(
                f"{format_label} im Theater auf der Bühne. {description}"
            )
        ticket = item.get("ticket") or {}
        ticket_info = rc.clean(ticket.get("ticket_info", "") if isinstance(ticket, dict) else "")
        if ticket_info and ticket_info.casefold() not in description.casefold():
            description = common.concise_description(f"{description} {ticket_info}")
        event = common.make_event(
            title, start, None, venue, "Bonn", description, _link(item), _SOURCE,
            category_hint, _TRUST, time_text,
            source_id="theater-bonn",
        )
        if event:
            if category_hint.startswith("theater"):
                stage = category_taxonomy.CATEGORY_BY_KEY["stage"]
                event["category_key"] = stage["key"]
                event["category_label"] = stage["label"]
                event["category_confidence"] = 1.0
                event["category_reason"] = "source:stage"
            events.append(event)
    return rc.dedupe_occurrences(events)


def fetch() -> list[dict]:
    try:
        payload = json.loads(common.fetch_url(_API, timeout=30, accept="application/json"))
        items = payload if isinstance(payload, list) else payload.get("events", [])
        return events_from_payload(items)
    except Exception as exc:
        common.log_source_error(_SOURCE, exc)
        return []
