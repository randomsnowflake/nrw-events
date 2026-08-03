"""Central Bonn Christmas markets from the first-party organizer website."""

import re
from datetime import datetime, timedelta

from .. import common
from ..dates import MONTH_DE
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
    main_anchor = re.search(
        r"\b(?:Bonner\s+Weihnachtsmarkt|Weihnachtsmarkt\s+Bonn)\b",
        main_text,
        re.I,
    )
    kings_anchor = re.search(r"\bDreikönigsmarkt\b", dates_text, re.I)
    main_section = main_text[main_anchor.start():] if main_anchor else ""
    kings_section = dates_text[kings_anchor.start():] if kings_anchor else ""
    main_range = re.search(
        r"(\d{1,2})\.\s*([A-Za-zäöüÄÖÜ]+)\s+bis\s+"
        r"(\d{1,2})\.\s*([A-Za-zäöüÄÖÜ]+)\s+(20\d{2})",
        main_section,
        re.I,
    )
    main_hours = re.search(
        r"Alle Geschäfte\s+(\d{1,2})[.:]00\s+bis\s+(\d{1,2})[.:]00\s+Uhr",
        main_section,
        re.I,
    )
    main_closed = re.search(
        r"Totensonntag\s*\((\d{1,2})\.(\d{1,2})\.(20\d{2})\).*?geschlossen",
        main_section,
        re.I,
    )
    final_day_hours = re.search(
        r"Letzter Tag\s*\((\d{1,2})\.(\d{1,2})\.(20\d{2})\).*?bis\s+(\d{1,2})\s+Uhr",
        main_section,
        re.I,
    )
    main_venue = all(
        name.casefold() in main_section.casefold()
        for name in ("Münster", "Bottler", "Friedensplatz", "Remigiusplatz")
    )
    kings_range = re.search(
        r"(\d{1,2})\.(\d{1,2})\.(20\d{2})\s+bis\s+"
        r"(\d{1,2})\.(\d{1,2})\.(20\d{2})",
        kings_section,
        re.I,
    )
    kings_closed = re.search(
        r"(\d{1,2})\.(\d{1,2})\.(20\d{2})\s+geschlossen",
        kings_section,
        re.I,
    )
    weekday_hours = re.search(
        r"(\d{1,2})\s+bis\s+(\d{1,2})\s+Uhr\s*"
        r"\(Sonntag\s+bis\s+Donnerstag\)",
        kings_section,
        re.I,
    )
    weekend_hours = re.search(
        r"(\d{1,2})\s+bis\s+(\d{1,2})\s+Uhr\s*"
        r"\(Freitag\s+und\s+Samstag\)",
        kings_section,
        re.I,
    )
    silvester_hours = re.search(
        r"Silvester:\s*(\d{1,2})\s+bis\s+(\d{1,2})\s+Uhr", kings_section, re.I,
    )
    kings_hours = all((weekday_hours, weekend_hours, silvester_hours))
    valid = all(
        (main_range, main_hours, main_closed, final_day_hours, main_venue,
         kings_range, kings_closed, kings_hours)
    )
    if not valid:
        if strict:
            raise rc.ParserEmptyError("Bonner Christmas-market date/hour contract changed")
        return []

    start_day, start_month_name, end_day, end_month_name, year_text = main_range.groups()
    year = int(year_text)
    start_month = MONTH_DE.get(start_month_name.casefold())
    end_month = MONTH_DE.get(end_month_name.casefold())
    if not start_month or not end_month:
        if strict:
            raise rc.ParserEmptyError("Bonner Christmas-market month contract changed")
        return []
    closed_day, closed_month, closed_year = (int(value) for value in main_closed.groups())
    final_day, final_month, final_year, final_hour = (int(value) for value in final_day_hours.groups())
    regular_start_hour, regular_end_hour = (int(value) for value in main_hours.groups())
    events = _daily_events(
        "Bonner Weihnachtsmarkt",
        datetime(year, start_month, int(start_day)),
        datetime(year, end_month, int(end_day)),
        venue=_VENUE,
        link=_MAIN_URL,
        source_id="bonner-weihnachtsmarkt",
        closed={datetime(closed_year, closed_month, closed_day).date()},
        hours=lambda day: (
            regular_start_hour,
            final_hour if day.date() == datetime(final_year, final_month, final_day).date()
            else regular_end_hour,
        ),
    )
    king_values = tuple(int(value) for value in kings_range.groups())
    king_start_day, king_start_month, kings_start_year = king_values[:3]
    king_end_day, king_end_month, kings_end_year = king_values[3:]
    kings_closed_day, kings_closed_month, kings_closed_year = (
        int(value) for value in kings_closed.groups()
    )
    weekday_start, weekday_end = (int(value) for value in weekday_hours.groups())
    weekend_start, weekend_end = (int(value) for value in weekend_hours.groups())
    silvester_start, silvester_end = (int(value) for value in silvester_hours.groups())
    events.extend(_daily_events(
        "Bonner Dreikönigsmarkt",
        datetime(kings_start_year, king_start_month, king_start_day),
        datetime(kings_end_year, king_end_month, king_end_day),
        venue="Remigiusplatz",
        link=_DATES_URL,
        source_id="bonner-dreikoenigsmarkt",
        closed={datetime(kings_closed_year, kings_closed_month, kings_closed_day).date()},
        hours=lambda day: (
            (silvester_start, silvester_end) if (day.month, day.day) == (12, 31)
            else ((weekend_start, weekend_end) if day.weekday() in {4, 5}
                  else (weekday_start, weekday_end))
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
