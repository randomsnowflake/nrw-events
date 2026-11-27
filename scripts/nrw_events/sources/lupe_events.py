"""Bonner Kirmessen und Märkte aus den offiziellen Tribe-REST-Kalendern."""

from __future__ import annotations

import re
from datetime import datetime, time, timedelta
from urllib.parse import urlencode, urlsplit

from .. import dates, event_builder, http, run_state, text
from ..health import SourceFetchResult
from ..location import guess_city_from_text, refine_bonn_location
from ..models import RawEvent
from . import regional_common as rc

_LUPE_URL = "https://lupe-events.de/wp-json/tribe/events/v1/events"
_LANNESDORF_URL = "https://ortsausschuss-lannesdorf.de/wp-json/tribe/events/v1/events"
_STOFFMARKT_URL = "https://deinstoffmarkt.de/wp-json/tribe/events/v1/events"
_LESSENICH_URL = "https://lupe-events.de/event/kirmes-in-lessenich-2026"

_LUPE_2026_VENUES = {
    "/event/kirmes-in-ippendorf-2026": "Bernhard-Berzheim-Platz",
    "/event/kirmes-in-endenich-2026": "Magdalenenplatz",
    "/event/kirmes-in-lessenich-2026": "Dorfplatz Lessenich",
    "/event/kirmes-in-roettgen-2026": "Herzogsfreudenweg",
    "/event/kirmes-in-lengsdorf-2026": "Dorfplatz Lengsdorf",
}


def _event(item: dict, source: str, source_id: str) -> RawEvent | None:
    if item.get("hide_from_listings"):
        return None
    title = text.clean_html(str(item.get("title") or ""))
    link = str(item.get("url") or "")
    start = dates.parse_iso_date(str(item.get("start_date") or ""))
    end = dates.parse_iso_date(str(item.get("end_date") or ""))
    if not title or not link.startswith("https://") or not start or not end or end < start:
        return None
    venue_data = item.get("venue")
    venue_data = venue_data if isinstance(venue_data, dict) else {}
    venue = text.clean_html(str(venue_data.get("venue") or ""))
    city = text.clean_html(str(venue_data.get("city") or ""))
    all_day = item.get("all_day") is True
    if all_day:
        # LuPe sets the TEC day cutoff to 05:00. The final 04:59:59 is
        # the end of the previous event day, like the standard 23:59:59.
        if start.time() == time(5) and end.time() == time(4, 59, 59) and end.date() > start.date():
            end -= timedelta(days=1)
        start = datetime.combine(start.date(), time.min)
        end = datetime.combine(end.date(), time.min)

    category = "festival"
    if source_id == "lupe-events":
        path = urlsplit(link).path.rstrip("/")
        venue = venue or _LUPE_2026_VENUES.get(path, "")
        if link.rstrip("/") == _LESSENICH_URL:
            title = "Laurentius-Kirmes"
            end = datetime(2026, 8, 16)
        city = city or guess_city_from_text(f"{venue} {title}") or "Bonn"
    elif source_id == "ortsausschuss-lannesdorf":
        if not re.search(r"\bkirmes\b", title, re.I):
            return None
        title = "Kirmes Lannesdorf" if title.casefold() == "kirmes" else title
        # The organizer puts map coordinates in the REST city field.
        city = "Bonn-Lannesdorf"
    else:
        if city.casefold() != "bonn":
            return None
        category = "market"

    city = refine_bonn_location(city, f"{title} {venue}")
    description = text.concise_description(str(item.get("description") or ""), max_chars=0)
    return event_builder.make_event(
        title, start, end, venue, city, description, link, source,
        "stoffmarkt" if category == "market" else "kirmes volksfest",
        source_id=source_id, all_day=all_day, default_category_key=category,
        category_locked=True, link_kind="detail",
    )


def _fetch(endpoint: str, source: str, source_id: str) -> SourceFetchResult:
    window = run_state.runtime_window()
    params = {
        "per_page": 50,
        "start_date": window.start.date().isoformat(),
        "end_date": window.end.date().isoformat(),
    }
    events: list[RawEvent] = []
    for page in range(1, 11):
        url = f"{endpoint}?{urlencode({**params, 'page': page})}"
        try:
            payload = http.fetch_json(url, timeout=20)
            items = payload.get("events")
            total_pages = payload.get("total_pages")
            if not isinstance(items, list) or type(total_pages) is not int or not 0 <= total_pages <= 10:
                raise ValueError("invalid or oversized Tribe calendar response")
        except Exception as exc:
            return SourceFetchResult.partial(rc.dedupe(events), str(exc))
        with run_state.capture_parser_metrics() as metrics:
            parsed = [
                event for item in items if isinstance(item, dict)
                if (event := _event(item, source, source_id))
            ]
        parser_empty = (
            source_id == "lupe-events" and not parsed and metrics["candidate_count"] == 0
            and any(isinstance(item, dict) and not item.get("hide_from_listings") for item in items)
        )
        http._record_endpoint(
            url, parser_type="tribe-rest-json", candidate_count=len(items),
            out_of_window_count=metrics["out_of_window_count"],
            parsed_event_count=len(parsed), parser_empty=parser_empty,
        )
        if parser_empty:
            return SourceFetchResult.partial(rc.dedupe(events), "LuPe REST rows did not contain valid events")
        events.extend(parsed)
        if page >= total_pages:
            return SourceFetchResult.success(rc.dedupe(events))
    return SourceFetchResult.success(rc.dedupe(events))


def fetch() -> SourceFetchResult:
    return _fetch(_LUPE_URL, "LuPe Events", "lupe-events")


def fetch_lannesdorf() -> SourceFetchResult:
    return _fetch(_LANNESDORF_URL, "Ortsausschuss Lannesdorf", "ortsausschuss-lannesdorf")


def fetch_stoffmarkt() -> SourceFetchResult:
    return _fetch(_STOFFMARKT_URL, "Dein Stoffmarkt", "dein-stoffmarkt-bonn")
