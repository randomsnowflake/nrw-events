"""Curated Bonn Meetup groups via public iCal and event JSON-LD.

Meetup's iCal feeds provide the complete group inventory and occurrence times.
The public event pages add visitor-facing venue, address, locality, organizer,
image and attendance mode. Publisher-authored prose is used for classification
only and is replaced with a factual description before records leave this
adapter.
"""

from __future__ import annotations

import os
import re
import time
import urllib.parse
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .. import common
from ..location import canonicalize_city
from . import regional_common as rc


SOURCE = "Meetup"
_CACHE_NAMESPACE = "meetup-bonn-groups"


@dataclass(frozen=True, slots=True)
class MeetupGroup:
    slug: str
    city: str
    category: str
    trust: float = 0.9

    @property
    def source_id(self) -> str:
        return f"meetup-{self.slug}"

    @property
    def calendar_url(self) -> str:
        return f"https://www.meetup.com/{self.slug}/events/ical/"


# Organizer-maintained Bonn groups with stable public calendars. A former Köln
# travel group is deliberately omitted because its events regularly occur in
# other municipalities while Meetup sometimes labels them as Köln.
GROUPS = (
    MeetupGroup("azure-bonn-meetup", "Bonn", "cloud tech meetup", 0.9),
    MeetupGroup("bonner-ki-meetup", "Bonn", "ki tech meetup", 0.95),
    MeetupGroup("jug-bonn", "Bonn", "java tech meetup", 0.9),
    MeetupGroup("board-games-in-bonn", "Bonn", "spiele meetup", 0.8),
    MeetupGroup("sprachcafe-bonn", "Bonn", "sprache meetup", 0.8),
)


def _title_key(value: Any) -> str:
    return re.sub(r"\W+", "", common.clean_html(str(value or "")).casefold())


def _schema_token(value: Any) -> str:
    return str(value or "").strip().rstrip("/").rsplit("/", 1)[-1]


def _venue_address(address: dict, city: str) -> str:
    street = common.clean_html(str(address.get("streetAddress") or ""))
    postcode = common.clean_html(str(address.get("postalCode") or ""))
    locality = " ".join(part for part in (postcode, city) if part)
    parts = [street]
    if locality and locality.casefold() not in street.casefold():
        parts.append(locality)
    value = ", ".join(part for part in parts if part)
    if city:
        value = re.sub(
            rf"\b({re.escape(city)})(?:,\s*{re.escape(city)})+$",
            r"\1",
            value,
            flags=re.I,
        )
    return value[:500]


def _detail_context(html: str, event: dict) -> dict[str, Any] | None:
    wanted_title = _title_key(event.get("title"))
    wanted_date = str(event.get("start_date") or event.get("date") or "")[:10]
    item = next((
        candidate
        for candidate in common.jsonld_event_items(html or "")
        if _title_key(candidate.get("name")) == wanted_title
        and str(candidate.get("startDate") or "")[:10] == wanted_date
    ), None)
    if item is None:
        return None

    attendance_mode = _schema_token(item.get("eventAttendanceMode"))
    location = item.get("location") if isinstance(item.get("location"), dict) else {}
    address = location.get("address") if isinstance(location.get("address"), dict) else {}
    city = common.clean_html(str(address.get("addressLocality") or ""))
    venue_address = _venue_address(address, city)
    images = item.get("image")
    if not isinstance(images, list):
        images = [images] if images else []
    image = next((
        str(candidate)
        for candidate in images
        if urllib.parse.urlsplit(str(candidate)).scheme in {"http", "https"}
    ), "")
    organizer = common._jsonld_entity_names(item.get("organizer"))
    price = common._jsonld_admission_price(item)
    status_token = _schema_token(item.get("eventStatus"))
    return {
        "online_only": attendance_mode == "OnlineEventAttendanceMode",
        "venue": common.clean_html(str(location.get("name") or ""))[:300],
        "venue_address": venue_address[:500],
        "city": city,
        "organizer": organizer,
        "image": image,
        "price": price or "",
        "status": {
            "EventCancelled": "cancelled",
            "EventPostponed": "postponed",
        }.get(status_token, ""),
    }


def _configured_detail_timeout(detail_batch_timeout: float | None) -> float:
    configured = os.environ.get("NRW_EVENTS_DETAIL_BATCH_TIMEOUT_SECONDS")
    if detail_batch_timeout is None:
        return float(configured or "45")
    if configured is not None:
        return min(detail_batch_timeout, float(configured))
    return detail_batch_timeout


def events_for_group(
    group: MeetupGroup, *, detail_fetcher=None,
    detail_batch_timeout: float | None = None,
) -> list:
    events = common.fetch_ical(
        group.calendar_url,
        SOURCE,
        group.city,
        group.category,
        group.trust,
        source_id=group.source_id,
        empty_calendar_is_valid=True,
    )
    detail_deadline = time.monotonic() + max(_configured_detail_timeout(detail_batch_timeout), 0.0)
    output = []
    for event in events:
        event["source_id"] = group.source_id
        context = None
        detail_processed = False
        remaining = detail_deadline - time.monotonic()
        if detail_fetcher and event.get("link") and remaining >= 3.0:
            request_timeout = 15.0 if remaining >= 30.0 else max(1.0, remaining / 3.0)
            try:
                detail_html = detail_fetcher(str(event["link"]), request_timeout)
                context = _detail_context(detail_html, event)
                detail_processed = True
            except Exception as exc:
                common.log_source_error(
                    f"{SOURCE} ({group.slug}) detail",
                    exc,
                    source_id=group.source_id,
                )
        if context and context.get("online_only"):
            continue
        if context:
            for field in ("venue", "venue_address", "city", "organizer", "image"):
                if context.get(field):
                    event[field] = context[field]
            if context.get("price"):
                event["price"] = context["price"]
                event["admission_basis"] = "explicit"
            if context.get("status"):
                event["status"] = context["status"]
        if detail_processed:
            event["_detail_page_enriched"] = True
        if not (event.get("venue") or event.get("venue_address")):
            # Meetup calendars currently omit LOCATION. The group's home city is
            # not occurrence evidence: without matched detail geography, dropping
            # the row is safer than publishing an excursion as a Bonn event.
            continue
        if not context:
            guessed_city = common.guess_city_from_text(str(event.get("venue") or ""))
            event["city"] = canonicalize_city(guessed_city.title() if guessed_city else group.city)
        common.keep_only_event_master_data(event)
        start = common.parse_iso_date(event.get("start_date") or event.get("date") or "")
        end = common.parse_iso_date(event.get("end_date") or "") or start
        if start and common.event_in_window_and_radius(start, end, event.get("city") or group.city):
            output.append(event)
    return rc.dedupe_occurrences(output)


def fetch() -> list:
    events = []
    detail_deadline = time.monotonic() + max(_configured_detail_timeout(None), 0.0)

    def detail_fetcher(link, timeout):
        return common.fetch_detail_url(
            link,
            cache_namespace=_CACHE_NAMESPACE,
            timeout=timeout,
            retry_attempts=1,
        )

    for group in GROUPS:
        try:
            events.extend(events_for_group(
                group,
                detail_fetcher=detail_fetcher,
                detail_batch_timeout=max(detail_deadline - time.monotonic(), 0.0),
            ))
        except Exception as exc:
            common.log_source_error(
                f"{SOURCE} ({group.slug})",
                exc,
                source_id=group.source_id,
            )
    return rc.dedupe_occurrences(events)
