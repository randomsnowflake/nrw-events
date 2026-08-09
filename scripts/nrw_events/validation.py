"""Canonical event validation at the boundary between sources and the pipeline."""

from __future__ import annotations

import math
import re
import urllib.parse
from typing import Any

from . import ai_enrichment, category_taxonomy, common, richtext
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


_MARKTCOM_SELLER_FEE = re.compile(
    r"\b(?:standgebühr|standpreis|lfdm|laufend(?:er|en)?\s+(?:front)?meter|reinigungskaution|"
    r"verkäufergebühr|händlergebühr)\b",
    re.IGNORECASE,
)
_VISITOR_ADMISSION = re.compile(
    r"\b(?:besucher(?:eintritt|preis)|eintritt(?:spreis)?|ticket(?:preis)?)\b",
    re.IGNORECASE,
)


def _requires_master_data_only(event: dict[str, Any]) -> bool:
    source_id = str(event.get("source_id") or "").casefold()
    source = str(event.get("source") or "").casefold()
    return (
        source_id in {"ruhr-guide", "beuel-net", "meetup"}
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
        end_date = end.strftime("%Y-%m-%d") if end else start_date
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
    # marktcom descriptions mix seller logistics with visitor information.
    # A stall fee is explicit, but it is not an admission price and must never
    # drive the visitor-facing price badge — including in retained snapshots.
    if (
        event["source_id"] == "marktcom"
        and _MARKTCOM_SELLER_FEE.search(event["price"])
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
    if event["link"]:
        parsed = urllib.parse.urlsplit(event["link"])
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise EventValidationError("link_invalid")
    link_kind = _text(event, "link_kind", 16)
    if link_kind not in {"", "detail", "overview"}:
        raise EventValidationError("link_kind_invalid")
    event["link_kind"] = link_kind
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
    if event["category_key"] not in category_taxonomy.CATEGORY_BY_KEY:
        raise EventValidationError("category_key_invalid")
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
    return CanonicalEvent(**{
        field: event.get(field, definition.default)
        for field, definition in CanonicalEvent.__dataclass_fields__.items()
    })


def validate_event(raw_event: RawEvent | object) -> CanonicalEvent:
    """Backward-compatible name for the canonical conversion boundary."""
    return canonicalize_event(raw_event)
