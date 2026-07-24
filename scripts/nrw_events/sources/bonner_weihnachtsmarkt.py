"""Central Bonn Christmas markets from the first-party organizer website."""

import re
from datetime import datetime, timedelta

from .. import common
from . import regional_common as rc


_SOURCE = "Bonner Weihnachtsmarkt"
_MAIN_URL = "https://bonnerweihnachtsmarkt.de/view/all/"
_DATES_URL = "https://bonnerweihnachtsmarkt.de/termine/"
_VENUE = (
    "Münsterplatz, Bottlerplatz, Friedensplatz, Windeckstraße, Vivatsgasse, "
    "Poststraße und Remigiusplatz"
)


def _daily_events(
    title: str,
    first: datetime,
    last: datetime,
    *,
    venue: str,
    link: str,
    source_id: str,
    closed: set,
    hours,
) -> list:
    events = []
    day = first
    while day <= last:
        if day.date() not in closed and common.window_contains(day):
            start_hour, end_hour = hours(day)
            start = day.replace(hour=start_hour)
            end = day.replace(hour=end_hour)
            event = common.make_event(
                title,
                start,
                end,
                venue,
                "Bonn",
                f"{title} in der Bonner Innenstadt; geöffnet von {start_hour:02d}:00 bis {end_hour:02d}:00 Uhr.",
                link,
                _SOURCE,
                "weihnachtsmarkt adventsmarkt markt kunsthandwerk gastronomie",
                0.99,
                f"{start_hour:02d}:00–{end_hour:02d}:00",
                source_id=source_id,
            )
            if event:
                events.append(event)
        day += timedelta(days=1)
    return events


def _events_from_pages(main_html: str, dates_html: str, *, strict: bool = False) -> list:
    main_text = rc.clean(main_html)
    dates_text = rc.clean(dates_html)
    main_range = re.search(
        r"18\.\s*November\s+bis\s+23\.\s*Dezember\s+(20\d{2})",
        main_text,
        re.I,
    )
    main_hours = re.search(
        r"Alle Geschäfte\s+12[.:]00\s+bis\s+21[.:]00\s+Uhr",
        main_text,
        re.I,
    )
    main_closed = re.search(
        r"Totensonntag\s*\(22\.11\.(20\d{2})\).*?geschlossen",
        main_text,
        re.I,
    )
    main_venue = all(
        name.casefold() in main_text.casefold()
        for name in ("Münster", "Bottler", "Friedensplatz", "Remigiusplatz")
    )
    kings_range = re.search(
        r"27\.12\.(20\d{2})\s+bis\s+06\.01\.(20\d{2})",
        dates_text,
        re.I,
    )
    kings_closed = re.search(
        r"01\.01\.(20\d{2})\s+geschlossen",
        dates_text,
        re.I,
    )
    kings_hours = all(
        pattern in dates_text
        for pattern in ("12 bis 20 Uhr", "12 bis 21 Uhr", "Silvester: 11 bis 17 Uhr")
    )
    valid = all(
        (main_range, main_hours, main_closed, main_venue, kings_range, kings_closed, kings_hours)
    )
    if not valid:
        if strict:
            raise rc.ParserEmptyError("Bonner Christmas-market date/hour contract changed")
        return []

    year = int(main_range.group(1))
    events = _daily_events(
        "Bonner Weihnachtsmarkt",
        datetime(year, 11, 18),
        datetime(year, 12, 23),
        venue=_VENUE,
        link=_MAIN_URL,
        source_id="bonner-weihnachtsmarkt",
        closed={datetime(year, 11, 22).date()},
        hours=lambda day: (12, 20 if day.date() == datetime(year, 12, 23).date() else 21),
    )
    kings_start_year, kings_end_year = map(int, kings_range.groups())
    events.extend(_daily_events(
        "Bonner Dreikönigsmarkt",
        datetime(kings_start_year, 12, 27),
        datetime(kings_end_year, 1, 6),
        venue="Remigiusplatz",
        link=_DATES_URL,
        source_id="bonner-dreikoenigsmarkt",
        closed={datetime(int(kings_closed.group(1)), 1, 1).date()},
        hours=lambda day: (
            (11, 17) if (day.month, day.day) == (12, 31)
            else (12, 21 if day.weekday() in {4, 5} else 20)
        ),
    ))
    return rc.dedupe(events)


def fetch() -> list:
    try:
        main_html = common.fetch_url(_MAIN_URL, timeout=20)
        dates_html = common.fetch_url(_DATES_URL, timeout=20)
        return _events_from_pages(main_html, dates_html, strict=True)
    except Exception as exc:
        common.log_source_error(_SOURCE, exc)
        return []
