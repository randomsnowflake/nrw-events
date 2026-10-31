"""Official event list of the Rock & Pop Zentrum (RPZ) Bonn."""

import re
from datetime import datetime

from .. import event_builder, text
from . import regional_common as rc

_SOURCE = "RPZ Bonn"
_SOURCE_ID = "rpz-bonn"
_URL = "https://rpz-bonn.de/"
_VENUE = "RPZ Bonn"
_ITEM = re.compile(r'<div class="qodef-el-item\b.*?(?=<div class="qodef-el-item\b|</section>|\Z)', re.S)
_LINK = re.compile(r'class="qodef-eli-title">\s*<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', re.S)
_LABEL = re.compile(r'<span class="qodef-eli-info-label">(.*?)</span>', re.S)
_DATED_TITLE = re.compile(r"^(\d{2})\.(\d{2})\.(\d{4})\s*[–—-]\s*(.+)$", re.S)
_LEADING_FACTS = re.compile(
    r"^(?:(?:Wann:|Datum:)?\s*\d{2}\.\d{2}\.\d{4}\s*|Einlass:\s*[\d.:]+\s*Uhr\s*/*\s*|Beginn:\s*[\d.:]+\s*Uhr\s*"
    r"|(?:Eintritt:|Tickets:|VK:|AK:|Vvk\.?:?|Ak\.?:?|/+)\s*(?:[\d,]+\s*(?:€|Euro)\s*)?(?:an der Abendkasse\s*)?)+",
    re.I,
)


def events_from_html(html: str) -> list:
    events = []
    for item in _ITEM.findall(html or ""):
        link = _LINK.search(item)
        dated = _DATED_TITLE.match(rc.clean(link.group(2))) if link else None
        if not (link and dated):
            continue
        day, month, year, title = dated.groups()
        labels = [rc.clean(label) for label in _LABEL.findall(item)]
        start_label = next((label for label in labels if "Beginn" in label), "")
        clock = re.search(r"Beginn:\s*(\d{1,2})[.:](\d{2})", start_label)
        time_text = f"{clock.group(1)}:{clock.group(2)}" if clock else ""
        start = rc.with_time(datetime(int(year), int(month), int(day)), time_text)
        event = event_builder.make_event(
            title, start, None, _VENUE, "Bonn",
            text.factual_event_description(title, date_value=start, time_text=time_text, venue=_VENUE, city="Bonn"),
            link.group(1), _SOURCE,
            "konzert musik rock pop", 1.0, time_text,
            source_id=_SOURCE_ID, default_category_key="concert",
        )
        if not event:
            continue
        price = next((label for label in labels if "€" in label or "Euro" in label), "")
        if price:
            event["price"] = re.sub(r"^Eintritt:\s*", "", price)
            event["admission_basis"] = "explicit"
        events.append(event)
    return rc.dedupe_occurrences(events)


def _detail_description(html: str, _event: dict) -> dict:
    body = re.search(r'class="qodef-ttevent-single-content[^"]*"[^>]*>(.*?)(?:<div class="qodef-|\Z)', html or "", re.S)
    lines = [rc.clean(line) for line in re.findall(r"<p\b[^>]*>(.*?)</p>", body.group(1) if body else "", re.S)]
    # The text opens with date, doors and price, which the event already carries.
    return {"description": text.concise_description(_LEADING_FACTS.sub("", " ".join(filter(None, lines))))}


def _parse(html: str) -> list:
    return rc.enrich_descriptions(
        events_from_html(html),
        source=_SOURCE,
        cache_namespace="rpz-bonn-v1",
        extract_context=_detail_description,
        fallback=lambda event: event.get("description", ""),
        needs_enrichment=lambda _event: True,
    )


def fetch() -> list:
    return rc.fetch_html_events(_SOURCE, _URL, _parse, source_id=_SOURCE_ID)
