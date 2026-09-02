"""Canonical event validation at the boundary between sources and the pipeline."""

from __future__ import annotations

import math
import re
import urllib.parse
from dataclasses import MISSING
from datetime import datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import ai_enrichment, category_taxonomy, common, event_types, richtext
from .models import (
    MAX_DISCOVERY_PROVENANCE_SOURCES,
    CanonicalEvent,
    RawEvent,
    normalize_source_id,
)
from .normalization import canonical_venue_id, resolve_venue
from .quality import evaluate_event_quality
from .title_normalization import normalize_event_title


class EventValidationError(ValueError):
    """A source record could not be safely published."""


_VISITOR_ADMISSION = re.compile(
    r"\b(?:besucher(?:eintritt|preis)|eintritt(?:spreis)?|ticket(?:preis)?)\b",
    re.IGNORECASE,
)
_EXPLICIT_FREE_VISITOR = re.compile(
    r"\b(?:(?:eintritt|teilnahme|besuch)\s+(?:ist\s+)?(?:frei|kostenlos|kostenfrei)|"
    r"(?:freier|kostenloser|kostenfreier)\s+(?:eintritt|besuch))\b",
    re.IGNORECASE,
)
_QUALIFIED_FREE_VISITOR = re.compile(
    r"\b(?:eintritt|teilnahme|besuch)\s+(?:ist\s+)?(?:frei|kostenlos|kostenfrei)\s+(?:nur\s+)?(?:für|mit|am)\b",
    re.IGNORECASE,
)

_EXHIBITOR_COPY = re.compile(
    r"\b(?:ausstell(?:ende|er|ern)|verkäufer(?:innen)?|verkaeufer(?:innen)?|"
    r"händler(?:innen)?|haendler(?:innen)?|stand(?:gebühr|gebuehr|fläche|flaeche|platz)|"
    r"selbst\s+verkaufen|aufbau|abbau|ticket[- ]?link)\b",
    re.IGNORECASE,
)
_RUNNING_METRE = re.compile(
    r"\b(?:pro\s+)?laufend(?:e|er|en|em)?\s+(?:front)?meter\b|\blfd\.?\s*m(?:eter)?\b",
    re.IGNORECASE,
)
_EXHIBITOR_FEE_AFTER = re.compile(
    r"(?:stand(?:gebühr|gebuehr|fläche|flaeche|platz)[^.]{0,70}?|"
    r"ausstell(?:ende|er|ern)[^.]{0,70}?)"
    r"(?P<amount>\d+(?:[,.]\d{1,2})?)\s*(?:€|eur\b|euro\b)",
    re.IGNORECASE,
)
_EXHIBITOR_FEE_BEFORE = re.compile(
    r"(?P<amount>\d+(?:[,.]\d{1,2})?)\s*(?:€|eur\b|euro\b)"
    r"[^.]{0,60}(?:laufend(?:e|er|en|em)?\s+(?:front)?meter|stand(?:fläche|flaeche|platz))",
    re.IGNORECASE,
)
_EXHIBITOR_FREE = re.compile(
    r"(?:stand(?:fläche|flaeche|platz)|ausstell(?:ende|er|ern)|verkäufer(?:innen)?|verkaeufer(?:innen)?)"
    r"[^.]{0,60}\b(?:kostenlos|kostenfrei|frei)\b",
    re.IGNORECASE,
)
_SETUP_TIME = re.compile(
    r"\baufbau(?:\s+beginnt)?\s+(?:ab|um)\s+(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*uhr\b",
    re.IGNORECASE,
)
_REGISTRATION_NOT_REQUIRED = re.compile(
    r"\b(?:eine\s+)?anmeldung\s+ist\s+nicht\s+erforderlich\b|\bohne\s+anmeldung\b",
    re.IGNORECASE,
)
_REGISTRATION_REQUIRED = re.compile(
    r"\b(?:anmeldung|stand(?:platz|fläche|flaeche))\b[^.]{0,60}\b(?:erforderlich|buchen|reservieren)\b",
    re.IGNORECASE,
)

