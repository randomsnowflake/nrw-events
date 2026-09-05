"""Owning implementation of jsonld; core is a compatibility facade."""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from typing import Any

from . import event_builder as _impl_event_builder
from . import run_state as _impl_run_state
from . import text as _impl_text
from .dates import parse_iso_date
from .location import (
    guess_city_from_text,
)
from .models import AdmissionDefault, RawEvent


def jsonld_event_items(html: str) -> list[dict[str, Any]]:
    """Extract schema.org Event objects from JSON-LD blobs."""
    items: list[dict[str, Any]] = []
    seen: set[int] = set()

    def walk(obj: Any) -> None:
        if isinstance(obj, list):
            for x in obj:
                walk(x)
        elif isinstance(obj, dict):
            if id(obj) in seen:
                return
            seen.add(id(obj))
            typ = obj.get("@type")
            types = typ if isinstance(typ, list) else [typ]
            if any(
                str(t or "").strip().rstrip("/").rsplit("/", 1)[-1].endswith("Event")
                for t in types
            ):
                items.append(obj)
            for value in obj.values():
                walk(value)

    for m in re.finditer(r"<script[^>]+application/ld\+json[^>]*>(.*?)</script>", html, re.S | re.I):
        raw = m.group(1).strip()
        # Some consent plugins incorrectly label executable JavaScript as
        # application/ld+json. JSON-LD roots must be objects or arrays, so
        # these blocks are not parse failures and should not create warnings.
        if not raw or raw[0] not in "[{":
            continue
        try:
            # Real publisher pages occasionally contain literal newlines or
            # tabs inside JSON strings. Browsers accept these blocks, and the
            # rest of the document remains useful, so parse them permissively.
            # Each decode creates a separate object graph. Wrapper roots are
            # released after walk and their addresses may be reused by CPython;
            # keeping their IDs across script blocks can silently skip events.
            seen.clear()
            walk(json.loads(raw, strict=False))
        except json.JSONDecodeError as exc:
            _impl_run_state.log_source_error("JSON-LD", exc)
            continue
    return items


def _jsonld_location(loc: Any) -> tuple[str, str]:
    """Return (venue_name, city) from a schema.org location that may be a dict or list."""
    if isinstance(loc, list):
        loc = next((item for item in loc if isinstance(item, dict | str)), {})
    if isinstance(loc, str):
        return _impl_text.clean_html(loc), ""
    if not isinstance(loc, dict):
        return "", ""
    location_type = str(loc.get("@type") or "").strip().rstrip("/").rsplit("/", 1)[-1]
    venue = "" if location_type == "PostalAddress" else _jsonld_text(loc.get("name"))
    address = loc if location_type == "PostalAddress" else loc.get("address", {})
    city = ""
    if isinstance(address, dict):
        city = address.get("addressLocality") or ""
    elif isinstance(address, str):
        city = guess_city_from_text(address) or ""
    city = re.sub(r"^\d{5}\s+", "", str(city)).strip()
    return venue, city


def _jsonld_schema_token(value: Any, allowed: tuple[str, ...]) -> str:
    """Return a recognized bare or schema.org vocabulary token."""
    raw = str(value or "").strip().rstrip("/")
    if raw in allowed:
        return raw
    parsed = urllib.parse.urlsplit(raw)
    if (
        parsed.scheme in {"http", "https"}
        and parsed.netloc.casefold() in {"schema.org", "www.schema.org"}
    ):
        token = parsed.path.rsplit("/", 1)[-1]
        return token if token in allowed else ""
    return ""


def _jsonld_entity_names(value: Any, *, max_length: int = 500) -> str:
    """Return bounded schema.org person or organization names in source order."""
    candidates = value if isinstance(value, list) else [value]
    names: list[str] = []
    for candidate in candidates:
        candidate_name = candidate
        if isinstance(candidate, dict):
            entity_type = candidate.get("@type")
            entity_types = entity_type if isinstance(entity_type, list) else [entity_type]
            explicit_types = [value for value in entity_types if value]
            if explicit_types and not any(
                _jsonld_schema_token(value, ("Organization", "Person"))
                for value in explicit_types
            ):
                continue
            candidate_name = candidate.get("name", "")
        if not isinstance(candidate_name, str):
            continue
        name = _impl_text.clean_html(candidate_name).strip()
        if not name or name in names:
            continue
        joined_length = sum(map(len, names)) + 2 * len(names) + len(name)
        if joined_length <= max_length:
            names.append(name)
    return "; ".join(names)


