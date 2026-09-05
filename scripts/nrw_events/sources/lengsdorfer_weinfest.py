"""First-party event data from Lengsdorfer Weinfest."""

from __future__ import annotations

import re
from datetime import timedelta

from .. import common
from . import regional_common as rc

URL = "https://lengsdorf-weinfest.de/"
SOURCE = "Lengsdorfer Weinfest"
SOURCE_ID = "lengsdorfer-weinfest"
_PROGRAMME_RE = re.compile(
    r"\{\s*tag:\s*'([^']+)'\s*,\s*zeit:\s*'(\d{1,2}:\d{2})'\s*,"
    r"\s*titel:\s*'([^']+)'\s*,\s*text:\s*'([^']*)'[^}]*\}",
    re.I,
)


def _programme(html: str) -> list[tuple[str, str, str, str]]:
    marker = re.search(
        r"const\s+weinfestDaten\s*=\s*\[(.+?)\]\s*;",
        html,
        re.I | re.S,
    )
    if not marker:
        return []
    return [
        (rc.clean(day), rc.clean(time), rc.clean(title), rc.clean(description))
        for day, time, title, description in _PROGRAMME_RE.findall(marker.group(1))
    ]


def _clock_key(value: str) -> tuple[int, int]:
    hour_text, minute_text = value.split(":", 1)
    hour = int(hour_text)
    minute = int(minute_text)
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError(f"invalid clock: {value}")
    return hour, minute


def _clock_text(value: str) -> str:
    hour, minute = _clock_key(value)
    return f"{hour:02d}:{minute:02d}"


def events_from_page(html: str) -> list[dict]:
    text = rc.clean(html)
    edition = re.search(
        r"\b(\d+)\.\s*Lengsdorfer Weinfest\s+(20\d{2})\b",
        text,
        re.I,
    )
    programme = _programme(html)
    if not edition or not programme:
        return []

    occurrences = [
        match
        for match in re.finditer(
            r"\b(\d{1,2})\s*[-–]\s*(\d{1,2})\.\s*"
            r"([A-Za-zÄÖÜäöü]+)\s+(\d{4})\b\s+(.{2,80}?)\s+53127\s+Bonn\b",
            text,
        )
        if match.group(4) == edition.group(2)
    ]
    if len(occurrences) != 1:
        return []
    occurrence = occurrences[0]

    try:
        for _, clock, _, _ in programme:
            _clock_key(clock)
    except ValueError:
        return []

    first_day, last_day, month, year, venue = occurrence.groups()
    start_day = common.parse_date(f"{first_day}. {month} {year}")
    end_day = common.parse_date(f"{last_day}. {month} {year}")
    if (
        not start_day
        or not end_day
        or start_day.weekday() != 4
        or end_day != start_day + timedelta(days=2)
    ):
        return []

    friday = [item for item in programme if item[0].casefold() == "freitag"]
    saturday = [item for item in programme if item[0].casefold() == "samstag"]
    sunday = [item for item in programme if item[0].casefold() == "sonntag"]
    if not friday or not saturday or not sunday:
        return []

    start_time = _clock_text(min((item[1] for item in friday), key=_clock_key))
    end_time = _clock_text(max((item[1] for item in sunday), key=_clock_key))
    start = rc.with_time(start_day, start_time)
    end = rc.with_time(end_day, end_time)

    coronation = next(
        (item for item in friday if "weinkönigin" in item[2].casefold()),
        None,
    )
    first_saturday = min(saturday, key=lambda item: _clock_key(item[1]))
    details = [f"Das Programm beginnt Freitag um {start_time} Uhr."]
    if coronation:
        details.append(
            f"{coronation[2]} ist Freitag um {_clock_text(coronation[1])} Uhr."
        )
    details.append(
        f"Samstag beginnt das Programm um {_clock_text(first_saturday[1])} Uhr."
    )
    details.append(f"Sonntag endet das Fest um {end_time} Uhr.")
    description = common.concise_description(" ".join(details), max_chars=420)

    description = common.concise_description(
        f"Das {edition.group(1)}. Lengsdorfer Weinfest. {description}",
        max_chars=420,
    )
    event = common.make_event(
        "Weinfest Lengsdorf",
        start,
        end,
        rc.clean(venue),
        "Bonn",
        description,
        URL,
        SOURCE,
        "weinfest weinkönigin live-musik dorfplatz",
        1.0,
        source_id=SOURCE_ID,
        description_source="generated",
        default_category_key="festival",
        category_locked=True,
        link_kind="detail",
    )
    return [event] if event else []


def fetch() -> list[dict]:
    return rc.fetch_html_events(
        SOURCE,
        URL,
        events_from_page,
        source_id=SOURCE_ID,
    )
