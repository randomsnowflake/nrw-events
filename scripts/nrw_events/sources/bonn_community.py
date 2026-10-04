"""First-party organiser calendars that used to reach the site only via Bonn.jetzt.

Each parser reads the organiser's own dated listing (or, for Datenburg, its
explicitly published weekly opening). Bonn.jetzt stays registered as a
fallback; ``bonn_jetzt_fallbacks`` drops its card once one of these records
covers the same occurrence.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import datetime, timedelta
from urllib.parse import urlencode, urljoin

from ..dates import parse_date
from ..detail_cache import fetch_detail_url
from ..event_builder import make_event
from ..ical import fetch_ical
from ..run_state import log_source_error, runtime_window
from ..text import clean_html
from . import regional_common as rc

# --- Studierendenwerk Bonn -------------------------------------------------

STW_URL = "https://www.studierendenwerk-bonn.de/internationales-kultur/veranstaltungen"
STW_SOURCE = "Studierendenwerk Bonn"
STW_SOURCE_ID = "studierendenwerk-bonn"
_STW_ITEM = re.compile(r'<li aria-labelledby="news-headline-\d+".*?</li>', re.S)
_HOUR_RANGE = re.compile(
    r"(\d{1,2})(?:[:.](\d{2}))?\s*(?:-|–|bis)\s*(\d{1,2})(?:[:.](\d{2}))?\s*Uhr", re.I,
)


def _hour_range(text: str) -> str:
    match = _HOUR_RANGE.search(text or "")
    if not match:
        return rc.time_text(text)
    start_h, start_m, end_h, end_m = match.groups()
    return f"{int(start_h):02d}:{start_m or '00'}–{int(end_h):02d}:{end_m or '00'}"


def _labelled(body_text: str, label: str) -> str:
    """Return the value after ``Wann:``/``Wo?``-style labels in an article body."""
    match = re.search(
        rf"\b{label}\s*[:?]\s*(.+?)(?=\s+(?:Wann|Wo|Wie|Eintritt|Aktuelle Informationen|Weitere Informationen)\b\s*[:?]?|$)",
        body_text,
        re.I,
    )
    return match.group(1).strip(" .") if match else ""


def events_from_studierendenwerk(listing: str, read_detail: Callable[[str], str]) -> list:
    events = []
    for block in _STW_ITEM.findall(listing):
        link_match = re.search(r'href="([^"]+)"', block)
        date_match = re.search(r'datetime="(\d{2}\.\d{2}\.\d{4})"', block)
        title_match = re.search(r'itemprop="headline">(.*?)<', block, re.S)
        if not (link_match and date_match and title_match):
            continue
        title = clean_html(title_match.group(1))
        # "Spieleabend - jeden 1. Montag im Monat": the listing date is the next
        # occurrence; the recurrence suffix is schedule copy, not the title.
        title = re.sub(r"\s*[-–]\s*jeden\b.*$", "", title, flags=re.I) or title
        link = urljoin(STW_URL, link_match.group(1))
        start = parse_date(date_match.group(1))
        teaser_match = re.search(r'itemprop="description"[^>]*>(.*?)</div>', block, re.S)
        teaser = clean_html(teaser_match.group(1)) if teaser_match else ""
        try:
            detail = read_detail(link)
        except Exception as exc:
            log_source_error(f"{STW_SOURCE} detail", exc, source_id=STW_SOURCE_ID)
            detail = ""
        body_match = re.search(r'itemprop="articleBody">(.*)', detail, re.S)
        body = body_match.group(1) if body_match else ""
        body_text = clean_html(body)
        when = _labelled(body_text, "Wann")
        where = _labelled(body_text, "Wo")
        venue, _, address = where.partition(",")
        intro = clean_html(re.split(r"<strong>\s*Wann", body, maxsplit=1)[0]) if body else ""
        event = make_event(
            title, start, None, venue.strip(), "Bonn", intro or teaser, link,
            STW_SOURCE, "Studierende Kultur", 1.0,
            time_text=_hour_range(when), source_id=STW_SOURCE_ID,
        )
        if not event:
            continue
        if address.strip():
            event["venue_address"] = address.strip()
        if re.search(r"\bEintritt\s*:\s*frei\b|\bkostenfrei\b", body_text, re.I):
            event["price"] = "kostenlos"
            event["admission_basis"] = "explicit"
        events.append(event)
    return events


def fetch_studierendenwerk() -> list:
    def read_detail(url: str) -> str:
        return fetch_detail_url(url, cache_namespace="studierendenwerk-bonn-v1", timeout=20)

    return rc.fetch_html_events(
        STW_SOURCE, STW_URL,
        lambda listing: events_from_studierendenwerk(listing, read_detail),
        source_id=STW_SOURCE_ID,
    )


# --- Käpt'n Book Lesefest --------------------------------------------------

KB_URL = "https://kaeptnbook-lesefest.de/veranstaltungen"
KB_SOURCE = "Käpt’n Book Lesefest"
KB_SOURCE_ID = "kaeptn-book-lesefest"
_KB_DAY = re.compile(r'<div class="h3 mb-0">\s*(.*?)\s*</div>', re.S)


def events_from_kaeptn_book(document: str) -> list:
    """Publish the festival once, spanning its first-party dated programme.

    The ~140 programme rows are mostly already published by the hosting venues
    (museums, Bonn.de); importing them individually would duplicate those.
    """
    reference = runtime_window().start
    days = sorted(filter(None, (
        parse_date(clean_html(raw), reference_date=reference)
        for raw in _KB_DAY.findall(document)
    )))
    if not days:
        return []
    first, last = days[0], days[-1]
    event = make_event(
        f"Käpt’n Book Lesefest {first.year}", first, last, "Bonn und Region", "Bonn",
        f"Kinder- und Jugendbuchfestival mit {len(days)} Lesungen, Theaterstücken und "
        "Familienfesten in Bonn und der Region.",
        KB_URL, KB_SOURCE, "Lesung Kinder Literatur Festival", 1.0, source_id=KB_SOURCE_ID,
    )
    return [event] if event else []


def fetch_kaeptn_book() -> list:
    return rc.fetch_html_events(KB_SOURCE, KB_URL, events_from_kaeptn_book, source_id=KB_SOURCE_ID)


# --- Datenburg -------------------------------------------------------------

DATENBURG_URL = "https://datenburg.org/"
DATENBURG_SOURCE = "Datenburg"
DATENBURG_SOURCE_ID = "datenburg"
_BURGABEND = re.compile(
    r"Die Datenburg öffnet jeden Dienstag ab (\d{1,2}) Uhr ihre Tore in der (.+?) in der Bonner Altstadt"
    r"(.*?Neugier!)",
    re.S,
)


def events_from_datenburg(document: str) -> list:
    """Expand the organiser's explicitly published weekly opening."""
    text = clean_html(document)
    match = _BURGABEND.search(text)
    if not match:
        raise rc.ParserEmptyError("Datenburg weekly opening marker missing")
    hour, street = int(match.group(1)), match.group(2).strip()
    description = match.group(0).strip()
    cursor = runtime_window().start
    while cursor.weekday() != 1:
        cursor += timedelta(days=1)
    events = []
    while cursor <= runtime_window().end:
        start = cursor.replace(hour=hour, minute=0, second=0, microsecond=0)
        event = make_event(
            "Offener Burgabend", start, None, "Datenburg", "Bonn", description,
            DATENBURG_URL, DATENBURG_SOURCE, "Hackspace Technik Treffen", 1.0,
            time_text=f"{hour:02d}:00", source_id=DATENBURG_SOURCE_ID,
        )
        if event:
            event["venue_address"] = street
            events.append(event)
        cursor += timedelta(days=7)
    return events


