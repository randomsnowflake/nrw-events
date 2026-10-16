"""Martinszüge from the city of Bonn's annual list of reported processions.

bonn.de/martinszuege publishes one table per Stadtbezirk (date and time,
procession name, start, destination) next to the city's route map. The list
covers the processions the organisers reported to the city and cleared for
publication. Rows marked as school-internal are skipped.
"""

from __future__ import annotations

import re
from datetime import datetime

from ..dates import parse_date
from ..event_builder import make_event
from ..location import refine_bonn_location, refine_city_from_text
from ..text import clean_html, sanitize_time_text
from . import regional_common as rc

URL = "https://www.bonn.de/bonn-erleben/ausgehen-und-erleben/martinszuege.php"
SOURCE = "Bonn.de Martinszüge"
SOURCE_ID = "bonn-martinszuege"
_DISTRICT_CITIES = {
    "bad godesberg": "Bonn-Bad Godesberg",
    "beuel": "Bonn-Beuel",
    "bonn": "Bonn",
    "hardtberg": "Bonn-Hardtberg",
}
_SECTION_RE = re.compile(r"<h[2-4][^>]*>(.*?)</h[2-4]>(.*?)(?=<h[2-4][^>]*>|\Z)", re.I | re.S)
_ROW_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.I | re.S)
_CELL_RE = re.compile(r"<td[^>]*>(.*?)</td>", re.I | re.S)
_DATE_RE = re.compile(r"(\d{1,2})\.\s*([A-Za-zäÄ]+)\s+(20\d{2})")
_TIME_RE = re.compile(r"(\d{1,2}(?::\d{2})?)\s*Uhr(\s+Start)?", re.I)


def _lines(cell: str) -> list[str]:
    parts = re.split(r"<br\s*/?>", cell, flags=re.I)
    return [line for line in (clean_html(part) for part in parts) if line]


def _title(name: str) -> str:
    # "Ausschuss Sankt Martin (Holzlar)": the district is the procession's name.
    district = re.search(r"\(([^)]+)\)$", name)
    if district and refine_city_from_text("Bonn", district.group(1)) != "Bonn":
        return f"Martinszug {district.group(1)}"
    if re.search(r"martins", name, re.I):
        return name
    if re.search(r"\bUmzug\b", name):
        return re.sub(r"\bUmzug\b", "Martinszug", name, count=1)
    return "Martinszug " + re.sub(r"^Umzüge in\s+", "", name, flags=re.I)


def _start(date_text: str) -> tuple[datetime | None, str]:
    date_match = _DATE_RE.search(date_text)
    if not date_match:
        return None, ""
    day = parse_date(" ".join(date_match.groups()))
    times = _TIME_RE.findall(date_text)
    # "17 Uhr (Aufstellung), 17:30 Uhr Start": the explicit start wins.
    chosen = next((value for value, start in times if start), times[0][0] if times else "")
    time_text = sanitize_time_text(chosen)
    if not day or not time_text:
        return day, ""
    hour, minute = (int(part) for part in time_text.split(":"))
    return day.replace(hour=hour, minute=minute), time_text


def events_from_page(html: str) -> list[dict]:
    events = []
    for heading, section in _SECTION_RE.findall(html or ""):
        district = re.sub(r"^Stadtbezirk\s+", "", clean_html(heading), flags=re.I)
        base_city = _DISTRICT_CITIES.get(district.casefold())
        if not base_city:
            continue
        for row in _ROW_RE.findall(section):
            cells = _CELL_RE.findall(row)
            if len(cells) < 4:
                continue
            date_lines, name_lines, start_lines, goal_lines = (_lines(cell) for cell in cells[:4])
            if not (date_lines and name_lines and start_lines):
                continue
            if any("schulveranstaltung" in line.casefold() for line in name_lines):
                continue
            start, time_text = _start(" ".join(date_lines))
            if start is None:
                continue
            name = " ".join(name_lines)
            start_point = " ".join(start_lines)
            goal = " ".join(goal_lines)
            place_text = f"{name} {start_point} {goal}"
            city = (
                refine_bonn_location(base_city, place_text)
                if base_city == "Bonn"
                else refine_city_from_text(base_city, place_text)
            )
            description = f"Start: {start_point}." + (f" Ziel: {goal}." if goal and goal != start_point else "")
            event = make_event(
                _title(name), start, None, start_point, city, description, URL, SOURCE,
                "martinszug sankt martin laternen kinder familie", 0.9,
                time_text=time_text, all_day=not time_text, source_id=SOURCE_ID,
                description_source="generated", default_category_key="kids",
                category_locked=True, link_kind="overview",
            )
            if event:
                events.append(event)
    return rc.dedupe(events)


def _no_list_published(html: str) -> bool:
    # Outside the season the city removes the tables; that page is healthy.
    return "<table" not in (html or "").casefold()


def fetch() -> list[dict]:
    return rc.fetch_html_events(
        SOURCE, URL, events_from_page, source_id=SOURCE_ID,
        empty_is_healthy=_no_list_published,
    )
