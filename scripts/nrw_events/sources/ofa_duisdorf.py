"""First-party event data from Ortsfestausschuss Duisdorf."""

from __future__ import annotations

import re
from datetime import date, timedelta

from .. import common
from . import regional_common as rc

URL = "https://www.ofa-duisdorf.de/duisdorfer-adventsmarkt-2018"
SOURCE = "OFA Duisdorf"
SOURCE_ID = "ofa-duisdorf"
_DATE_RE = re.compile(
    r"\bDuisdorfer Adventsmarkt\s+(20\d{2})\s+vom\s+"
    r"(\d{2}\.\d{2}\.\d{4})\s+bis\s+(\d{2}\.\d{2}\.\d{4})\b",
    re.I,
)


def events_from_page(html: str) -> list[dict]:
    text = rc.clean(html)
    occurrences_by_value = {
        match.groups(): match for match in _DATE_RE.finditer(text)
    }
    occurrences = list(occurrences_by_value.values())
    if not occurrences:
        return []

    latest_year = max(int(match.group(1)) for match in occurrences)
    current = [match for match in occurrences if int(match.group(1)) == latest_year]
    if len(current) != 1:
        return []
    occurrence = current[0]

    start = common.parse_date(occurrence.group(2))
    end = common.parse_date(occurrence.group(3))
    if (
        not start
        or not end
        or start > end
        or start.year != latest_year
        or end.year != latest_year
    ):
        return []

    first_advent = date(latest_year, 11, 27)
    first_advent += timedelta(days=(6 - first_advent.weekday()) % 7)
    if (
        start.date() != first_advent - timedelta(days=2)
        or end.date() != first_advent
    ):
        return []

    details = re.search(
        r"Der Adventsmarkt ist .{1,160}?ehrenamtlich organisierter "
        r"Adventsmarkt.{1,240}?ortsansässigen Vereinen.{1,200}?"
        r"Gewerbetreibenden\.",
        text,
        re.I,
    )
    description = common.concise_description(
        rc.clean(details.group(0)) if details else "",
        max_chars=420,
    )
    event = common.make_event(
        "Duisdorfer Adventsmarkt",
        start,
        end,
        "",
        "Bonn-Duisdorf",
        description,
        URL,
        SOURCE,
        "adventsmarkt weihnachtsmarkt vereine familie",
        1.0,
        source_id=SOURCE_ID,
        description_source="source",
        default_category_key="market",
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
