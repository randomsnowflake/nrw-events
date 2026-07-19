"""Official RheinEvents dates from the public vivenu shop payload."""

import json
import re
import urllib.parse
from datetime import datetime
from zoneinfo import ZoneInfo

from .. import common
from . import regional_common as rc


URL = "https://tickets.rheinevents.de/"
SOURCE = "RheinEvents"
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
    label = f"ab {common.parse_float(item['startingPrice']):g} €"
    if str(item.get("saleStatus", "")).casefold() == "soldout":
        label += " (ausverkauft)"
    return label


def _events_from_listing(html: str) -> list:
    match = re.search(
        r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',
        html or "",
        re.S | re.I,
    )
    if not match:
        return []
    try:
        payload = json.loads(match.group(1))
        items = payload["props"]["pageProps"]["sellerPage"]["events"]
    except (KeyError, TypeError, ValueError):
        return []

    events = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        title = common.clean_html(str(item.get("name") or ""))
        start = _local_datetime(str(item.get("start") or ""))
        end = _local_datetime(str(item.get("end") or "")) or start
        venue = common.clean_html(str(item.get("locationName") or ""))
        city = common.clean_html(str(item.get("locationCity") or "Bonn"))
        description = common.factual_event_description(
            title, date_value=start, venue=venue, city=city
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
        )
        if event:
            price = _price(item)
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
            URL, parser_type="next-data-json", candidate_count=metrics["candidate_count"],
            out_of_window_count=metrics["out_of_window_count"],
            parsed_event_count=len(events), parser_empty=parser_empty,
        )
        if parser_empty:
            common.log_source_error(SOURCE, rc.ParserEmptyError("parser returned no event records"))
        return events
    except Exception as exc:
        common.log_source_error(SOURCE, exc)
        return []