_CLOCK_RANGE = re.compile(r"^(\d{2}):(\d{2})–(\d{2}):(\d{2})$")
_GERMAN_WEEKDAYS = {
    "montag": 0, "dienstag": 1, "mittwoch": 2, "donnerstag": 3,
    "freitag": 4, "samstag": 5, "sonntag": 6,
}
_DAILY_HOURS = re.compile(
    r"(?P<days>(?:montag|dienstag|mittwoch|donnerstag|freitag|samstag|sonntag)"
    r"(?:\s*(?:und|&|/|\+)\s*(?:montag|dienstag|mittwoch|donnerstag|freitag|samstag|sonntag))*)"
    r"\s*(?:von\s*)?(?P<start>\d{1,2}(?::\d{2})?)\s*(?:uhr\s*)?"
    r"(?:[-–]|–|bis)\s*(?P<end>\d{1,2}(?::\d{2})?)\s*uhr",
    re.IGNORECASE,
)


def _publication_warning(
    event: dict[str, Any], rule_id: str, field: str, resolution: str, message: str,
) -> None:
    warnings = event.setdefault("quality_warnings", [])
    if not isinstance(warnings, list):
        raise EventValidationError("quality_warnings_type")
    warning = {
        "rule_id": rule_id,
        "field": field,
        "resolution": resolution,
        "message": message,
    }
    if warning not in warnings:
        warnings.append(warning)


def _iso_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _empty_exhibitor() -> dict[str, Any]:
    return {
        "fee": {
            "isFree": None,
            "amount": None,
            "currency": "EUR",
            "unit": "",
            "basis": "",
            "note": "",
        },
        "setupTime": "",
        "accessHours": "",
        "registration": {
            "required": None,
            "url": "",
            "contact": "",
            "note": "",
        },
    }


def _clean_exhibitor_text(value: object, limit: int = 300) -> str:
    text = common.clean_html(str(value or "")).strip()
    if len(text) > limit:
        raise EventValidationError("exhibitor_text_too_long")
    return text


def _canonical_exhibitor(event: dict[str, Any]) -> None:
    """Keep seller logistics separate from visitor admission and opening time."""
    raw = event.get("exhibitor") or {}
    if not isinstance(raw, dict):
        raise EventValidationError("exhibitor_type")
    raw_fee = raw.get("fee") or {}
    raw_registration = raw.get("registration") or {}
    if not isinstance(raw_fee, dict):
        raise EventValidationError("exhibitor_fee_type")
    if not isinstance(raw_registration, dict):
        raise EventValidationError("exhibitor_registration_type")

    exhibitor = _empty_exhibitor()
    fee = exhibitor["fee"]
    registration = exhibitor["registration"]
    text = common.clean_html(" ".join((event.get("description", ""), event.get("price", ""))))

    amount_value = raw_fee.get("amount")
    amount: float | None = None
    if amount_value is not None:
        try:
            amount = float(amount_value)
        except (TypeError, ValueError) as exc:
            raise EventValidationError("exhibitor_fee_amount_invalid") from exc
        if not math.isfinite(amount) or amount < 0:
            raise EventValidationError("exhibitor_fee_amount_invalid")
    fee_match = _EXHIBITOR_FEE_AFTER.search(text) or _EXHIBITOR_FEE_BEFORE.search(text)
    if amount is None and fee_match:
        amount = float(fee_match.group("amount").replace(",", "."))
    explicit_free = raw_fee.get("isFree")
    if explicit_free not in {None, True, False}:
        raise EventValidationError("exhibitor_fee_free_invalid")
    if explicit_free is None and _EXHIBITOR_FREE.search(text):
        explicit_free = True
        amount = 0.0
    if amount is not None and explicit_free is None:
        explicit_free = amount == 0
    unit = _clean_exhibitor_text(raw_fee.get("unit"), 32)
    if not unit and fee_match and _RUNNING_METRE.search(text):
        unit = "running_metre"
    if unit not in {"", "flat", "running_metre", "table", "day"}:
        raise EventValidationError("exhibitor_fee_unit_invalid")
    basis = _clean_exhibitor_text(raw_fee.get("basis"), 16)
    if not basis and (amount is not None or explicit_free is not None):
        basis = "structured"
    if basis not in {"", "structured", "editorial"}:
        raise EventValidationError("exhibitor_fee_basis_invalid")
    fee.update({
        "isFree": explicit_free,
        "amount": amount,
        "currency": "EUR",
        "unit": unit,
        "basis": basis,
        "note": _clean_exhibitor_text(raw_fee.get("note")) or (common.concise_description(fee_match.group(0), max_chars=160) if fee_match else ""),
    })

    setup_time = _clean_exhibitor_text(raw.get("setupTime"), 100)
    setup_match = _SETUP_TIME.search(text)
    if not setup_time and setup_match:
        setup_time = f"{int(setup_match.group('hour')):02d}:{setup_match.group('minute') or '00'}"
    exhibitor["setupTime"] = setup_time
    exhibitor["accessHours"] = _clean_exhibitor_text(raw.get("accessHours"), 100)

    required = raw_registration.get("required")
    if required not in {None, True, False}:
        raise EventValidationError("exhibitor_registration_required_invalid")
    if required is None and _REGISTRATION_NOT_REQUIRED.search(text):
        required = False
    elif required is None and _REGISTRATION_REQUIRED.search(text):
        required = True
    url = str(raw_registration.get("url") or "").strip()
    if url:
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise EventValidationError("exhibitor_registration_url_invalid")
    registration.update({
        "required": required,
        "url": url,
        "contact": _clean_exhibitor_text(raw_registration.get("contact")),
        "note": _clean_exhibitor_text(raw_registration.get("note")),
    })
    event["exhibitor"] = exhibitor


