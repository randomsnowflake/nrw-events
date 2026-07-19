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


def evaluate_event_quality(event: Mapping[str, Any]) -> QualityDecision:
    """Evaluate the ordered compatibility policy and explain its outcome.

    The compatibility policy is imported from the implementation module rather
    than through ``common``, keeping the public facade out of the dependency
    graph and making this module independently importable.
    """
    title = str(event.get("title") or "").lower()
    description = str(event.get("description") or "").lower()
    text = f"{title} {description}"

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
