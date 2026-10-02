"""Official event calendar of the Beethoven-Haus Bonn.

Month lists carry date, time, category and title; detail pages add the
description and ticket prices through the shared persistent TTL cache. The
list repeats every exhibition as a daily opening-hours entry; those are skipped.
"""

import re
from datetime import datetime

from .. import event_builder, http, run_state, text
from . import regional_common as rc

_SOURCE = "Beethoven-Haus Bonn"
_SOURCE_ID = "beethoven-haus-bonn"
_BASE = "https://www.beethoven.de"
_CACHE_NAMESPACE = "beethoven-haus-detail"
_CATEGORY = "konzert klassik kammermusik museum"
_TRUST = 1.0
_VENUE = "Beethoven-Haus Bonn"
_HALL = "Kammermusiksaal Beethoven-Haus"
_ITEM = re.compile(
    r'<div class="js-event-item[^"]*\bevent-item-(?P<day>\d{8})\b[^"]*"[^>]*>'
    r'\s*<a href="(?P<href>/de/termine/view/[^"]+)"[^>]*>(?P<body>.*?)</a>',
    re.S,
)
_PRICE = re.compile(r"\b(?:Karten|Eintritt|Preis)\b", re.I)


def _list_url(month: datetime) -> str:
    return f"{_BASE}/de/termine/list?bymonth={month:%Y%m}01"


def _months() -> list[datetime]:
    window = run_state.runtime_window()
    current = window.start.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    months = []
    while current <= window.end:
        months.append(current)
        current = current.replace(year=current.year + current.month // 12, month=current.month % 12 + 1)
    return months


def events_from_list(html: str) -> list[dict]:
    events = []
    for match in _ITEM.finditer(html):
        body = match["body"]
        category = rc.first_group_clean(r'<span class="terminlistcatname">(.*?)</span>', body)
        if category.casefold().startswith("ausstellung"):
            continue
        title = rc.first_group_clean(r"<h2[^>]*>(.*?)</h2>", body)
        performers = rc.first_group_clean(r"<strong>(.*?)</strong>", body)
        if performers and performers.casefold() not in title.casefold():
            title = f"{title}: {performers}"
        time_text = rc.time_text(rc.first_group_clean(r'<div class="is-dateline">(.*?)</div>', body))
        start = rc.with_time(datetime.strptime(match["day"], "%Y%m%d"), time_text)
        event = event_builder.make_event(
            title, start, None, _VENUE, "Bonn", "",
            rc.abs_url(_BASE, match["href"]), _SOURCE, f"{category} {_CATEGORY}", _TRUST, time_text,
            source_id=_SOURCE_ID,
        )
        if event:
            events.append(event)
    return rc.dedupe_occurrences(events)


def _parse_detail(html: str, _event: dict | None = None) -> dict:
    blocks = [rc.clean(block) for block in re.findall(r'<div class="termin-descr">(.*?)</div>', html, re.S)]
    descriptions = [block for block in blocks if block and not _PRICE.match(block)]
    price = next((block for block in blocks if _PRICE.match(block)), "")
    description = text.concise_description(descriptions[0]) if descriptions else ""
    return {
        "description": description,
        "price": price,
        "venue": _HALL if re.search(r"\bKammermusiksaal\b", description) else "",
    }


def _merge_detail(event: dict, context: dict) -> dict:
    enriched = dict(event)
    if context.get("venue"):
        enriched["venue"] = context["venue"]
    if context.get("price"):
        enriched["price"] = context["price"][:160]
        enriched["admission_basis"] = "explicit"
    return enriched


def fetch() -> list[dict]:
    events: list[dict] = []
    for month in _months():
        try:
            events.extend(events_from_list(http.fetch_url(_list_url(month), timeout=25)))
        except Exception as exc:  # noqa: PERF203 - one failed month must not drop the others
            run_state.log_source_error(f"{_SOURCE} {month:%Y-%m}", exc, source_id=_SOURCE_ID)
    return rc.enrich_descriptions(
        events,
        source=_SOURCE,
        cache_namespace=_CACHE_NAMESPACE,
        extract_context=_parse_detail,
        fallback=lambda event: event.get("description", ""),
        needs_enrichment=lambda _event: True,
        merge_context=_merge_detail,
    )