def _visitor_description(value: str) -> str:
    """Remove operational seller sentences after their facts were structured."""
    paragraphs: list[str] = []
    for paragraph in re.split(r"\n\s*\n", value):
        sentences = re.split(r"(?<=[.!?])\s+", paragraph.strip())
        kept = [sentence for sentence in sentences if not _EXHIBITOR_COPY.search(sentence)]
        if kept:
            paragraphs.append(" ".join(kept))
    return "\n\n".join(paragraphs).strip()


def _clock_range_datetimes(
    date_value: str, time_value: str, timezone_name: str,
) -> tuple[datetime, datetime] | None:
    match = _CLOCK_RANGE.fullmatch(time_value)
    if not match:
        return None
    try:
        zone = ZoneInfo(timezone_name)
        day = common.parse_iso_date(date_value)
    except ZoneInfoNotFoundError:
        return None
    if day is None:
        return None
    start_hour, start_minute, end_hour, end_minute = map(int, match.groups())
    if start_hour > 23 or end_hour > 23 or start_minute > 59 or end_minute > 59:
        return None
    start = datetime.combine(day, time(start_hour, start_minute), zone)
    end = datetime.combine(day, time(end_hour, end_minute), zone)
    if end <= start:
        end += timedelta(days=1)
    return start, end


def _canonical_daily_schedule(event: dict[str, Any]) -> None:
    value = event.get("daily_schedule") or _daily_schedule_from_prose(event)
    if not isinstance(value, list):
        raise EventValidationError("daily_schedule_type")
    slots: list[dict[str, str]] = []
    conflicted_dates: set[str] = set()
    for index, candidate in enumerate(value[:31]):
        if not isinstance(candidate, dict):
            _publication_warning(event, "publication.schedule-invalid", "daily_schedule", "omitted", f"schedule slot {index} is not an object")
            continue
        date_value = str(candidate.get("date") or "").strip()
        start_value = str(candidate.get("start_at") or "").strip()
        end_value = str(candidate.get("end_at") or "").strip()
        start = _iso_datetime(start_value)
        end = _iso_datetime(end_value)
        if (
            not common.parse_iso_date(date_value)
            or not start or not end or end <= start
            or start.date().isoformat() != date_value
            or not event["start_date"] <= date_value <= event["end_date"]
        ):
            _publication_warning(event, "publication.schedule-invalid", "daily_schedule", "omitted", f"schedule slot {index} violates the event date/time invariant")
            continue
        slot = {"date": date_value, "start_at": start.isoformat(), "end_at": end.isoformat()}
        if date_value in conflicted_dates:
            continue
        existing = next((item for item in slots if item["date"] == date_value), None)
        if existing and existing != slot:
            slots.remove(existing)
            conflicted_dates.add(date_value)
            _publication_warning(event, "publication.schedule-conflict", "daily_schedule", "unknown", f"conflicting schedule slots for {date_value} were omitted")
            continue
        if not existing:
            slots.append(slot)
    event["daily_schedule"] = sorted(slots, key=lambda slot: slot["start_at"])


