"""Fact extraction stage with bounded retries and durable cache accounting."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from . import ai_cache as _impl_ai_cache
from . import ai_contracts as _impl_ai_contracts
from . import ai_policy as _impl_ai_policy
from . import ai_transport as _impl_ai_transport


@dataclass(frozen=True)
class FactStageResult:
    facts: dict[str, Any] | None
    row: sqlite3.Row


def extract_facts(
    connection: sqlite3.Connection, row: sqlite3.Row, *,
    facts: dict[str, Any] | None, payload: dict[str, Any],
    api: _impl_ai_contracts.StructuredClient,
    configured: _impl_ai_transport.AISettings, current_time: datetime,
    routing: dict[str, Any] | None, configured_timeout_seconds: float | None,
) -> FactStageResult:
    while facts is None and row["stage1_attempts"] < configured.max_attempts:
        usage = _impl_ai_contracts.Usage()
        try:
            extracted_facts, usage = api.structured(
                stage="facts", system=_impl_ai_contracts._EXTRACT_PROMPT, payload=payload,
                schema=_impl_ai_contracts._FACT_SCHEMA, attempt=row["stage1_attempts"] + 1,
            )
            facts = _impl_ai_policy._sanitize_extracted_facts(extracted_facts, payload)
            if routing:
                facts["_jev"] = routing["metadata"]
            row = _impl_ai_cache._record_success(connection, row, stage=1, payload=facts, usage=usage, now=current_time)
        except Exception as exc:
            if isinstance(exc, _impl_ai_contracts.AIEnrichmentError) and isinstance(exc.usage, _impl_ai_contracts.Usage):
                usage = exc.usage
            safe_error = (
                exc
                if isinstance(exc, _impl_ai_contracts.AIEnrichmentError)
                else _impl_ai_contracts.AIEnrichmentError(type(exc).__name__, transient=True)
            )
            terminal = row["stage1_attempts"] + 1 >= configured.max_attempts
            if (
                terminal
                and configured_timeout_seconds is not None
                and configured.timeout_seconds < configured_timeout_seconds
                and (
                    isinstance(exc, TimeoutError)
                    or "TimeoutError" in str(safe_error)
                    or "wall-clock deadline" in str(safe_error)
                )
            ):
                terminal = False
            row = _impl_ai_cache._record_failure(
                connection, row, stage=1, error=safe_error, usage=usage,
                settings=configured, now=current_time, terminal=terminal,
            )
            if row["stage1_attempts"] < configured.max_attempts:
                _impl_ai_transport._sleep_before_ai_retry(safe_error, row["stage1_attempts"] - 1, configured)
    return FactStageResult(facts, row)
