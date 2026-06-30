"""Rausgegangen Bonn nightlife listings."""

import re
from datetime import datetime
from html import unescape

from .. import common


URL = "https://rausgegangen.de/bonn/kategorie/party/"


def fetch() -> list:
    source = "Rausgegangen Party"
    try:
        html = common.fetch_url(URL, timeout=25)
    except Exception as e:
        common.log_source_error(source, e)
        return []
    return events_from_party_page(html)


def events_from_party_page(html: str) -> list:
    events = []
    seen = set()
    for block in re.findall(
        r'<div class="tile tile-medium hover-lift" data-testid="event-tile">(.*?)</div>\s*</div>\s*</div>\s*</a>\s*</div>',
        html,
        re.S,
    ):
        href = _match_text(r'data-testid="event-tile-link"[^>]+href="([^"]+)"', block)
        title = _match_text(r'data-testid="event-tile-name">(.*?)</span>', block)
        venue = _match_text(r'data-testid="event-tile-location">(.*?)</p>', block)
        price = _match_text(r'data-testid="event-tile-price">(.*?)</p>', block)
        dt = re.search(
            r'data-testid="event-tile-datetime".*?<span[^>]*>(.*?)</span>\s*<span[^>]*>(.*?)</span>',
            block,
            re.S,
        )
        if not (href and title and dt):
            continue

        start = _parse_tile_datetime(_clean(dt.group(1)), _clean(dt.group(2)))
        link = common.urllib.parse.urljoin("https://rausgegangen.de", href)
        key = (link, start.isoformat() if start else "")
        if key in seen:
            continue
        seen.add(key)

        event = common.make_event(
            title,
            start,
            start,
            venue,
            "Bonn",
            "",
            link,
            "Rausgegangen Party",
            "party nightlife dj club tanz",
            trust=0.82,
            time_text=_clean(dt.group(2)),
        )
        if event:
            event["price"] = price
            events.append(_with_nightlife_category(event))
    return events


def _parse_tile_datetime(date_text: str, time_text: str):
    match = re.search(r"(\d{1,2})\.\s*([A-Za-zäöüÄÖÜ]+)", date_text)
    if not match:
        return None
    day, month = match.groups()
    month_num = common.MONTH_DE.get(month.lower().rstrip("."))
    if not month_num:
        return None

    year = common.TODAY.year
    if month_num < common.TODAY.month - 1:
        year += 1
    hour, minute = 0, 0
    time_match = re.search(r"(\d{1,2}):(\d{2})", time_text)
    if time_match:
        hour, minute = map(int, time_match.groups())
    try:
        return datetime(year, month_num, int(day), hour, minute)
    except ValueError:
        return None


def _match_text(pattern: str, text: str) -> str:
    match = re.search(pattern, text, re.S)
    return _clean(match.group(1)) if match else ""


def _clean(value: str) -> str:
    return unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", value or ""))).strip()


def _with_nightlife_category(event: dict) -> dict:
    return {
        **event,
        "category_key": "nightlife",
        "category_label": "Nachtleben & Party",
        "category_confidence": max(event.get("category_confidence", 0), 0.84),
        "category_reason": f"source:Rausgegangen Party; {event.get('category_reason', '')}".strip(),
    }
