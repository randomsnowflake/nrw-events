"""First-party event data from Kessenicher Herbstmarkt."""

from __future__ import annotations

import re

from .. import common
from . import regional_common as rc

URL = (
    "https://www.kessenicher-herbstmarkt.de/herbstmarkt/"
    "informationen-kessenicher-herbstmarkt/"
)
HOME_URL = "https://www.kessenicher-herbstmarkt.de/"
SOURCE = "Kessenicher Herbstmarkt"
SOURCE_ID = "kessenicher-herbstmarkt"


def events_from_page(html: str) -> list[dict]:
    text = rc.clean(html)
    date_match = re.search(
        r"\bTag:\s*(\d{2}\s*\.\s*\d{2}\s*\.\s*\d{4})\b",
        text,
        re.I,
    )
    if not date_match:
        return []

    date_text = re.sub(r"\s+", "", date_match.group(1))
    start = common.parse_date(date_text)
    if not start:
        return []

    hours = re.search(
        r"Öffnungszeiten:\s*Herbstmarkt:\s*"
        r"(\d{1,2}[.:]\d{2})\s*Uhr\s*bis\s*"
        r"(\d{1,2}[.:]\d{2})\s*Uhr",
        text,
        re.I,
    )
    if not hours:
        return []
    time_text = f"{hours.group(1).replace('.', ':')}–{hours.group(2).replace('.', ':')}"

    location = re.search(
        r"Standort Herbstmarkt:\s*(.+?)(?=\s+Bühne:|\s+Bühnenprogramm:|$)",
        text,
        re.I,
    )
    if not location:
        return []
    venue = rc.clean(location.group(1)).replace(" • ", " / ")

    stage = re.search(
        r"Bühne:\s*(.+?)(?=\s+Bühnenprogramm:|\s+Ansprechpartner:|$)",
        text,
        re.I,
    )
    details = [f"Der Herbstmarkt öffnet von {time_text} Uhr."] if time_text else []
    if stage:
        details.append(f"Die Bühne ist {rc.clean(stage.group(1)).rstrip('.')}.")
    details.append(f"Der Markt findet in {venue} statt.")

    description = common.concise_description(" ".join(details), max_chars=420)
    event = common.make_event(
        "Kessenicher Herbstmarkt",
        start,
        start,
        venue,
        "Bonn",
        description,
        URL,
        SOURCE,
        "herbstmarkt stadtteilfest bühne flohmarkt familie",
        1.0,
        time_text=time_text,
        source_id=SOURCE_ID,
        description_source="generated",
        default_category_key="market",
        category_locked=True,
        link_kind="detail",
    )
    return [event] if event else []


def opening_events_from_page(html: str) -> list[dict]:
    text = rc.clean(html)
    occurrence = re.search(
        r"Am Samstag,\s*den\s*(\d{2}\.\d{2}\.\d{4})\s*,"
        r"\s*abends\s*ab\s*(\d{1,2}[.:]\d{2})\s*Uhr"
        r"[^.!?]{0,160}\bHerbstmarkt Opening\b",
        text,
        re.I,
    )
    if not occurrence:
        return []

    day = common.parse_date(occurrence.group(1))
    if not day:
        return []
    time_text = occurrence.group(2).replace(".", ":")
    start = rc.with_time(day, time_text)
    description = (
        f"Die Eröffnung des Kessenicher Herbstmarkts beginnt am Samstag "
        f"um {time_text} Uhr."
    )
    event = common.make_event(
        "Herbstmarkt Opening",
        start,
        None,
        "Pützstraße",
        "Bonn",
        description,
        HOME_URL,
        SOURCE,
        "herbstmarkt eröffnung stadtteilfest musik",
        1.0,
        time_text=time_text,
        source_id=SOURCE_ID,
        description_source="generated",
        default_category_key="festival",
        category_locked=True,
        link_kind="detail",
    )
    return [event] if event else []


def fetch() -> list[dict]:
    market = rc.fetch_html_events(
        SOURCE,
        URL,
        events_from_page,
        source_id=SOURCE_ID,
    )
    opening = rc.fetch_html_events(
        SOURCE,
        HOME_URL,
        opening_events_from_page,
        source_id=SOURCE_ID,
    )
    return rc.dedupe([*market, *opening])
