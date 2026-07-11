"""Shared helpers for regional Bonn/Rhein-Sieg source scrapers."""

import re
import urllib.parse
from datetime import datetime
from html import unescape
from .. import common
from ..dates import MONTH_DE, MONTH_EN
from ..source_types import TextParser

_MONTH = {
    **MONTH_DE,
    **MONTH_EN,
    "mar": 3, "mär": 3, "sept": 9, "oct": 10, "dec": 12,
}


def abs_url(base: str, href: str) -> str:
    return urllib.parse.urljoin(base, unescape(href or "").strip())


def clean(text: str) -> str:
    return common.clean_html(unescape(text or ""))


def parse_dt(text: str):
    text = clean(text)
    dt = common.parse_date(text)
    if dt:
        return dt
    m = re.search(r"(\d{1,2})\.\s*([A-Za-zäöüÄÖÜ.]+)\s*(20\d{2})", text)
    if not m:
        return None
    day, month, year = m.groups()
    mon = _MONTH.get(month.lower().rstrip("."))
    return datetime(int(year), mon, int(day)) if mon else None


def with_time(dt, text: str):
    if not dt:
        return None
    m = re.search(r"(\d{1,2}):(\d{2})", text or "")
    return dt.replace(hour=int(m.group(1)), minute=int(m.group(2))) if m else dt


def date_for_window(day: int, month: int):
    """Resolve a yearless day/month to the year that keeps it current.

    Listings that omit the year (e.g. the LVR calendar) otherwise default to
    ``TODAY.year``; during a late-December run a January date then lands in the
    year that is ending and gets dropped as stale. Pick the first of this year /
    next year that is not already past so the New-Year rollover is handled.
    """
    for year in (common.TODAY.year, common.TODAY.year + 1):
        try:
            dt = datetime(year, month, day)
        except ValueError:
            return None
        if dt >= common.TODAY:
            return dt
    return None


def time_text(text: str) -> str:
    times = re.findall(r"\d{1,2}:\d{2}", text or "")
    if len(times) >= 2:
        return f"{times[0]}–{times[1]}"
    return times[0] if times else ""


def city_from_text(text: str, default_city: str) -> str:
    return common.guess_city_from_text(text) or default_city


def dedupe(events: list) -> list:
    seen, out = set(), []
    for ev in events:
        key = (ev["source"], ev["title"].lower(), ev["date"], ev["city"].lower())
        if key in seen:
            continue
        seen.add(key)
        out.append(ev)
    return out


def title_from_href(href: str) -> str:
    slug = urllib.parse.urlparse(unescape(href or "")).path.rstrip("/").split("/")[-1]
    slug = re.sub(r"\.(?:html|php)$", "", slug)
    slug = re.sub(r"[-_]+", " ", slug)
    return slug.strip().title()


def range_dates(text: str):
    text = clean(text)
    m = re.search(r"(\d{1,2})\.(\d{1,2})\.\s*[–-]\s*(\d{1,2})\.(\d{1,2})\.(20\d{2})", text)
    if m:
        start_day, start_month, end_day, end_month, year = (int(part) for part in m.groups())
        return datetime(year, start_month, start_day), datetime(year, end_month, end_day)
    dates = re.findall(r"\d{1,2}\.\d{1,2}\.20\d{2}", text)
    if dates:
        start = common.parse_date(dates[0])
        end = common.parse_date(dates[-1]) if len(dates) > 1 else start
        return start, end
    return parse_dt(text), None


def fetch_html_events(name: str, url: str, parser: TextParser, timeout: int = 25) -> list:
    try:
        events = parser(common.fetch_url(url, timeout=timeout))
        common._record_endpoint(url, parser_type="html", parsed_event_count=len(events),
                                parser_empty=not bool(events))
        return events
    except Exception as e:
        common.log_source_error(name, e)
        return []
