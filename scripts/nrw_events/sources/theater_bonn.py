"""Official Theater Bonn JSON calendar and programme details."""

import os
import re
import time
from collections.abc import Callable
from datetime import datetime

from .. import category_taxonomy, common
from . import regional_common as rc

_SOURCE = "Theater Bonn"
_API = "https://www.theater-bonn.de/de/api/events/"
_CALENDAR = "https://www.theater-bonn.de/de/?mode=kalender#programm"
_TRUST = 1.0


def _start(item: dict) -> tuple[datetime | None, str]:
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


def _programme_link(item: dict) -> str:
    href = rc.clean(item.get("url", ""))
    return rc.abs_url("https://www.theater-bonn.de/", href) if href else ""


def _link(item: dict) -> str:
    ticket = item.get("ticket") or {}
    link = (
        _programme_link(item)
        or rc.clean(ticket.get("url", "") if isinstance(ticket, dict) else "")
        or rc.clean(item.get("link_to_registration_url", ""))
        or _CALENDAR
    )
    return rc.abs_url("https://www.theater-bonn.de/", link)


def _detail_description(html: str) -> str:
    body = rc.first_group(
        r'<div[^>]+class=["\'][^"\']*\breadmore-text\b[^"\']*["\'][^>]*>(.*?)</div>',
        html,
        flags=re.I | re.S,
    )
    return common.concise_description(rc.clean_blocks(body))


def _detail_pages(items: list[dict], detail_fetcher: Callable[[str], str] | None = None) -> dict[str, str]:
    """Fetch programme pages within one bounded, deduplicated source budget.

    Pages without API copy come first: they supply the description. The rest
    only add labelled facts (age, language, credits) and may be cut by the budget.
    """
    batch_timeout = float(os.environ.get("NRW_EVENTS_DETAIL_BATCH_TIMEOUT_SECONDS", "45"))
    deadline = time.monotonic() + max(batch_timeout, 0.0)
    pages = {}
    failed_links = set()
    candidates = sorted(
        (item for item in items if isinstance(item, dict)),
        key=lambda item: bool(common.concise_description(item.get("description", ""))),
    )
    for item in candidates:
        status = rc.clean(item.get("status", ""))
        if any(word in status.casefold() for word in ("abgesagt", "entfällt", "cancelled")):
            continue
        start, _ = _start(item)
        link = _programme_link(item)
        remaining = deadline - time.monotonic()
        if not start or not common.window_contains(start) or not link:
            continue
        if link in pages or link in failed_links:
            continue
        if remaining < 3.0:
            break
        request_timeout = 20.0 if remaining >= 40.0 else max(1.0, remaining / 3.0)
        try:
            pages[link] = (
                detail_fetcher(link) if detail_fetcher
                else common.fetch_detail_url(
                    link, cache_namespace="theater-bonn-v3", timeout=request_timeout,
                )
            )
        except Exception as exc:
            failed_links.add(link)
            common.log_source_error(f"{_SOURCE} detail", exc)
    return pages


_PERFORMANCE_NOTES = {"premiere": "premiere", "wiederaufnahme": "revival", "zum-letzten-mal": "final"}
_TICKET_AVAILABILITY = {"sold-out": "SoldOut", "restkarten": "LimitedAvailability"}


def _subtitle_facts(html: str) -> dict:
    """Read the labelled header facts of a programme page.

    The header subtitle carries one paragraph per fact and a trailing
    ``Altersempfehlung ab 12 Jahren | 2 Stunden`` or ``10+ | ca. 2 Stunden`` line.
    """
    subtitle = rc.first_group(
        r'<span[^>]+class=["\']subtitle["\'][^>]*>(.*?)</span>\s*</header>', html, flags=re.I | re.S,
    )
    if not subtitle:
        return {}
    facts: dict = {}
    paragraphs = [rc.clean(paragraph) for paragraph in re.findall(r"<p[^>]*>(.*?)</p>", subtitle, re.I | re.S)]
    language = next(
        (paragraph for paragraph in paragraphs if re.search(r"\b(?:Sprache|Übertitel)", paragraph)), "",
    )
    if language:
        facts["language"] = language.strip(" –-")
    credits = [
        f"{rc.clean(label)}: {rc.clean(name)}"
        for label, name in re.findall(
            r"<strong>([^<]+)</strong>\s*:\s*(.*?)(?=\||</p>)", subtitle, re.I | re.S,
        )
        if rc.clean(label) and rc.clean(name)
    ]
    if credits:
        facts["performers"] = credits
    trailing = rc.clean(re.split(r"</p>", subtitle, flags=re.I)[-1])
    age = re.search(r"Altersempfehlung\s+ab\s+(\d{1,2})\s+Jahren?\b|(?<![\d.,])(\d{1,2})\+(?!\d)", trailing)
    if age:
        facts["age"] = f"ab {age.group(1) or age.group(2)} Jahren"
    return facts


def _details(item: dict, page: str) -> dict:
    details = _subtitle_facts(page) if page else {}
    performance = _PERFORMANCE_NOTES.get(rc.clean(item.get("status", "")).casefold())
    if performance:
        details["performance"] = performance
    video = rc.clean(item.get("video_url", ""))
    if video:
        details["video_url"] = video
    return details


def events_from_payload(items: list[dict], detail_fetcher: Callable[[str], str] | None = None) -> list[dict]:
    detail_pages = _detail_pages(items, detail_fetcher)
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
        programme_link = _programme_link(item)
        page = detail_pages.get(programme_link, "") if programme_link else ""
        if not description and page:
            description = _detail_description(page)
        description_generated = not description
        if description_generated:
            description = common.factual_event_description(
                title, date_value=start, time_text=time_text, venue=venue, city="Bonn"
            )
        category_hint = _category(item)
        if category_hint.startswith("theater") and description_generated:
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
            description_source="generated" if description_generated else "scraped",
        )
        if event:
            availability = _TICKET_AVAILABILITY.get(
                rc.clean(ticket.get("status", "") if isinstance(ticket, dict) else "").casefold()
            )
            if availability:
                event["availability"] = availability
            details = _details(item, page)
            if details:
                event["details"] = details
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
        payload = common.fetch_json(_API, timeout=30)
        items = payload if isinstance(payload, list) else payload.get("events", [])
        return events_from_payload(items)
    except Exception as exc:
        common.log_source_error(_SOURCE, exc)
        return []
