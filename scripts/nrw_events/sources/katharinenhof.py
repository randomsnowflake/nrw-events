"""First-party Katharinenhof flea-market dates from Konrad Beikircher."""

import html as html_lib
import json
import re

from .. import common
from . import regional_common as rc


_SOURCE = "Katharinenhof"
_SOURCE_ID = "katharinenhof-flohmarkt"
_URL = "https://beikircher.de/event-type/katharinenhof/"


def _schema_datetime(value: str):
    normalized = re.sub(
        r"^(20\d{2})-(\d{1,2})-(\d{1,2})(T.*)$",
        lambda match: (
            f"{match.group(1)}-{int(match.group(2)):02d}-"
            f"{int(match.group(3)):02d}{match.group(4)}"
        ),
        value or "",
    )
    normalized = re.sub(
        r"([+-])(\d):(\d{2})$",
        lambda match: f"{match.group(1)}0{match.group(2)}:{match.group(3)}",
        normalized,
    )
    return common.parse_iso_date(normalized)


def _events_from_page(html: str, *, strict: bool = False) -> list:
    candidates = 0
    events = []
    for raw in re.findall(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html or "",
        re.S | re.I,
    ):
        try:
            item = json.loads(html_lib.unescape(raw))
        except (TypeError, ValueError):
            continue
        title = common.clean_html(str(item.get("name") or ""))
        if item.get("@type") != "Event" or "flohmarkt" not in title.casefold():
            continue
        candidates += 1
        start = _schema_datetime(str(item.get("startDate") or ""))
        if not start:
            continue
        locations = item.get("location") or []
        location = locations[0] if isinstance(locations, list) and locations else locations
        location = location if isinstance(location, dict) else {}
        address = location.get("address") or {}
        address = address if isinstance(address, dict) else {}
        street_address = common.clean_html(str(address.get("streetAddress") or ""))
        venue_name = common.clean_html(str(location.get("name") or "Katharinenhof"))
        street = street_address.split(",", 1)[0]
        venue = ", ".join(part for part in (venue_name, street) if part)
        description = common.concise_description(str(item.get("description") or ""))
        event = common.make_event(
            "Flohmarkt im Katharinenhof",
            start,
            start,
            venue,
            "Bonn-Bad Godesberg",
            description,
            str(item.get("url") or _URL),
            _SOURCE,
            "flohmarkt trödelmarkt markt",
            0.99,
            start.strftime("%H:%M"),
            source_id=_SOURCE_ID,
        )
        if event:
            price = re.search(r"Eintritt:\s*(\d+(?:[,.]\d+)?)\s*(?:Eur|Euro|€)", description, re.I)
            if price:
                event["price"] = f"{price.group(1).replace(',', '.')} €"
            events.append(event)
    if strict and candidates == 0:
        raise rc.ParserEmptyError("Katharinenhof flea-market JSON-LD contract changed")
    return rc.dedupe(events)


def fetch() -> list:
    return rc.fetch_html_events(
        _SOURCE,
        _URL,
        lambda html: _events_from_page(html, strict=True),
        timeout=20,
        source_id=_SOURCE_ID,
        empty_is_healthy=True,
    )
