"""Haus der Geschichte Bonn — official calendar and public guided tours."""

import re
from datetime import datetime, timedelta

from .. import common
from . import regional_common as rc

_URL = "https://www.hdg.de/haus-der-geschichte/veranstaltungen"
_GUIDED_TOURS_URL = "https://www.hdg.de/haus-der-geschichte/begleitungen"
_SOURCE = "Haus der Geschichte"
_DEFAULT_VENUE = "Haus der Geschichte"
_PRIMARY_SOURCE_SCORE_FLOOR = 0.45
_WEEKDAYS = {
    "montag": 0,
    "dienstag": 1,
    "mittwoch": 2,
    "donnerstag": 3,
    "freitag": 4,
    "samstag": 5,
    "sonntag": 6,
}


def _text(value: str) -> str:
    return common.clean_html(value or "")


def _panel_blocks(html: str) -> list[str]:
    starts = [match.start() for match in re.finditer(r'<div class="panel\s+bonn"', html or "", re.I)]
    return [html[start:end] for start, end in zip(starts, [*starts[1:], len(html)], strict=False)]


def _embedded_family_tours(panel: str, date_text: str, link: str) -> list:
    """Expose separately timed family tours buried in umbrella-event prose."""
    text = _text(panel)
    match = re.search(
        r"Um\s+((?:\d{1,2}(?::\d{2})?\s*(?:Uhr)?\s*(?:,|und)\s*)+\d{1,2}(?::\d{2})?)\s*Uhr\s+"
        r"finden\s+Familienbegleitungen.*?zum\s+Thema\s+[„“\"]([^“”\"]+)[“”\"]",
        text,
        re.I,
    )
    if not match:
        return []
    events = []
    for hour, minute in re.findall(r"\b(\d{1,2})(?::(\d{2}))?\b", match.group(1)):
        start = datetime.strptime(date_text, "%Y%m%d").replace(
            hour=int(hour), minute=int(minute or 0)
        )
        end = start + timedelta(minutes=60)
        event = common.make_event(
            f"Familienbegleitung „{match.group(2).strip()}“",
            start,
            end,
            _DEFAULT_VENUE,
            "Bonn",
            "Öffentliche Familienbegleitung im Rahmen des Familienprogramms. Eintritt frei.",
            link,
            _SOURCE,
            "Museum Führung Familie Kinder",
            1.0,
            f"{start:%H:%M}–{end:%H:%M}",
            all_day=False,
        )
        if event:
            events.append(event)
    return events


def events_from_html(html: str) -> list:
    events = []
    for panel in _panel_blocks(html):
        date_match = re.search(r'data-date="(20\d{6})"', panel, re.I)
        title_match = re.search(r"<h4[^>]*>(.*?)</h4>", panel, re.S | re.I)
        if not (date_match and title_match):
            continue

        time_match = re.search(r'class="calendar-events-time"[^>]*>(.*?)</div>', panel, re.S | re.I)
        time_text = _text(time_match.group(1) if time_match else "")
        clock = re.search(r"\b(\d{1,2}):(\d{2})\b", time_text)
        start = datetime.strptime(date_match.group(1), "%Y%m%d")
        if clock:
            start = start.replace(hour=int(clock.group(1)), minute=int(clock.group(2)))

        heading_match = re.search(r"<h6[^>]*>(.*?)</h6>", panel, re.S | re.I)
        heading = heading_match.group(1) if heading_match else ""
        venue_match = re.search(r'<span[^>]*class="black"[^>]*>(.*?)</span>', heading, re.S | re.I)
        explicit_venue = _text(venue_match.group(1) if venue_match else "")
        venue = explicit_venue or _DEFAULT_VENUE
        venue = re.split(r",\s*(?:[A-ZÄÖÜ][^,]+\s+)?\d{1,5}\b", venue, maxsplit=1)[0].strip()
        category = _text(re.sub(r"<span\b.*?</span>", "", heading, flags=re.S | re.I)) or "Museum"

        description_match = re.search(
            r'class="[^"]*calendar-bodycopy[^"]*"[^>]*>(.*?)</div>', panel, re.S | re.I
        )
        description = _text(description_match.group(1) if description_match else "")
        if description_match:
            description = " ".join(filter(None, [description, _text(panel[:description_match.start()])]))

        link_match = re.search(r'<a[^>]*class="hidden"[^>]*href="([^"]+)"', panel, re.S | re.I)
        link = common.urllib.parse.urljoin(_URL, link_match.group(1)) if link_match else _URL
        event = common.make_event(
            _text(title_match.group(1)), start, None, venue, "Bonn", description,
            link, _SOURCE, category, 1.0, start.strftime("%H:%M") if clock else "",
            all_day=not bool(clock),
        )
        if event:
            events.append(event)
        events.extend(_embedded_family_tours(panel, date_match.group(1), link))
    events = rc.dedupe_occurrences(events)
    for event in events:
        # The global ranking deliberately downranks kids-only listings. This
        # requested first-party museum programme must still reach publication.
        event["score"] = max(float(event.get("score") or 0), _PRIMARY_SOURCE_SCORE_FLOOR)
    return events


