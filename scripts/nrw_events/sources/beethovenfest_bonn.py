"""Official Beethovenfest programme API."""

from __future__ import annotations

import urllib.parse

from .. import common, richtext
from . import regional_common as rc

API = "https://www.beethovenfest.de/de/api/events/"
BASE = "https://www.beethovenfest.de"
SOURCE = "Beethovenfest Bonn"

_VENUE_CITIES = {
    "burg namedy": "Andernach",
    "adenauerhaus, rhöndorf": "Bad Honnef",
    "burg adendorf": "Wachtberg",
    "steigenberger grandhotel petersberg": "Königswinter",
    "meys fabrik, hennef": "Hennef",
    "st. martinus, ollheim": "Swisttal",
    "kursaal bad honnef": "Bad Honnef",
    "james-simon-galerie, auditorium": "Berlin",
}


def _city_for_venue(venue: str) -> str:
    normalized = venue.casefold()
    for prefix, city in _VENUE_CITIES.items():
        if normalized == prefix or normalized.startswith(prefix + ","):
            return city
    return "Bonn"


def _localized(value) -> str:
    if isinstance(value, dict):
        return str(value.get("de") or value.get("en") or value.get("title") or "")
    return str(value or "")


def _events_from_items(items: list[dict]) -> list:
    events = []
    for item in items:
        if not isinstance(item, dict):
            continue
        title = common.clean_html(_localized(item.get("title")))
        start = common.parse_iso_date(str(item.get("date_and_time") or ""))
        venue_obj = item.get("venue_obj") if isinstance(item.get("venue_obj"), dict) else {}
        venue = common.clean_html(str(venue_obj.get("name") or ""))
        city = _city_for_venue(venue)
        if city == "Berlin":
            continue
        slug = urllib.parse.quote(_localized(item.get("slug")).strip("/"))
        link = f"{BASE}/de/programm-tickets/{slug}/{item.get('id')}" if slug and item.get("id") else BASE + "/de/programm-tickets/"
        raw_description = _localized(item.get("description"))
        description = common.clean_html_blocks(raw_description)
        genres = " ".join(common.clean_html(_localized(value)) for value in (item.get("genres") or []))
        genre_words = genres.casefold()
        default_category = (
            "talk" if "diskurs" in genre_words else
            "activities" if "mitmachen" in genre_words else
            "kids" if "kinder & familien" in genre_words else
            "stage" if "tanz, performance & musiktheater" in genre_words else
            "concert"
        )
        if not description:
            description = common.factual_event_description(
                title, date_value=start, time_text=start.strftime("%H:%M") if start else "",
                venue=venue, city=city, calendar_name="Beethovenfest Bonn",
            )
        # The API publishes a start timestamp but no end timestamp. Do not turn
        # that into a zero-duration structured interval; an unknown end is both
        # more accurate and publishable without an invariant warning.
        event = common.make_event(title, start, None, venue, city, description, link, SOURCE, f"classical music concert festival {genres}", 1.0, source_id="beethovenfest-bonn", description_source="scraped" if raw_description else "generated", default_category_key=default_category, category_locked=True)
        if not event:
            continue
        if raw_description:
            event["description_html"] = richtext.sanitize_rich_text(raw_description)
        status = str(item.get("button_status") or "").casefold()
        if status == "free":
            event["price"] = "kostenlos"
            event["admission_basis"] = "explicit"
        elif status == "sold_out":
            event["availability"] = "SoldOut"
        elif status == "remaining":
            event["availability"] = "LimitedAvailability"
        events.append(event)
    return rc.dedupe(events)


def fetch() -> list:
    items, url, expected = [], API, None
    try:
        for _page in range(20):
            payload = common.fetch_json(url, timeout=25)
            if not isinstance(payload, dict):
                raise ValueError("Beethovenfest API did not return an object")
            expected = int(payload.get("count") or 0)
            results = payload.get("results") or []
            if not isinstance(results, list):
                raise ValueError("Beethovenfest API results are not a list")
            items.extend(results)
            next_url = payload.get("next")
            if not next_url:
                break
            url = urllib.parse.urljoin(API, str(next_url))
        if expected and len(items) != expected:
            raise ValueError(f"incomplete Beethovenfest API: expected {expected}, received {len(items)}")
        with common.capture_parser_metrics() as metrics:
            events = _events_from_items(items)
        parser_empty = not events and metrics["out_of_window_count"] == 0
        common._record_endpoint(API, parser_type="paginated-json-api", candidate_count=metrics["candidate_count"], out_of_window_count=metrics["out_of_window_count"], parsed_event_count=len(events), parser_empty=parser_empty)
        if parser_empty:
            common.log_source_error(SOURCE, rc.ParserEmptyError("parser returned no event records"))
        return events
    except Exception as exc:
        common.log_source_error(SOURCE, exc)
        return []