def _daily_schedule_from_prose(event: dict[str, Any]) -> list[dict[str, str]]:
    start_day = common.parse_iso_date(str(event.get("start_date") or ""))
    end_day = common.parse_iso_date(str(event.get("end_date") or ""))
    if not start_day or not end_day or end_day <= start_day or (end_day - start_day).days > 31:
        return []
    try:
        zone = ZoneInfo(str(event.get("timezone") or "Europe/Berlin"))
    except ZoneInfoNotFoundError:
        return []
    text_value = " ".join(str(event.get(field) or "") for field in ("time_note", "description"))
    slots: list[dict[str, str]] = []
    for match in _DAILY_HOURS.finditer(text_value):
        weekdays = {
            _GERMAN_WEEKDAYS[name.casefold()]
            for name in re.findall("|".join(_GERMAN_WEEKDAYS), match.group("days"), re.IGNORECASE)
        }
        start_text, end_text = match.group("start"), match.group("end")
        start_hour, _, start_minute = start_text.partition(":")
        end_hour, _, end_minute = end_text.partition(":")
        start_parts = int(start_hour), int(start_minute or 0)
        end_parts = int(end_hour), int(end_minute or 0)
        if start_parts[0] > 23 or start_parts[1] > 59 or end_parts[0] > 24 or end_parts[1] > 59 or (end_parts[0] == 24 and end_parts[1]):
            continue
        day = start_day
        while day <= end_day:
            if day.weekday() in weekdays:
                start = datetime.combine(day, time(*start_parts), zone)
                if end_parts[0] == 24:
                    end = datetime.combine(day + timedelta(days=1), time(0, end_parts[1]), zone)
                else:
                    end = datetime.combine(day, time(*end_parts), zone)
                    if end <= start:
                        end += timedelta(days=1)
                slots.append({"date": day.strftime("%Y-%m-%d"), "start_at": start.isoformat(), "end_at": end.isoformat()})
            day += timedelta(days=1)
    return slots


def _requires_master_data_only(event: dict[str, Any]) -> bool:
    source_id = str(event.get("source_id") or "").casefold()
    source = str(event.get("source") or "").casefold()
    link_host = (
        urllib.parse.urlsplit(str(event.get("link") or "")).hostname or ""
    ).casefold().removeprefix("www.")
    verified_jmj_description = (
        source_id == "beuel-net"
        and source == "jmj-online.de"
        and link_host == "jmj-online.de"
        and event.get("description_source") == "scraped"
    )
    return (
        (source_id in {"ruhr-guide", "beuel-net", "meetup"} and not verified_jmj_description)
        or source_id.startswith("meetup-")
        or source in {"marktcom", "meetup", "ruhr-guide", "beuel.net"}
    )


def _text(event: dict[str, Any], field: str, limit: int, required: bool = False) -> str:
    value = event.get(field, "")
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise EventValidationError(f"{field}_type")
    value = value.strip()
    if required and not value:
        raise EventValidationError(f"{field}_missing")
    if len(value) > limit:
        raise EventValidationError(f"{field}_too_long")
    return value


def _discovery_provenance(event: dict[str, Any]) -> None:
    source_role = _text(event, "source_role", 32) or "primary"
    if source_role not in {"primary", "discovery"}:
        raise EventValidationError("source_role_invalid")
    discovered_via = event.get("discovered_via", [])
    if not isinstance(discovered_via, list):
        raise EventValidationError("discovered_via_type")
    normalized: list[str] = []
    for value in discovered_via:
        if not isinstance(value, str):
            raise EventValidationError("discovered_via_item_type")
        source_id = normalize_source_id(value)
        if not source_id:
            raise EventValidationError("discovered_via_item_invalid")
        if source_id not in normalized:
            normalized.append(source_id)
    if len(normalized) > MAX_DISCOVERY_PROVENANCE_SOURCES:
        raise EventValidationError("discovered_via_too_many")
    if source_role == "discovery" and not normalized:
        raise EventValidationError("discovered_via_missing")
    event["source_role"] = source_role
    event["discovered_via"] = normalized


