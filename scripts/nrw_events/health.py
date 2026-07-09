"""Per-source health data used by the import runner and its metadata export."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class SourceStatus(str, Enum):
    HEALTHY = "healthy"
    HEALTHY_EMPTY = "healthy_empty"
    DISABLED = "disabled"
    DEGRADED = "degraded"
    FAILED = "failed"
    PARSER_EMPTY = "parser_empty"


@dataclass
class SourceResult:
    """A source outcome, including failures swallowed by legacy fetchers."""

    source: str
    status: SourceStatus = SourceStatus.HEALTHY_EMPTY
    raw_event_count: int = 0
    accepted_event_count: int = 0
    rejected_event_count: int = 0
    rejection_reasons: dict[str, int] = field(default_factory=dict)
    endpoints: dict[str, dict[str, Any]] = field(default_factory=dict)
    baseline: dict[str, Any] = field(default_factory=dict)
    anomalies: list[str] = field(default_factory=list)
    duration_ms: int = 0
    warnings: list[dict[str, str]] = field(default_factory=list)
    error: Optional[dict[str, str]] = None

    def warning(self, source: str, error_type: str, message: str) -> None:
        self.warnings.append({"source": source, "error_type": error_type, "error": message})

    def reject(self, reason: str) -> None:
        self.rejected_event_count += 1
        self.rejection_reasons[reason] = self.rejection_reasons.get(reason, 0) + 1

    def endpoint(self, url: str, **details: Any) -> None:
        current = self.endpoints.setdefault(url, {"attempts": 0})
        current.update(details)
        if "status" in details or "error" in details:
            current["attempts"] += 1

    def finish(self, events: list[Any]) -> None:
        self.raw_event_count = len(events)
        self.accepted_event_count = len(events)
        if self.status == SourceStatus.DISABLED:
            return
        if self.error:
            self.status = SourceStatus.FAILED
        elif self.warnings or any("error_type" in endpoint for endpoint in self.endpoints.values()):
            self.status = SourceStatus.DEGRADED
        elif events:
            self.status = SourceStatus.HEALTHY
        else:
            self.status = SourceStatus.HEALTHY_EMPTY

    def as_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "status": self.status.value,
            "raw_event_count": self.raw_event_count,
            "accepted_event_count": self.accepted_event_count,
            "rejected_event_count": self.rejected_event_count,
            "rejection_reasons": self.rejection_reasons,
            "endpoints": self.endpoints,
            "baseline": self.baseline,
            "anomalies": self.anomalies,
            "duration_ms": self.duration_ms,
            "warnings": self.warnings,
            "error": self.error,
        }