def _apply_jsonld_provenance(
    event: RawEvent, *, organizer: str, admission_price: str | None, availability: str,
) -> None:
    """Attach optional source evidence without duplicating occurrence paths."""
    if organizer:
        event["organizer"] = organizer
    if admission_price is not None:
        event["price"] = admission_price
        event["admission_basis"] = "explicit"
    if availability:
        event["availability"] = availability


def _jsonld_schedule_items(schedule: Any) -> list[dict[str, Any]]:
    """Return schema.org Schedule objects as a list, preserving source order."""
    if isinstance(schedule, list):
        return [s for s in schedule if isinstance(s, dict)]
    if isinstance(schedule, dict):
        return [schedule]
    return []


def _jsonld_schedule_dt(schedule: dict, date_key: str, time_key: str = "") -> datetime | None:
    """Parse a Schedule date and optional time into a naive datetime."""
    dt = parse_iso_date(schedule.get(date_key, ""))
    if not dt:
        return None
    time_value = (schedule.get(time_key, "") if time_key else "") or ""
    m = re.match(r"^(\d{1,2}):(\d{2})", str(time_value).strip())
    if m:
        hour, minute = map(int, m.groups())
        dt = dt.replace(hour=hour, minute=minute)
    return dt


def _jsonld_schedule_time_text(schedule: dict) -> str:
    """Return a compact display time from schema.org Schedule start/end times."""
    start = str(schedule.get("startTime", "") or "").strip()
    end = str(schedule.get("endTime", "") or "").strip()
    if start and end:
        return f"{start}–{end}"
    return start or end


