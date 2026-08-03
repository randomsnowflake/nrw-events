"""Explainable editorial quality decisions."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable, Mapping

from .core import _legacy_junk_decision, event_status


class QualityAction(str, Enum):
    KEEP = "keep"
    DROP = "drop"


@dataclass(frozen=True, slots=True)
class QualityDecision:
    action: QualityAction
    rule_id: str
    reason: str
    matched_terms: tuple[str, ...] = ()

    @property
    def should_drop(self) -> bool:
        return self.action is QualityAction.DROP


REQUIRED_PUBLICATION_FIELDS = (
    "title", "source", "start_date", "end_date", "date", "city", "link",
    "score", "status", "timezone", "category_key", "category_label",
    "category_confidence", "category_reason", "all_day", "location_confidence",
)
OPTIONAL_CONTENT_FIELDS = (
    "time", "time_note", "venue", "venue_id", "venue_address",
    "venue_district", "venue_type", "description", "price",
)

# Warning-only gates. They deliberately require a meaningful sample so a single
# sparse record cannot turn a healthy import into alert noise. These thresholds
# describe suspicious distributions; they do not decide whether an event is true.
QUALITY_GATE_MIN_EVENTS = 10
QUALITY_GATE_THRESHOLDS = {
    "uncategorized_rate": 0.06,
    "low_confidence_rate": 0.5,
    "unresolved_location_rate": 0.25,
    "missing_venue_rate": 0.25,
    "drop_rate": 0.5,
}


def summarize_event_quality(events: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Return stable completeness and classification metrics for monitoring."""
    rows = list(events)

    def present(event: Mapping[str, Any], field: str) -> bool:
        value = event.get(field)
        return value is not None and (not isinstance(value, str) or bool(value.strip()))

    category_counts: dict[str, int] = {}
    sources: dict[str, list[Mapping[str, Any]]] = {}
    for event in rows:
        key = str(event.get("category_key") or "other")
        category_counts[key] = category_counts.get(key, 0) + 1
        source = str(event.get("source") or "unknown")
        sources.setdefault(source, []).append(event)

    def source_metrics(source_rows: list[Mapping[str, Any]]) -> dict[str, Any]:
        count = len(source_rows)
        low_confidence = sum(
            float(event.get("category_confidence") or 0) < 0.6
            for event in source_rows
        )
        unresolved = sum(
            event.get("location_confidence") == "unresolved"
            for event in source_rows
        )
        missing_venue = sum(not present(event, "venue") for event in source_rows)
        registered_venue = sum(present(event, "venue_id") for event in source_rows)
        venue_address = sum(present(event, "venue_address") for event in source_rows)
        return {
            "event_count": count,
            "low_confidence_count": low_confidence,
            "low_confidence_rate": round(low_confidence / count, 4) if count else 0.0,
            "unresolved_location_count": unresolved,
            "unresolved_location_rate": round(unresolved / count, 4) if count else 0.0,
            "missing_venue_count": missing_venue,
            "missing_venue_rate": round(missing_venue / count, 4) if count else 0.0,
            "registered_venue_count": registered_venue,
            "registered_venue_rate": round(registered_venue / count, 4) if count else 0.0,
            "venue_address_count": venue_address,
            "venue_address_rate": round(venue_address / count, 4) if count else 0.0,
        }

    by_source = {
        source: source_metrics(source_rows)
        for source, source_rows in sorted(sources.items())
    }
    return {
        "event_count": len(rows),
        "missing_required_fields": {
            field: sum(not present(event, field) for event in rows)
            for field in REQUIRED_PUBLICATION_FIELDS
        },
        "optional_field_coverage": {
            field: sum(present(event, field) for event in rows)
            for field in OPTIONAL_CONTENT_FIELDS
        },
        "registered_venue_count": sum(present(event, "venue_id") for event in rows),
        "venue_address_count": sum(present(event, "venue_address") for event in rows),
        "category_counts": dict(sorted(category_counts.items())),
        "uncategorized_count": category_counts.get("other", 0),
        "uncategorized_rate": (
            round(category_counts.get("other", 0) / len(rows), 4) if rows else 0.0
        ),
        "by_source": by_source,
    }


