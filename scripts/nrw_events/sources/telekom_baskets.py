"""Telekom Baskets Bonn — home games in the Telekom Dome.

Reads:  the club's public Google calendar, linked as "Kalender-Abo" on
        telekom-baskets-bonn.de/team/spielplan.
Yields: games whose SUMMARY names Bonn first ("Telekom Baskets Bonn vs. X -
        Wettbewerb") and whose LOCATION is the Telekom Dome. Away games, test
        trips, placeholders without an opponent ("Playoffs Spiel 2") and
        non-game entries are skipped. The feed carries no prices.
"""

from __future__ import annotations

import re
from datetime import datetime

from ..ical import fetch_ical

ICAL_URL = (
    "https://calendar.google.com/calendar/ical/"
    "4uhevpalgjlrql674cdkdjupoo%40group.calendar.google.com/public/basic.ics"
)
SCHEDULE_URL = "https://www.telekom-baskets-bonn.de/team/spielplan"
SOURCE = "Telekom Baskets Bonn"
VENUE = "Telekom Dome"
# Upstream separates the competition with " - " and once with "- " ("NINERS Chemnitz- easyCredit BBL").
_HOME_GAME = re.compile(
    r"^Telekom Baskets Bonn\s+vs\.?\s+(?P<opponent>.+?)(?:\s*-\s+(?P<competition>.+))?$",
    re.IGNORECASE,
)


def _is_home_game(props: dict[str, str], _start: datetime, _end: datetime) -> bool:
    return bool(_HOME_GAME.match(props.get("SUMMARY", "").strip())) and VENUE in props.get("LOCATION", "")


def home_games(events: list) -> list:
    """Rewrite parsed home-game rows into the published title/venue contract."""
    games = []
    for event in events:
        match = _HOME_GAME.match(event["title"].strip())
        if not match:
            continue
        competition = match.group("competition")
        event["title"] = f"{SOURCE} – {match.group('opponent').strip()}" + (
            f" ({competition.strip()})" if competition else ""
        )
        # One row says "Telekom Baskets Bonn, Telekom Dome, ..."; the filter already proved the venue.
        event["venue"] = VENUE
        event["link"] = SCHEDULE_URL
        event["organizer"] = SOURCE
        games.append(event)
    return games


def fetch() -> list:
    return home_games(fetch_ical(
        ICAL_URL, SOURCE, "Bonn", "Sport Basketball", 1.0, "telekom-baskets-bonn",
        event_filter=_is_home_game,
        default_category_key="sports",
        category_locked=True,
    ))