def _canonical_temporal_fields(event: dict[str, Any]) -> None:
    # Canonical records can be revalidated after source continuity or
    # deduplication. Recompute this invariant from the current fields instead
    # of carrying a warning that an earlier importer version already repaired.
    warnings = event.get("quality_warnings")
    if isinstance(warnings, list):
        event["quality_warnings"] = [
            warning
            for warning in warnings
            if not (
                isinstance(warning, dict)
                and warning.get("rule_id") == "publication.end-not-after-start"
            )
        ]
    start_date = _text(event, "start_date", 10)
    end_date = _text(event, "end_date", 10)
    legacy_date = _text(event, "date", 80)
    legacy_ongoing = legacy_date.casefold().startswith("ongoing until ")
    if not start_date:
        if "–" in legacy_date:
            start_text, end_text = legacy_date.split("–", 1)
            start = common.parse_date(start_text)
            end = common.parse_date(end_text)
        else:
            start = common.parse_date(legacy_date)
            end = start
        if not start:
            raise EventValidationError("start_date_missing_or_invalid")
        start_date = start.strftime("%Y-%m-%d")
        end_date = end_date or (end.strftime("%Y-%m-%d") if end else start_date)
    if not common.parse_iso_date(start_date):
        raise EventValidationError("start_date_invalid")
    if end_date and not common.parse_iso_date(end_date):
        raise EventValidationError("end_date_invalid")
    event["start_date"] = start_date
    event["end_date"] = end_date or start_date
    event["date"] = start_date
    event["ongoing"] = bool(event.get("ongoing", legacy_ongoing))
    event["all_day"] = bool(event.get(
        "all_day",
        not event.get("start_at") and not event.get("time") and not event.get("time_note"),
    ))
    event["timezone"] = _text(event, "timezone", 64) or "Europe/Berlin"
    _canonical_daily_schedule(event)

    raw_start_at = str(event.get("start_at") or "").strip()
    raw_end_at = str(event.get("end_at") or "").strip()
    start_at = _iso_datetime(raw_start_at)
    end_at = _iso_datetime(raw_end_at)
    clock_range = _clock_range_datetimes(start_date, str(event.get("time") or ""), event["timezone"])
    if start_at and end_at and end_at <= start_at:
        if clock_range:
            start_at, end_at = clock_range
            _publication_warning(event, "publication.end-not-after-start", "end_at", "repaired_from_time", "structured end was not after start; the explicit clock range was used")
        else:
            end_at = None
            _publication_warning(event, "publication.end-not-after-start", "end_at", "unknown", "structured end was not after start and was omitted")
    elif clock_range and (not start_at or not end_at):
        start_at, end_at = clock_range
    event["start_at"] = raw_start_at if start_at and start_at == _iso_datetime(raw_start_at) else (start_at.isoformat() if start_at else "")
    event["end_at"] = raw_end_at if end_at and end_at == _iso_datetime(raw_end_at) else (end_at.isoformat() if end_at else "")
    if event["daily_schedule"]:
        event["time"] = ""
        event["start_at"] = ""
        event["end_at"] = ""
        event["all_day"] = False
    elif event["time"] or event["start_at"]:
        event["all_day"] = False


