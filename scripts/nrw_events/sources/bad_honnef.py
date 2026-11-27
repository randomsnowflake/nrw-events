"""Public Bad Honnef events from the Stadtjournal and Centrum calendars."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from .. import dates, event_builder, http, run_state, text
from ..health import SourceFetchResult
from ..models import RawEvent
from ..normalization import comparison_text
from .regional_common import dedupe_occurrences

_JOURNAL_URL = "https://www.stadtjournal.online/wp-json/tribe/events/v1/events"
_CENTRUM_URL = "https://innenstadt-bad-honnef.de/kaminzimer/"
_CITY_ADVENT_URL = "https://meinbadhonnef.de/pressezentrale/pressemitteilungen/vorweihnachtszeit/"
_CITY = "Bad Honnef"
_JOURNAL = "Stadtjournal Bad Honnef"
_JOURNAL_ID = "stadtjournal-bad-honnef"
_CENTRUM = "Centrum Bad Honnef"
_CENTRUM_ID = "centrum-bad-honnef"
_BERLIN = ZoneInfo("Europe/Berlin")
_DISTRICTS = {"rhoendorf": "Rhöndorf", "aegidienberg": "Aegidienberg", "selhof": "Selhof",
              "rommersdorf": "Rommersdorf", "bondorf": "Bondorf"}
# The prose lists each day's start without an end. The REST end clock is a placeholder.
_SELHOF_DAY = re.compile(
    r"\b(\d{1,2})\.\s*([A-Za-zÄÖÜäöü]+)\.?\s*(20\d{2}),?\s*(?:Uhr\s*)?ab\s*(\d{1,2})[.:](\d{2})",
    re.I,
)
_CITY_SPAN = re.compile(
    r"(?P<day>\d{1,2})\.\s*(?:(?P<month>[a-zä]+)\s+)?(?:(?P<year>20\d{2})\s+)?bis\s+"
    r"(?P<end_day>\d{1,2})\.\s*(?P<end_month>[a-zä]+)\s+(?P<end_year>20\d{2}),\s*"
    r"(?P<days>täglich|Freitag bis Sonntag) von (?P<opens>\d{1,2}) bis (?P<closes>\d{1,2}) Uhr",
    re.I,
)


def _district(locality: str) -> str | None:
    place = comparison_text(locality)
    if place in _DISTRICTS:
        return _DISTRICTS[place]
    if place == "bad honnef":
        return ""
    if place.startswith("bad honnef "):
        return _DISTRICTS.get(place.removeprefix("bad honnef "))
    return None


def _city_hours(html: str, title: str, start: date, end: date) -> tuple[str, str, int, int] | None:
    blocks = text.clean_html_blocks(html)
    for entry in re.finditer(rf"^{re.escape(title)}[ \t]+[–-][ \t]+([^\n]+)\n\s*([^\n]+)", blocks, re.M | re.I):
        match = _CITY_SPAN.fullmatch(entry[2].strip())
        if not match:
            continue
        end_month = dates.MONTH_ALL.get(match["end_month"].lower())
        start_month = dates.MONTH_ALL.get(match["month"].lower()) if match["month"] else end_month
        if not start_month or not end_month:
            continue
        try:
            last = date(int(match["end_year"]), end_month, int(match["end_day"]))
            first = date(int(match["year"] or match["end_year"]), start_month, int(match["day"]))
        except ValueError:
            continue
        opens, closes = int(match["opens"]), int(match["closes"])
        if (first, last) == (start, end) and 0 <= opens < closes <= 23:
            return entry[1].strip(), match["days"].lower(), opens, closes
    return None


def _advent_press() -> str:
    try:
        return http.fetch_url(_CITY_ADVENT_URL, timeout=12)
    except Exception as exc:
        run_state.log_source_error("Stadt Bad Honnef – Adventsprogramm", exc)
        return ""


def _selhof_slots(prose: str, start: date, end: date) -> list[dict[str, str]]:
    by_day = {}
    for day, month, year, hour, minute in _SELHOF_DAY.findall(prose):
        number = dates.MONTH_ALL.get(month.lower())
        if not number:
            continue
        try:
            opens = datetime(int(year), number, int(day), int(hour), int(minute), tzinfo=_BERLIN)
        except ValueError:
            continue
        if start <= opens.date() <= end:
            by_day[opens.date()] = {"date": opens.date().isoformat(), "start_at": opens.isoformat(), "end_at": ""}
    count = (end - start).days + 1
    return [by_day[day] for day in sorted(by_day)] if 0 < count <= 31 and len(by_day) == count else []


def _journal_event(item: dict, press: str) -> RawEvent | None:
    title = text.clean_html(str(item.get("title") or ""))
    link = str(item.get("url") or "")
    start = dates.parse_iso_date(str(item.get("start_date") or ""))
    end = dates.parse_iso_date(str(item.get("end_date") or "")) or start
    if item.get("hide_from_listings") or not title or not start or not end or end < start:
        return None
    if not link.startswith("https://www.stadtjournal.online/event/"):
        return None
    venue_data = item.get("venue")
    venue_data = venue_data if isinstance(venue_data, dict) else {}
    venue = text.clean_html(str(venue_data.get("venue") or ""))
    prose = text.clean_html(str(item.get("description") or ""))
    # Venue-less entries identify the place in the first sentence, e.g. "Rhöndorf."
    explicit_city = str(venue_data.get("city") or "").strip()
    district = _district(explicit_city or prose.split(".", 1)[0])
    if district is None:
        return None
    address = text.clean_html(str(venue_data.get("address") or ""))
    key = comparison_text(title)
    category = ""
    note = ""
    slots = []
    description = ""
    sources = []
    if key == "weihnachtsmarkt rhoendorf" and district == "Rhöndorf":
        title = "Rhöndorfer Weihnachtsmarkt"
        venue = "Ziepchensplatz" if "ziepchensplatz" in prose.casefold() else venue
        category = "market"
        description = "Am dritten Advent lädt der Rhöndorfer Weihnachtsmarkt zu Kunsthandwerk, regionalen Produkten und Essen im Ortskern ein."
    elif key == "advent im dorf" and district == "Aegidienberg":
        category = "market"
        if start.date() == date(2026, 11, 29):
            venue = "Platz vor St. Aegidius"
            sources = ["https://www.aegidienberg.de/pages/posts/weihnachtsmarkt-advent-im-dorf-3888.php"]
        description = "Die Aegidienberger Ortsvereine richten ihren Adventsmarkt mit Kunsthandwerk, Glühwein und Musik aus."
    elif key == "winterkino":
        category = "cinema"
        description = "Auf dem Dreiecksplatz zeigt das Winterkino Weihnachtsfilme unter freiem Himmel."
        if hours := _city_hours(press, "Winterkino", start.date(), end.date()):
            venue, days, opens, closes = hours
            if days == "freitag bis sonntag" and start.weekday() == 4 and (end - start).days == 2:
                note = f"Freitag bis Sonntag von {opens} bis {closes} Uhr"
                sources = [_CITY_ADVENT_URL]
        elif "dreiecksplatz am unteren markt" in prose.casefold():
            venue = "Dreiecksplatz am unteren Markt"
    elif key == "selhofer adventsalm" and district == "Selhof":
        venue = "Kelterplatz" if "kelterplatz" in prose.casefold() else venue
        category = "festival"
        slots = _selhof_slots(prose, start.date(), end.date())
        description = (
            "Die Selhofer Ortsvereine treffen sich im Advent am Kelterplatz. An allen Tagen gibt es Winzer-Glühwein. "
            "Zum Programm gehören der Abend unter dem Weihnachtsbaum und ein Familientag."
        )

    all_day = item.get("all_day") is True
    # A multi-day calendar envelope does not describe an uninterrupted event.
    if all_day or start.date() != end.date():
        start, end = datetime.combine(start.date(), datetime.min.time()), datetime.combine(end.date(), datetime.min.time())
    event = event_builder.make_event(
        title, start, end, venue, _CITY, description, link, _JOURNAL, title,
        0.78, all_day=all_day, source_id=_JOURNAL_ID, time_note=note,
        default_category_key=category, category_locked=bool(category), link_kind="detail",
    )
    if event is not None:
        if district:
            event["venue_district"] = district
        if address:
            zipcode = text.clean_html(str(venue_data.get("zip") or ""))
            event["venue_address"] = f"{address}, {' '.join(filter(None, (zipcode, _CITY)))}"
        if sources:
            event["source_links"] = [link, *sources]
        if slots:
            event["daily_schedule"] = slots
        if category == "market":
            event["event_types"] = ["christmas-market"]
    return event


def fetch_stadtjournal() -> SourceFetchResult:
    window = run_state.runtime_window()
    params = {
        "tags": "bad-honnef",
        "start_date": (window.start - timedelta(days=28)).date().isoformat(),
        "end_date": window.end.date().isoformat(),
        "per_page": 50,
    }
    events: list[RawEvent] = []
    press = None
    for page in range(1, 11):
        url = f"{_JOURNAL_URL}?{urlencode({**params, 'page': page})}"
        try:
            payload = http.fetch_json(url, timeout=15)
            items, pages = payload.get("events"), payload.get("total_pages")
            if not isinstance(items, list) or type(pages) is not int or not 0 <= pages <= 10:
                raise ValueError("invalid or oversized Stadtjournal calendar response")
        except Exception as exc:
            return SourceFetchResult.partial(dedupe_occurrences(events), str(exc))
        if press is None and any(isinstance(item, dict) and comparison_text(str(item.get("title"))) == "winterkino" for item in items):
            press = _advent_press()
        with run_state.capture_parser_metrics() as metrics:
            parsed = [event for item in items if isinstance(item, dict) and (event := _journal_event(item, press or ""))]
        http._record_endpoint(
            url, parser_type="tribe-rest-json", candidate_count=len(items),
            out_of_window_count=metrics["out_of_window_count"], parsed_event_count=len(parsed),
        )
        events.extend(parsed)
        if page >= pages:
            break
    return SourceFetchResult.success(dedupe_occurrences(events))


def _centrum_event(html: str, press: str) -> RawEvent | None:
    article = text.clean_html(html)
    match = re.search(r"\bBeginn:\s*(\d{1,2}\.\d{1,2}\.\d{2,4})\s+Ende:\s*(\d{1,2}\.\d{1,2}\.\d{2,4})", article)
    if not match:
        raise ValueError("Centrum Kaminzimmer listing does not contain a date range")
    fmt = ["%d.%m.%Y" if len(value.rsplit(".", 1)[-1]) == 4 else "%d.%m.%y" for value in match.groups()]
    start, end = (datetime.strptime(value, pattern) for value, pattern in zip(match.groups(), fmt, strict=True))
    if end < start:
        raise ValueError("Centrum Kaminzimmer date range is reversed")
    location = re.search(r"\bOrt:\s*([^<]+)</span>", html)
    venue = text.clean_html(location[1]) if location else ""
    note = ""
    if hours := _city_hours(press, "Kaminzimmer", start.date(), end.date()):
        venue, days, opens, closes = hours
        if days == "täglich":
            note = f"Täglich von {opens} bis {closes} Uhr"
    event = event_builder.make_event(
        "Kaminzimmer", start, end, venue, _CITY,
        "In der Berghütte des Centrums gibt es ein Kaminfeuer und warme Getränke. Die Hütte ist ein Treffpunkt für eine Pause beim Stadtbummel.",
        _CENTRUM_URL, _CENTRUM, "winter stadtfest",
        1.0, all_day=False, source_id=_CENTRUM_ID, time_note=note,
        default_category_key="festival", category_locked=True, link_kind="detail",
    )
    if event is not None:
        event["organizer"] = "Centrum Bad Honnef e. V."
        if note:
            event["source_links"] = [_CENTRUM_URL, _CITY_ADVENT_URL]
    return event


def fetch_centrum() -> SourceFetchResult:
    try:
        html = http.fetch_url(_CENTRUM_URL, timeout=15)
        with run_state.capture_parser_metrics() as metrics:
            event = _centrum_event(html, _advent_press())
        http._record_endpoint(
            _CENTRUM_URL, parser_type="centrum-advent-html", candidate_count=1,
            out_of_window_count=metrics["out_of_window_count"], parsed_event_count=int(event is not None),
        )
    except Exception as exc:
        return SourceFetchResult.partial([], str(exc))
    return SourceFetchResult.success([event] if event is not None else [])
