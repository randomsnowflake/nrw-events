"""Public dance events from Salsa in Bonn e.V.'s official Tribe REST feed."""

import json
import re
import urllib.parse

from .. import common
from . import regional_common as rc


API_URL = "https://www.salsainbonn.de/wp-json/tribe/events/v1/events"
SOURCE = "Salsa in Bonn"
_DANCE_RE = re.compile(r"\b(?:salsa|bachata|kizomba|milonga|tanz(?:en|party)?)\b", re.I)
_MEETING_RE = re.compile(r"\b(?:mitglieder)?versammlung\b", re.I)


def _nightlife(event: dict) -> dict:
    return {
        **event,
        "category_key": "nightlife",
        "category_label": "Nachtleben & Party",
        "category_confidence": 0.99,
        "category_reason": "source:Salsa in Bonn e.V.; public dance-event filter",
    }


def _events_from_payload(payload: dict) -> list:
    events = []
    items = payload.get("events", []) if isinstance(payload, dict) else []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        title = common.clean_html(str(item.get("title") or ""))
        description = common.concise_description(str(item.get("description") or ""))
        text = f"{title} {description}"
        if not _DANCE_RE.search(text) or _MEETING_RE.search(text):
            continue
        venue_data = item.get("venue") if isinstance(item.get("venue"), dict) else {}
        venue = common.clean_html(str(venue_data.get("venue") or ""))
        city = common.clean_html(str(venue_data.get("city") or "Bonn"))
        start = common.parse_iso_date(str(item.get("start_date") or ""))
        end = common.parse_iso_date(str(item.get("end_date") or "")) or start
        description = description or common.factual_event_description(
            title, date_value=start, venue=venue, city=city
        )
        event = common.make_event(
            title, start, end, venue, city, description,
            str(item.get("url") or "https://www.salsainbonn.de/events/liste/"),
            SOURCE, "salsa bachata dance party nightlife", 0.98,
            source_id="salsa-in-bonn",
        )
        if event:
            cost = common.clean_html(str(item.get("cost") or ""))
            price = common.infer_free_admission_price(title, description, cost)
            event["price"] = price or cost
            events.append(_nightlife(event))
    return rc.dedupe(events)


def fetch() -> list:
    query = urllib.parse.urlencode({
        "per_page": 50,
        "start_date": common.TODAY.strftime("%Y-%m-%d"),
        "end_date": common.END_DATE.strftime("%Y-%m-%d"),
    })
    url = f"{API_URL}?{query}"
    try:
        raw = common.fetch_url(
            url, timeout=25, accept="application/json",
            sec_fetch_mode="cors", sec_fetch_dest="empty",
            expected_content_types=("application/json",),
        )
        payload = json.loads(raw)
        with common.capture_parser_metrics() as metrics:
            events = _events_from_payload(payload)
        parser_empty = not events and metrics["out_of_window_count"] == 0 and bool(payload.get("events"))
        common._record_endpoint(
            url, parser_type="tribe-rest-json", candidate_count=metrics["candidate_count"],
            out_of_window_count=metrics["out_of_window_count"],
            parsed_event_count=len(events), parser_empty=parser_empty,
        )
        if parser_empty:
            common.log_source_error(SOURCE, rc.ParserEmptyError("feed had records but no public dance events"))
        return events
    except Exception as exc:
        common.log_source_error(SOURCE, exc)
        return []
