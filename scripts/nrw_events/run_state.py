"""Owning implementation of run state; core is a compatibility facade."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import Token
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from . import category_taxonomy, config, performance
from .health import SourceResult, SourceStatus
from .observability import LOGGER_NAME, log, redact
from .runtime import ACTIVE_RUNTIME as _RUNTIME_STATE
from .runtime import EventWindow, RunContext
from .runtime import RuntimeState as _RuntimeState

DAYS_AHEAD = 3


LOCAL_TIMEZONE = ZoneInfo("Europe/Berlin")


TODAY = datetime.now(LOCAL_TIMEZONE).replace(
    hour=0, minute=0, second=0, microsecond=0, tzinfo=None
)


END_DATE = TODAY + timedelta(days=max(DAYS_AHEAD - 1, 0))




BONN_LAT, BONN_LON = config.BONN_LAT, config.BONN_LON


MAX_RADIUS_KM = config.MAX_RADIUS_KM


DESCRIPTION_MAX_CHARS = 700


_SOURCE_CONTEXT = threading.local()


_RUN_ID = ""


_LOGGER = logging.getLogger(LOGGER_NAME)


def _runtime_state() -> _RuntimeState:
    state = _RUNTIME_STATE.get()
    if state is not None:
        return state
    # Compatibility defaults for direct source/parser calls that do not create
    # a runner context. Legacy tests may still narrow these constants locally.
    settings = config.RuntimeConfig(
        radius_km=MAX_RADIUS_KM,
        description_max_chars=DESCRIPTION_MAX_CHARS,
        http_retry_attempts=_HTTP_RETRY_ATTEMPTS,
        http_retry_base_seconds=_HTTP_RETRY_BASE_SECONDS,
        http_request_budget_seconds=_HTTP_REQUEST_BUDGET_SECONDS,
        http_retry_max_delay_seconds=_HTTP_RETRY_MAX_DELAY_SECONDS,
        http_max_response_bytes=_HTTP_MAX_RESPONSE_BYTES,
        bonn_de_delay_seconds=_HOST_THROTTLE_SECONDS_BY_SUFFIX.get("bonn.de", 0.5),
    )
    return _RuntimeState(settings, _RUN_ID, _LOGGER)


def runtime_radius_km() -> float:
    """Return the radius belonging to the current import context."""
    return _runtime_state().settings.radius_km


_HTTP_RETRY_ATTEMPTS = 5


_HTTP_RETRY_BASE_SECONDS = 1.0


_HTTP_REQUEST_BUDGET_SECONDS = 45.0


_HTTP_RETRY_MAX_DELAY_SECONDS = 60.0


_HTTP_MAX_RESPONSE_BYTES = 10_000_000


_HOST_THROTTLE_SECONDS_BY_SUFFIX = {
    # Bonn.de's MyraCDN/backend intermittently returns 503 when official Bonn
    # sources fan out without a shared limit. Serialize them and space starts at
    # two requests per second; retries still back off on transient responses.
    "bonn.de": 0.5,
}


def configure_runtime(
    settings: config.RuntimeConfig, run_id: str, logger: logging.Logger,
) -> Token[_RuntimeState | None]:
    """Apply validated settings after the optional env file has been loaded."""
    cache = category_taxonomy.load_fallback_cache(settings.category_fallback_cache)
    return _RUNTIME_STATE.set(_RuntimeState(settings, run_id, logger, category_cache=cache))


def reset_runtime(token: Token[_RuntimeState | None]) -> None:
    """Restore the caller's runtime context after an isolated import."""
    _RUNTIME_STATE.reset(token)


def configure_context(context: RunContext) -> Token[_RuntimeState | None]:
    """Compatibility composition hook while source adapters migrate to context."""
    cache = category_taxonomy.load_fallback_cache(context.settings.category_fallback_cache)
    return _RUNTIME_STATE.set(_RuntimeState(context.settings, context.run_id, context.logger, context.window, cache))


def runtime_window() -> EventWindow:
    """Return this worker's immutable window; retain direct-parser defaults."""
    state = _RUNTIME_STATE.get()
    return state.window if state is not None and state.window is not None else EventWindow(TODAY, END_DATE)


def runtime_days_ahead() -> int:
    state = _RUNTIME_STATE.get()
    return state.settings.days_ahead if state is not None else DAYS_AHEAD


def set_source_context(
    result: SourceResult | None,
    timeout_seconds: float | None = None,
    cancel_event: threading.Event | None = None,
) -> None:
    """Attach warnings emitted by a legacy fetcher to its runner-owned result."""
    _SOURCE_CONTEXT.result = result
    if result is not None and timeout_seconds is not None:
        _SOURCE_CONTEXT.timeout_seconds = timeout_seconds
        _SOURCE_CONTEXT.hard_deadline = time.perf_counter() + timeout_seconds
        _SOURCE_CONTEXT.deadline = _SOURCE_CONTEXT.hard_deadline
        _SOURCE_CONTEXT.cancel_event = cancel_event
    else:
        for attribute_name in ("timeout_seconds", "deadline", "hard_deadline", "cancel_event"):
            if hasattr(_SOURCE_CONTEXT, attribute_name):
                delattr(_SOURCE_CONTEXT, attribute_name)


@contextmanager
def capture_parser_metrics() -> Iterator[dict[str, int]]:
    """Capture candidates rejected by the report window in the current thread."""
    previous = getattr(_SOURCE_CONTEXT, "parser_metrics", None)
    metrics = {"candidate_count": 0, "out_of_window_count": 0}
    _SOURCE_CONTEXT.parser_metrics = metrics
    try:
        yield metrics
    finally:
        if previous is not None:
            previous["candidate_count"] += metrics["candidate_count"]
            previous["out_of_window_count"] += metrics["out_of_window_count"]
        if previous is None:
            delattr(_SOURCE_CONTEXT, "parser_metrics")
        else:
            _SOURCE_CONTEXT.parser_metrics = previous


def _record_parser_candidate(*, out_of_window: bool = False) -> None:
    performance.count("parser_candidates")
    if out_of_window:
        performance.count("parser_out_of_window")
    metrics = getattr(_SOURCE_CONTEXT, "parser_metrics", None)
    if metrics is None:
        return
    metrics["candidate_count"] += 1
    if out_of_window:
        metrics["out_of_window_count"] += 1


def log_source_disabled(source: str, reason: str) -> None:
    """Mark an optional source as intentionally disabled for this run."""
    result = getattr(_SOURCE_CONTEXT, "result", None)
    if result is not None:
        result.status = SourceStatus.DISABLED
    runtime = _runtime_state()
    log(runtime.logger, logging.INFO, reason, run_id=runtime.run_id, source=source)


def log_source_error(source: str, err: Exception, *, source_id: str = "") -> None:
    """Record/log a source failure without aborting legacy fetchers."""
    message = redact(err)
    warning = {"source": source, "error_type": type(err).__name__, "error": message}
    if source_id:
        warning["source_id"] = source_id
    result = getattr(_SOURCE_CONTEXT, "result", None)
    should_log = True
    if result is not None:
        should_log = result.warning(
            source, type(err).__name__, message, source_id=source_id
        )
    if not should_log:
        return
    runtime = _runtime_state()
    log(runtime.logger, logging.WARNING, message, run_id=runtime.run_id,
        source=source, error_type=type(err).__name__)


def log_source_quality_skip(source: str, reason: str) -> None:
    """Record an expected bad source record without degrading source health."""
    result = getattr(_SOURCE_CONTEXT, "result", None)
    if result is not None:
        result.reject(f"quality:{reason}")
