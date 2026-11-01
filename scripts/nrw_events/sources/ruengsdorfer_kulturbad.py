"""Full programme of the Rüngsdorfer KulturBad from its official Tribe REST feed."""

import re
import urllib.parse
from datetime import timedelta

from .. import dates, event_builder, http, run_state, text
from ..location import refine_bonn_location
from ..models import RawEvent
from . import regional_common as rc

API_URL = "https://ruengsdorfer-kulturbad.de/wp-json/tribe/events/v1/events"
SOURCE = "Rüngsdorfer KulturBad"
SOURCE_ID = "ruengsdorfer-kulturbad"
_SCHEDULE_PREFIX = re.compile(
    r"^(?:\d{1,2}\.\d{1,2}(?:\.\d{2,4})?\s*\|\s*)?\d{1,2}[.:]\d{2}\s*Uhr\s*:\s*",
    re.I,
)
_SOLD_OUT_PREFIX = re.compile(r"^ausverkauft\s*!*\s*", re.I)
# Keyword classification reads "tanzen" as sports and "Karneval" as festival.
_CATEGORY_KEYS = {
    "jazz im kulturbad": "concert",
    "kabarett": "stage",
    "lesung": "talk",
    "palm am sonntag": "concert",
    "tanzabend": "nightlife",
    "theater": "stage",
}
# Uncategorised shows: Songs ("kreativ" reads as workshop), open stage ("feiern" reads as party).
_TITLE_CATEGORY_KEYS = (
    (re.compile(r"\btribute\b", re.I), "concert"),
    (re.compile(r"\b(?:talentbühne|talentturm)\b", re.I), "stage"),
)
# The feed gives one jazz matinee an 18:00 end although its text only names 11:30.
_MAX_CONCERT_LENGTH = timedelta(hours=4)
_MAX_PAGES = 10


def _price(item: dict) -> str:
    """Prefer the source's ticket values; its currency_code is a wrong "USD" default."""
    details = item.get("cost_details")
    values = details.get("values") if isinstance(details, dict) else None
    amounts = [str(value).replace(".", ",") for value in values or []
               if re.fullmatch(r"\d+(?:\.\d+)?", str(value))]
    if amounts:
        return f"{'–'.join(dict.fromkeys(amounts))} €"
    return text.clean_html(str(item.get("cost") or ""))


def _category_key(title: str, categories: list[str]) -> str:
    for name in categories:
        if key := _CATEGORY_KEYS.get(name.casefold()):
            return key
    return next((key for pattern, key in _TITLE_CATEGORY_KEYS if pattern.search(title)), "")


def _event(item: dict) -> RawEvent | None:
    if item.get("hide_from_listings"):
        return None
    raw_title = text.clean_html(str(item.get("title") or ""))
    title = _SOLD_OUT_PREFIX.sub("", raw_title)
    sold_out = title != raw_title
    title = _SCHEDULE_PREFIX.sub("", title)
    venue_value = item.get("venue")
    venue_data = venue_value if isinstance(venue_value, dict) else {}
    venue = text.clean_html(str(venue_data.get("venue") or ""))
    city = text.clean_html(str(venue_data.get("city") or "Bonn"))
    street = text.clean_html(str(venue_data.get("address") or ""))
    postcode = text.clean_html(str(venue_data.get("zip") or ""))
    start = dates.parse_iso_date(str(item.get("start_date") or ""))
    end = dates.parse_iso_date(str(item.get("end_date") or "")) or start
    description = text.concise_description(str(item.get("description") or ""))
    categories = [
        str(category.get("name") or "")
        for category in item.get("categories") or [] if isinstance(category, dict)
    ]
    category_key = _category_key(title, categories)
    if category_key == "concert" and start and end and end - start > _MAX_CONCERT_LENGTH:
        end = start
    event = event_builder.make_event(
        title, start, end, venue, refine_bonn_location(city, postcode), description,
        str(item.get("url") or ""), SOURCE, " ".join(["kultur", *categories]), 0.98,
        source_id=SOURCE_ID, default_category_key=category_key, category_locked=bool(category_key),
    )
    if not event:
        return None
    if street and postcode:
        event["venue_address"] = f"{street}, {postcode} {city}"
    price = _price(item)
    if price:
        event["price"] = price
        event["admission_basis"] = "explicit"
    if sold_out:
        event["availability"] = "SoldOut"
    return event


def _events_from_payload(payload: dict) -> list:
    items = payload.get("events", []) if isinstance(payload, dict) else []
    events = [_event(item) for item in items if isinstance(item, dict)] if isinstance(items, list) else []
    return rc.dedupe([event for event in events if event])


def _page_url(page: int) -> str:
    return f"{API_URL}?" + urllib.parse.urlencode({
        "per_page": 50,
        "page": page,
        "start_date": run_state.runtime_window().start.strftime("%Y-%m-%d"),
        "end_date": run_state.runtime_window().end.strftime("%Y-%m-%d"),
    })


def fetch() -> list:
    url = _page_url(1)
    try:
        payload = http.fetch_json(url, timeout=25)
        items = list(payload.get("events") or [])
        for page in range(2, min(int(payload.get("total_pages") or 1), _MAX_PAGES) + 1):
            items.extend(http.fetch_json(_page_url(page), timeout=25).get("events") or [])
        with run_state.capture_parser_metrics() as metrics:
            events = _events_from_payload({"events": items})
        parser_empty = not events and metrics["out_of_window_count"] == 0 and bool(items)
        http._record_endpoint(
            url, parser_type="tribe-rest-json", candidate_count=metrics["candidate_count"],
            out_of_window_count=metrics["out_of_window_count"],
            parsed_event_count=len(events), parser_empty=parser_empty,
        )
        if parser_empty:
            run_state.log_source_error(SOURCE, rc.ParserEmptyError("feed had records but no parsable events"))
        return events
    except Exception as exc:
        run_state.log_source_error(SOURCE, exc)
        return []
