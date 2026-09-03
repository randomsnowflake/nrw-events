"""Dorf-Flohmarkt dates from the Bürgerverein Rossel-Wilberhofen."""

import re
from datetime import datetime

from .. import common
from . import regional_common as rc

_SOURCE = "Bürgerverein Rossel-Wilberhofen"
_SOURCE_ID = "rossel-wilberhofen-dorfflohmarkt"
_NEWS_URL = "https://www.rossel-wilberhofen.com/deutsch/aktuelles-1/"
_CALENDAR_URL = "https://www.rossel-wilberhofen.com/deutsch/termine/"
_PAGE_SEPARATOR = "<!-- NRW-EVENTS-CALENDAR -->"


def _events_from_pages(news_html: str, calendar_html: str, *, strict: bool = False) -> list:
    news = common.clean_html(news_html or "")
    calendar = common.clean_html(calendar_html or "")
    year_match = re.search(r"Termine(?: und Veranstaltungen)?\s+(20\d{2})", calendar, re.I)
    calendar_match = re.search(
        r"(\d{1,2})\.(\d{1,2})\.\s+ab\s+09:30.*?Traditionelles\s+Rochusfest",
        calendar,
        re.I,
    )
    event_match = re.search(
        r"Am\s+Sonntag\s*,\s+den\s+(\d{1,2})\.\s+August\s+findet\s+zudem\s+"
        r"in\s+der\s+Zeit\s+zwischen\s+(\d{1,2})[.:](\d{2})\s*[-–]\s*"
        r"(\d{1,2})[.:](\d{2})\s+Uhr\s+wieder\s+ein\s+Dorf-Flohmarkt\s+"
        r"im\s+gesamten\s+Ort\s+statt",
        news,
        re.I,
    )
    organizer_match = re.search(r"Bürgerverein\s+Rossel-Wilberhofen", news_html or "", re.I)
    if not (year_match and calendar_match and event_match and organizer_match):
        if strict:
            raise rc.ParserEmptyError("Rossel-Wilberhofen date, time, or organizer contract changed")
        return []

    year = int(year_match.group(1))
    calendar_day, calendar_month = (
        int(value) for value in calendar_match.groups()
    )
    day, start_hour, start_minute, end_hour, end_minute = (
        int(value) for value in event_match.groups()
    )
    try:
        start = datetime(year, 8, day, start_hour, start_minute)
        end = datetime(year, 8, day, end_hour, end_minute)
    except ValueError as exc:
        if strict:
            raise rc.ParserEmptyError("Rossel-Wilberhofen date contract changed") from exc
        return []
    if (day, 8) != (calendar_day, calendar_month):
        if strict:
            raise rc.ParserEmptyError("Rossel-Wilberhofen news/calendar dates disagree")
        return []

    event = common.make_event(
        "Dorf-Flohmarkt Wilberhofen",
        start,
        end,
        "Wilberhofen",
        "Windeck",
        (
            "Dorf-Flohmarkt im gesamten Ort Wilberhofen, parallel zum traditionellen "
            "Rochusfest rund um die Rochuskapelle."
        ),
        _NEWS_URL,
        _SOURCE,
        "dorf-flohmarkt hofflohmarkt trödelmarkt markt",
        1.0,
        f"{start_hour:02d}:{start_minute:02d}–{end_hour:02d}:{end_minute:02d}",
        source_id=_SOURCE_ID,
    )
    if event:
        event["organizer"] = _SOURCE
    return [event] if event and common.event_in_window(event) else []


def _events_from_combined_page(html: str, *, strict: bool = False) -> list:
    news_html, separator, calendar_html = (html or "").partition(_PAGE_SEPARATOR)
    if not separator:
        if strict:
            raise rc.ParserEmptyError("Rossel-Wilberhofen combined-page contract changed")
        return []
    return _events_from_pages(news_html, calendar_html, strict=strict)


def _fetch_combined(_url: str, timeout: int = 25) -> str:
    news_html = common.fetch_url(_NEWS_URL, timeout=timeout)
    calendar_html = common.fetch_url(_CALENDAR_URL, timeout=timeout)
    return f"{news_html}{_PAGE_SEPARATOR}{calendar_html}"


def fetch() -> list:
    return rc.fetch_html_events(
        _SOURCE,
        _NEWS_URL,
        lambda html: _events_from_combined_page(html, strict=True),
        timeout=25,
        source_id=_SOURCE_ID,
        fetcher=_fetch_combined,
    )
