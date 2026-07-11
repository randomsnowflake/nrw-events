"""Canonical event validation at the boundary between sources and the pipeline."""

from __future__ import annotations

import math
import urllib.parse
from typing import Any

from . import category_taxonomy, common
from .models import CanonicalEvent, RawEvent
from .quality import evaluate_event_quality


class EventValidationError(ValueError):
    """A source record could not be safely published."""


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


def _canonical_temporal_fields(event: dict[str, Any]) -> None:
    start_date = _text(event, "start_date", 10)
    end_date = _text(event, "end_date", 10)
    legacy_date = _text(event, "date", 80)
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
    event["all_day"] = bool(event.get("all_day", not event.get("start_at")))
    event["timezone"] = _text(event, "timezone", 64) or "Europe/Berlin"


def canonicalize_event(raw_event: RawEvent | object) -> CanonicalEvent:
    """Return one canonical event or raise a reason-coded validation error."""
    if not isinstance(raw_event, dict):
        raise EventValidationError("record_not_object")
    event = dict(raw_event)
    event["title"] = _text(event, "title", 500, required=True)
    event["source"] = _text(event, "source", 160, required=True)
    for field, limit in (("time", 80), ("venue", 300), ("city", 160), ("description", 8000),
                         ("price", 160), ("category", 500), ("link", 2048)):
        event[field] = _text(event, field, limit)
    if event["link"]:
        parsed = urllib.parse.urlsplit(event["link"])
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise EventValidationError("link_invalid")
    _canonical_temporal_fields(event)
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
    event["status"] = status
    # URLs contain venue slugs and navigation words such as ``museum`` or
    # ``events``; they are transport metadata, not editorial category evidence.
    canonical = category_taxonomy.categorize_event(
        event["category"], event["title"], event["description"]
    )
    event.setdefault("category_key", canonical["key"])
    event.setdefault("category_label", canonical["label"])
    event.setdefault("category_confidence", canonical.get("confidence", 0))
    event.setdefault("category_reason", canonical.get("reason", ""))
    if event["category_key"] not in category_taxonomy.CATEGORY_BY_KEY:
        raise EventValidationError("category_key_invalid")
    decision = evaluate_event_quality(event)
    if decision.should_drop:
        raise EventValidationError(f"quality:{decision.rule_id}")
    return CanonicalEvent(**{
        field: event.get(field, definition.default)
        for field, definition in CanonicalEvent.__dataclass_fields__.items()
    })


def validate_event(raw_event: RawEvent | object) -> CanonicalEvent:
    """Backward-compatible name for the canonical conversion boundary."""
    return canonicalize_event(raw_event)
