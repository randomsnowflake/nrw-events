"""Scheduled Bonn neighborhood flea markets from HofFloh's public API."""

import json
import re

from .. import common
from . import regional_common as rc


_SOURCE = "HofFloh Bonn"
_SOURCE_ID = "hoffloh-bonn"
_API_URL = (
    "https://api.hoffloh.de/api/featured-date/search"
    "?page={page}&limit=100&sortBy=date&sortOrder=asc&city=Bonn&includeEmptyDate=true"
)


def _events_from_payload(payload: dict) -> list:
    events = []
    for item in payload.get("items", []) if isinstance(payload, dict) else []:
        if not isinstance(item, dict):
            continue
        date_text = str(item.get("featuredDate") or item.get("date") or "").strip()
        district = common.clean_html(str(item.get("districtName") or "")).strip()
        city = common.clean_html(str(item.get("city") or "")).strip()
        event_id = str(item.get("id") or "").strip()
        if not (date_text and district and city.casefold() == "bonn" and event_id):
            continue
        start = common.parse_date(date_text)
        start_time = _parse_time(str(item.get("startTime") or ""))
        end_time = _parse_time(str(item.get("endTime") or ""))
        if not (start and start_time and end_time):
            continue
        start = start.replace(hour=start_time[0], minute=start_time[1])
        end = start.replace(hour=end_time[0], minute=end_time[1])
        if not common.window_contains(start, end):
            continue
        count = item.get("count")
        stand_text = f" Aktuell sind {count} Stände angemeldet." if isinstance(count, int) and count > 0 else ""
        event = common.make_event(
            f"Hofflohmarkt Bonn-{district}",
            start,
            end,
            f"Stadtteil {district}",
            "Bonn",
            (
                f"Nachbarschaftlicher Hofflohmarkt in Bonn-{district}: "
                f"Private Höfe, Einfahrten und Garagen öffnen zum Stöbern.{stand_text}"
            ),
            f"https://www.hoffloh.de/events/{common.urllib.parse.quote(event_id, safe='')}",
            _SOURCE,
            "hofflohmarkt flohmarkt nachbarschaft markt",
            0.96,
            f"{start_time[0]:02d}:{start_time[1]:02d}–{end_time[0]:02d}:{end_time[1]:02d}",
            source_id=_SOURCE_ID,
        )
        if event:
            events.append(event)
    return rc.dedupe(events)


def _parse_time(value: str):
    match = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", value or "")
    if not match:
        return None
    hour, minute = (int(part) for part in match.groups())
    return (hour, minute) if hour <= 23 and minute <= 59 else None


def fetch() -> list:
    events = []
    page = 1
    total_pages = 1
    while page <= total_pages and page <= 20:
        url = _API_URL.format(page=page)
        try:
            payload = json.loads(common.fetch_url(
                url,
                timeout=20,
                accept="application/json,*/*;q=0.8",
                sec_fetch_mode="cors",
                sec_fetch_dest="empty",
            ))
            if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
                raise rc.ParserEmptyError("HofFloh API contract changed")
            parsed = _events_from_payload(payload)
            common._record_endpoint(
                url,
                parser_type="json",
                candidate_count=len(payload["items"]),
                parsed_event_count=len(parsed),
                parser_empty=False,
            )
            events.extend(parsed)
            total_pages = max(1, int(payload.get("totalPages") or 1))
        except Exception as exc:
            common.log_source_error(_SOURCE, exc, source_id=_SOURCE_ID)
            break
        page += 1
    return rc.dedupe(events)
