"""Per-source health data used by the import runner and its metadata export."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import json
import re
from typing import Any, Mapping, Optional

from .models import RawEvent, normalize_source_id


_NO_REJECTION_SAMPLE = object()
_DIAGNOSTIC_CONTROLS = re.compile(r"[\x00-\x1f\x7f-\x9f]+")
_REJECTION_FIELD_LIMITS = {
    "title": 200,
    "date": 32,
    "start_date": 32,
    "end_date": 32,
    "record_type": 100,
}
_WARNING_FIELD_LIMITS = {
    "source": 100,
    "source_id": 100,
    "error_type": 100,
    "error": 512,
}
MAX_REJECTION_SAMPLE_JSON_LENGTH = 1024


def bounded_diagnostic_text(value: Any, max_bytes: int) -> str:
    """Render untrusted diagnostic text as safe, deterministically byte-capped UTF-8."""
    try:
        rendered = str(value)
    except (TypeError, ValueError):
        rendered = f"<{type(value).__name__}>"
    # JSON can legally decode an escaped lone surrogate into a Python string,
    # but UTF-8 cannot encode it. Replace malformed code points before every
    # persisted diagnostic is sized.
    rendered = rendered.encode("utf-8", errors="replace").decode("utf-8")
    rendered = _DIAGNOSTIC_CONTROLS.sub(" ", rendered)
    rendered = " ".join(rendered.split())
    encoded = rendered.encode("utf-8")
    if len(encoded) <= max_bytes:
        return rendered
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


def sanitized_warning(warning: Mapping[str, Any]) -> dict[str, Any]:
    """Bound untrusted warning text while preserving typed monitoring fields."""
    sanitized = dict(warning)
    for field_name, limit in _WARNING_FIELD_LIMITS.items():
        if field_name in sanitized:
            sanitized[field_name] = bounded_diagnostic_text(sanitized[field_name], limit)
    return sanitized


def diagnostic_warning(
    source: Any,
    error_type: Any,
    message: Any,
    *,
    source_id: Any = "",
) -> dict[str, Any]:
    """Build a warning through the single persisted diagnostic boundary."""
    warning = {"source": source, "error_type": error_type, "error": message}
    if source_id:
        warning["source_id"] = source_id
    return sanitized_warning(warning)


def _fit_rejection_sample(sample: dict[str, Any]) -> dict[str, Any]:
    """Keep the complete serialized sample within its persisted size budget."""
    mutable_fields = ("title", "date", "start_date", "end_date", "record_type")
    while len(json.dumps(sample, ensure_ascii=False, separators=(",", ":")).encode()) > MAX_REJECTION_SAMPLE_JSON_LENGTH:
        field = max(mutable_fields, key=lambda key: len(str(sample.get(key, ""))))
        value = str(sample.get(field, ""))
        if not value:
            break
        sample[field] = value[:-1]
    return sample


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
    # Target events the AI pass never reached. Restricted sources publish no
    # source prose, so each one of these ships with an empty description.
    ai_skipped_event_count: int = 0
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
    source_id: str = ""

    def __post_init__(self) -> None:
        self.source = bounded_diagnostic_text(self.source, 100) or "unknown-source"
        self.source_id = bounded_diagnostic_text(
            normalize_source_id(self.source_id or self.source), 100
        ) or "unknown-source"

    def warning(self, source: str, error_type: str, message: str, *, source_id: str = "") -> bool:
        warning = diagnostic_warning(
            source, error_type, message, source_id=source_id
        )
        if warning in self.warnings:
            return False
        self.warnings.append(warning)
        return True

    def reject(
        self,
        reason: str,
        event: Any = _NO_REJECTION_SAMPLE,
        *,
        in_window: Optional[bool] = None,
    ) -> None:
        reason = bounded_diagnostic_text(reason, 160) or "unknown_rejection"
        self.rejected_event_count += 1
        self.rejection_reasons[reason] = self.rejection_reasons.get(reason, 0) + 1
        if reason in self.rejection_samples:
            return
        if event is _NO_REJECTION_SAMPLE:
            return
        sample: dict[str, Any] = {
            "source": self.source,
            "source_id": self.source_id,
        }
        if isinstance(event, dict):
            for key in ("title", "date", "start_date", "end_date"):
                value = event.get(key)
                if isinstance(value, (str, int, float, bool)) and value != "":
                    sample[key] = bounded_diagnostic_text(value, _REJECTION_FIELD_LIMITS[key])
        else:
            sample["record_type"] = bounded_diagnostic_text(
                type(event).__name__, _REJECTION_FIELD_LIMITS["record_type"]
            )
        if in_window is not None:
            sample["in_window"] = in_window
        self.rejection_samples[reason] = _fit_rejection_sample(sample)

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
            "ai_skipped_event_count": self.ai_skipped_event_count,
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
