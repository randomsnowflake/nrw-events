"""Official Freizeitpark flea-market dates from the City of Rheinbach."""

import re
from datetime import datetime

from .. import common
from ..dates import MONTH_DE
from . import regional_common as rc

_SOURCE = "Rheinbach Flohmarkt"
_SOURCE_ID = "rheinbach-freizeitpark-flohmarkt"
_URL = "https://www.rheinbach.de/flohmarkt"


def _events_from_page(html: str, *, strict: bool = False) -> list:
    clean = common.clean_html(html or "")
    schedule_match = re.search(
        r"Nächster\s+Flohmarkttermin:(.*?)(?:Reservierung|Verkaufszeiten)",
        clean,
        re.S | re.I,
    )
    dates = re.findall(
        r"Samstag,\s*(\d{1,2})\.\s*([A-Za-zäöüÄÖÜ.]+)\s*(20\d{2})",
        schedule_match.group(1) if schedule_match else "",
        re.I,
    )
    time_match = re.search(
        r"Verkaufszeiten.*?von\s+(\d{1,2}):([0-5]\d)\s*Uhr\s+bis\s+(\d{1,2}):([0-5]\d)\s*Uhr",
        clean,
        re.S | re.I,
    )
    address_ok = bool(
        re.search(
            r"Münstereifeler\s+Str\.\s*69\s*53359\s+Rheinbach",
            clean,
            re.I,
        )
    )
    private_market_ok = bool(
        re.search(r"nur\s+Privatanbieter", clean, re.I)
        and re.search(r"(?:keine|außer)\s+Neuwaren,\s*Lebensmittel", clean, re.I)
    )
    if not (dates and time_match and address_ok and private_market_ok):
        if strict:
            raise rc.ParserEmptyError("Rheinbach date, time, address, or market contract changed")
        return []

    start_hour, start_minute, end_hour, end_minute = (int(value) for value in time_match.groups())
    events = []
    for day_text, month_text, year_text in dates:
        month = MONTH_DE.get(month_text.casefold().rstrip("."))
        if not month:
            if strict:
                raise rc.ParserEmptyError("Rheinbach month contract changed")
            return []
        try:
            start = datetime(int(year_text), month, int(day_text), start_hour, start_minute)
            end = datetime(int(year_text), month, int(day_text), end_hour, end_minute)
        except ValueError as exc:
            if strict:
                raise rc.ParserEmptyError("Rheinbach date contract changed") from exc
            return []
        event = common.make_event(
            "Flohmarkt im Freizeitpark Rheinbach",
            start,
            end,
            "Freizeitpark Rheinbach, Münstereifeler Straße 69",
            "Rheinbach",
            "Städtischer Flohmarkt für private Anbieter; Neuwaren und Lebensmittel sind ausgeschlossen.",
            _URL,
            _SOURCE,
            "flohmarkt trödelmarkt markt",
            0.99,
            f"{start_hour:02d}:{start_minute:02d}–{end_hour:02d}:{end_minute:02d}",
            source_id=_SOURCE_ID,
        )
        if event and common.event_in_window(event):
            events.append(event)
    return rc.dedupe(events)


def fetch() -> list:
    return rc.fetch_html_events(
        _SOURCE,
        _URL,
        lambda html: _events_from_page(html, strict=True),
        timeout=25,
        source_id=_SOURCE_ID,
    )
