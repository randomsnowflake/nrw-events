"""
Wachtberg — Voreifel municipality just south of Bonn (~10 km).

Reads:  wachtberg.de calendar iCal export (same CMS family as Siegburg).
Yields: community life — board-game nights, ADFC bike tours, photo-club
        evenings, neighbourhood treffs — small stuff aggregators never list.
"""

from .. import common

_ICS = "https://www.wachtberg.de/kalender/veranstaltungen/event.ics?weekends=false&tagMode=ALL"


def fetch() -> list:
    source = "Wachtberg"
    try:
        return common.fetch_ical(_ICS, source, "Wachtberg", "", 1.0)
    except Exception as e:
        common.log_source_error(source, e)
        return []
