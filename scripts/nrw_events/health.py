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
    duration_ms: int = 0
    warnings: list[dict[str, str]] = field(default_factory=list)
    error: Optional[dict[str, str]] = None

    def warning(self, source: str, error_type: str, message: str) -> None:
        self.warnings.append({"source": source, "error_type": error_type, "error": message})

    def finish(self, events: list[Any]) -> None:
        self.raw_event_count = len(events)
        self.accepted_event_count = len(events)
        if self.status == SourceStatus.DISABLED:
            return
        if self.error:
            self.status = SourceStatus.FAILED
        elif self.warnings:
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
            "duration_ms": self.duration_ms,
            "warnings": self.warnings,
            "error": self.error,
        }
