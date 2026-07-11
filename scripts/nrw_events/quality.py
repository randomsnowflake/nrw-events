"""Explainable editorial quality decisions."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping


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


def evaluate_event_quality(event: Mapping[str, Any]) -> QualityDecision:
    """Evaluate the ordered compatibility policy and explain its outcome.

    Importing lazily avoids coupling the domain type to the legacy facade while
    its individual policy families move into this module incrementally.
    """
    from .common import _legacy_is_junk_event

    if _legacy_is_junk_event(event):
        return QualityDecision(QualityAction.DROP, "legacy.editorial-policy",
                               "event matched the established editorial exclusion policy")
    return QualityDecision(QualityAction.KEEP, "quality.accepted",
                           "event passed all editorial quality rules")

