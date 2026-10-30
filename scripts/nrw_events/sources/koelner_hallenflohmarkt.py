"""Sunday dates of the Kölner Hallenflohmarkt in Köln-Rodenkirchen.

The organizer's one-page site lists "Kommende Termine" as ``So, 27.09. | 10:00 –
16:00 Uhr`` without a year. The stall-booking form on the same page offers the
same Sundays as ``27.09.26``; that official label supplies the year, so the
runtime clock never guesses it. A listed date without a booking label is skipped.
"""

import re
from datetime import datetime

from .. import reviewed_corrections
from ..event_builder import make_event, window_contains
from ..models import AdmissionDefault
from ..run_state import runtime_window
from ..text import clean_html
from . import regional_common as rc

_SOURCE = "Kölner Hallenflohmarkt"
_SOURCE_ID = "koelner-hallenflohmarkt"
_URL = "https://www.koelnerhallenflohmarkt.de/"
_TITLE = "Kölner Hallenflohmarkt"
_DATE_LINE = re.compile(
    r"So,?\s*(\d{1,2})\.(\d{1,2})\.\s*\|\s*(\d{1,2}:\d{2})\s*[–-]\s*(\d{1,2}:\d{2})\s*Uhr",
    re.I,
)
_BOOKING_LABEL = re.compile(r'data-hook="checkbox-(\d{2})\.(\d{2})\.(\d{2})"')
_DESCRIPTION = re.compile(r"Wir laden Sie ein, die besondere Atmosphäre.*?genießen\.", re.S)


def _programme_closed(html: str) -> bool:
    text = clean_html(html or "")
    return bool(
        "Kommende Termine" in text
        and "KEINE WEITEREN TERMINE GEPLANT!" in text
        and re.search(r"Kölner Hallenflohmarkt.*?Industriestraße\s+131\b", text, re.I)
    )


def _events_from_page(html: str, *, strict: bool = False) -> list:
    text = clean_html(html or "")
    if _programme_closed(html):
        cancelled = []
        years = {(int(d), int(m)): 2000 + int(y) for d, m, y in _BOOKING_LABEL.findall(html or "")}
        for entry in reviewed_corrections.active_entries("koelner_hallenflohmarkt_cancellations", runtime_window().start):
            day, month = entry["match"]
            years.setdefault((int(day), int(month)), int(entry["value"]))
        for day, month in re.findall(r"So,?\s*(\d{1,2})\.(\d{1,2})\.\s*\|\s*ABGESAGT!", text, re.I):
            # A removed booking form must not turn an old cancellation into a
            # future edition. Only its reviewed, expiring year can fill the gap.
            year = years.get((int(day), int(month)))
            if not year:
                continue
            start = datetime(year, int(month), int(day))
            if start.weekday() != 6 or not window_contains(start, start):
                continue
            event = make_event(
                _TITLE, start, start, "Kölner Hallenflohmarkt, Industriestraße 131",
                "Köln", "Der Veranstalter hat diesen Termin abgesagt.", _URL,
                _SOURCE, "flohmarkt markt", 1.0, source_id=_SOURCE_ID,
            )
            if event:
                event["status"] = "cancelled"
                cancelled.append(event)
        return cancelled
    if not re.search(r"Industriestraße\s+131,?\s+50999\s+Köln", text):
        if strict:
            raise rc.ParserEmptyError("Kölner Hallenflohmarkt address contract changed")
        return []
    listed = _DATE_LINE.findall(text.partition("Kommende Termine")[2])
    if not listed:
        if strict:
            raise rc.ParserEmptyError("Kölner Hallenflohmarkt date list changed")
        return []
    years = {(int(d), int(m)): 2000 + int(y) for d, m, y in _BOOKING_LABEL.findall(html or "")}
    free = "Eintritt ist für alle Besucher frei" in text
    description_match = _DESCRIPTION.search(text)
    description = description_match.group(0) if description_match else ""

    events = []
    for day, month, start_time, end_time in listed:
        year = years.get((int(day), int(month)))
        if not year:
            continue
        start_hour, start_minute = (int(part) for part in start_time.split(":"))
        end_hour, end_minute = (int(part) for part in end_time.split(":"))
        start = datetime(year, int(month), int(day), start_hour, start_minute)
        if start.weekday() != 6:  # the list says "So"; a mismatch means a wrong year
            continue
        end = start.replace(hour=end_hour, minute=end_minute)
        event = make_event(
            _TITLE,
            start,
            end,
            "Kölner Hallenflohmarkt, Industriestraße 131",
            "Köln",
            description,
            _URL,
            _SOURCE,
            "flohmarkt trödelmarkt vintage second hand markt",
            1.0,
            f"{start_time}–{end_time}",
            source_id=_SOURCE_ID,
            admission=AdmissionDefault.SOURCE_CONFIRMED_FREE if free else None,
        )
        if event and window_contains(start, end):
            event["organizer"] = _SOURCE
            events.append(event)
    return rc.dedupe(events)


def fetch() -> list:
    return rc.fetch_html_events(
        _SOURCE,
        _URL,
        lambda html: _events_from_page(html, strict=True),
        timeout=25,
        source_id=_SOURCE_ID,
        empty_is_healthy=_programme_closed,
    )
