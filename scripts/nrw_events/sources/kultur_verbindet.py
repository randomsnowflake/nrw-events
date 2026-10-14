"""Kultur verbindet e.V. — Bad Godesberg association and its Klimaviertel project.

Reads:  the public kalender.digital ICS exports linked from
        kulturverbindet.de/kalender (sub-calendars "Kultur verbindet e.V." and
        "Klimaviertel unter der Godesburg"). The "Arbeitsraum im Rathaus"
        sub-calendar is a room booking that repeats the public Klimaviertel
        consultations at other times, so it is not read.
Yields: dated entries of both calendars. Public holidays the main calendar
        embeds (linked to feiertage-deutschland.de) are skipped, as is the
        RepaRad bike repair café, which ``repair-cafes-bonn`` already publishes
        with its full schedule under another title. An entry listed in both
        sub-calendars keeps one row per title and start.
"""

from __future__ import annotations

import re
from datetime import datetime

from ..ical import fetch_ical

_EXPORT = "https://export.kalender.digital/ics/{}/16445e3529209c55506f/{}.ics?past_months=0&future_months=12"
ICAL_URLS = (
    _EXPORT.format(5612563, "kulturverbindetev"),
    _EXPORT.format(5612566, "klimaviertelunterdergodesburg"),
)
SOURCE = "Kultur verbindet e.V."
SOURCE_ID = "kultur-verbindet"
_REPARAD = re.compile(r"\brepa-?rad\b", re.IGNORECASE)


def _is_own_event(props: dict[str, str], _start: datetime, _end: datetime) -> bool:
    if "feiertage-deutschland.de" in props.get("URL", ""):
        return False
    return not _REPARAD.search(props.get("SUMMARY", ""))


def unique_events(events: list) -> list:
    """Drop the second copy of an entry shown in both sub-calendars."""
    seen: set[tuple[str, str]] = set()
    unique = []
    for event in events:
        key = (event["title"], event["start_at"])
        if key not in seen:
            seen.add(key)
            unique.append(event)
    return unique


def fetch() -> list:
    return unique_events([
        event
        for url in ICAL_URLS
        for event in fetch_ical(url, SOURCE, "Bonn", "", 0.95, SOURCE_ID, event_filter=_is_own_event)
    ])