def canonicalize_event(raw_event: RawEvent | object) -> CanonicalEvent:
    """Return one canonical event or raise a reason-coded validation error."""
    if not isinstance(raw_event, dict):
        raise EventValidationError("record_not_object")
    event = dict(raw_event)
    event["title"] = _text(event, "title", 500, required=True)
    event["source"] = _text(event, "source", 160, required=True)
    event["source_id"] = normalize_source_id(
        _text(event, "source_id", 200) or event["source"]
    )
    _discovery_provenance(event)
    inferred_description_source = common.description_source_for(event.get("description", ""))
    for field, limit in (("time", 500), ("time_note", 500), ("venue", 300), ("city", 160), ("organizer", 500), ("description", 8000), ("description_html", 100000), ("ai_summary", 4000),
                         ("price", 160), ("category", 500), ("link", 2048)):
        event[field] = _text(event, field, limit)
    _canonical_exhibitor(event)
    event["description"] = _visitor_description(event["description"])
    # Source price fields sometimes mix seller logistics with visitor facts.
    # A stall fee is explicit, but it is not an admission price and must never
    # drive the visitor-facing price badge — regardless of which adapter found it.
    if (
        common.has_seller_fee(event["price"])
        and not _VISITOR_ADMISSION.search(event["price"])
    ):
        event["price"] = ""
        event["admission_basis"] = ""
    # Re-built from the allowed vocabulary at the canonical boundary, so a
    # source that sets this field directly cannot smuggle markup past it, and
    # discarded outright when it no longer renders the description it belongs to.
    rich_text = richtext.sanitize_rich_text(event["description_html"])
    if not richtext.describes_same_copy(rich_text, event["description"]):
        rich_text = ""
    event["description_html"] = rich_text or richtext.from_plain_text(event["description"])
    event["identity_venue"] = _text(event, "identity_venue", 300)
    event["identity_venue_locked"] = bool(event.get("identity_venue_locked", False))
    event["identity_time"] = _text(event, "identity_time", 100)
    event["identity_time_locked"] = bool(event.get("identity_time_locked", False))
    explicit_venue_id = _text(event, "venue_id", 160)
    if explicit_venue_id and not re.fullmatch(
        r"[a-z0-9]+(?:-[a-z0-9]+)*",
        explicit_venue_id,
    ):
        raise EventValidationError("venue_id_invalid")
    explicit_venue_address = _text(event, "venue_address", 500)
    explicit_venue_district = _text(event, "venue_district", 160)
    explicit_venue_type = _text(event, "venue_type", 80)
    explicit_venue_latitude = event.get("venue_latitude")
    explicit_venue_longitude = event.get("venue_longitude")
    # Keep a source-provided address in the resolution input. Otherwise a
    # generic venue label can wrongly acquire another branch's address and
    # coordinates during this second canonicalization pass.
    venue_input = ", ".join(
        part for part in (event["venue"], explicit_venue_address) if part
    )
    venue = resolve_venue(venue_input, event["city"], explicit_id=explicit_venue_id)
    event["venue"] = venue.venue
    event["venue_id"] = venue.venue_id or explicit_venue_id
    event["venue_address"] = venue.venue_address or explicit_venue_address
    event["venue_district"] = venue.venue_district or explicit_venue_district
    event["venue_type"] = venue.venue_type or explicit_venue_type
    event["venue_latitude"] = (
        venue.venue_latitude
        if venue.venue_latitude is not None else explicit_venue_latitude
    )
    event["venue_longitude"] = (
        venue.venue_longitude
        if venue.venue_longitude is not None else explicit_venue_longitude
    )
    event["venue_id"] = canonical_venue_id(event)
    canonical_time, inferred_time_note = common.normalize_time_fields(event["time"])
    event["time"] = canonical_time
    event["time_note"] = common.combine_time_notes(
        event["time_note"], inferred_time_note,
    )
    if len(event["time_note"]) > 500:
        raise EventValidationError("time_note_too_long")
    if event["time"] and not re.fullmatch(r"\d{2}:\d{2}(?:–\d{2}:\d{2})?", event["time"]):
        raise EventValidationError("time_invalid")
    admission_basis = _text(event, "admission_basis", 16)
    if admission_basis not in {"", "explicit", "inferred", "implicit"}:
        raise EventValidationError("admission_basis_invalid")
    availability = _text(event, "availability", 32)
    if availability not in {"", "InStock", "SoldOut", "LimitedAvailability", "PreOrder"}:
        raise EventValidationError("availability_invalid")
    event["availability"] = availability
    event["description_source"] = (
        _text(event, "description_source", 16) or inferred_description_source
    )
    if event["description_source"] not in {"scraped", "generated"}:
        raise EventValidationError("description_source_invalid")
    inferred_free_price, inferred_admission_basis = common.infer_admission(
        event["title"],
        event["description"],
        event["price"],
        admission_basis=admission_basis,
    )
    explicit_free_visitor = (
        bool(_EXPLICIT_FREE_VISITOR.search(" ".join((event["description"], event["price"]))))
        and not common.has_conditional_free_admission(event["description"])
        and not _QUALIFIED_FREE_VISITOR.search(event["description"])
    )
    if inferred_admission_basis == "inferred" and (
        explicit_free_visitor
        or (
            common.source_preserves_explicit_admission(event["source"], event["source_id"])
            and common.has_explicit_free_admission_wording(event["title"], event["description"])
        )
    ):
        inferred_admission_basis = "explicit"
    elif inferred_admission_basis in {"implicit", "inferred"}:
        _publication_warning(event, "publication.admission-not-explicit", "admission", "unknown", "free admission lacked explicit visitor evidence and was omitted")
        inferred_free_price = ""
        inferred_admission_basis = ""
        event["price"] = ""
    if inferred_free_price:
        event["price"] = inferred_free_price
    elif admission_basis == "implicit":
        event["price"] = ""
    event["admission_basis"] = inferred_admission_basis
    admission_text = " ".join((
        event["title"], event["description"], event["price"],
    )).casefold()
    amount_match = re.search(
        r"(?<!\d)(\d+(?:[.,]\d{1,2})?)\s*(?:€|eur\b|euro\b)",
        event["price"].casefold(),
    )
    amount = (
        float(amount_match.group(1).replace(",", "."))
        if amount_match else None
    )
    normalized_price = event["price"].strip().casefold()
    donation_suggested = bool(re.search(
        r"\b(?:spendenbasis|spende(?:n)?\s+erbeten|hut(?:kasse|spende|spenden))\b",
        admission_text,
    ))
    is_free = (
        True
        if normalized_price in {"frei", "kostenfrei", "kostenlos", "free"}
        or amount == 0 or donation_suggested
        else False if normalized_price or amount is not None else None
    )
    event["admission"] = {
        "isFree": is_free,
        "amount": amount,
        "currency": "EUR",
        "basis": (
            "structured" if inferred_admission_basis == "explicit"
            else inferred_admission_basis
        ),
        "note": event["price"],
        "donationSuggested": donation_suggested,
    }
    if event["admission"]["isFree"] is True and event["admission"]["basis"] not in {"structured", "editorial"}:
        _publication_warning(event, "publication.admission-not-explicit", "admission", "unknown", "free admission lacked explicit visitor evidence and was omitted")
        event["price"] = ""
        event["admission_basis"] = ""
        event["admission"] = {
            "isFree": None,
            "amount": None,
            "currency": "EUR",
            "basis": "",
            "note": "",
            "donationSuggested": False,
        }
    if event["link"]:
        parsed = urllib.parse.urlsplit(event["link"])
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise EventValidationError("link_invalid")
    link_kind = _text(event, "link_kind", 16)
    if link_kind not in {"", "detail", "overview"}:
        raise EventValidationError("link_kind_invalid")
    event["link_kind"] = link_kind
    source_links = event.get("source_links") or []
    if not isinstance(source_links, (list, tuple)):
        raise EventValidationError("source_links_invalid")
    validated_source_links: list[str] = []
    for value in source_links:
        link = str(value or "").strip()
        parsed = urllib.parse.urlsplit(link)
        if not link or parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise EventValidationError("source_links_invalid")
        if link not in validated_source_links:
            validated_source_links.append(link)
    if link_kind == "detail" and event["link"] and event["link"] not in validated_source_links:
        validated_source_links.append(event["link"])
    event["source_links"] = validated_source_links[:20]
    previous_event_ids = event.get("previous_event_ids") or []
    if not isinstance(previous_event_ids, (list, tuple)):
        raise EventValidationError("previous_event_ids_invalid")
    event["previous_event_ids"] = list(dict.fromkeys(
        str(value or "").strip() for value in previous_event_ids
        if str(value or "").strip()
    ))[:20]
    _canonical_temporal_fields(event)
    event["title"] = normalize_event_title(
        event["title"],
        start=common.parse_iso_date(event["start_date"]),
        end=common.parse_iso_date(event["end_date"]),
        source=event["source"],
    )
    if (event["venue_latitude"] is None) != (event["venue_longitude"] is None):
        raise EventValidationError("venue_coordinates_incomplete")
    if event["venue_latitude"] is not None:
        latitude = float(event["venue_latitude"])
        longitude = float(event["venue_longitude"])
        if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
            raise EventValidationError("venue_coordinates_invalid")
        event["venue_latitude"] = latitude
        event["venue_longitude"] = longitude
        event["distance_km"] = round(
            common.haversine(common.BONN_LAT, common.BONN_LON, latitude, longitude),
            2,
        )
        event["location_confidence"] = "exact"
        event["location_source"] = "venue_registry"
    for field in ("score", "distance_km"):
        value = event.get(field)
        if value is None and field == "distance_km":
            continue
        try:
            value = float(value)
        except (TypeError, ValueError) as exc:
            raise EventValidationError(f"{field}_invalid") from exc
        if not math.isfinite(value) or (field == "score" and not 0 <= value <= 10) or value < 0:
            raise EventValidationError(f"{field}_invalid")
        event[field] = round(value, 2)
    if not event.get("location_confidence"):
        resolved_coords, confidence, location_source = common.resolve_location(event["city"])
        event["location_confidence"] = confidence
        event["location_source"] = location_source
        if confidence == "unresolved":
            # Older direct-dict sources sometimes used Bonn as an implicit fallback.
            # Do not let that fallback bypass the radius check or dominate ranking.
            event["distance_km"] = None
            event["score"] = min(event["score"], 0.3)
        elif event.get("distance_km") is None and resolved_coords:
            event["distance_km"] = round(common.haversine(common.BONN_LAT, common.BONN_LON, *resolved_coords), 2)
    status = _text(event, "status", 32) or "scheduled"
    if status not in {"scheduled", "cancelled", "postponed", "unknown"}:
        raise EventValidationError("status_invalid")
    if status == "scheduled" and common.event_status(event["title"], event["description"]) == "postponed":
        status = "postponed"
    event["status"] = status
    event["early_publication"] = bool(event.get("early_publication", False))
    # URLs contain venue slugs and navigation words such as ``museum`` or
    # ``events``; they are transport metadata, not editorial category evidence.
    try:
        current_confidence = float(event.get("category_confidence") or 0)
    except (TypeError, ValueError):
        current_confidence = 0.0
    current_reason = str(event.get("category_reason") or "")
    category_locked = current_reason.startswith("source:locked-default:")
    category_incomplete = not event.get("category_key") or not event.get("category_label")
    # Adapter-produced canonical records without an evidence decision are
    # already complete and stay on the hot path. Reconsider only incomplete
    # records or records whose earlier classification exposes a confidence and
    # reason that richer detail copy can legitimately improve.
    should_reconsider = category_incomplete or bool(current_reason and not category_locked)
    if should_reconsider:
        canonical = category_taxonomy.categorize_event(
            event["category"],
            event["title"],
            event["description"],
            venue=event["venue"],
            source=event["source"],
            source_id=event["source_id"],
        )
        canonical_confidence = float(canonical.get("confidence") or 0)
        canonical_reason = str(canonical.get("reason") or "")
        canonical_has_content_evidence = any(
            marker in canonical_reason
            for marker in ("title=", "description=", "forced:", "format:")
        )
        current_has_content_evidence = any(
            marker in current_reason
            for marker in ("title=", "description=", "forced:", "format:")
        )
        if (
            category_incomplete
            or canonical_confidence > current_confidence
            or (
                canonical_confidence == current_confidence
                and canonical_has_content_evidence
                and not current_has_content_evidence
            )
        ):
            event["category_key"] = canonical["key"]
            event["category_label"] = canonical["label"]
            event["category_confidence"] = canonical_confidence
            event["category_reason"] = canonical_reason
    if not should_reconsider:
        event.setdefault("category_confidence", current_confidence)
        event.setdefault("category_reason", "")
    if not current_reason and not event.get("category_reason"):
        # Some direct-dict adapters already supply a canonical category but
        # predate the evidence metadata. Preserve that explicit source choice
        # and keep the hot path free of an unnecessary reclassification.
        event["category_reason"] = f"source:canonical:{event['category_key']}"
    if event["category_key"] not in category_taxonomy.CATEGORY_BY_KEY:
        raise EventValidationError("category_key_invalid")
    try:
        event["event_types"] = event_types.classify_event_types(event)
    except ValueError as exc:
        raise EventValidationError(str(exc)) from exc
    decision = evaluate_event_quality(event)
    if decision.should_drop:
        raise EventValidationError(f"quality:{decision.rule_id}")
    # Retention can reintroduce an older snapshot after a live source failure.
    # Enforce the source policy at the final canonical boundary as well as in
    # the adapters so historical prose can never be republished.
    if _requires_master_data_only(event):
        common.keep_only_event_master_data(event)
    if ai_enrichment.is_target_event(event):
        ai_enrichment.strip_restricted_copy(event)
    canonical_fields: dict[str, Any] = {}
    for field, definition in CanonicalEvent.__dataclass_fields__.items():
        if field in event:
            canonical_fields[field] = event[field]
        elif definition.default_factory is not MISSING:
            canonical_fields[field] = definition.default_factory()
        else:
            canonical_fields[field] = definition.default
    return CanonicalEvent(**canonical_fields)


def validate_event(raw_event: RawEvent | object) -> CanonicalEvent:
    """Backward-compatible name for the canonical conversion boundary."""
    return canonicalize_event(raw_event)
