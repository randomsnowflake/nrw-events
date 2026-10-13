"""Wave of Joy Gospelchor Bonn — first-party concert calendar.

The Angular site loads its calendar from a JSON endpoint that also carries the
choir's internal dates; only rows marked public and active are events. Clock
times and prices exist only in the ticket reservation form of the next concert.
"""

from __future__ import annotations

import json
import re
from datetime import datetime

from .. import event_builder, http, run_state
from ..dates import MONTH_DE
from . import regional_common as rc

_BASE_URL = "https://www.wave-of-joy.de"
_CALENDAR_URL = f"{_BASE_URL}/php/termine.php"
_PAGE_URL = f"{_BASE_URL}/termine"
_SOURCE = "Wave of Joy"
_SOURCE_ID = "wave-of-joy"

_MONTHS = "|".join(MONTH_DE)
_DATE = re.compile(
    rf"(\d{{1,2}})\.(?:\s*[-/–]\s*(\d{{1,2}})\.)?\s*({_MONTHS})\s+(20\d{{2}})", re.I,
)
_TICKET_SLOT = re.compile(
    rf"(\d{{1,2}})\.\s*({_MONTHS})\s+(\d{{1,2}}:\d{{2}})\s*Uhr(?:\s*\(Einlass\s+(\d{{1,2}}:\d{{2}})\))?",
    re.I,
)


def fetch() -> list:
    return rc.fetch_html_events(
        _SOURCE,
        _CALENDAR_URL,
        lambda text: events_from_calendar(text, _ticket_form()),
        source_id=_SOURCE_ID,
        fetcher=http.fetch_url,
        # Between seasons the choir lists only internal dates.
        empty_is_healthy=lambda text: not _public_rows(text),
    )


def _ticket_form() -> str:
    """Return the reservation form chunk, or "" when no concert sells tickets."""
    try:
        page = http.fetch_url(_PAGE_URL, timeout=20)
        main = rc.first_group(r'<script src="(main-[\w-]+\.js)"', page)
        bundle = http.fetch_url(f"{_BASE_URL}/{main}", timeout=20) if main else ""
        chunk = rc.first_group(r'path:"tickets",loadComponent:\(\)=>import\("\./([\w-]+\.js)"\)', bundle)
        return http.fetch_url(f"{_BASE_URL}/{chunk}", timeout=20) if chunk else ""
    except Exception as exc:
        # Dates stay publishable without the clock; the error stays visible.
        run_state.log_source_error(f"{_SOURCE} ticket form", exc, source_id=_SOURCE_ID)
        return ""


def _public_rows(text: str) -> list[dict]:
    rows = json.loads(text or "[]")
    return [
        row for row in rows
        if isinstance(row, dict) and row.get("active") is True and row.get("intern") is False
    ]


def _ticket_slots(form: str) -> tuple[dict[tuple[int, int], tuple[str, str]], str]:
    """Map (day, month) to (start, doors) and read the advertised prices."""
    text = re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), form or "")
    slots = {
        (int(day), MONTH_DE[month.casefold()]): (start, doors or "")
        for day, month, start, doors in _TICKET_SLOT.findall(text)
    }
    prices = [
        f"{rc.clean(label)} {amount} €"
        for label, amount in re.findall(r"Ticketanzahl \(([^()\d]+?)\s*(\d+)\s*€\)", text)
    ]
    return slots, "; ".join(prices)


def events_from_calendar(text: str, ticket_form: str = "") -> list:
    slots, price = _ticket_slots(ticket_form)
    events = []
    for row in _public_rows(text):
        match = _DATE.search(str(row.get("date") or ""))
        title = re.sub(r"^\d+\.\s+", "", rc.clean(str(row.get("text") or "")))
        if not match or not title:
            continue
        first_day, last_day, month_name, year = match.groups()
        month = MONTH_DE[month_name.casefold()]
        start = datetime(int(year), month, int(first_day))
        end = datetime(int(year), month, int(last_day)) if last_day else None
        venue = rc.clean(str(row.get("location") or ""))
        clock, doors = slots.get((start.day, month), ("", "")) if not end else ("", "")
        event = event_builder.make_event(
            title,
            rc.with_time(start, clock),
            end,
            venue,
            rc.city_from_text(venue, "Bonn"),
            "",
            _PAGE_URL,
            _SOURCE,
            "konzert gospel chor musik",
            1.0,
            clock,
            source_id=_SOURCE_ID,
            time_note=f"Einlass {doors} Uhr" if doors else "",
        )
        if event:
            if clock and price:
                event["price"] = price
            events.append(event)
    return events