def fetch_datenburg() -> list:
    return rc.fetch_html_events(
        DATENBURG_SOURCE, DATENBURG_URL, events_from_datenburg, source_id=DATENBURG_SOURCE_ID,
    )


# --- bitcircus101 ----------------------------------------------------------

BITCIRCUS_ICAL = "https://bitcircus101.de/ical.ics"
BITCIRCUS_SOURCE = "bitcircus101"
BITCIRCUS_SOURCE_ID = "bitcircus101"


def _at_bitcircus(props: dict[str, str], _start: datetime, _end: datetime) -> bool:
    # The hackspace calendar also lists partner events elsewhere (Uni Bonn,
    # Datenburg); those have their own organisers. Keep only the own space.
    return "dorotheenstra" in props.get("LOCATION", "").casefold()


def fetch_bitcircus() -> list:
    events = fetch_ical(
        BITCIRCUS_ICAL, BITCIRCUS_SOURCE, "Bonn", "Hackspace Technik Treffen", 1.0,
        BITCIRCUS_SOURCE_ID, event_filter=_at_bitcircus,
    )
    for event in events:
        event["venue_address"] = event.get("venue_address") or event.get("venue", "")
        event["venue"] = "bitcircus101"
    return events


# --- VHS Bonn --------------------------------------------------------------

