"""Per-source health data used by the import runner and its metadata export."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from .models import RawEvent


class SourceStatus(str, Enum):
    HEALTHY = "healthy"
    HEALTHY_EMPTY = "healthy_empty"
    SCHEDULED_SKIP = "scheduled_skip"
    DISABLED = "disabled"
    DEGRADED = "degraded"
    FAILED = "failed"
    PARSER_EMPTY = "parser_empty"


@dataclass(frozen=True, slots=True)
class EndpointOutcome:
    url: str
    status: Optional[int] = None
    error_type: str = ""
    error: str = ""


@dataclass(frozen=True, slots=True)
class SourceFetchResult:
    """Typed adapter output, including partial and intentionally empty states."""

    events: tuple[RawEvent, ...] = ()
    status: SourceStatus = SourceStatus.HEALTHY_EMPTY
    disabled_reason: str = ""
    warnings: tuple[str, ...] = ()
    endpoints: tuple[EndpointOutcome, ...] = ()

    @classmethod
    def success(cls, events: list[RawEvent]) -> "SourceFetchResult":
        return cls(tuple(events), SourceStatus.HEALTHY if events else SourceStatus.HEALTHY_EMPTY)

    @classmethod
    def partial(cls, events: list[RawEvent], *warnings: str,
                endpoints: tuple[EndpointOutcome, ...] = ()) -> "SourceFetchResult":
        return cls(tuple(events), SourceStatus.DEGRADED, warnings=warnings, endpoints=endpoints)

    @classmethod
    def disabled(cls, reason: str) -> "SourceFetchResult":
        return cls(status=SourceStatus.DISABLED, disabled_reason=reason)

    @classmethod
    def scheduled_skip(cls, reason: str) -> "SourceFetchResult":
        return cls(status=SourceStatus.SCHEDULED_SKIP, disabled_reason=reason)

    @classmethod
    def parser_empty(cls, warning: str = "parser returned no records") -> "SourceFetchResult":
        return cls(status=SourceStatus.PARSER_EMPTY, warnings=(warning,))


@dataclass
class SourceResult:
    """A source outcome, including failures swallowed by legacy fetchers."""

    source: str
    status: SourceStatus = SourceStatus.HEALTHY_EMPTY
    raw_event_count: int = 0
    accepted_event_count: int = 0
    rejected_event_count: int = 0
    rejection_reasons: dict[str, int] = field(default_factory=dict)
    rejection_samples: dict[str, dict[str, Any]] = field(default_factory=dict)
    endpoints: dict[str, dict[str, Any]] = field(default_factory=dict)
    baseline: dict[str, Any] = field(default_factory=dict)
    anomalies: list[str] = field(default_factory=list)
    duration_ms: int = 0
    ai_duration_ms: int = 0
    ai_candidate_event_count: int = 0
    event_sources: list[str] = field(default_factory=list)
    event_source_ids: list[str] = field(default_factory=list)
    cancelled_events: list[dict[str, Any]] = field(default_factory=list)
    announced_events: list[dict[str, Any]] = field(default_factory=list)
    research_leads: list[dict[str, Any]] = field(default_factory=list)
    research_lead_count: int = 0
    research_lead_reasons: dict[str, int] = field(default_factory=dict)
    warnings: list[dict[str, str]] = field(default_factory=list)
    error: Optional[dict[str, str]] = None
    status_reason: str = ""

    def warning(self, source: str, error_type: str, message: str, *, source_id: str = "") -> bool:
        warning = {"source": source, "error_type": error_type, "error": message}
        if source_id:
            warning["source_id"] = source_id
        if warning in self.warnings:
            return False
        self.warnings.append(warning)
        return True

    def reject(
        self,
        reason: str,
        event: Any = None,
        *,
        in_window: Optional[bool] = None,
    ) -> None:
        self.rejected_event_count += 1
        self.rejection_reasons[reason] = self.rejection_reasons.get(reason, 0) + 1
        if reason in self.rejection_samples:
            return
        sample: dict[str, Any] = {"source": self.source}
        if isinstance(event, dict):
            for key in ("title", "source", "source_id", "date", "start_date", "end_date"):
                value = event.get(key)
                if isinstance(value, (str, int, float, bool)) and value != "":
                    sample[key] = value
        else:
            sample["record_type"] = type(event).__name__
        if in_window is not None:
            sample["in_window"] = in_window
        self.rejection_samples[reason] = sample

    def endpoint(self, url: str, **details: Any) -> None:
        current = self.endpoints.setdefault(url, {"attempts": 0})
        if "status" in details and not ({"error", "error_type"} & details.keys()):
            current.pop("error", None)
            current.pop("error_type", None)
        elif ({"error", "error_type"} & details.keys()) and "status" not in details:
            current.pop("status", None)
        current.update(details)
        if "status" in details or "error" in details:
            current["attempts"] += 1

    def finish(self, events: list[Any]) -> None:
        self.raw_event_count = len(events)
        self.accepted_event_count = len(events)
        if self.status in {SourceStatus.DISABLED, SourceStatus.SCHEDULED_SKIP}:
            return
        parser_empty = any(
            endpoint.get("parser_empty") is True
            for endpoint in self.endpoints.values()
        )
        parser_measured = any(
            "parser_empty" in endpoint
            for endpoint in self.endpoints.values()
        )
        if self.error:
            self.status = SourceStatus.FAILED
        elif parser_empty and not events:
            self.status = SourceStatus.PARSER_EMPTY
        elif self.warnings or any("error_type" in endpoint for endpoint in self.endpoints.values()):
            self.status = SourceStatus.DEGRADED
        elif parser_empty:
            # A grouped adapter can return valid records from one child while
            # another child's parser yielded an untrusted empty result.
            self.status = SourceStatus.DEGRADED
        elif events:
            self.status = SourceStatus.HEALTHY
        elif not parser_measured:
            # A successful transport with no parser measurement cannot prove
            # that an empty result is authoritative. Treat legacy direct-fetch
            # adapters as parser drift until they opt into a measured facade.
            self.status = SourceStatus.PARSER_EMPTY
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
            "rejection_samples": self.rejection_samples,
            "endpoints": self.endpoints,
            "baseline": self.baseline,
            "anomalies": self.anomalies,
            "duration_ms": self.duration_ms,
            "ai_duration_ms": self.ai_duration_ms,
            "ai_candidate_event_count": self.ai_candidate_event_count,
            "event_sources": self.event_sources,
            "event_source_ids": self.event_source_ids,
            "cancelled_event_count": len(self.cancelled_events),
            "announced_event_count": len(self.announced_events),
            "research_lead_count": self.research_lead_count,
            "research_lead_reasons": self.research_lead_reasons,
            "cancelled_rate": (
                round(len(self.cancelled_events) / self.raw_event_count, 4)
                if self.raw_event_count else 0.0
            ),
            "warnings": self.warnings,
            "error": self.error,
            "status_reason": self.status_reason,
        }
