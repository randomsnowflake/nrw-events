"""First-party BonnLive programme, enriched with its public ticket shop."""

from __future__ import annotations

import json
import re
import urllib.parse
from datetime import datetime
from zoneinfo import ZoneInfo

from .. import common
from . import regional_common as rc


URL = "https://www.bonn-live.com/events"
TICKET_URL = "https://rheinaue-konzerte-gmbh-7eap.vivenushop.com/"
SOURCE = "BonnLive"
_BERLIN = ZoneInfo("Europe/Berlin")


def _local_datetime(value: str):
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(_BERLIN).replace(tzinfo=None) if parsed.tzinfo else parsed


def _ticket_key(value: str) -> str:
    value = re.sub(r"\s*\|\s*kulturgarten\s+2026\s*$", "", value, flags=re.I)
    value = re.sub(r"\bbonn\b", "", value, flags=re.I)
    return re.sub(r"\W+", "", value.casefold())


def _next_events(html: str) -> dict[str, dict]:
    match = re.search(r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>', html or "", re.S | re.I)
    if not match:
        return {}
    try:
        items = json.loads(match.group(1))["props"]["pageProps"]["sellerPage"]["events"]
    except (KeyError, TypeError, ValueError):
        return {}
    return {
        _ticket_key(common.clean_html(str(item.get("name") or ""))): item
        for item in items if isinstance(item, dict) and item.get("name")
    }


def _field(block: str, class_name: str, *, occurrence: int = 0) -> str:
    values = re.findall(
        rf'<[^>]+class=["\'][^"\']*(?<![\w-]){re.escape(class_name)}(?![\w-])[^"\']*["\'][^>]*>(.*?)</[^>]+>',
        block, re.S | re.I,
    )
    return common.clean_html(values[occurrence]) if len(values) > occurrence else ""


def _events_from_pages(listing_html: str, ticket_html: str) -> list:
    tickets = _next_events(ticket_html)
    blocks = re.split(
        r'<div\s+role=["\']listitem["\']\s+class=["\']collection-item w-dyn-item["\']>',
        listing_html or "", flags=re.I,
    )[1:]
    events = []
    for block in blocks:
        title = _field(block, "event_title")
        date_values = re.findall(
            r'<div[^>]+class=["\'][^"\']*(?:date-text|event_date)[^"\']*["\'][^>]*>(.*?)</div>',
            block, re.S | re.I,
        )
        date_parts = [common.clean_html(value) for value in date_values[:3]]
        start = common.parse_date(" ".join(date_parts)) if len(date_parts) == 3 else None
        if not title or not start:
            continue
        venue = _field(block, "event_location") or "Kulturgarten am Post Tower"
        category = _field(block, "event_category")
        description = _field(block, "event_description")
        detail_match = re.search(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>Eventdetails</a>', block, re.I)
        link = urllib.parse.urljoin(URL, detail_match.group(1)) if detail_match else URL
        ticket = tickets.get(_ticket_key(title), {})
        ticket_start = _local_datetime(str(ticket.get("start") or ""))
        ticket_end = _local_datetime(str(ticket.get("end") or ""))
        if ticket_start and ticket_start.date() == start.date():
            start, end = ticket_start, ticket_end or ticket_start
        else:
            end = start
        if not description:
            description = common.factual_event_description(
                title, date_value=start, time_text=start.strftime("%H:%M") if ticket_start else "",
                venue=venue, city="Bonn", calendar_name="BonnLive",
            )
        event = common.make_event(
            title, start, end, venue, "Bonn", description, link, SOURCE,
            f"{category} open air concert theatre comedy family", 1.0,
            source_id="bonnlive", description_source="scraped" if _field(block, "event_description") else "generated",
            default_category_key="concert" if category.casefold().startswith("konzert") else "stage",
            category_locked=True,
        )
        if not event:
            continue
        price = ticket.get("startingPrice")
        if price not in (None, ""):
            event["price"] = f"ab {common.parse_float(price):g} €"
            event["admission_basis"] = "explicit"
        address = ", ".join(part for part in (
            common.clean_html(str(ticket.get("locationStreet") or "")),
            " ".join(part for part in (
                common.clean_html(str(ticket.get("locationPostal") or "")),
                common.clean_html(str(ticket.get("locationCity") or "")),
            ) if part),
        ) if part)
        if address:
            event["venue_address"] = address
        series_title = re.sub(r"\s+#\d+\s*$", "", title).strip()
        series_title = re.sub(
            r"\|\s*Junges Theater\s*$", "| Junges Theater Bonn",
            series_title, flags=re.I,
        )
        if series_title != title:
            event["series_title"] = series_title
        events.append(event)
    return rc.dedupe(events)


def fetch() -> list:
    try:
        listing = common.fetch_url(URL, timeout=25)
        try:
            tickets = common.fetch_url(TICKET_URL, timeout=25)
        except Exception as exc:
            common.log_source_error(f"{SOURCE} ticket shop", exc)
            tickets = ""
        with common.capture_parser_metrics() as metrics:
            events = _events_from_pages(listing, tickets)
        parser_empty = not events and metrics["out_of_window_count"] == 0
        common._record_endpoint(URL, parser_type="webflow-cms", candidate_count=metrics["candidate_count"], out_of_window_count=metrics["out_of_window_count"], parsed_event_count=len(events), parser_empty=parser_empty)
        if parser_empty:
            common.log_source_error(SOURCE, rc.ParserEmptyError("parser returned no event records"))
        return events
    except Exception as exc:
        common.log_source_error(SOURCE, exc)
        return []
