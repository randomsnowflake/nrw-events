"""Official performance calendar for Theater Marabu."""

import re

from .. import common
from . import regional_common as rc


_SOURCE = "Theater Marabu"
_CALENDAR = "https://www.theater-marabu.de/kalender/"
_CATEGORY = "theater bühne performance tanz"
_TRUST = 1.0
_MONTHS = {
    "JAN": 1, "FEB": 2, "MÄR": 3, "MRZ": 3, "APR": 4, "MAI": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OKT": 10, "NOV": 11, "DEZ": 12,
}


def _meta_description(html: str) -> str:
    patterns = (
        r'<meta[^>]+(?:property|name)=["\'](?:og:description|description)["\'][^>]+content=(["\'])(.*?)\1',
        r'<meta[^>]+content=(["\'])(.*?)\1[^>]+(?:property|name)=["\'](?:og:description|description)["\']',
    )
    for pattern in patterns:
        match = re.search(pattern, html, re.I)
        if match:
            return common.concise_description(match.group(2))
    editor = re.search(r'elementor-widget-text-editor[^>]*>.*?<div[^>]*>(.*?)</div>', html, re.I | re.S)
    return common.concise_description(editor.group(1)) if editor else ""


def _detail_description(url: str) -> str:
    try:
        return _meta_description(common.fetch_detail_url(
            url, cache_namespace="theater-marabu", timeout=15
        ))
    except Exception as exc:
        common.log_source_error(f"{_SOURCE} detail", exc)
        return ""


def _date(block: str):
    explicit = re.search(r'data-vorstellung=["\'][^"\']*\|\s*(\d{2}\.\d{2}\.20\d{2})', block, re.I)
    if explicit:
        return common.parse_date(explicit.group(1))
    date = re.search(r'class=["\']spieltermin-datum["\'][^>]*>.*?<span>(\d{1,2})</span>\s*([A-ZÄÖÜ]{3})', block, re.I | re.S)
    if not date:
        return None
    day, month_name = date.groups()
    month = _MONTHS.get(month_name.upper())
    if not month:
        return None
    return rc.date_for_window(int(day), month)


def events_from_html(html: str, detail_fetcher=None) -> list[dict]:
    detail_fetcher = detail_fetcher or _detail_description
    starts = [match.start() for match in re.finditer(r'<li\s+class=["\'][^"\']*spieltermin-item', html, re.I)]
    events = []
    for index, start_pos in enumerate(starts):
        block = html[start_pos:starts[index + 1] if index + 1 < len(starts) else len(html)]
        submeta_match = re.search(r'class=["\']spieltermin-submeta["\'][^>]*>(.*?)</div>', block, re.I | re.S)
        submeta = rc.clean(submeta_match.group(1)) if submeta_match else ""
        if not re.search(r'\bBonn\b|Theater\s+Marabu|Brotfabrik', submeta, re.I):
            continue
        title_match = re.search(
            r'class=["\']spieltermin-title["\'][^>]*>\s*(?:<a[^>]+href=["\']([^"\']+)["\'][^>]*>)?(.*?)(?:</a>)?\s*</div>',
            block, re.I | re.S,
        )
        if not title_match:
            continue
        href, title_html = title_match.groups()
        title = rc.clean(title_html)
        start_dt = _date(block)
        time_match = re.search(r'class=["\']spieltermin-meta["\'][^>]*>.*?(\d{1,2}:\d{2})\s*Uhr', block, re.I | re.S)
        time_text = time_match.group(1) if time_match else ""
        start_dt = rc.with_time(start_dt, time_text)
        if not title or not start_dt:
            continue
        link = rc.abs_url(_CALENDAR, href) if href else _CALENDAR
        description = detail_fetcher(link) if href else ""
        venue = "Brotfabrik Bonn" if "brotfabrik" in submeta.casefold() else "Theater Marabu"
        if description:
            description = common.concise_description(
                f"Tanztheater-Performance auf der Bühne. {description}"
            )
        else:
            description = common.factual_event_description(
                title, date_value=start_dt, time_text=time_text, venue=venue, city="Bonn"
            )
        if submeta and submeta.casefold() not in description.casefold():
            description = common.concise_description(f"{description} {submeta}")
        event = common.make_event(
            title, start_dt, None, venue, "Bonn", description, link, _SOURCE,
            _CATEGORY, _TRUST, time_text,
            source_id="theater-marabu",
        )
        if event:
            events.append(event)
    return rc.dedupe_occurrences(events)


def fetch() -> list[dict]:
    return rc.fetch_html_events(_SOURCE, _CALENDAR, events_from_html)
