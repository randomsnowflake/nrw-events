"""Shared helpers for regional Bonn/Rhein-Sieg source scrapers."""

import re
import urllib.parse
from datetime import datetime
from html import unescape
from typing import Callable

from .. import common

_MONTH = {
    **common.MONTH_DE,
    **common.MONTH_EN,
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
    dates = re.findall(r"\d{1,2}\.\d{1,2}\.20\d{2}", text)
    if dates:
        start = common.parse_date(dates[0])
        end = common.parse_date(dates[-1]) if len(dates) > 1 else start
        return start, end
    return parse_dt(text), None


def fetch_html_events(name: str, url: str, parser: Callable[[str], list], timeout: int = 25) -> list:
    try:
        return parser(common.fetch_url(url, timeout=timeout))
    except Exception as e:
        common.log_source_error(name, e)
        return []
