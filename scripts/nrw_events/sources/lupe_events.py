"""LuPe Events — first-party Bonn and Rhein-Sieg fair calendar."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from .. import common


_ICAL_URL = "https://lupe-events.de/events/?ical=1"
_LESSENICH_URL = "https://lupe-events.de/event/kirmes-in-lessenich-2026"


def _city_for(location: str) -> str:
    normalized = common.clean_html(location).casefold()
    for city in ("Alfter", "Bornheim", "Bonn"):
        if city.casefold() in normalized:
            return city
    return "Bonn"


def fetch() -> list:
    """Read the advertised ICS and reconcile one reviewed listing conflict.

    LuPe's Lessenich detail/ICS currently ends on 17 August while its public
    calendar overview says 14–16 August.  The latter agrees with the municipal
    occurrence and is the bounded correction audited for this exact URL.
    """
    events = common.fetch_ical(
        _ICAL_URL,
        "LuPe Events",
        "Bonn",
        "Kirmes Volksfest",
        1.0,
        "lupe-events",
        city_resolver=_city_for,
    )
    for event in events:
        if event.get("link", "").rstrip("/") != _LESSENICH_URL:
            continue
        event.update({
            "title": "Laurentius-Kirmes",
            "end_date": "2026-08-16",
            "venue": "Dorfplatz Lessenich",
            "city": "Bonn",
        })
        event["end_at"] = datetime(
            2026, 8, 17, tzinfo=ZoneInfo("Europe/Berlin"),
        ).isoformat()
    return events
