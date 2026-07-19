"""Neighborhood courtyard flea markets published by Hofflohmärkte Köln."""

import re

from .. import common
from ..dates import MONTH_DE
from . import regional_common as rc


_URL = "https://www.hofflohmaerkte.de/pages/hofflohmarkte-koln"
_DATE_PATTERN = re.compile(
    r"(?:Sa|So)\.\s*(\d{1,2})\.\s*([A-Za-zäöüÄÖÜ]+)\s+(20\d{2})\s*"
    r"·\s*(\d{1,2})\s*-\s*(\d{1,2})\s*Uhr\s*·\s*<strong>(.*?)(?:<br\s*/?>|</strong>)",
    re.S | re.I,
)


def _events_from_page(html: str) -> list:
    events = []
    for match in _DATE_PATTERN.finditer(html or ""):
        day, month_name, year, start_hour, end_hour, neighborhood_html = match.groups()
        month = MONTH_DE.get(month_name.casefold().rstrip("."))
        if not month:
            continue
        start = common.parse_date(f"{int(day):02d}.{month:02d}.{year}")
        neighborhood = rc.clean(neighborhood_html)
        if not (start and neighborhood):
            continue
        city = "Frechen" if "frechen" in neighborhood.casefold() else "Köln"
        time_text = f"{int(start_hour):02d}:00–{int(end_hour):02d}:00"
        title = f"Hofflohmarkt {neighborhood}"
        description = (
            f"Beim Hofflohmarkt in {neighborhood} verkaufen Hausanwohnerinnen und "
            "Hausanwohner auf ihren eigenen Höfen und in ihren Gärten."
        )
        event = common.make_event(
            title,
            start,
            None,
            neighborhood,
            city,
            description,
            _URL,
            "Hofflohmärkte Köln",
            "hofflohmarkt flohmarkt nachbarschaft markt",
            0.94,
            time_text,
        )
        if event:
            events.append(event)
    return rc.dedupe(events)


def fetch() -> list:
    return rc.fetch_html_events("Hofflohmärkte Köln", _URL, _events_from_page, timeout=20)