def quality_gate_warnings(
    metrics: Mapping[str, Any],
    source_results: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Return warning-only source distribution gates for monitoring metadata."""
    warnings: list[dict[str, Any]] = []
    event_count = int(metrics.get("event_count") or 0)
    uncategorized = int(metrics.get("uncategorized_count") or 0)
    uncategorized_rate = float(metrics.get("uncategorized_rate") or 0)
    uncategorized_threshold = QUALITY_GATE_THRESHOLDS["uncategorized_rate"]
    if (event_count >= QUALITY_GATE_MIN_EVENTS
            and uncategorized_rate > uncategorized_threshold):
        warnings.append({
            "source": "all",
            "error_type": "QualityGateWarning",
            "error": (
                "published events remain uncategorized: "
                f"{uncategorized}/{event_count} ({uncategorized_rate:.1%})"
            ),
            "rule_id": "quality.uncategorized-rate",
            "count": uncategorized,
            "event_count": event_count,
            "rate": round(uncategorized_rate, 4),
            "threshold": uncategorized_threshold,
        })
    rate_rules = (
        (
            "low_confidence_rate",
            "low_confidence_count",
            "quality.low-confidence-rate",
            "published events have low category confidence",
        ),
        (
            "unresolved_location_rate",
            "unresolved_location_count",
            "quality.unresolved-location-rate",
            "published events have unresolved locations",
        ),
        (
            "missing_venue_rate",
            "missing_venue_count",
            "quality.missing-venue-rate",
            "published events are missing venue names",
        ),
    )
    for source, source_metrics in sorted((metrics.get("by_source") or {}).items()):
        count = int(source_metrics.get("event_count") or 0)
        if count < QUALITY_GATE_MIN_EVENTS:
            continue
        for rate_key, count_key, rule_id, description in rate_rules:
            rate = float(source_metrics.get(rate_key) or 0)
            threshold = QUALITY_GATE_THRESHOLDS[rate_key]
            if rate <= threshold:
                continue
            affected = int(source_metrics.get(count_key) or 0)
            warnings.append({
                "source": source,
                "error_type": "QualityGateWarning",
                "error": f"{description}: {affected}/{count} ({rate:.1%})",
                "rule_id": rule_id,
                "count": affected,
                "event_count": count,
                "rate": rate,
                "threshold": threshold,
            })

    for source, result in sorted(source_results.items()):
        reasons = result.get("rejection_reasons") or {}
        quality_drops = sum(
            int(count)
            for reason, count in reasons.items()
            if str(reason).startswith("quality:")
        )
        accepted = int(result.get("accepted_event_count") or 0)
        candidates = accepted + quality_drops
        if candidates < QUALITY_GATE_MIN_EVENTS:
            continue
        rate = quality_drops / candidates
        threshold = QUALITY_GATE_THRESHOLDS["drop_rate"]
        if rate <= threshold:
            continue
        warnings.append({
            "source": source,
            "error_type": "QualityGateWarning",
            "error": (
                "editorial quality rules dropped "
                f"{quality_drops}/{candidates} in-window candidates ({rate:.1%})"
            ),
            "rule_id": "quality.drop-rate",
            "count": quality_drops,
            "event_count": candidates,
            "rate": round(rate, 4),
            "threshold": threshold,
        })
    return warnings


_WORK_TITLE = re.compile(r"[»«„““”\"']\s*\S")
_ADVERTISING_MARKER = re.compile(
    r"^\s*(anzeige|advertorial|sponsored)\b",
    re.IGNORECASE,
)
_UNAVAILABLE_STATUS = (
    r"ausgebucht|ausverkauft|sold\s*out|abgesagt|abgesetzt|entfällt|entfaellt|"
    r"fällt\s+(?:leider\s+)?aus|faellt\s+(?:leider\s+)?aus|"
    r"findet\s+(?:leider\s+)?nicht\s+statt"
)
_SOLD_OUT_STATUS = r"ausgebucht|ausverkauft|sold\s*out"
_CANCELLED_STATUS = (
    r"abgesagt|abgesetzt|entfällt|entfaellt|"
    r"fällt\s+(?:leider\s+)?aus|faellt\s+(?:leider\s+)?aus|"
    r"findet\s+(?:leider\s+)?nicht\s+statt"
)
_UNAVAILABLE_TITLE_MARKER = re.compile(
    rf"^\s*[-+–—:()]*\s*(?P<marker>{_UNAVAILABLE_STATUS}|geschlossen)\b"
    rf"|\b(?P<suffix>{_UNAVAILABLE_STATUS}|geschlossen)\s*[-+–—:()!.?]*\s*$",
    re.IGNORECASE,
)
_UNAVAILABLE_DESCRIPTION_EDGE = re.compile(
    rf"^\s*[-+–—:()]*\s*(?P<prefix>{_CANCELLED_STATUS}|geschlossen)\b"
    rf"|\b(?P<suffix>{_CANCELLED_STATUS})\s*[-+–—:()!.?]*\s*$",
    re.IGNORECASE,
)
_SOLD_OUT_DESCRIPTION_SENTENCE = re.compile(
    rf"^\s*[-+–—:()]*\s*(?P<prefix>{_SOLD_OUT_STATUS})\b"
    rf"|(?:^|[.!?]\s+|[-–—:]\s*)"
    rf"(?P<suffix>{_SOLD_OUT_STATUS})\s*[-+–—:()!.?]*\s*$",
    re.IGNORECASE,
)
_UNAVAILABLE_CURRENT_STATE = re.compile(
    rf"\b(?:die|der|das)?\s*"
    rf"(?:veranstaltung|termin|event|konzert|lesung|show|party|kurs|workshop|"
    rf"führung|fuehrung|vorstellung|tickets?|karten?|plätze?|plaetze?|"
    rf"anmeldung|buchung|ticketverkauf)\b"
    rf"[^.\n!?]{{0,80}}\b(?:ist|sind|bleibt|bleiben|wurde|wurden)\b"
    rf"[^.\n!?]{{0,40}}\b(?P<marker>{_CANCELLED_STATUS}|geschlossen|"
    rf"nicht\s+mehr\s+(?:verfügbar|verfuegbar|buchbar))\b",
    re.IGNORECASE,
)
_NO_AVAILABILITY = re.compile(
    r"\b(?P<marker>keine|0)\s+(?:tickets?|karten?|plätze|plaetze)"
    r"\s+(?:mehr\s+)?(?:verfügbar|verfuegbar|vorhanden|buchbar)\b",
    re.IGNORECASE,
)
_ONLINE_ONLY_VENUE = re.compile(
    r"^(zoom|webex|jitsi|bigbluebutton|(?:ms|microsoft)\s+teams|online|digital)$",
    re.IGNORECASE,
)


def _names_a_work(title: str) -> bool:
    """Whether a title quotes the specific work an event is built around."""
    return bool(_WORK_TITLE.search(title))


def _online_only_platform(venue: str) -> str:
    """Return the conferencing platform when a venue is nothing but a platform.

    Parenthetical joining hints ("Zoom (Der Link folgt)") are stripped first, so
    a real venue that merely mentions a stream is not mistaken for one.
    """
    stripped = re.sub(r"\([^)]*\)", " ", venue)
    stripped = re.sub(r"\s+", " ", stripped).strip(" ,;-")
    match = _ONLINE_ONLY_VENUE.match(stripped)
    return match.group(0) if match else ""


def _unavailable_status_marker(title: str, description: str) -> str:
    """Return an explicit current unavailability marker without matching narrative copy."""
    for pattern, content in (
        (_UNAVAILABLE_TITLE_MARKER, title),
        (_UNAVAILABLE_DESCRIPTION_EDGE, description),
        (_SOLD_OUT_DESCRIPTION_SENTENCE, description),
        (_UNAVAILABLE_CURRENT_STATE, description),
        (_NO_AVAILABILITY, description),
    ):
        if match := pattern.search(content):
            return match.group(0).strip()
    return ""


def evaluate_event_quality(event: Mapping[str, Any]) -> QualityDecision:
    """Evaluate the ordered compatibility policy and explain its outcome.

    The compatibility policy is imported from the implementation module rather
    than through ``common``, keeping the public facade out of the dependency
    graph and making this module independently importable.
    """
    title = str(event.get("title") or "").lower()
    description = str(event.get("description") or "").lower()
    text = f"{title} {description}"

    advertising_marker = next(
        (match for content in (title, description) if (match := _ADVERTISING_MARKER.match(content))),
        None,
    )
    if advertising_marker:
        return QualityDecision(
            QualityAction.DROP,
            "editorial.advertising-marker",
            "publisher marked the event content as advertising",
            (advertising_marker.group(1).lower(),),
        )

    unavailable_marker = _unavailable_status_marker(title, description)
    if unavailable_marker and event.get("status") not in {"cancelled", "postponed"}:
        return QualityDecision(
            QualityAction.DROP,
            "availability.unavailable",
            "publisher marked the event as unavailable to visitors",
            (unavailable_marker,),
        )

    # Public participation records are valuable civic information, but they are
    # planning procedures rather than dated leisure events.
    if "planung" in description and "stellungnahme" in description:
        return QualityDecision(
            QualityAction.DROP,
            "civic.public-consultation",
            "planning consultation is not a destination event",
            ("planung", "stellungnahme"),
        )

    if re.search(r"\bblutspende(?:termin|aktion)?\b", title):
        return QualityDecision(
            QualityAction.DROP,
            "civic.health-service",
            "routine public health service is not a destination event",
            ("blutspende",),
        )

    if re.search(r"\b(?:offenes\s+)?plenum\b", title):
        return QualityDecision(
            QualityAction.DROP,
            "civic.organizational-meeting",
            "organizational meeting is not a destination event",
            ("plenum",),
        )

    reading_circle = re.search(r"\b(?:lesekreis|literaturkreis|lesezirkel)\b", title)
    if reading_circle and not _names_a_work(title):
        # Library and club reading circles are standing groups that meet to
        # discuss a book among themselves. Curated literary series carry the
        # discussed work in the title ("Lesezirkel: Autor »Titel«") and stay.
        return QualityDecision(
            QualityAction.DROP,
            "civic.reading-circle",
            "standing reading group is not a destination event",
            (reading_circle.group(0),),
        )

    platform = _online_only_platform(str(event.get("venue") or ""))
    if platform:
        return QualityDecision(
            QualityAction.DROP,
            "civic.online-only",
            "online-only session has no venue to visit",
            (platform,),
        )

    recurring = re.search(
        r"\b(?:jeden|jeden\s+\w+|wöchentlich|woechentlich|regelmäßig|regelmaessig)\b",
        description,
    )
    routine_sale = re.search(r"\b(?:verkauf|ausgabe)\b", text) and re.search(
        r"\b(?:gespendet|kleidung|kleider|sachen)\b", text
    )
    if recurring and routine_sale:
        return QualityDecision(
            QualityAction.DROP,
            "civic.recurring-service",
            "recurring community service is not a destination event",
            (recurring.group(0), "verkauf"),
        )

    compatibility_event = dict(event)
    if event_status(title, description) == "postponed":
        compatibility_event["status"] = "postponed"
    if legacy := _legacy_junk_decision(compatibility_event):
        rule_id, reason, matched_terms = legacy
        return QualityDecision(QualityAction.DROP, rule_id, reason, matched_terms)
    return QualityDecision(QualityAction.KEEP, "quality.accepted",
                           "event passed all editorial quality rules")
