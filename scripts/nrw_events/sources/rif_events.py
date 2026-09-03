"""Official Bonn dates from the RiF public ticket shop."""

from __future__ import annotations

import json
import re
import urllib.parse
from datetime import datetime
from zoneinfo import ZoneInfo

from .. import common
from . import regional_common as rc

URL = "https://ticketshop.rif-bonn.de/"
SOURCE = "RiF Events"
_BERLIN = ZoneInfo("Europe/Berlin")


def _local(value: str):
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(_BERLIN).replace(tzinfo=None) if parsed.tzinfo else parsed


def _events_from_listing(html: str) -> list:
    match = re.search(r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>', html or "", re.S | re.I)
    if not match:
        return []
    try:
        items = json.loads(match.group(1))["props"]["pageProps"]["sellerPage"]["events"]
    except (KeyError, TypeError, ValueError):
        return []
    events = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict) or common.clean_html(str(item.get("locationCity") or "")).casefold() != "bonn":
            continue
        title = common.clean_html(str(item.get("name") or ""))
        start, end = _local(item.get("start")), _local(item.get("end"))
        venue = common.clean_html(str(item.get("locationName") or ""))
        slogan = common.clean_html(str(item.get("slogan") or ""))
        description = common.concise_description(slogan) or common.factual_event_description(title, date_value=start, venue=venue, city="Bonn", calendar_name="RiF Events")
        slug = str(item.get("url") or "").strip("/")
        event = common.make_event(title, start, end or start, venue, "Bonn", description, urllib.parse.urljoin(URL, f"event/{slug}") if slug else URL, SOURCE, "concert festival party comedy live music", 1.0, source_id="rif-events", description_source="scraped" if slogan else "generated")
        if not event:
            continue
        price = item.get("startingPrice")
        if price not in (None, ""):
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
            common.log_source_error(SOURCE, rc.ParserEmptyError("parser returned no Bonn event records"))
        return events
    except Exception as exc:
        common.log_source_error(SOURCE, exc)
        return []
