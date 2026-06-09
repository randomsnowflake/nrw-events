"""
Siegburg — Kreisstadt event calendar (Rhein-Sieg-Kreis, ~10 km from Bonn).

Reads:  siegburg.de combined calendar iCal export (RFC 5545 .ics).
Yields: exhibitions, museum events, town markets, readings and local happenings.
        The feed also carries recurring/historical anniversary entries; those fall
        outside the window and are dropped by the shared make_event guard.
"""

from .. import common

_ICS_URL = ("https://siegburg.de/kalender/kombinierter-kalender/"
            "event.ics?weekends=false&tagMode=ANY")


def fetch() -> list:
    source = "Siegburg"
    try:
        return common.fetch_ical(_ICS_URL, source, "Siegburg", "", 1.0)
    except Exception as e:
        common.log_source_error(source, e)
        return []
