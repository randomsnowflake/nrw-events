"""Pure parsing of Bonn annual press-release titles, venues and dates."""

import re
from datetime import datetime

from ..dates import MONTH_DE


def _press_event_title(text: str) -> str:
    """Extract the event name before the press-release venue/date fields.

    A comma normally separates the title from the venue. Some official names
    contain a punctuation comma themselves, notably "Antik-, Kunst- &
    Designmarkt Bonn". Keep the following segment when the text before the
    first comma ends in a hyphen so the title is not truncated to "Antik-".
    """
    parts = [part.strip() for part in text.split(",")]
    if not parts:
        return ""
    if parts[0].endswith("-") and len(parts) > 1:
        return f"{parts[0]}, {parts[1]}".strip()
    return parts[0]


def _press_event_venue(text: str, title: str) -> str:
    """Keep the official location text between the title and first date."""
    remainder = text[len(title):].lstrip(" ,")
    date_start = re.search(
        r"\b\d{1,2}\.\s*(?:(?:bis|und)\s*\d{1,2}\.\s*)?"
        r"(?:Januar|Februar|März|April|Mai|Juni|Juli|August|"
        r"September|Oktober|November|Dezember)\b",
        remainder,
        re.I,
    )
    if not date_start:
        return ""
    return remainder[:date_start.start()].strip(" ,")


def _press_date_ranges(text: str, default_year: int) -> list[tuple[datetime, datetime]]:
    """Parse the date grammar used by Bonn's annual event press release."""
    month_pattern = (
        r"Januar|Februar|März|April|Mai|Juni|Juli|August|"
        r"September|Oktober|November|Dezember"
    )
    consumed: list[tuple[int, int]] = []
    ranges: list[tuple[datetime, datetime]] = []

    def add(
        match: re.Match[str],
        start_parts: tuple[int, int, int],
        end_parts: tuple[int, int, int],
    ) -> None:
        try:
            start = datetime(*start_parts)
            end = datetime(*end_parts)
        except (ValueError, KeyError):
            return
        consumed.append(match.span())
        ranges.append((start, max(start, end)))

    # 27. bis 29. November 2026 / 3. und 4. Oktober 2026
    for match in re.finditer(
        rf"(\d{{1,2}})\.\s*(?:bis|und)\s*(\d{{1,2}})\.\s*"
        rf"({month_pattern})\s*(20\d{{2}})?",
        text,
        re.I,
    ):
        first, last, month_name, year_text = match.groups()
        month = MONTH_DE.get(month_name.casefold())
        if month:
            year = int(year_text or default_year)
            add(match, (year, month, int(first)), (year, month, int(last)))

    # 20. November bis 23. Dezember 2026
    for match in re.finditer(
        rf"(\d{{1,2}})\.\s*({month_pattern})\s*(20\d{{2}})?\s*bis\s*"
        rf"(\d{{1,2}})\.\s*({month_pattern})\s*(20\d{{2}})?",
        text,
        re.I,
    ):
        if any(start <= match.start() < end for start, end in consumed):
            continue
        first, first_month_name, first_year, last, last_month_name, last_year = match.groups()
        first_month = MONTH_DE.get(first_month_name.casefold())
        last_month = MONTH_DE.get(last_month_name.casefold())
        if first_month and last_month:
            end_year = int(last_year or first_year or default_year)
            start_year = int(first_year or end_year)
            add(
                match,
                (start_year, first_month, int(first)),
                (end_year, last_month, int(last)),
            )

    for match in re.finditer(
        rf"(\d{{1,2}})\.\s*({month_pattern})\s*(20\d{{2}})?",
        text,
        re.I,
    ):
        if any(start <= match.start() < end for start, end in consumed):
            continue
        day, month_name, year_text = match.groups()
        month = MONTH_DE.get(month_name.casefold())
        if not month:
            continue
        try:
            value = datetime(int(year_text or default_year), month, int(day))
        except ValueError:
            continue
        ranges.append((value, value))
    return sorted(set(ranges))