VHS_SOURCE = "VHS Bonn"
VHS_SOURCE_ID = "vhs-bonn"
_VHS_PLUGIN_URL = "//www.vhs-bonn.de/programm/politik-wissenschaft-und-internationales.html"
# "Politik, Wissenschaft & Internationales": lectures, excursions and open
# days. Multi-session courses are skipped below; they are not events.
_VHS_CATEGORY = "484-CAT-KAT126"
VHS_LIST_URL = "https://www.vhs-bonn.de/page_/VhsConnectSearch?action=ajaxSearch&" + urlencode({
    "socketsId": "143",
    "filters[params]": json.dumps({
        "sort": ["startDate,asc"], "extraFields": ["title", "startDate"],
        "hideEmptyCategories": True, "catId": _VHS_CATEGORY,
    }),
    "filters[page]": "1",
    "filters[pageSize]": "200",
    "course_url": f"{_VHS_PLUGIN_URL}?action%5B143%5D=course",
    "plugin_url": _VHS_PLUGIN_URL,
    "sockets_ID": "143",
})
_VHS_ROW = re.compile(r"<tr data-href.*?</tr>", re.S)
_VHS_ICAL = "https://www.vhs-bonn.de/page_/VhsConnect/serveIcal/courseId/{course_id}/f/kurs.ics"


def _ical_local(raw: str, name: str) -> datetime | None:
    match = re.search(rf"^{name}(?:;TZID=Europe/Berlin)?:(\d{{8}}T\d{{4}})", raw, re.M)
    return datetime.strptime(match.group(1), "%Y%m%dT%H%M") if match else None


def _vhs_rows(listing: str) -> list[tuple[str, str, str, datetime]]:
    window = runtime_window()
    rows = []
    for row in _VHS_ROW.findall(listing):
        course = re.search(r"courseId=([^&\"]+)", row)
        title_match = re.search(r'class="title">(.*?)</a>', row, re.S)
        date_match = re.search(r'<td class="startDate[^>]*>\s*\w+\.,\s*(\d{2}\.\d{2}\.\d{4})', row)
        if not (course and title_match and date_match):
            continue
        if re.search(r"CourseCancelled|CourseCompleted|label-Online", row) or course.group(1).endswith("ON"):
            continue
        day = parse_date(date_match.group(1))
        if not day or not window.start <= day <= window.end:
            continue
        subtitle_match = re.search(r'<span class="subtitle">(.*?)</span>', title_match.group(1), re.S)
        subtitle = clean_html(subtitle_match.group(1)) if subtitle_match else ""
        title = clean_html(re.sub(r"<span.*?</span>", "", title_match.group(1), flags=re.S))
        rows.append((course.group(1), title, subtitle, day))
    return rows


def events_from_vhs(listing: str, read_calendar: Callable[[str], str]) -> list:
    events = []
    for course_id, title, subtitle, _day in _vhs_rows(listing):
        try:
            calendar = read_calendar(_VHS_ICAL.format(course_id=course_id))
        except Exception as exc:
            log_source_error(f"{VHS_SOURCE} course calendar", exc, source_id=VHS_SOURCE_ID)
            continue
        if calendar.count("BEGIN:VEVENT") != 1:
            continue  # multi-session course, not a single event
        # The VTIMEZONE block precedes the event and carries its own DTSTARTs.
        calendar = calendar[calendar.index("BEGIN:VEVENT"):]
        start, end = _ical_local(calendar, "DTSTART"), _ical_local(calendar, "DTEND")
        location_match = re.search(r"^LOCATION:(.*)$", calendar, re.M)
        location = (location_match.group(1) if location_match else "").replace("\\,", ",").strip()
        parts = list(dict.fromkeys(
            part.strip() for part in location.split(",")
            if part.strip() and "adresse folgt" not in part.casefold()
        ))
        venue = "VHS Bonn" if parts[:1] == ["VHS"] else (parts[0] if parts else "")
        number = course_id.rsplit("-", 1)[-1]
        event = make_event(
            f"{title} – {subtitle}" if subtitle else title, start, end, venue,
            rc.city_from_text(location, "Bonn") or "Bonn", subtitle,
            f"https://www.vhs-bonn.de/kurs/{number}", VHS_SOURCE, "Vortrag Bildung", 1.0,
            time_text=f"{start:%H:%M}–{end:%H:%M}" if start and end else "",
            source_id=VHS_SOURCE_ID,
        )
        if event:
            if len(parts) > 1:
                event["venue_address"] = ", ".join(parts[1:])
            events.append(event)
    return events


def fetch_vhs() -> list:
    def read_calendar(url: str) -> str:
        return fetch_detail_url(
            url, cache_namespace="vhs-bonn-course-ical-v1", timeout=20,
            accept="text/calendar,*/*;q=0.8",
        ).replace("\r", "")

    return rc.fetch_html_events(
        VHS_SOURCE, VHS_LIST_URL,
        lambda listing: events_from_vhs(listing, read_calendar),
        timeout=45, source_id=VHS_SOURCE_ID,
    )
