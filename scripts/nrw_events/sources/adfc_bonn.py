"""First-party ADFC Bonn/Rhein-Sieg tours and events with detail enrichment."""

from __future__ import annotations

import re
import urllib.parse
from datetime import datetime
from html import escape
from zoneinfo import ZoneInfo

from .. import common, richtext
from . import regional_common as rc


SOURCE = "ADFC Bonn/Rhein-Sieg"
SOURCE_ID = "adfc-bonn"
SEARCH_URL = "https://api-touren-termine.adfc.de/api/eventItems/search"
DETAIL_API_URL = "https://api-touren-termine.adfc.de/api/eventItems/{}"
PUBLIC_DETAIL_URL = "https://touren-termine.adfc.de/radveranstaltung/{}"
_BONN_LAT = 50.73743
_BONN_LON = 7.0982068
# Match the search page's default radius when the supplied Bonn URL does not
# include an explicit ``distance`` query parameter.
_DISTANCE_KM = 20
_PAGE_SIZE = 100
_MAX_PAGES = 20
_BERLIN = ZoneInfo("Europe/Berlin")
_POSTAL_CITY = re.compile(r"^(.*?)\s+(\d{5})\s+(.+?)\s*$")


def _clean(value: object) -> str:
    return common.clean_html(str(value or "")).strip()


def _number(value: object) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return ""
    number = float(value)
    if number.is_integer():
        return str(int(number))
    return f"{number:.2f}".rstrip("0").rstrip(".").replace(".", ",")


def _local_datetime(value: object, *, all_day: bool = False) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if all_day:
        try:
            return datetime.strptime(text[:10], "%Y-%m-%d")
        except ValueError:
            return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(_BERLIN).replace(tzinfo=None) if parsed.tzinfo else parsed


def _location_parts(payload: dict, listing: dict) -> tuple[str, str, str, tuple | None]:
    locations = [
        item for item in (payload.get("tourLocations") or [])
        if isinstance(item, dict)
    ]
    locations.sort(key=lambda item: (
        _clean(item.get("type")).casefold() != "startpunkt",
        int(item.get("position") or 0),
    ))
    location = locations[0] if locations else {}
    city = _clean(location.get("city")) or _clean(listing.get("city")) or "Bonn"
    name = _clean(location.get("name"))
    street = _clean(location.get("street"))
    postal = _clean(location.get("zipCode"))
    coords = None
    latitude = location.get("latitude", listing.get("latitude"))
    longitude = location.get("longitude", listing.get("longitude"))
    if isinstance(latitude, (int, float)) and isinstance(longitude, (int, float)):
        coords = (float(latitude), float(longitude))

    if not name and not street:
        listing_location = _clean(listing.get("startLocation"))
        match = _POSTAL_CITY.match(listing_location)
        if match:
            name, postal, city = (part.strip(" ,") for part in match.groups())
        else:
            name = listing_location
    venue = name or street or city
    city_line = " ".join(part for part in (postal, city) if part)
    address = ", ".join(part for part in (street, city_line) if part)
    return venue[:300], address[:500], city, coords


def _price(payload: dict) -> str:
    prices = []
    for item in payload.get("eventItemPrices") or []:
        if not isinstance(item, dict):
            continue
        amount = _number(item.get("price"))
        if not amount:
            continue
        label = _clean(item.get("groupName"))
        value = "kostenfrei" if float(item["price"]) == 0 else f"{amount} €"
        rendered = f"{label}: {value}" if label else value
        if rendered not in prices:
            prices.append(rendered)
    return ", ".join(prices)[:240]


def _description(payload: dict, listing: dict) -> tuple[str, str]:
    item = payload.get("eventItem") if isinstance(payload.get("eventItem"), dict) else listing
    full_html = richtext.sanitize_rich_text(str(item.get("description") or ""))
    full_text = richtext.to_plain_text(full_html)
    short = _clean(item.get("cShortDescription")) or _clean(listing.get("cShortDescription"))
    blocks = []
    if short and short.casefold() not in full_text.casefold():
        blocks.append(f"<p>{escape(short, quote=False)}</p>")
    if full_html:
        blocks.append(full_html)

    facts = []
    for label, field, unit in (
        ("Tourlänge", "cTourLengthKm", "km"),
        ("Geschwindigkeit", "cTourSpeedKmh", "km/h"),
        ("Höhenmeter", "cTourHeight", "m"),
    ):
        value = _number(item.get(field))
        if value and float(item[field]) > 0:
            facts.append((label, f"{value} {unit}"))
    if not facts:
        for label, field in (("Tourlänge", "tourLength"), ("Geschwindigkeit", "tourSpeed")):
            if value := _clean(listing.get(field)):
                facts.append((label, value))
    if facts:
        blocks.append("<h3>Tourdaten</h3><ul>" + "".join(
            f"<li><strong>{escape(label, quote=False)}:</strong> {escape(value, quote=False)}</li>"
            for label, value in facts
        ) + "</ul>")

    tags = {}
    for tag in payload.get("itemTags") or []:
        if not isinstance(tag, dict):
            continue
        category = _clean(tag.get("category"))
        value = _clean(tag.get("tag"))
        if category and value and value not in tags.setdefault(category, []):
            tags[category].append(value)
    if tags:
        blocks.append("<h3>Merkmale</h3><ul>" + "".join(
            f"<li><strong>{escape(category, quote=False)}:</strong> "
            f"{escape(', '.join(values), quote=False)}</li>"
            for category, values in tags.items()
        ) + "</ul>")

    description_html = richtext.sanitize_rich_text("".join(blocks))
    return richtext.to_plain_text(description_html), description_html