def _jsonld_accessible_for_free(value: Any) -> bool | None:
    """Parse schema.org Boolean values without treating arbitrary strings as true."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in {"true", "1"}:
            return True
        if normalized in {"false", "0"}:
            return False
    return None


def _jsonld_offer_price(offers: Any) -> str | None:
    """Return a conservative schema.org Offer price as the legacy display string."""
    if isinstance(offers, dict):
        candidates = [offers]
    elif isinstance(offers, list):
        candidates = [offer for offer in offers if isinstance(offer, dict)]
    else:
        candidates = []
    has_explicitly_free_offer = False
    for offer in candidates:
        amount = offer.get("price")
        if amount in (None, "") or isinstance(amount, dict | list | bool):
            continue
        amount_text = _impl_text.clean_html(str(amount)).strip()
        if not amount_text:
            continue
        currency = offer.get("priceCurrency")
        currency_text = (
            "" if isinstance(currency, dict | list) else _impl_text.clean_html(str(currency or "")).strip()
        )
        if _impl_event_builder._FREE_PRICE_PATTERN.fullmatch(amount_text):
            has_explicitly_free_offer = True
            continue
        if re.fullmatch(r"0+(?:[.,]0+)?", amount_text):
            # A bare zero without a currency is what many calendar plugins emit for
            # "no price maintained". Only trust it as free when the source states a
            # currency, otherwise leave it to the remaining offers or text inference.
            if currency_text:
                has_explicitly_free_offer = True
            continue
        return " ".join(part for part in (amount_text, currency_text) if part)
    return "kostenlos" if has_explicitly_free_offer else None


def _jsonld_offer_availability(offers: Any) -> str:
    """Return an explicit schema.org availability, preferring a purchasable tier."""
    candidates = offers if isinstance(offers, list) else [offers]
    recognized: list[str] = []
    allowed = ("InStock", "LimitedAvailability", "PreOrder", "SoldOut")
    for offer in candidates:
        if not isinstance(offer, dict):
            continue
        value = _jsonld_schema_token(offer.get("availability"), allowed)
        if value in allowed:
            recognized.append(value)
    for value in allowed:
        if value in recognized:
            return value
    return ""


def _jsonld_text(value: Any) -> str:
    """Return the first textual JSON-LD value without guessing from objects."""
    if isinstance(value, list):
        value = next((item for item in value if isinstance(item, str)), "")
    return value if isinstance(value, str) else ""


def jsonld_event_status(value: object) -> str:
    """Map a schema.org eventStatus value to the canonical publication status."""
    token = _jsonld_schema_token(value, ("EventCancelled", "EventPostponed"))
    if token == "EventCancelled":
        return "cancelled"
    if token == "EventPostponed":
        return "postponed"
    return ""


def _jsonld_admission_price(item: dict) -> str | None:
    """Resolve structured admission, with the direct free-access flag authoritative."""
    accessible_for_free = _jsonld_accessible_for_free(item.get("isAccessibleForFree"))
    offer_price = _jsonld_offer_price(item.get("offers"))
    if accessible_for_free is True:
        return "kostenlos"
    if accessible_for_free is False:
        return offer_price if offer_price and offer_price != "kostenlos" else "kostenpflichtig"
    return offer_price


_VISIBLE_PAID_ADMISSION_RE = re.compile(
    r"\b(?:eintritt|teilnahme|ticket(?:s)?|kostenbeitrag|teilnahmebeitrag|teilnahmegeb(?:u|ü)hr)"
    r"\s+(?:kostet|kosten|betr(?:a|ä)gt|betragen)\s+"
    r"(?P<amount>\d+(?:[.,]\d{1,2})?)\s*(?P<currency>€|eur\b|euro\b)",
    re.IGNORECASE,
)


def _visible_paid_admission_price(text: str) -> str | None:
    """Return an explicit visitor price from prose, without guessing fees."""
    match = _VISIBLE_PAID_ADMISSION_RE.search(text or "")
    if not match:
        return None
    currency = match.group("currency")
    if currency.casefold() == "eur":
        currency = "Euro"
    return f'{match.group("amount")} {currency}'


def events_from_jsonld(html: str, source: str, default_city: str, category: str,
                       trust: float, default_link: str, source_id: str = "",
                       admission: AdmissionDefault | None = None,
                       default_category_key: str = "",
                       category_locked: bool = False) -> list:
    """Build events from every schema.org Event in a page's JSON-LD."""
    events = []
    for item in jsonld_event_items(html):
        try:
            title = _jsonld_text(item.get("name"))
            start_dt = parse_iso_date(_jsonld_text(item.get("startDate")))
            end_dt = parse_iso_date(_jsonld_text(item.get("endDate"))) or start_dt
            venue, city = _jsonld_location(item.get("location"))
            city = city or default_city
            desc = _jsonld_text(item.get("description"))
            link = _jsonld_text(item.get("url")) or default_link
            admission_price = _jsonld_admission_price(item)
        except (TypeError, AttributeError, ValueError) as exc:
            _impl_run_state.log_source_error("JSON-LD event", exc)
            continue
        # Calendar plugins often publish a default ``price: 0`` even when the
        # visible event copy names a visitor fee. A narrowly phrased amount in
        # that copy is stronger evidence than this structured placeholder.
        if (
            admission_price == "kostenlos"
            and _jsonld_accessible_for_free(item.get("isAccessibleForFree")) is not True
        ):
            admission_price = _visible_paid_admission_price(desc) or admission_price
        organizer = _jsonld_entity_names(item.get("organizer"))
        availability = _jsonld_offer_availability(item.get("offers"))
        event_status = jsonld_event_status(item.get("eventStatus"))

        schedules = _jsonld_schedule_items(item.get("eventSchedule"))
        if schedules:
            for schedule in schedules:
                sched_start = _jsonld_schedule_dt(schedule, "startDate", "startTime")
                sched_end = _jsonld_schedule_dt(schedule, "endDate", "endTime") or sched_start
                ev = _impl_event_builder.make_event(
                    title, sched_start, sched_end, venue, city, desc, link, source,
                    category, trust, time_text=_jsonld_schedule_time_text(schedule),
                    source_id=source_id, admission=admission,
                    default_category_key=default_category_key,
                    category_locked=category_locked,
                )
                if ev:
                    if event_status:
                        ev["status"] = event_status
                    _apply_jsonld_provenance(
                        ev,
                        organizer=organizer,
                        admission_price=admission_price,
                        availability=availability,
                    )
                    events.append(ev)
            # Explicit schedule entries are the real appointments. The top-level
            # start/end often describes only a season span, e.g. Rheinauen-Flohmarkt
            # April→October, and must not be emitted as a stale appointment.
            continue

        ev = _impl_event_builder.make_event(
            title, start_dt, end_dt, venue, city, desc, link, source, category, trust,
            source_id=source_id, admission=admission,
            default_category_key=default_category_key,
            category_locked=category_locked,
        )
        if ev:
            if event_status:
                ev["status"] = event_status
            _apply_jsonld_provenance(
                ev,
                organizer=organizer,
                admission_price=admission_price,
                availability=availability,
            )
            events.append(ev)
    return events
