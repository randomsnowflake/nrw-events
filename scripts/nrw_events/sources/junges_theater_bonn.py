"""Official monthly performance calendar for Junges Theater Bonn."""

import re
from datetime import datetime
from urllib.parse import urlencode

from .. import common
from . import regional_common as rc


_SOURCE = "Junges Theater Bonn"
_ROOT = "https://www.jt-bonn.de/"
_CALENDAR = f"{_ROOT}termine-tickets/"
_CATEGORY = "theater bühne schauspiel musical familie"
_TRUST = 1.0
_VENUES = ("Junges Theater Bonn", "Kuppelsaal Thalia", "Online-Stream")


def _months() -> list[datetime]:
    current = common.TODAY.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    end = common.END_DATE.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    result = []
    while current <= end:
        result.append(current)
        current = current.replace(
            year=current.year + (current.month == 12),
            month=1 if current.month == 12 else current.month + 1,
        )
    return result


def _url(month: datetime) -> str:
    query = urlencode({
        "tx_theatre_event[monthYear]": month.strftime("%m-%Y"),
        "tx_theatre_event[action]": "eventList",
        "tx_theatre_event[controller]": "Event",
    })
    return f"{_CALENDAR}?{query}"


def _meta_description(html: str) -> str:
    patterns = (
        r'<meta[^>]+(?:property|name)=["\'](?:og:description|description)["\'][^>]+content=(["\'])(.*?)\1',
        r'<meta[^>]+content=(["\'])(.*?)\1[^>]+(?:property|name)=["\'](?:og:description|description)["\']',
    )
    for pattern in patterns:
        match = re.search(pattern, html, re.I)
        if match:
            return common.concise_description(match.group(2))
    return ""


def _detail_description(url: str) -> str:
    try:
        return _meta_description(common.fetch_detail_url(
            url, cache_namespace="junges-theater-bonn", timeout=15
        ))
    except Exception as exc:
        common.log_source_error(f"{_SOURCE} detail", exc)
        return ""


def events_from_html(html: str, detail_fetcher=None) -> list[dict]:
    detail_fetcher = detail_fetcher or _detail_description
    starts = [match.start() for match in re.finditer(r'<div\s+class=["\']event-list-rowflex["\']', html, re.I)]
    events = []
    for index, start_pos in enumerate(starts):
        row = html[start_pos:starts[index + 1] if index + 1 < len(starts) else len(html)]
        date_match = re.search(r'class=["\']cal-date["\'][^>]*>(.*?)</div>', row, re.I | re.S)
        date_value = common.parse_date(rc.clean(date_match.group(1))) if date_match else None
        if not date_value:
            continue
        columns = re.split(r'<div\s+class=["\']event-flex-item[^"\']*["\'][^>]*>', row, flags=re.I)[1:4]
        for column_index, column in enumerate(columns):
            venue = _VENUES[column_index]
            items = re.split(r'<div\s+class=["\']cal-list-item\s+clearfix["\'][^>]*>', column, flags=re.I)[1:]
            for item in items:
                anchor = re.search(
                    r'class=["\']event-title["\'][^>]*>\s*<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
                    item, re.I | re.S,
                )
                if not anchor:
                    continue
                href, anchor_title = anchor.groups()
                title = rc.clean(anchor_title)
                loose = re.search(
                    r'class=["\']tickets\s+pull-left["\'][^>]*>(.*?)(?:class=["\']tickets\s+pull-right|$)',
                    item, re.I | re.S,
                )
                loose_title = rc.clean(loose.group(1)) if loose else ""
                loose_title = re.sub(r'^\s*\d{1,2}:\d{2}\s*(?:Uhr)?\s*', '', loose_title, flags=re.I)
                loose_title = re.sub(r'\bausverkauft\b', '', loose_title, flags=re.I).strip(' |–-')
                loose_title = re.sub(r'\s*<div\s*$', '', loose_title, flags=re.I).strip()
                is_kulturgarten = title.casefold().startswith("jtb im kulturgarten") and bool(loose_title)
                if is_kulturgarten:
                    venue, title = title, loose_title
                time_match = re.search(r'(\d{1,2}:\d{2})\s*Uhr', item, re.I)
                time_text = time_match.group(1) if time_match else ""
                start_dt = rc.with_time(date_value, time_text)
                link = rc.abs_url(_ROOT, href)
                if is_kulturgarten:
                    ticket = re.search(
                        r'class=["\'][^"\']*ticket-button[^"\']*["\'][^>]*'
                        r'href=["\']([^"\']+)["\']',
                        item,
                        re.I,
                    )
                    if ticket:
                        link = rc.abs_url(_ROOT, ticket.group(1))
                description = detail_fetcher(link) if link and not is_kulturgarten else ""
                if not description:
                    description = common.factual_event_description(
                        title, date_value=start_dt, time_text=time_text, venue=venue, city="Bonn"
                    )
                event = common.make_event(
                    title, start_dt, None, venue, "Bonn", description, link, _SOURCE,
                    _CATEGORY, _TRUST, time_text,
                    source_id="junges-theater-bonn",
                )
                if event:
                    events.append(event)
    return rc.dedupe_occurrences(events)


def fetch() -> list[dict]:
    events = []
    for month in _months():
        try:
            events.extend(events_from_html(common.fetch_url(_url(month), timeout=25)))
        except Exception as exc:
            common.log_source_error(f"{_SOURCE} {month:%Y-%m}", exc)
    return rc.dedupe_occurrences(events)