def _guided_tour_sections(html: str) -> list[tuple[str, str]]:
    starts = list(re.finditer(r"<h4[^>]*>(.*?)</h4>", html or "", re.S | re.I))
    return [
        (
            _text(match.group(1)),
            html[match.end():(starts[index + 1].start() if index + 1 < len(starts) else len(html))],
        )
        for index, match in enumerate(starts)
    ]


def guided_tours_from_html(html: str) -> list:
    """Expand explicitly published weekly public tours inside the run window."""
    definitions = []
    for heading, section in _guided_tour_sections(html):
        if "Begleitungen zur Wechselausstellung" in heading:
            exhibition = re.search(r'[„“\"]([^“”\"]+)[“”\"]', heading)
            title = (
                f"Öffentliche Begleitung „{exhibition.group(1)}“"
                if exhibition
                else "Öffentliche Begleitung zur Wechselausstellung"
            )
        elif "Begleitungen im Museumsgarten" in heading:
            title = "Öffentliche Begleitung im Museumsgarten"
        else:
            continue
        text = _text(section)
        if "Öffentliche Begleitungen" not in text:
            continue
        time_match = re.search(r"(?:jeweils\s+)?um\s+(\d{1,2})(?::(\d{2}))?\s*Uhr", text, re.I)
        if not time_match:
            continue
        weekday_names = re.findall(
            r"Montag|Dienstag|Mittwoch|Donnerstag|Freitag|Samstag|Sonntag",
            text[:time_match.start()],
            re.I,
        )
        weekdays = sorted({_WEEKDAYS[name.casefold()] for name in weekday_names})
        if not weekdays:
            continue
        link_match = re.search(r'<a[^>]+href="([^"]+)"[^>]*>[^<]*Buchungsportal', section, re.S | re.I)
        link = common.urllib.parse.urljoin(_GUIDED_TOURS_URL, link_match.group(1)) if link_match else _GUIDED_TOURS_URL
        definitions.append((title, weekdays, int(time_match.group(1)), int(time_match.group(2) or 0), link))

    events = []
    cursor = common.TODAY
    while cursor <= common.END_DATE:
        for title, weekdays, hour, minute, link in definitions:
            if cursor.weekday() not in weekdays:
                continue
            start = cursor.replace(hour=hour, minute=minute)
            event = common.make_event(
                title,
                start,
                None,
                _DEFAULT_VENUE,
                "Bonn",
                "Öffentliche Begleitung. Eintritt frei. Anmeldung erforderlich.",
                link,
                _SOURCE,
                "Museum Führung",
                1.0,
                start.strftime("%H:%M"),
                all_day=False,
                source_id="haus-der-geschichte-begleitungen",
            )
            if event:
                events.append(event)
        cursor += timedelta(days=1)
    return rc.dedupe_occurrences(events)


def fetch_calendar() -> list:
    return rc.fetch_html_events(
        _SOURCE,
        _URL,
        lambda document: events_from_html(document),
        timeout=30,
        source_id="haus-der-geschichte",
    )


def fetch_guided_tours() -> list:
    return rc.fetch_html_events(
        "Haus der Geschichte Begleitungen",
        _GUIDED_TOURS_URL,
        lambda document: guided_tours_from_html(document),
        timeout=30,
        source_id="haus-der-geschichte-begleitungen",
    )


def fetch() -> list:
    """Compatibility wrapper for callers outside the source registry."""
    return rc.dedupe_occurrences(fetch_calendar() + fetch_guided_tours())
