"""Confirmed Bonn festival programmes outside the machine-readable calendars."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from .. import dates, event_builder, run_state
from ..health import SourceFetchResult
from ..models import RawEvent
from . import regional_common as rc

_BEUEL_URL = "https://beuelhats.de/veranstaltungen"
_MEHLEM_URL = "https://ortsausschuss-mehlem.de/unser-verein/weihnachtsmarkt/"
_FRIESDORF_URL = "https://weihnachten.friesdorf.net/"
_FRIESDORF_PROGRAMME_2026 = "https://friesdorf.net/onewebmedia/weihnachtsmarkt/2026-weihnachtsmarkt.pdf"
_PROGRAMMES = Path(__file__).with_name("bonn_festival_programmes.json")
_GERMAN_DATE = r"\d{1,2}\.\s+[A-Za-zÄÖÜäöü]+\s+20\d{2}"


def _mehlemer_markt(html: str) -> list[RawEvent]:
    match = re.search(
        rf"Wann\?\s*(?:Wo\?\s*)?📆\s*\w+,\s*({_GERMAN_DATE})"
        r"\s*,?\s*(?:(\d{1,2}:\d{2})\s+bis\s+(\d{1,2}:\d{2})\s+Uhr\b)?", rc.clean(html),
    )
    if not match:
        return []
    day, opening, closing = match.groups()
    start = dates.parse_date(day)
    if not start:
        return []
    event = event_builder.make_event(
        "Weihnachtsmarkt Mehlem", start, start, "Marktplatz Mehlem", "Bonn-Mehlem",
        "Der Ortsausschuss Mehlem veranstaltet den Weihnachtsmarkt auf dem Mehlemer Marktplatz.",
        _MEHLEM_URL, "Ortsausschuss Mehlem", "Weihnachtsmarkt",
        source_id="ortsausschuss-mehlem", time_text=f"{opening}–{closing}" if opening and closing else "",
        description_source="generated", default_category_key="market", category_locked=True,
        link_kind="detail",
    )
    if event and start.date().isoformat() == "2026-11-28":
        event["preserved_event_id"] = "weihnachtsmarkt-mehlem-110c2ef01a"
    return [event] if event else []


def _friesdorfer_markt(html: str) -> list[RawEvent]:
    text = rc.clean(html)
    match = re.search(rf"Am\s+\w+,\s+den\s+({_GERMAN_DATE})\s*,\s*lädt der Ortsausschuss Friesdorf", text)
    start = dates.parse_date(match[1]) if match else None
    if not start:
        return []
    hours = re.search(r"\b(\d{1,2}:\d{2})\s*(?:Uhr\s*)?(?:bis|[-–])\s*(\d{1,2}:\d{2})\s*Uhr\b", text)
    is_2026 = start.date().isoformat() == "2026-11-28"
    # The organizer gives the opening hours in its printed 2026 programme.
    time_text = f"{hours[1]}–{hours[2]}" if hours else "12:00–19:00" if is_2026 else ""
    event = event_builder.make_event(
        "Weihnachtsmarkt Friesdorf", start, start, "Margaretenplatz", "Bonn-Friesdorf",
        "Der Ortsausschuss Friesdorf lädt auf den Parkplatz an der Margaretenstraße ein.",
        _FRIESDORF_URL, "Ortsausschuss Friesdorf 1929", "Weihnachtsmarkt",
        source_id="ortsausschuss-friesdorf", time_text=time_text,
        description_source="generated", default_category_key="market", category_locked=True,
        link_kind="detail",
    )
    if event and is_2026:
        event["preserved_event_id"] = "weihnachtsmarkt-friesdorf-cf8050d137"
        event["source_links"] = [_FRIESDORF_PROGRAMME_2026]
    return [event] if event else []


def _beueler_fruehlingsfest(html: str) -> list[RawEvent]:
    match = re.search(
        rf"Veranstaltungen\s+(20\d{{2}})\s+(\d{{1,2}})\s*\.\s*[-–]\s*({_GERMAN_DATE})"
        r"\s+Unsere Veranstaltungen\s+Beueler Frühlingsfest\b", rc.clean(html),
    )
    if not match or not match[3].endswith(match[1]):
        return []
    start = dates.parse_date(f"{match[2]}. {match[3].split('.', 1)[1].strip()}")
    end = dates.parse_date(match[3])
    if not start or not end or end < start:
        return []
    event = event_builder.make_event(
        "Beueler Frühlingsfest", start, end, "", "Bonn-Beuel",
        "Die Gewerbe-Gemeinschaft Beuel kündigt ihr Frühlingsfest an.",
        _BEUEL_URL, "Gewerbe-Gemeinschaft Beuel", "Stadtteilfest",
        source_id="gewerbegemeinschaft-beuel", all_day=True, description_source="generated",
        default_category_key="festival", category_locked=True, link_kind="detail",
    )
    return [event] if event else []


def fetch_beuel() -> SourceFetchResult:
    window = run_state.runtime_window()
    if not any(datetime(year, 3, 1) <= window.end and window.start <= datetime(year, 4, 30)
               for year in range(window.start.year, window.end.year + 1)):
        return SourceFetchResult.scheduled_skip("The Beuel spring festival falls outside the current window")
    return SourceFetchResult.success(rc.fetch_html_events(
        "Gewerbe-Gemeinschaft Beuel", _BEUEL_URL, _beueler_fruehlingsfest,
        source_id="gewerbegemeinschaft-beuel",
    ))


def fetch_mehlem() -> SourceFetchResult:
    window = run_state.runtime_window()
    if window.start.month not in (11, 12) and window.end.month not in (11, 12):
        return SourceFetchResult.scheduled_skip("The Mehlemer Christmas market falls outside the current window")
    return SourceFetchResult.success(rc.fetch_html_events(
        "Ortsausschuss Mehlem", _MEHLEM_URL, _mehlemer_markt, source_id="ortsausschuss-mehlem",
    ))


def fetch_friesdorf() -> SourceFetchResult:
    window = run_state.runtime_window()
    if window.start.month not in (11, 12) and window.end.month not in (11, 12):
        return SourceFetchResult.scheduled_skip("The Friesdorf Christmas market falls outside the current window")
    return SourceFetchResult.success(rc.fetch_html_events(
        "Ortsausschuss Friesdorf 1929", _FRIESDORF_URL, _friesdorfer_markt, source_id="ortsausschuss-friesdorf",
    ))


def fetch_reviewed() -> SourceFetchResult:
    events: list[RawEvent] = []
    for item in json.loads(_PROGRAMMES.read_text(encoding="utf-8")):
        start = datetime.fromisoformat(item["start"])
        end = datetime.fromisoformat(item.get("end", item["start"]))
        if not event_builder.window_contains(start, end):
            continue
        event = event_builder.make_event(
            item["title"], start, end, item["venue"], item["city"], item["description"],
            item["url"], item["source"], item["category"], source_id=item["source_id"],
            all_day="T" not in item["start"], description_source="generated",
            default_category_key=item["category"], category_locked=True, link_kind="detail",
        )
        if event:
            event.update(item.get("facts", {}))
            events.append(event)
    return SourceFetchResult.success(events)
