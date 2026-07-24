"""Weekly first-party Lampert flea-market dates for Bonn Siemensstraße."""

import re
import urllib.parse
from datetime import datetime, timedelta

from .. import common
from . import regional_common as rc


_URL = "https://lampert-maerkte.de/53121-bonn-an-der-ehem-biskuithalle/"
_SOURCE = "Lampert Märkte"
_SOURCE_ID = "lampert-bonn-siemensstrasse"
_TITLE = "Flohmarkt Bonn Siemensstraße"


def _entry_content(html: str) -> str:
    match = re.search(
        r'<div[^>]+class="[^"]*\bentry-content\b[^"]*"[^>]*>(.*?)</div>',
        html or "",
        re.S | re.I,
    )
    return match.group(1) if match else ""


def _schedule_from_page(html: str):
    content = _entry_content(html)
    clean_content = rc.clean(content)
    heading = rc.first_group_clean(r'<h1[^>]+class="[^"]*\bentry-title\b[^"]*"[^>]*>(.*?)</h1>', html)
    decoded = urllib.parse.unquote(content)
    address_match = re.search(r"Siemensstra(?:ße|sse)(?:\+|\s)+26\b", decoded, re.I)
    hours_match = re.search(
        r"jeden\s+Samstag\s+(\d{1,2}):(\d{2})\s*[-–]\s*(\d{1,2}):(\d{2})\s*Uhr",
        clean_content,
        re.I,
    )
    goods_match = re.search(r"alle\s+typischen\s+Waren\s+erlaubt\s*\(([^)]+)\)", clean_content, re.I)
    schedule_match = re.search(
        r"Termine\s+(20\d{2})(.*?)(?=</p>|<h[1-6]\b|$)",
        content,
        re.S | re.I,
    )
    if not (
        content
        and "bonn" in heading.casefold()
        and "siemensstrasse" in heading.casefold()
        and address_match
        and hours_match
        and goods_match
        and schedule_match
        and re.search(r"\bjeden\s+Samstag\b", rc.clean(schedule_match.group(2)), re.I)
    ):
        return None

    year = int(schedule_match.group(1))
    exception_dates = set()
    for day, month in re.findall(r"\b(\d{1,2})\.(\d{1,2})\.", rc.clean(schedule_match.group(2))):
        try:
            exception_dates.add(datetime(year, int(month), int(day)).date())
        except ValueError:
            return None

    start_hour, start_minute, end_hour, end_minute = (int(value) for value in hours_match.groups())
    return {
        "year": year,
        "start_hour": start_hour,
        "start_minute": start_minute,
        "end_hour": end_hour,
        "end_minute": end_minute,
        "goods": goods_match.group(1),
        "exceptions": exception_dates,
    }


def _events_from_page(html: str, *, strict: bool = False) -> list:
    schedule = _schedule_from_page(html)
    if schedule is None:
        if strict:
            raise rc.ParserEmptyError("Lampert recurrence contract changed")
        return []

    year = schedule["year"]
    window_start = max(common.TODAY.date(), datetime(year, 1, 1).date())
    window_end = min(common.END_DATE.date(), datetime(year, 12, 31).date())
    if window_start > window_end:
        return []

    first_saturday = window_start + timedelta(days=(5 - window_start.weekday()) % 7)
    time_text = (
        f"{schedule['start_hour']:02d}:{schedule['start_minute']:02d}–"
        f"{schedule['end_hour']:02d}:{schedule['end_minute']:02d}"
    )
    description = (
        "Flohmarkt an der ehemaligen Biskuithalle in Bonn-Dransdorf; "
        "jeden Samstag außer an den vom Veranstalter genannten Feiertagen. "
        f"Erlaubte Waren laut Veranstalter: {schedule['goods']}."
    )
    events = []
    occurrence = first_saturday
    while occurrence <= window_end:
        if occurrence not in schedule["exceptions"]:
            start = datetime(
                occurrence.year,
                occurrence.month,
                occurrence.day,
                schedule["start_hour"],
                schedule["start_minute"],
            )
            end = datetime(
                occurrence.year,
                occurrence.month,
                occurrence.day,
                schedule["end_hour"],
                schedule["end_minute"],
            )
            event = common.make_event(
                _TITLE,
                start,
                end,
                "Ehemalige Biskuithalle, Siemensstraße 26",
                "Dransdorf",
                description,
                _URL,
                _SOURCE,
                "flohmarkt trödelmarkt markt",
                1.0,
                time_text,
                source_id=_SOURCE_ID,
            )
            if event:
                events.append(event)
        occurrence += timedelta(days=7)
    return events


def fetch() -> list:
    return rc.fetch_html_events(
        _SOURCE,
        _URL,
        lambda html: _events_from_page(html, strict=True),
        timeout=20,
        source_id=_SOURCE_ID,
        empty_is_healthy=True,
    )
