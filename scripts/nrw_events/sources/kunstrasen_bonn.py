"""First-party KUNST!RASEN Bonn dates from the public vivenu ticket shop."""

from __future__ import annotations

import urllib.parse
from datetime import datetime
from zoneinfo import ZoneInfo

from .. import common
from . import regional_common as rc

URL = "https://tickets.kunstrasen-bonn.de/"
SOURCE = "KUNST!RASEN Bonn"
VENUE = "KUNST!RASEN Bonn"
_BERLIN = ZoneInfo("Europe/Berlin")


def _local(value: str):
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(_BERLIN).replace(tzinfo=None) if parsed.tzinfo else parsed


def _events_from_listing(html: str) -> list:
    events = []
    for item in rc.vivenu_seller_events(html):
        if common.clean_html(str(item.get("locationCity") or "")).casefold() != "bonn":
            continue
        title = common.clean_html(str(item.get("name") or ""))
        start, end = _local(item.get("start")), _local(item.get("end"))
        venue = common.clean_html(str(item.get("locationName") or "")) or VENUE
        if venue.casefold() == VENUE.casefold():
            venue = VENUE
        slogan = common.clean_html(str(item.get("slogan") or ""))
        description = common.concise_description(slogan) or common.factual_event_description(title, date_value=start, venue=venue, city="Bonn")
        slug = str(item.get("url") or "").strip("/")
        default_category = "festival" if "festival" in title.casefold() else "concert"
        event = common.make_event(title, start, end or start, venue, "Bonn", description, urllib.parse.urljoin(URL, f"event/{slug}") if slug else URL, SOURCE, "open air concert festival live music", 1.0, source_id="kunstrasen-bonn", description_source="scraped" if slogan else "generated", default_category_key=default_category, category_locked=True)
        if not event:
            continue
        price = item.get("startingPrice")
        if price not in (None, "") and common.parse_float(price):
            event["price"] = f"ab {common.parse_float(price):g} €"
            event["admission_basis"] = "explicit"
        if str(item.get("saleStatus") or "").casefold() == "soldout":
            event["availability"] = "SoldOut"
        street = common.clean_html(str(item.get("locationStreet") or ""))
        locality = " ".join(filter(None, (common.clean_html(str(item.get("locationPostal") or "")), "Bonn")))
        if street or locality:
            event["venue_address"] = ", ".join(filter(None, (street, locality)))
        events.append(event)
    return rc.dedupe(events)


def fetch() -> list:
    try:
        html = common.fetch_url(URL, timeout=25)
        with common.capture_parser_metrics() as metrics:
            events = _events_from_listing(html)
        parser_empty = not events and metrics["out_of_window_count"] == 0
        common._record_endpoint(URL, parser_type="next-data-json", candidate_count=metrics["candidate_count"], out_of_window_count=metrics["out_of_window_count"], parsed_event_count=len(events), parser_empty=parser_empty)
        if parser_empty:
            common.log_source_error(SOURCE, rc.ParserEmptyError("parser returned no event records"))
        return events
    except Exception as exc:
        common.log_source_error(SOURCE, exc)
        return []