def _event_from_payload(listing: dict, detail: dict) -> dict | None:
    detail_item = detail.get("eventItem")
    item = detail_item if isinstance(detail_item, dict) else listing
    title = _clean(item.get("title")) or _clean(listing.get("title"))
    slug = _clean(item.get("cSlug")) or _clean(listing.get("cSlug"))
    event_type = _clean(item.get("eventType")) or _clean(listing.get("eventType"))
    all_day = bool(item.get("cWithoutTime", listing.get("cWithoutTime", False)))
    start = _local_datetime(item.get("beginning", listing.get("beginning")), all_day=all_day)
    end = _local_datetime(item.get("end", listing.get("end")), all_day=all_day)
    venue, address, city, coords = _location_parts(detail, listing)
    description, description_html = _description(detail, listing)
    description_source = (
        "scraped"
        if _clean(item.get("description"))
        or _clean(item.get("cShortDescription"))
        or _clean(listing.get("cShortDescription"))
        else "generated"
    )
    if not description:
        description = common.factual_event_description(
            title, date_value=start, end_date_value=end, venue=venue, city=city,
            calendar_name=SOURCE,
        )
        description_html = richtext.from_plain_text(description)
        description_source = "generated"

    category = " ".join(filter(None, (
        event_type,
        " ".join(_clean(tag.get("tag")) for tag in detail.get("itemTags") or [] if isinstance(tag, dict)),
        "radtour radfahren outdoor aktivität" if event_type.casefold() == "radtour" else "",
    )))
    event = common.make_event(
        title, start, end, venue, city, description,
        PUBLIC_DETAIL_URL.format(urllib.parse.quote(slug, safe="-")),
        SOURCE, category, 1.0, coords=coords, all_day=all_day,
        source_id=SOURCE_ID, description_source=description_source,
        default_category_key="outdoor" if event_type.casefold() == "radtour" else "",
        category_locked=event_type.casefold() == "radtour",
    )
    if not event:
        return None
    event["description_html"] = description_html
    if address:
        event["venue_address"] = address
    if coords:
        event["venue_latitude"], event["venue_longitude"] = coords
    if price := _price(detail):
        event["price"] = price
        event["admission_basis"] = "explicit"
    if organizer := _clean(item.get("cUnitName")) or _clean(listing.get("cUnitName")):
        event["organizer"] = organizer
    if bool(item.get("isCancelled", listing.get("isCancelled", False))) or _clean(
        item.get("cStatus", listing.get("cStatus"))
    ).casefold() == "cancelled":
        notice = "Die Veranstaltung ist abgesagt."
        event["status"] = "cancelled"
        event["description"] = f"{notice} {event['description']}".strip()
        event["description_html"] = richtext.sanitize_rich_text(
            f"<p><strong>{notice}</strong></p>{event['description_html']}"
        )
    return event


def events_from_payload(items: list, *, detail_fetcher) -> list:
    events = []
    for listing in items:
        if not isinstance(listing, dict):
            continue
        slug = _clean(listing.get("cSlug"))
        detail = {}
        if slug:
            try:
                candidate = detail_fetcher(slug)
                if isinstance(candidate, dict):
                    detail = candidate
            except Exception as exc:
                common.log_source_error(f"{SOURCE} detail", exc, source_id=SOURCE_ID)
        if event := _event_from_payload(listing, detail):
            events.append(event)
    # The API returns occurrence-specific rows. Do not apply the legacy local
    # date/title deduper here: same-day tours can legitimately have two start
    # times (for example two Pedelec course sessions).
    return events


def _search_url(offset: int) -> str:
    query = urllib.parse.urlencode({
        "lat": _BONN_LAT,
        "lng": _BONN_LON,
        "distance": _DISTANCE_KM,
        "beginning": common.TODAY.strftime("%Y-%m-%d"),
        "end": common.END_DATE.strftime("%Y-%m-%d"),
        "sort": "date",
        "limit": _PAGE_SIZE,
        "offset": offset,
    })
    return f"{SEARCH_URL}?{query}"


def _listing_items() -> list:
    items = []
    for page in range(_MAX_PAGES):
        payload = common.fetch_json(_search_url(page * _PAGE_SIZE), timeout=25)
        page_items = payload.get("items") if isinstance(payload, dict) else None
        if not isinstance(page_items, list):
            raise ValueError("ADFC search response has no items list")
        items.extend(item for item in page_items if isinstance(item, dict))
        results = payload.get("results")
        if not page_items or not isinstance(results, int) or len(items) >= results:
            return items
    raise ValueError(f"ADFC search exceeded {_MAX_PAGES} pages")


def fetch() -> list:
    try:
        items = _listing_items()
        with common.capture_parser_metrics() as metrics:
            events = events_from_payload(
                items,
                detail_fetcher=lambda slug: common.fetch_json(
                    DETAIL_API_URL.format(urllib.parse.quote(slug, safe="-")),
                    timeout=20,
                ),
            )
        parser_empty = bool(items) and not events and metrics["out_of_window_count"] == 0
        common._record_endpoint(
            SEARCH_URL,
            parser_type="json-api",
            candidate_count=len(items),
            out_of_window_count=metrics["out_of_window_count"],
            parsed_event_count=len(events),
            parser_empty=parser_empty,
        )
        if parser_empty:
            common.log_source_error(
                SOURCE, rc.ParserEmptyError("parser returned no event records"),
                source_id=SOURCE_ID,
            )
        return events
    except Exception as exc:
        common.log_source_error(SOURCE, exc, source_id=SOURCE_ID)
        return []
