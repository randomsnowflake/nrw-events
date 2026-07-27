"""Explainable editorial quality decisions."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable, Mapping

from .core import _legacy_is_junk_event


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
OPTIONAL_CONTENT_FIELDS = ("time", "venue", "description", "price")


def summarize_event_quality(events: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Return stable completeness and classification metrics for monitoring."""
    rows = list(events)

    def present(event: Mapping[str, Any], field: str) -> bool:
        value = event.get(field)
        return value is not None and (not isinstance(value, str) or bool(value.strip()))

    category_counts: dict[str, int] = {}
    for event in rows:
        key = str(event.get("category_key") or "other")
        category_counts[key] = category_counts.get(key, 0) + 1
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
        "category_counts": dict(sorted(category_counts.items())),
        "uncategorized_count": category_counts.get("other", 0),
    }


_WORK_TITLE = re.compile(r"[»«„““”\"']\s*\S")
_ADVERTISING_MARKER = re.compile(
    r"^\s*(anzeige|advertorial|sponsored)\b",
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

    if _legacy_is_junk_event(event):
        return QualityDecision(QualityAction.DROP, "legacy.editorial-policy",
                               "event matched the established editorial exclusion policy")
    return QualityDecision(QualityAction.KEEP, "quality.accepted",
                           "event passed all editorial quality rules")
