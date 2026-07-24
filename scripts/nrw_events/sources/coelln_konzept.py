"""Flea and antique market dates from the direct organizer Cölln Konzept."""

import re
from datetime import datetime

from .. import common
from ..dates import MONTH_DE
from . import regional_common as rc


_BASE_URL = "https://www.coelln-konzept.de/"
_URL = rc.abs_url(_BASE_URL, "termine.php")
_MARKET_WORDS = re.compile(r"floh|trödel|antik|mädchenkram|sammler", re.I)


def _date_range(text: str, year: int):
    cleaned = rc.clean(text)
    shared = re.search(r"(\d{1,2})\./(\d{1,2})\.\s*([A-Za-zäöüÄÖÜ]+)", cleaned)
    if shared:
        first, last, month_name = shared.groups()
        month = MONTH_DE.get(month_name.casefold().rstrip("."))
        if month:
            return datetime(year, month, int(first)), datetime(year, month, int(last))

    matches = re.findall(r"(\d{1,2})\.\s*([A-Za-zäöüÄÖÜ]+)", cleaned)
    dates = []
    for day, month_name in matches:
        month = MONTH_DE.get(month_name.casefold().rstrip("."))
        if month:
            dates.append(datetime(year, month, int(day)))
    return (dates[0], dates[-1]) if dates else (None, None)


def _detail_context(html: str) -> dict:
    heading = re.search(r"<h2[^>]*>(.*?)</h2>", html or "", re.S | re.I)
    first_description = re.search(
        r"<h2[^>]*>.*?</h2>\s*<p[^>]+class=['\"][^'\"]*\btextmarkt\b[^'\"]*['\"][^>]*>(.*?)</p>",
        html or "",
        re.S | re.I,
    )
    location = re.search(
        r"<h3[^>]*>\s*Standort:\s*</h3>\s*<p[^>]+class=['\"][^'\"]*\btextmarkt\b[^'\"]*['\"][^>]*>(.*?)</p>",
        html or "",
        re.S | re.I,
    )
    description = common.concise_description(rc.clean(first_description.group(1) if first_description else ""))
    venue = rc.clean(location.group(1) if location else "")
    time_match = re.search(
        r"(?:von|Marktzeit(?:en)?[^\d]{0,20})\s*(\d{1,2})(?::(\d{2}))?\s*"
        r"(?:bis|[-–])\s*(\d{1,2})(?::(\d{2}))?\s*Uhr",
        f"{description} {rc.clean(html)}",
        re.I,
    )
    time_text = ""
    if time_match:
        start_hour, start_minute, end_hour, end_minute = time_match.groups()
        time_text = f"{int(start_hour):02d}:{start_minute or '00'}–{int(end_hour):02d}:{end_minute or '00'}"
    return {
        "title": rc.clean(heading.group(1) if heading else ""),
        "description": description,
        "venue": venue,
        "time": time_text,
    }


def _events_from_listing(html: str, detail_fetcher=None) -> list:
    events = []
    detail_cache = {}
    year = common.TODAY.year
    rows = re.findall(r"<tr\b[^>]*>(.*?)</tr>", html or "", re.S | re.I)
    for row in rows:
        year_match = re.search(r"Termine\s+(20\d{2})", rc.clean(row), re.I)
        if year_match:
            year = int(year_match.group(1))
            continue
        date_match = re.search(r'<td[^>]+class="datum"[^>]*>(.*?)</td>', row, re.S | re.I)
        market_match = re.search(
            r'<a[^>]+class=[\'\"]linkmarkt[\'\"][^>]+href=[\'\"]([^\'\"]+)[\'\"][^>]*>(.*?)</a>',
            row,
            re.S | re.I,
        )
        if not (date_match and market_match):
            continue
        title = rc.clean(market_match.group(2))
        if not _MARKET_WORDS.search(title):
            continue
        start, end = _date_range(date_match.group(1), year)
        if not start:
            continue
        link = rc.abs_url(_BASE_URL, market_match.group(1))
        if link not in detail_cache:
            try:
                detail_cache[link] = (
                    _detail_context(detail_fetcher(link))
                    if detail_fetcher and common.window_contains(start, end) else {}
                )
            except Exception as exc:
                common.log_source_error("Cölln Konzept detail", exc)
                detail_cache[link] = {}
        detail = detail_cache[link]
        venue = detail.get("venue") or title
        city = rc.city_from_text(f"{venue} {title}", "Köln")
        description = detail.get("description") or common.factual_event_description(
            title,
            date_value=start,
            time_text=detail.get("time", ""),
            venue=venue,
            city=city,
        )
        event = common.make_event(
            title,
            start,
            end,
            venue,
            city,
            description,
            link,
            "Cölln Konzept",
            "flohmarkt trödelmarkt antikmarkt second hand markt",
            0.96,
            detail.get("time", ""),
        )
        if event:
            events.append(event)
    return rc.dedupe(events)


def fetch() -> list:
    def parse(html: str) -> list:
        return _events_from_listing(
            html,
            detail_fetcher=lambda link: common.fetch_detail_url(
                link,
                cache_namespace="coelln-konzept",
                timeout=20,
            ),
        )

    return rc.fetch_html_events("Cölln Konzept", _URL, parse, timeout=20)
