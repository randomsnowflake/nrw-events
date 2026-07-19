"""Official AfterJobParty Bonn dates from its public ticket.io storefront."""

import re

from .. import common
from . import regional_common as rc


URL = "https://afterjobparty.ticket.io/"
SOURCE = "AfterJobParty Bonn"

_ROW_RE = re.compile(
    r'<tr\b[^>]*>\s*<td\b[^>]*id="event-row-[^"]+".*?</td>\s*</tr>',
    re.S | re.I,
)


def _nightlife(event: dict) -> dict:
    return {
        **event,
        "category_key": "nightlife",
        "category_label": "Nachtleben & Party",
        "category_confidence": 0.99,
        "category_reason": "source:official AfterJobParty Bonn ticket shop",
    }


def _description(row: str) -> str:
    match = re.search(
        r"<i[^>]*aria-hidden[^>]*>\s*info\s*</i>\s*<span>(.*?)</span>\s*</span>\s*</li>",
        row,
        re.S | re.I,
    )
    return common.concise_description(match.group(1)) if match else ""


def _price(offer) -> str:
    if not isinstance(offer, dict) or offer.get("price") in (None, ""):
        return ""
    amount = common.parse_float(offer.get("price"))
    label = f"ab {amount:g} €"
    if str(offer.get("availability", "")).casefold().endswith("outofstock"):
        label += " (ausverkauft)"
    return label


def _events_from_listing(html: str) -> list:
    events = []
    for row in _ROW_RE.findall(html or ""):
        items = common.jsonld_event_items(row)
        if not items:
            continue
        item = items[0]
        title = common.clean_html(str(item.get("name") or ""))
        if re.search(r"\bgutscheine?\b|\bvoucher\b", title, re.I):
            continue
        if str(item.get("eventStatus", "")).casefold().endswith("eventcancelled"):
            continue

        location = item.get("location") if isinstance(item.get("location"), dict) else {}
        address = location.get("address") if isinstance(location.get("address"), dict) else {}
        city = common.clean_html(str(address.get("addressLocality") or "Bonn"))
        if city.casefold() != "bonn":
            continue
        venue = common.clean_html(str(location.get("name") or ""))
        geo = location.get("geo") if isinstance(location.get("geo"), dict) else {}
        coords = None
        if geo.get("latitude") not in (None, "") and geo.get("longitude") not in (None, ""):
            coords = (common.parse_float(geo["latitude"]), common.parse_float(geo["longitude"]))
        start = common.parse_iso_date(str(item.get("startDate") or ""))
        end = common.parse_iso_date(str(item.get("endDate") or "")) or start
        description = _description(row) or common.factual_event_description(
            title, date_value=start, venue=venue, city=city
        )
        event = common.make_event(
            title, start, end, venue, city, description,
            str(item.get("url") or URL), SOURCE,
            "after work party nightlife dj dance club", 0.98,
            coords=coords, source_id="afterjobparty-bonn",
        )
        if event:
            price = _price(item.get("offers"))
            if price:
                event["price"] = price
            events.append(_nightlife(event))
    return rc.dedupe(events)


def fetch() -> list:
    try:
        html = common.fetch_url(URL, timeout=25)
        with common.capture_parser_metrics() as metrics:
            events = _events_from_listing(html)
        parser_empty = not events and metrics["out_of_window_count"] == 0
        common._record_endpoint(
            URL, parser_type="json-ld-in-html", candidate_count=metrics["candidate_count"],
            out_of_window_count=metrics["out_of_window_count"],
            parsed_event_count=len(events), parser_empty=parser_empty,
        )
        if parser_empty:
            common.log_source_error(SOURCE, rc.ParserEmptyError("parser returned no event records"))
        return events
    except Exception as exc:
        common.log_source_error(SOURCE, exc)
        return []
