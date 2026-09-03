"""Official RheinEvents dates from the public vivenu shop payload."""

import json
import re
import urllib.parse
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from .. import common
from . import regional_common as rc

URL = "https://tickets.rheinevents.de/"
API_URL = "https://vivenu.com/api/events/public/listings"
SOURCE = "RheinEvents"
_SELLER_ID = "6900854dac377f08c7509516"
_PAGE_SIZE = 100
_MAX_PAGES = 10
_BERLIN = ZoneInfo("Europe/Berlin")


def _nightlife(event: dict) -> dict:
    return {
        **event,
        "category_key": "nightlife",
        "category_label": "Nachtleben & Party",
        "category_confidence": 0.99,
        "category_reason": "source:official RheinEvents ticket shop",
    }


def _local_datetime(value: str):
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo:
        parsed = parsed.astimezone(_BERLIN).replace(tzinfo=None)
    return parsed


def _price(item: dict) -> str:
    if item.get("startingPrice") in (None, ""):
        return ""
    price = common.parse_float(item["startingPrice"])
    if price <= 0:
        return ""
    label = f"ab {price:g} €"
    if str(item.get("saleStatus", "")).casefold() == "soldout":
        label += " (ausverkauft)"
    return label


def _address(item: dict) -> str:
    street = common.clean_html(str(item.get("locationStreet") or ""))
    postal = common.clean_html(str(item.get("locationPostal") or ""))
    city = common.clean_html(str(item.get("locationCity") or ""))
    locality = " ".join(part for part in (postal, city) if part)
    return ", ".join(part for part in (street, locality) if part)


def _events_from_items(items: list) -> list:
    events = []
    for item in items:
        if not isinstance(item, dict):
            continue
        title = common.clean_html(str(item.get("name") or ""))
        start = _local_datetime(str(item.get("start") or ""))
        end = _local_datetime(str(item.get("end") or "")) or start
        venue = common.clean_html(str(item.get("locationName") or ""))
        city = common.clean_html(str(item.get("locationCity") or "Bonn"))
        start_time = start.strftime("%H:%M") if start else ""
        end_time = end.strftime("%H:%M") if start and end and end > start else ""
        description = common.factual_event_description(
            title, date_value=start, time_text=start_time,
            end_time_text=end_time, venue=venue, city=city,
        )
        slogan = common.clean_html(str(item.get("slogan") or ""))
        if slogan:
            description = common.concise_description(f"Line-up: {slogan}. {description}")
        slug = str(item.get("url") or "").strip("/")
        link = urllib.parse.urljoin(URL, f"event/{slug}") if slug else URL
        event = common.make_event(
            title, start, end, venue, city, description, link, SOURCE,
            "open air electronic techno party nightlife dj concert", 0.98,
            source_id="rheinevents",
            description_source="generated",
        )
        if event:
            # Bikini Beach was added to the venue registry after these dates
            # had already been published. Keep the source venue name as the
            # occurrence identity so enriching the address and coordinates
            # does not move the existing public event URLs.
            if event.get("venue_id") == "bikini-beach-bonn":
                event["identity_venue"] = venue
            address = _address(item)
            if address:
                event["venue_address"] = address
            event["organizer"] = "RheinEvents Konzerte GmbH"
            price = _price(item)
            if price:
                event["price"] = price
                event["admission_basis"] = "explicit"
            if str(item.get("saleStatus", "")).casefold() == "soldout":
                event["availability"] = "SoldOut"
            events.append(_nightlife(event))
    return rc.dedupe(events)


def _events_from_listing(html: str) -> list:
    """Parse the legacy seller-page payload for compatibility fixtures."""
    match = re.search(
        r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',
        html or "",
        re.S | re.I,
    )
    if not match:
        return []
    try:
        items = json.loads(match.group(1))["props"]["pageProps"]["sellerPage"]["events"]
    except (KeyError, TypeError, ValueError):
        return []
    return _events_from_items(items if isinstance(items, list) else [])


def _window_timestamp(value: datetime, *, end_of_day: bool = False) -> str:
    if end_of_day:
        value = value.replace(hour=23, minute=59, second=59, microsecond=999000)
    else:
        value = value.replace(hour=0, minute=0, second=0, microsecond=0)
    utc = value.replace(tzinfo=_BERLIN).astimezone(timezone.utc)
    return utc.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _listing_url(skip: int) -> str:
    query = urllib.parse.urlencode({
        "sellerId": _SELLER_ID,
        "visibleInListing": "true",
        "endMin": _window_timestamp(common.TODAY),
        "startMax": _window_timestamp(common.END_DATE, end_of_day=True),
        "top": _PAGE_SIZE,
        "skip": skip,
    })
    return f"{API_URL}?{query}"


def _listing_items() -> list:
    items = []
    for page in range(_MAX_PAGES):
        payload = common.fetch_json(_listing_url(page * _PAGE_SIZE), timeout=25)
        if not isinstance(payload, list):
            raise ValueError("RheinEvents listing API did not return an array")
        if any(not isinstance(item, dict) for item in payload):
            raise ValueError("RheinEvents listing API returned a non-object event")
        items.extend(payload)
        if len(payload) < _PAGE_SIZE:
            return items
    raise ValueError(f"RheinEvents listing API exceeded {_MAX_PAGES} pages")


def fetch() -> list:
    try:
        items = _listing_items()
        with common.capture_parser_metrics() as metrics:
            events = _events_from_items(items)
        parser_empty = bool(items) and not events and metrics["out_of_window_count"] == 0
        common._record_endpoint(
            API_URL, parser_type="json-api", candidate_count=len(items),
            out_of_window_count=metrics["out_of_window_count"],
            parsed_event_count=len(events), parser_empty=parser_empty,
        )
        if parser_empty:
            common.log_source_error(SOURCE, rc.ParserEmptyError("parser returned no event records"))
        return events
    except Exception as exc:
        common.log_source_error(SOURCE, exc)
        return []
