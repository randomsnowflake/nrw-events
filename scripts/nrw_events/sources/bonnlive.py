"""First-party BonnLive programme, enriched with its public ticket shop."""

from __future__ import annotations

import json
import os
import re
import time
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


def _next_events(html: str) -> dict[str, list[dict]]:
    match = re.search(r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>', html or "", re.S | re.I)
    if not match:
        return {}
    try:
        items = json.loads(match.group(1))["props"]["pageProps"]["sellerPage"]["events"]
    except (KeyError, TypeError, ValueError):
        return {}
    events: dict[str, list[dict]] = {}
    for item in items:
        if not isinstance(item, dict) or not item.get("name"):
            continue
        key = _ticket_key(common.clean_html(str(item["name"])))
        events.setdefault(key, []).append(item)
    return events


def _ticket_for_occurrence(
    tickets: dict[str, list[dict]], title: str, start: datetime,
    claimed: set[tuple[str, int]] | None = None,
) -> dict:
    key = _ticket_key(title)
    for index, ticket in enumerate(tickets.get(key, [])):
        ticket_ref = (key, index)
        if claimed is not None and ticket_ref in claimed:
            continue
        ticket_start = _local_datetime(str(ticket.get("start") or ""))
        if ticket_start and ticket_start.date() == start.date():
            if claimed is not None:
                claimed.add(ticket_ref)
            return ticket
    return {}


def _category_key(categories: list[str]) -> str:
    normalized = {category.casefold() for category in categories}
    if "kino" in normalized:
        return "cinema"
    if "konzerte" in normalized:
        return "concert"
    if normalized.intersection({"theater", "comedy"}):
        return "stage"
    if "karneval" in normalized:
        return "festival"
    return ""


def _field(block: str, class_name: str, *, occurrence: int = 0) -> str:
    values = re.findall(
        rf'<[^>]+class=["\'][^"\']*(?<![\w-]){re.escape(class_name)}(?![\w-])[^"\']*["\'][^>]*>(.*?)</[^>]+>',
        block, re.S | re.I,
    )
    return common.clean_html(values[occurrence]) if len(values) > occurrence else ""


def _price_number(value) -> float | None:
    if isinstance(value, int | float):
        return float(value)
    match = re.fullmatch(
        r"\s*(?:tickets?\s+)?(?:ab\s+)?€?\s*(\d+(?:[.,]\d{1,2})?)\s*€?\s*",
        common.clean_html(str(value or "")), re.I,
    )
    return common.parse_float(match.group(1)) if match else None


def _listing_price(block: str) -> float | None:
    values = re.findall(
        r'<[^>]+class=["\'][^"\']*(?<![\w-])event_price(?![\w-])[^"\']*["\'][^>]*>(.*?)</[^>]+>',
        block, re.S | re.I,
    )
    for value in values:
        price = _price_number(value)
        if price is not None:
            return price
    return None


def _detail_start_time(html: str, title: str, occurrence_date) -> str:
    detail_title = _field(html, "event_name")
    date_parts = [_field(html, "event-details_date", occurrence=index) for index in range(3)]
    detail_date = common.parse_date(" ".join(date_parts)) if all(date_parts) else None
    if detail_title != title or not detail_date or detail_date.date() != occurrence_date:
        return ""
    match = re.search(
        r'>\s*Beginn\s*</div>.{0,500}?\b([01]?\d|2[0-3]):([0-5]\d)\b',
        html or "", re.S | re.I,
    )
    return f"{int(match.group(1)):02d}:{match.group(2)}" if match else ""


def _events_from_pages(
    listing_html: str, ticket_html: str, *, detail_fetcher=None,
    detail_batch_timeout: float | None = None,
) -> list:
    tickets = _next_events(ticket_html)
    configured_batch_timeout = os.environ.get("NRW_EVENTS_DETAIL_BATCH_TIMEOUT_SECONDS")
    if detail_batch_timeout is None:
        detail_batch_timeout = float(configured_batch_timeout or "45")
    elif configured_batch_timeout is not None:
        detail_batch_timeout = min(detail_batch_timeout, float(configured_batch_timeout))
    detail_deadline = time.monotonic() + max(detail_batch_timeout, 0.0)
    detail_pages: dict[str, str] = {}
    failed_detail_links: set[str] = set()
    claimed_tickets: set[tuple[str, int]] = set()
    ticket_assignments: dict[tuple[str, str, str], dict] = {}
    blocks = re.split(
        r'<div(?=[^>]*\brole=["\']listitem["\'])'
        r'(?=[^>]*\bclass=["\'][^"\']*(?<![\w-])collection-item(?![\w-])[^"\']*["\'])[^>]*>',
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
        categories = [
            common.clean_html(value) for value in re.findall(
                r'<[^>]+class=["\'][^"\']*(?<![\w-])event_category(?![\w-])[^"\']*["\'][^>]*>(.*?)</[^>]+>',
                block, re.S | re.I,
            )
        ]
        category = " ".join(dict.fromkeys(value for value in categories if value))
        category_key = _category_key(categories)
        description = _field(block, "event_description")
        detail_match = re.search(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>Eventdetails</a>', block, re.I)
        link = urllib.parse.urljoin(URL, detail_match.group(1)) if detail_match else URL
        assignment_key = (_ticket_key(title), start.date().isoformat(), link)
        if assignment_key not in ticket_assignments:
            ticket_assignments[assignment_key] = _ticket_for_occurrence(tickets, title, start, claimed_tickets)
        ticket = ticket_assignments[assignment_key]
        ticket_start = _local_datetime(str(ticket.get("start") or ""))
        ticket_end = _local_datetime(str(ticket.get("end") or ""))
        # Vivenue exposes admission as ``start`` and showtime as ``end`` for
        # BonnLive events. The latter matches the visitor-facing "Beginn".
        showtime = ticket_end or ticket_start
        if showtime and showtime.date() == start.date():
            start = start.replace(hour=showtime.hour, minute=showtime.minute)
        detail_time = ""
        if detail_match and detail_fetcher and common.event_in_window_and_radius(start, start, "Bonn"):
            remaining = detail_deadline - time.monotonic()
            if link not in detail_pages and link not in failed_detail_links and remaining >= 3.0:
                request_timeout = 15.0 if remaining >= 30.0 else max(1.0, remaining / 3.0)
                try:
                    detail_pages[link] = detail_fetcher(link, request_timeout)
                except Exception as exc:
                    failed_detail_links.add(link)
                    common.log_source_error(f"{SOURCE} detail", exc)
            detail_time = _detail_start_time(detail_pages.get(link, ""), title, start.date())
            if detail_time:
                hour, minute = map(int, detail_time.split(":"))
                start = start.replace(hour=hour, minute=minute)
        end = start
        if not description:
            description = common.factual_event_description(
                title, date_value=start,
                time_text=start.strftime("%H:%M") if start.time() != datetime.min.time() else "",
                venue=venue, city="Bonn", calendar_name="BonnLive",
            )
        event = common.make_event(
            title, start, end, venue, "Bonn", description, link, SOURCE,
            f"{category} open air", 1.0,
            source_id="bonnlive", description_source="scraped" if _field(block, "event_description") else "generated",
            default_category_key=category_key,
            category_locked=bool(category_key),
        )
        if not event:
            continue
        listing_price = _listing_price(block)
        price = listing_price if listing_price is not None else _price_number(ticket.get("startingPrice"))
        if price is not None:
            event["price"] = "kostenlos" if price == 0 else f"ab {price:g} €"
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
    return rc.dedupe_occurrences(events)


def fetch() -> list:
    try:
        listing = common.fetch_url(URL, timeout=25)
        try:
            tickets = common.fetch_url(TICKET_URL, timeout=25)
        except Exception as exc:
            common.log_source_error(f"{SOURCE} ticket shop", exc)
            tickets = ""
        with common.capture_parser_metrics() as metrics:
            events = _events_from_pages(
                listing, tickets,
                detail_fetcher=lambda link, timeout: common.fetch_detail_url(
                    link, cache_namespace="bonnlive", timeout=timeout,
                    retry_attempts=1,
                ),
            )
        parser_empty = not events and metrics["out_of_window_count"] == 0
        common._record_endpoint(URL, parser_type="webflow-cms", candidate_count=metrics["candidate_count"], out_of_window_count=metrics["out_of_window_count"], parsed_event_count=len(events), parser_empty=parser_empty)
        if parser_empty:
            common.log_source_error(SOURCE, rc.ParserEmptyError("parser returned no event records"))
        return events
    except Exception as exc:
        common.log_source_error(SOURCE, exc)
        return []
