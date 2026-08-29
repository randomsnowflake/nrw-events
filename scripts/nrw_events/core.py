"""
Implementation layer for the NRW event aggregator.

This module holds every piece of generic machinery that the per-source fetchers
reuse: HTTP, HTML/JSON-LD/iCal parsing, German/English date parsing, geo +
distance scoring, the central ``make_event`` builder, and the junk filter.

Source files in ``sources/`` should contain *only* the logic specific to one
website. Anything reusable belongs here.

The public compatibility facade is :mod:`nrw_events.common`; new code should
prefer the focused HTTP, text, event-builder, JSON-LD, and iCal modules.
"""

from __future__ import annotations

import json
from hashlib import sha256
import inspect
import logging
import os
import random
import re
import threading
import time
import urllib.request
import urllib.parse  # noqa: F401  (re-exported for sources that build URLs)
import urllib.error
from contextlib import closing, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from html import unescape
from pathlib import Path
from typing import Any, Callable, Iterator, NoReturn, Optional, TypedDict
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import category_taxonomy, config, richtext
from .health import SourceResult, SourceStatus
from .location import canonicalize_city as canonicalize_city
from .location import coords_for_city as coords_for_city
from .location import refine_city_from_text as refine_city_from_text
from .junk_rules import legacy_junk_decision
from .quality import evaluate_event_quality
from .location import refine_bonn_location as refine_bonn_location
from .location import guess_city_from_text, haversine, resolve_location
from .models import AdmissionDefault, EventDraft, RawEvent
from .normalization import resolve_venue
from .observability import LOGGER_NAME, log, redact
from .scoring import category_score, distance_score
from .runtime import RunContext
from .title_normalization import normalize_event_title
from .dates import configure_reference_date as _configure_date_reference
from .dates import MONTH_DE as MONTH_DE
from .dates import MONTH_EN as MONTH_EN
from .dates import parse_date
from .dates import parse_iso_date

# ── Report window (set by the runner at startup) ────────────────────
DAYS_AHEAD = 3
LOCAL_TIMEZONE = ZoneInfo("Europe/Berlin")
TODAY = datetime.now(LOCAL_TIMEZONE).replace(
    hour=0, minute=0, second=0, microsecond=0, tzinfo=None
)
END_DATE = TODAY + timedelta(days=max(DAYS_AHEAD - 1, 0))
_configure_date_reference(TODAY)


# Re-export common config values for convenience.
BONN_LAT, BONN_LON = config.BONN_LAT, config.BONN_LON
MAX_RADIUS_KM = config.MAX_RADIUS_KM
DESCRIPTION_MAX_CHARS = 700

# Per-run source telemetry. Source modules intentionally keep the overall import
# alive when one remote page breaks; this records those partial failures so the
# caller can alert on a degraded but otherwise successful run.
_SOURCE_CONTEXT = threading.local()
_RUN_ID = ""
_LOGGER = logging.getLogger(LOGGER_NAME)


@dataclass(frozen=True, slots=True)
class _RuntimeState:
    settings: config.RuntimeConfig
    run_id: str
    logger: logging.Logger


_RUNTIME_STATE: ContextVar[_RuntimeState | None] = ContextVar("nrw_events_runtime", default=None)


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


# ── HTTP ────────────────────────────────────────────────────────────

_BROWSER_PROFILES = [
    {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
        "Sec-CH-UA": (
            '"Google Chrome";v="131", "Chromium";v="131", "Not.A/Brand";v="24"'
        ),
        "Sec-CH-UA-Platform": '"Windows"',
    },
    {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
        "Sec-CH-UA": (
            '"Google Chrome";v="131", "Chromium";v="131", "Not.A/Brand";v="24"'
        ),
        "Sec-CH-UA-Platform": '"macOS"',
    },
    {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
        "Sec-CH-UA": (
            '"Google Chrome";v="131", "Chromium";v="131", "Not.A/Brand";v="24"'
        ),
        "Sec-CH-UA-Platform": '"Linux"',
    },
]
_BROWSER_PROFILE = random.SystemRandom().choice(_BROWSER_PROFILES)
_TRANSIENT_HTTP_STATUSES = {408, 425, 429, 500, 502, 503, 504}
_HTTP_RETRY_ATTEMPTS = 5
_HTTP_RETRY_BASE_SECONDS = 1.0
_HTTP_REQUEST_BUDGET_SECONDS = 45.0
_HTTP_RETRY_MAX_DELAY_SECONDS = 60.0
_HTTP_MAX_RESPONSE_BYTES = 10_000_000
_BRIGHT_DATA_API_URL = "https://api.brightdata.com/request"
_HOST_THROTTLE_SECONDS_BY_SUFFIX = {
    # Bonn.de's MyraCDN/backend intermittently returns 503 when official Bonn
    # sources fan out without a shared limit. Serialize them and space starts at
    # two requests per second; retries still back off on transient responses.
    "bonn.de": 0.5,
}
_HOST_FETCH_LOCK = threading.Lock()
_HOST_LAST_FETCH_AT: dict[str, float] = {}
_HOST_SLOT_LOCK = threading.Lock()
_HOST_SLOTS: dict[str, threading.Lock] = {}


def configure_runtime(settings: config.RuntimeConfig, run_id: str, logger: logging.Logger):
    """Apply validated settings after the optional env file has been loaded."""
    category_taxonomy.configure_fallback_cache(settings.category_fallback_cache)
    return _RUNTIME_STATE.set(_RuntimeState(settings, run_id, logger))


def reset_runtime(token) -> None:
    """Restore the caller's runtime context after an isolated import."""
    _RUNTIME_STATE.reset(token)


def configure_context(context: RunContext):
    """Compatibility composition hook while source adapters migrate to context."""
    token = configure_runtime(context.settings, context.run_id, context.logger)
    global DAYS_AHEAD, TODAY, END_DATE
    DAYS_AHEAD = context.settings.days_ahead
    TODAY = context.window.start
    END_DATE = context.window.end
    _configure_date_reference(TODAY)
    return token


def set_source_context(
    result: Optional[SourceResult],
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
        if previous is None:
            delattr(_SOURCE_CONTEXT, "parser_metrics")
        else:
            _SOURCE_CONTEXT.parser_metrics = previous


def _record_parser_candidate(*, out_of_window: bool = False) -> None:
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


class ResponseTooLargeError(ValueError):
    pass


_HTTP_READ_CHUNK_BYTES = 64 * 1024


def _set_response_socket_timeout(response: Any, timeout: float) -> None:
    """Best-effort update of urllib's underlying socket timeout."""
    for chain in (
        ("fp", "raw", "_sock"),
        ("fp", "fp", "raw", "_sock"),
        ("fp", "_sock"),
        ("_sock",),
    ):
        candidate = response
        for attribute in chain:
            candidate = getattr(candidate, attribute, None)
            if candidate is None:
                break
        setter = getattr(candidate, "settimeout", None)
        if callable(setter):
            setter(max(timeout, 0.001))
            return


def _response_read_timeout(deadline: float) -> float:
    cancel_event = getattr(_SOURCE_CONTEXT, "cancel_event", None)
    if cancel_event is not None and cancel_event.is_set():
        raise TimeoutError("source wall-clock budget exhausted while reading response body")
    remaining = deadline - time.perf_counter()
    if remaining <= 0:
        raise TimeoutError("request or source time budget exhausted while reading response body")
    return remaining


def _read_response_body(response: Any, max_bytes: int, *, deadline: float) -> bytes:
    """Read a response incrementally while enforcing size and wall-clock limits."""
    read1 = getattr(response, "read1", None)
    if not (inspect.ismethod(read1) or inspect.isbuiltin(read1)):
        # Non-HTTP test/file objects do not necessarily expose ``read1``. Keep
        # their one-shot semantics; real urllib HTTP responses use the bounded
        # incremental path below.
        _set_response_socket_timeout(response, _response_read_timeout(deadline))
        body = response.read() if max_bytes <= 0 else response.read(max_bytes + 1)
        if len(body) > max_bytes > 0:
            raise ResponseTooLargeError(f"response exceeds {max_bytes} bytes")
        _response_read_timeout(deadline)
        return body

    body = bytearray()
    while True:
        remaining_timeout = _response_read_timeout(deadline)
        _set_response_socket_timeout(response, remaining_timeout)
        read_size = _HTTP_READ_CHUNK_BYTES
        if max_bytes > 0:
            read_size = min(read_size, max_bytes + 1 - len(body))
        chunk = read1(read_size)
        if not chunk:
            break
        if not isinstance(chunk, (bytes, bytearray)):
            raise TypeError("HTTP response body reader returned non-bytes data")
        body.extend(chunk)
        if len(body) > max_bytes > 0:
            raise ResponseTooLargeError(f"response exceeds {max_bytes} bytes")
    _response_read_timeout(deadline)
    return bytes(body)


class UnexpectedContentTypeError(ValueError):
    pass


def _record_endpoint(url: str, **details: Any) -> None:
    result = getattr(_SOURCE_CONTEXT, "result", None)
    if result is not None:
        result.endpoint(redact(url), **details)
    status = details.get("status")
    timeout_seconds = getattr(_SOURCE_CONTEXT, "timeout_seconds", None)
    if isinstance(status, int) and 200 <= status < 400 and timeout_seconds is not None:
        hard_deadline = getattr(_SOURCE_CONTEXT, "hard_deadline", None)
        renewed_deadline = time.perf_counter() + timeout_seconds
        _SOURCE_CONTEXT.deadline = min(renewed_deadline, hard_deadline) if hard_deadline else renewed_deadline


def _throttle_bucket(url: str) -> tuple[str, float] | tuple[None, float]:
    hostname = (urllib.parse.urlsplit(url).hostname or "").lower()
    state = _RUNTIME_STATE.get()
    delays = (
        {"bonn.de": state.settings.bonn_de_delay_seconds}
        if state is not None else _HOST_THROTTLE_SECONDS_BY_SUFFIX
    )
    for suffix, delay in delays.items():
        if hostname == suffix or hostname.endswith(f".{suffix}"):
            return suffix, delay
    return None, 0.0


def _throttle_before_request(url: str) -> None:
    bucket, delay = _throttle_bucket(url)
    if not bucket or delay <= 0:
        return
    with _HOST_FETCH_LOCK:
        now = time.monotonic()
        wait = max(0.0, _HOST_LAST_FETCH_AT.get(bucket, 0.0) + delay - now)
        scheduled_at = now + wait
        _HOST_LAST_FETCH_AT[bucket] = scheduled_at
    if wait > 0:
        time.sleep(wait)


def _request_deadline() -> float:
    cancel_event = getattr(_SOURCE_CONTEXT, "cancel_event", None)
    if cancel_event is not None and cancel_event.is_set():
        raise TimeoutError("source wall-clock budget exhausted")
    deadline = time.perf_counter() + _runtime_state().settings.http_request_budget_seconds
    source_deadline = getattr(_SOURCE_CONTEXT, "deadline", None)
    hard_deadline = getattr(_SOURCE_CONTEXT, "hard_deadline", None)
    candidates = [value for value in (deadline, source_deadline, hard_deadline) if value is not None]
    return min(candidates)


def _remaining_timeout(deadline: float, requested_timeout: float) -> float:
    remaining = deadline - time.perf_counter()
    if remaining <= 0:
        raise TimeoutError("request or source time budget exhausted")
    return max(min(float(requested_timeout), remaining), 0.1)


@contextmanager
def _host_request_slot(url: str, deadline: float) -> Iterator[None]:
    """Serialize one host without preventing requests to other hosts."""
    hostname = (urllib.parse.urlsplit(url).hostname or "<missing>").lower()
    throttle_bucket, _ = _throttle_bucket(url)
    slot_key = throttle_bucket or hostname
    with _HOST_SLOT_LOCK:
        slot = _HOST_SLOTS.setdefault(slot_key, threading.Lock())
    wait = _remaining_timeout(deadline, deadline - time.perf_counter())
    if not slot.acquire(timeout=wait):
        raise TimeoutError(f"timed out waiting for request slot on {hostname}")
    try:
        _throttle_before_request(url)
        _remaining_timeout(deadline, deadline - time.perf_counter())
        yield
    finally:
        slot.release()


def _sleep_for_retry(delay: float, deadline: float) -> None:
    remaining = deadline - time.perf_counter()
    if delay >= remaining:
        raise TimeoutError("request or source time budget exhausted before retry")
    time.sleep(delay)


def _retry_delay(exc: Exception, attempt_index: int) -> float:
    if isinstance(exc, urllib.error.HTTPError):
        retry_after = exc.headers.get("Retry-After") if exc.headers else None
        if retry_after:
            try:
                return max(float(retry_after), 0.0)
            except ValueError:
                pass
    settings = _runtime_state().settings
    base = settings.http_retry_base_seconds
    jitter = random.SystemRandom().uniform(0, base / 2) if base else 0.0
    return min(base * (2 ** attempt_index) + jitter, settings.http_retry_max_delay_seconds)


def _is_retryable_fetch_error(exc: Exception) -> bool:
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code in _TRANSIENT_HTTP_STATUSES
    return isinstance(exc, (urllib.error.URLError, TimeoutError, ConnectionError))


def _close_http_error(exc: Exception) -> None:
    """Close urllib's response-shaped HTTPError on both retry and raise paths."""
    if isinstance(exc, urllib.error.HTTPError):
        exc.close()


def browser_headers(
    *,
    accept: str,
    sec_fetch_mode: str,
    sec_fetch_dest: str,
    extra: Optional[dict] = None,
) -> dict:
    """Return realistic browser request headers for public event-source fetches.

    The default urllib user agent advertises Python and is easy for sites to
    reject. Use one coherent browser profile for the whole process instead of
    changing identity on every request; callers can still override individual
    headers when a feed/API needs a source-specific ``Accept`` or auth header.
    """
    hdrs = {
        **_BROWSER_PROFILE,
        "User-Agent": os.environ.get("NRW_EVENTS_USER_AGENT", _BROWSER_PROFILE["User-Agent"]),
        "Accept": accept,
        "Accept-Language": "de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Sec-CH-UA-Mobile": "?0",
        "Sec-Fetch-Dest": sec_fetch_dest,
        "Sec-Fetch-Mode": sec_fetch_mode,
        "Sec-Fetch-Site": "none",
    }
    if sec_fetch_mode == "navigate":
        hdrs["Upgrade-Insecure-Requests"] = "1"
        hdrs["Sec-Fetch-User"] = "?1"
    if extra:
        hdrs.update(extra)
    return hdrs


def fetch_url(
    url: str,
    timeout: int = 15,
    headers: Optional[dict] = None,
    accept: str = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    sec_fetch_mode: str = "navigate",
    sec_fetch_dest: str = "document",
    expected_content_types: Optional[tuple] = None,
    retry_attempts: Optional[int] = None,
    accepted_http_statuses: tuple[int, ...] = (),
) -> str:
    """GET a URL and return decoded text. Raises on network/HTTP error.

    Defaults model a browser document navigation for HTML event pages. Feed/API
    callers should pass a content-specific ``accept`` value so negotiating
    endpoints do not return their human HTML fallback instead of data. Optional
    best-effort requests may lower ``retry_attempts`` so one broken detail page
    cannot consume a source's complete enrichment budget.
    """
    hdrs = browser_headers(
        accept=accept,
        sec_fetch_mode=sec_fetch_mode,
        sec_fetch_dest=sec_fetch_dest,
        extra=headers,
    )
    deadline = _request_deadline()
    settings = _runtime_state().settings
    attempts = (
        settings.http_retry_attempts
        if retry_attempts is None
        else max(int(retry_attempts), 1)
    )
    for attempt in range(attempts):
        try:
            started = time.perf_counter()
            req = urllib.request.Request(url, headers=hdrs)
            with _host_request_slot(url, deadline):
                with closing(urllib.request.urlopen(req, timeout=_remaining_timeout(deadline, timeout))) as resp:
                    headers_obj = getattr(resp, "headers", None)
                    content_type = (
                        headers_obj.get_content_type()
                        if headers_obj is not None and hasattr(headers_obj, "get_content_type")
                        else ""
                    )
                    if not isinstance(content_type, str):
                        content_type = ""
                    if expected_content_types and content_type and not any(content_type.startswith(item) for item in expected_content_types):
                        raise UnexpectedContentTypeError(f"expected {expected_content_types}, got {content_type}")
                    body = _read_response_body(
                        resp, settings.http_max_response_bytes, deadline=deadline,
                    )
                    charset = (
                        headers_obj.get_content_charset()
                        if headers_obj is not None and hasattr(headers_obj, "get_content_charset")
                        else None
                    )
                    if not isinstance(charset, str):
                        charset = None
                    _record_endpoint(url, status=getattr(resp, "status", 200), content_type=content_type,
                                     bytes=len(body), duration_ms=round((time.perf_counter() - started) * 1000))
            try:
                return body.decode(charset or "utf-8")
            except UnicodeDecodeError:
                # A few long-running regional calendars advertise UTF-8 while
                # still mixing in individual Windows-1252 bytes. Preserve both
                # valid UTF-8 and those legacy characters instead of corrupting
                # the complete page or dropping its source.
                decoded = body.decode("utf-8", errors="surrogateescape")
                return "".join(
                    bytes((ord(char) - 0xDC00,)).decode("cp1252", errors="replace")
                    if 0xDC80 <= ord(char) <= 0xDCFF else char
                    for char in decoded
                )
        except Exception as exc:
            if isinstance(exc, urllib.error.HTTPError) and exc.code in accepted_http_statuses:
                headers_obj = exc.headers
                content_type = (
                    headers_obj.get_content_type()
                    if headers_obj is not None and hasattr(headers_obj, "get_content_type")
                    else ""
                )
                if expected_content_types and content_type and not any(
                    content_type.startswith(item) for item in expected_content_types
                ):
                    _close_http_error(exc)
                    raise UnexpectedContentTypeError(
                        f"expected {expected_content_types}, got {content_type}"
                    ) from exc
                try:
                    body = _read_response_body(
                        exc, settings.http_max_response_bytes, deadline=deadline,
                    )
                except Exception:
                    _close_http_error(exc)
                    raise
                charset = (
                    headers_obj.get_content_charset()
                    if headers_obj is not None and hasattr(headers_obj, "get_content_charset")
                    else None
                )
                _record_endpoint(
                    url,
                    status=exc.code,
                    content_type=content_type,
                    bytes=len(body),
                    duration_ms=round((time.perf_counter() - started) * 1000),
                    accepted_error_status=True,
                )
                _close_http_error(exc)
                return body.decode(charset or "utf-8", errors="replace")
            _record_endpoint(url, error_type=type(exc).__name__, error=redact(exc))
            retry = attempt < attempts - 1 and _is_retryable_fetch_error(exc)
            delay = _retry_delay(exc, attempt) if retry else 0
            _close_http_error(exc)
            if not retry:
                raise
            _sleep_for_retry(delay, deadline)
    raise RuntimeError("fetch_url retry loop exhausted unexpectedly")  # pragma: no cover


def fetch_json(url: str, timeout: int = 15, headers: Optional[dict] = None):
    """Fetch and decode a JSON API response with a strict content-type guard."""
    return json.loads(fetch_url(
        url, timeout=timeout, headers=headers,
        accept="application/json,*/*;q=0.8",
        sec_fetch_mode="cors", sec_fetch_dest="empty",
        expected_content_types=("application/json", "text/json"),
    ))


def _raise_brightdata_failure(url: str, started: float, exc: Exception) -> NoReturn:
    _record_endpoint(
        url,
        error_type=type(exc).__name__,
        error=redact(exc),
        duration_ms=round((time.perf_counter() - started) * 1000),
        transport="brightdata",
    )
    raise exc


def fetch_url_with_brightdata(
    url: str,
    timeout: int = 15,
    *,
    allowed_hosts: tuple[str, ...],
    required_body_markers: tuple[str, ...] = (),
    country: str = "DE",
    fresh_request_budget: bool = False,
) -> str:
    """Fetch a public page exclusively through Bright Data Web Unlocker."""
    hostname = (urllib.parse.urlsplit(url).hostname or "").lower()
    if hostname not in {host.lower() for host in allowed_hosts}:
        raise ValueError(f"Bright Data target host is not allowlisted: {hostname or '<missing>'}")

    api_key = os.environ.get("BRIGHT_DATA_API_KEY", "").strip()
    zone = os.environ.get("BRIGHT_DATA_ZONE", "").strip()
    if not api_key or not zone:
        raise RuntimeError("Bright Data credentials are required for this source")

    payload = {
        "zone": zone,
        "url": url,
        "format": "raw",
        "method": "GET",
        "country": country.upper(),
    }
    request = urllib.request.Request(
        _BRIGHT_DATA_API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    started = time.perf_counter()
    # A direct-first fallback may be entered precisely because the ordinary
    # request/source budget expired. Give that one allowlisted proxy attempt a
    # fresh, still-bounded budget instead of failing before it starts.
    deadline = (
        time.perf_counter() + max(timeout, 120)
        if fresh_request_budget
        else _request_deadline()
    )
    settings = _runtime_state().settings
    try:
        with _host_request_slot(_BRIGHT_DATA_API_URL, deadline):
            with closing(urllib.request.urlopen(
                request,
                timeout=_remaining_timeout(deadline, max(timeout, 120)),
            )) as response:
                raw = _read_response_body(
                    response, settings.http_max_response_bytes, deadline=deadline,
                )
                api_status = getattr(response, "status", 200)
    except Exception as exc:
        _raise_brightdata_failure(url, started, exc)

    decoded = raw.decode("utf-8", errors="replace")
    try:
        result = json.loads(decoded)
    except json.JSONDecodeError:
        target_status = api_status
        body = decoded
    else:
        # `format=raw` may return a JSON target body directly. Only treat a
        # decoded object as the Web Unlocker envelope when both envelope fields
        # are present; JSON lists and ordinary JSON objects are target content.
        if isinstance(result, dict) and {"status_code", "body"} <= result.keys():
            target_status = result.get("status_code")
            body = result.get("body", "")
        else:
            target_status = api_status
            body = decoded

    if not isinstance(target_status, (int, str)):
        _raise_brightdata_failure(
            url, started, RuntimeError("Bright Data response omitted the target status"))
    try:
        target_status = int(target_status)
    except (TypeError, ValueError) as exc:
        error = RuntimeError("Bright Data response omitted the target status")
        error.__cause__ = exc
        _raise_brightdata_failure(url, started, error)
    if not 200 <= target_status < 300:
        _raise_brightdata_failure(
            url, started, RuntimeError(f"Bright Data target returned HTTP {target_status}"))
    if not isinstance(body, str) or not body.strip():
        _raise_brightdata_failure(
            url, started, RuntimeError("Bright Data returned an empty target body"))
    missing_markers = [marker for marker in required_body_markers if marker not in body]
    if missing_markers:
        _raise_brightdata_failure(
            url, started, RuntimeError("Bright Data target body failed source validation"))

    _record_endpoint(
        url,
        status=target_status,
        content_type="text/html",
        bytes=len(body.encode("utf-8")),
        duration_ms=round((time.perf_counter() - started) * 1000),
        transport="brightdata",
    )
    return body


def fetch_url_with_brightdata_fallback(
    url: str,
    timeout: int = 15,
    *,
    allowed_hosts: tuple[str, ...],
    required_body_markers: tuple[str, ...] = (),
    fallback_statuses: tuple[int, ...] = (429,),
    fallback_on_timeout: bool = False,
    country: str = "DE",
    **fetch_kwargs: Any,
) -> str:
    """Fetch directly first, then recover selected HTTP failures via Web Unlocker.

    The fallback is deliberately opt-in per source. If credentials are absent,
    the original direct-fetch error is preserved instead of changing behavior.
    """
    fallback_needs_fresh_budget = False
    try:
        return fetch_url(url, timeout=timeout, **fetch_kwargs)
    except (urllib.error.HTTPError, TimeoutError) as direct_error:
        cancel_event = getattr(_SOURCE_CONTEXT, "cancel_event", None)
        if cancel_event is not None and cancel_event.is_set():
            raise
        api_key = os.environ.get("BRIGHT_DATA_API_KEY", "").strip()
        zone = os.environ.get("BRIGHT_DATA_ZONE", "").strip()
        hostname = (urllib.parse.urlsplit(url).hostname or "").lower()
        eligible_host = hostname in {host.lower() for host in allowed_hosts}
        eligible_failure = (
            direct_error.code in fallback_statuses
            if isinstance(direct_error, urllib.error.HTTPError)
            else fallback_on_timeout
        )
        if (not eligible_failure or not eligible_host or not api_key or not zone):
            raise
        fallback_needs_fresh_budget = isinstance(direct_error, TimeoutError)

    return fetch_url_with_brightdata(
        url,
        timeout=timeout,
        allowed_hosts=allowed_hosts,
        required_body_markers=required_body_markers,
        country=country,
        fresh_request_budget=fallback_needs_fresh_budget,
    )


# Detail pages are comparatively expensive because one listing can fan out into
# dozens of requests. Keep their raw HTML in small source-specific files so a
# parser can be improved without coupling the cache format to its parsed fields.
_DETAIL_PAGE_CACHE_VERSION = 1
# The Bonn 28-day calendar alone can exceed 250 unique detail URLs. The byte
# ceiling remains the hard storage bound; this higher count prevents a daily
# alternating miss set when many compact pages fit comfortably below it.
_DETAIL_PAGE_CACHE_DEFAULT_MAX_ENTRIES = 500
_DETAIL_PAGE_CACHE_DEFAULT_MAX_BYTES = 25 * 1024 * 1024
# Bonn's 28-day calendar currently carries roughly 300 detail pages and their
# rich article HTML does not fit into the generic namespace budget. Keep the
# exception narrow: otherwise the LRU alternates between two miss sets and a
# nominally warm refresh still spends minutes behind Bonn.de's polite throttle.
_DETAIL_PAGE_CACHE_MAX_BYTES_BY_NAMESPACE = {
    "bonn-detail": 50 * 1024 * 1024,
}
_DETAIL_PAGE_CACHE_LOCK = threading.RLock()


class DetailCacheEntry(TypedDict):
    fetched_at: float
    accessed_at: float
    body: str


class DetailCacheState(TypedDict):
    namespace: str
    path: Path
    ttl_seconds: float
    entries: dict[str, DetailCacheEntry]
    dirty: bool  # entry added or removed; access-only LRU bumps do not set it


_DETAIL_PAGE_CACHE_STATES: dict[str, DetailCacheState] = {}


def _detail_page_cache_slug(namespace: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (namespace or "").casefold()).strip("-")
    if not slug:
        raise ValueError("cache_namespace must contain a letter or number")
    return slug


def _detail_page_cache_ttl_seconds() -> float:
    try:
        return max(float(os.environ.get("NRW_EVENTS_DETAIL_CACHE_TTL_HOURS", "24")), 0) * 60 * 60
    except (TypeError, ValueError):
        return 24 * 60 * 60


def _detail_page_cache_limit(name: str, default: int) -> int:
    try:
        return max(int(os.environ.get(name, str(default))), 0)
    except (TypeError, ValueError):
        return default


def _detail_page_cache_max_bytes(namespace: str) -> int:
    default = _DETAIL_PAGE_CACHE_MAX_BYTES_BY_NAMESPACE.get(
        namespace, _DETAIL_PAGE_CACHE_DEFAULT_MAX_BYTES,
    )
    return _detail_page_cache_limit("NRW_EVENTS_DETAIL_CACHE_MAX_BYTES", default)


def _prune_detail_page_cache_entries(
    entries: dict,
    *,
    namespace: str,
    ttl_seconds: float,
    now: float,
) -> dict[str, dict]:
    """Drop expired entries, then retain the newest entries within both caps."""
    valid: list[tuple[str, dict]] = []
    for url, entry in entries.items():
        if not isinstance(url, str) or not isinstance(entry, dict):
            continue
        try:
            fetched_at = float(entry.get("fetched_at", 0))
            accessed_at = float(entry.get("accessed_at", fetched_at))
        except (TypeError, ValueError):
            continue
        body = entry.get("body")
        if not isinstance(body, str) or now - fetched_at > ttl_seconds:
            continue
        valid.append((url, {
            "fetched_at": fetched_at,
            "accessed_at": accessed_at,
            "body": body,
        }))

    max_entries = _detail_page_cache_limit(
        "NRW_EVENTS_DETAIL_CACHE_MAX_ENTRIES", _DETAIL_PAGE_CACHE_DEFAULT_MAX_ENTRIES,
    )
    max_bytes = _detail_page_cache_max_bytes(namespace)
    valid.sort(key=lambda item: (item[1]["accessed_at"], item[1]["fetched_at"], item[0]), reverse=True)
    empty_payload_size = len(json.dumps(
        {"version": _DETAIL_PAGE_CACHE_VERSION, "namespace": namespace, "entries": {}},
        ensure_ascii=False, separators=(",", ":"),
    ).encode("utf-8"))
    retained: dict[str, dict] = {}
    serialized_size = empty_payload_size
    for url, entry in valid:
        if len(retained) >= max_entries:
            break
        fragment_size = len(json.dumps(
            {url: entry}, ensure_ascii=False, separators=(",", ":"),
        ).encode("utf-8")) - 2
        candidate_size = serialized_size + fragment_size + (1 if retained else 0)
        if candidate_size > max_bytes:
            continue
        retained[url] = entry
        serialized_size = candidate_size
    return retained


def _detail_page_cache_path(namespace: str) -> Path:
    configured = os.environ.get("NRW_EVENTS_CACHE_DIR", "").strip()
    if configured:
        cache_dir = Path(configured).expanduser()
    else:
        xdg_cache = os.environ.get("XDG_CACHE_HOME", "").strip()
        cache_dir = Path(xdg_cache).expanduser() if xdg_cache else Path.home() / ".cache"
        cache_dir /= "nrw-events"
    return cache_dir / f"detail-pages-{_detail_page_cache_slug(namespace)}-v1.json"


def _reset_detail_page_cache(namespace: str | None = None) -> None:
    """Reset process-local detail cache state; useful for isolated tests."""
    with _DETAIL_PAGE_CACHE_LOCK:
        flush_detail_page_caches(namespace)
        if namespace is None:
            _DETAIL_PAGE_CACHE_STATES.clear()
        else:
            _DETAIL_PAGE_CACHE_STATES.pop(_detail_page_cache_slug(namespace), None)


def _load_detail_page_cache(namespace: str, ttl_seconds: float) -> DetailCacheState:
    slug = _detail_page_cache_slug(namespace)
    path = _detail_page_cache_path(namespace)
    state = _DETAIL_PAGE_CACHE_STATES.get(slug)
    if state and state["path"] == path and state["ttl_seconds"] == ttl_seconds:
        return state

    entries: dict[str, DetailCacheEntry] = {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, TypeError, ValueError):
        payload = {}
    if (isinstance(payload, dict)
            and payload.get("version") == _DETAIL_PAGE_CACHE_VERSION
            and payload.get("namespace") == slug):
        entries = _prune_detail_page_cache_entries(
            payload.get("entries") or {}, namespace=slug,
            ttl_seconds=ttl_seconds, now=time.time(),
        )

    state = {
        "namespace": slug,
        "path": path,
        "ttl_seconds": ttl_seconds,
        "entries": entries,
        "dirty": False,
    }
    _DETAIL_PAGE_CACHE_STATES[slug] = state
    return state


def _persist_detail_page_cache(state: DetailCacheState) -> dict[str, str] | None:
    path = state["path"]
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        existing_entries: dict[str, DetailCacheEntry] = {}
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
            if (
                isinstance(existing, dict)
                and existing.get("version") == _DETAIL_PAGE_CACHE_VERSION
                and existing.get("namespace") == state["namespace"]
            ):
                raw_entries = existing.get("entries") or {}
                if isinstance(raw_entries, dict):
                    existing_entries = raw_entries
        except (FileNotFoundError, OSError, TypeError, ValueError):
            pass
        entries = _prune_detail_page_cache_entries(
            {**existing_entries, **state["entries"]},
            namespace=state["namespace"], ttl_seconds=state["ttl_seconds"],
            now=time.time(),
        )
        temporary.write_text(
            json.dumps(
                {
                    "version": _DETAIL_PAGE_CACHE_VERSION,
                    "namespace": state["namespace"],
                    "entries": entries,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )
        os.replace(temporary, path)
        state["entries"] = entries
        state["dirty"] = False
        return None
    except OSError as exc:
        log_source_error(f"{state['namespace']} detail cache", exc)
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        return {
            "source": "detail-cache",
            "error_type": type(exc).__name__,
            "error": f"failed to persist {state['namespace']} detail cache: {exc}",
        }


def flush_detail_page_caches(namespace: str | None = None) -> list[dict[str, str]]:
    """Persist dirty cache namespaces once at a source-run boundary."""
    warnings: list[dict[str, str]] = []
    with _DETAIL_PAGE_CACHE_LOCK:
        slug = _detail_page_cache_slug(namespace) if namespace else None
        for key, state in list(_DETAIL_PAGE_CACHE_STATES.items()):
            if state.get("dirty") and (slug is None or key == slug):
                if warning := _persist_detail_page_cache(state):
                    warnings.append(warning)
    return warnings


def fetch_detail_url(
    url: str,
    *,
    cache_namespace: str,
    timeout: int = 15,
    brightdata_fallback: bool = False,
    brightdata: bool = False,
    cache_failures: bool = False,
    retry_attempts: Optional[int] = None,
    **fetch_kwargs: Any,
) -> str:
    """Fetch a public event detail page through the persistent TTL cache.

    Successful responses are cached by default. Sources that must enforce a
    strict request ceiling can set ``cache_failures=True``; a failed attempt is
    then represented by an empty cached body until the TTL expires. Set
    ``retry_attempts`` controls transport behavior without changing the cached
    representation's identity. Set ``NRW_EVENTS_DETAIL_CACHE_TTL_HOURS=0`` to
    bypass both memory and disk.
    """
    if brightdata and brightdata_fallback:
        raise ValueError("brightdata and brightdata_fallback are mutually exclusive")
    if brightdata:
        fetcher: Callable[..., str] = fetch_url_with_brightdata
        transport = "brightdata"
    elif brightdata_fallback:
        fetcher = fetch_url_with_brightdata_fallback
        transport = "direct-with-brightdata-fallback"
    else:
        fetcher = fetch_url
        transport = "direct"
    transport_kwargs = dict(fetch_kwargs)
    if retry_attempts is not None:
        transport_kwargs["retry_attempts"] = retry_attempts
    ttl_seconds = _detail_page_cache_ttl_seconds()
    if not ttl_seconds:
        return fetcher(url, timeout=timeout, **transport_kwargs)
    cache_parameters = json.dumps(
        {
            "url": url,
            "transport": transport,
            "fetch_kwargs": fetch_kwargs,
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    cache_key = (
        url
        if transport == "direct" and not fetch_kwargs
        else f"{url}#{sha256(cache_parameters.encode()).hexdigest()[:16]}"
    )

    with _DETAIL_PAGE_CACHE_LOCK:
        state = _load_detail_page_cache(cache_namespace, ttl_seconds)
        cached = state["entries"].get(cache_key)
        if cached is not None and time.time() - cached["fetched_at"] <= ttl_seconds:
            # Access-only LRU bump: kept in memory only, so a fully cached run
            # never rewrites multi-MB namespace files. The bump is persisted
            # alongside the next insertion in this namespace, which is the only
            # time LRU precision matters (eviction happens during persist).
            cached["accessed_at"] = time.time()
            return cached["body"]
        state["entries"].pop(cache_key, None)

    try:
        body = fetcher(url, timeout=timeout, **transport_kwargs)
    except Exception:
        if cache_failures:
            with _DETAIL_PAGE_CACHE_LOCK:
                state = _load_detail_page_cache(cache_namespace, ttl_seconds)
                fetched_at = time.time()
                state["entries"][cache_key] = {
                    "fetched_at": fetched_at, "accessed_at": fetched_at, "body": "",
                }
                state["dirty"] = True
        raise
    with _DETAIL_PAGE_CACHE_LOCK:
        state = _load_detail_page_cache(cache_namespace, ttl_seconds)
        fetched_at = time.time()
        state["entries"][cache_key] = {
            "fetched_at": fetched_at, "accessed_at": fetched_at, "body": body,
        }
        state["dirty"] = True
    return body


def parse_float(value: Any, default: float = 0.0) -> float:
    """Parse source-provided numeric values, accepting German decimal commas."""
    if value is None or value == "":
        return default
    try:
        return float(str(value).strip().replace(",", "."))
    except (TypeError, ValueError):
        return default


def post_json(url: str, payload: dict[str, Any], timeout: int = 45,
              headers: Optional[dict[str, str]] = None,
              retry_safe: bool = False) -> dict[str, Any]:
    """POST JSON and parse JSON; callers opt into retries for idempotent APIs."""
    hdrs = browser_headers(
        accept="application/json",
        sec_fetch_mode="cors",
        sec_fetch_dest="empty",
        extra={"Content-Type": "application/json", **(headers or {})},
    )
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=hdrs, method="POST")
    settings = _runtime_state().settings
    attempts = settings.http_retry_attempts if retry_safe else 1
    deadline = _request_deadline()
    for attempt in range(attempts):
        try:
            started = time.perf_counter()
            with _host_request_slot(url, deadline):
                with closing(urllib.request.urlopen(req, timeout=_remaining_timeout(deadline, timeout))) as resp:
                    body = _read_response_body(
                        resp, settings.http_max_response_bytes, deadline=deadline,
                    )
                    _record_endpoint(url, status=getattr(resp, "status", 200), content_type="application/json",
                                     bytes=len(body), duration_ms=round((time.perf_counter() - started) * 1000))
            return json.loads(body.decode("utf-8"))
        except Exception as exc:
            _record_endpoint(url, error_type=type(exc).__name__, error=redact(exc))
            retry = attempt < attempts - 1 and _is_retryable_fetch_error(exc)
            delay = _retry_delay(exc, attempt) if retry else 0
            _close_http_error(exc)
            if not retry:
                raise
            _sleep_for_retry(delay, deadline)
    raise RuntimeError("post_json retry loop exhausted unexpectedly")  # pragma: no cover


def post_form(url: str, fields: Any, timeout: int = 45,
              headers: Optional[dict[str, str]] = None,
              retry_safe: bool = False) -> dict[str, Any]:
    """POST URL-encoded form fields and parse a JSON response."""
    hdrs = browser_headers(
        accept="application/json",
        sec_fetch_mode="cors",
        sec_fetch_dest="empty",
        extra={"Content-Type": "application/x-www-form-urlencoded", **(headers or {})},
    )
    data = urllib.parse.urlencode(fields, doseq=True).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=hdrs, method="POST")
    settings = _runtime_state().settings
    attempts = settings.http_retry_attempts if retry_safe else 1
    deadline = _request_deadline()
    for attempt in range(attempts):
        try:
            started = time.perf_counter()
            with _host_request_slot(url, deadline):
                with closing(urllib.request.urlopen(req, timeout=_remaining_timeout(deadline, timeout))) as resp:
                    body = _read_response_body(
                        resp, settings.http_max_response_bytes, deadline=deadline,
                    )
                    _record_endpoint(url, status=getattr(resp, "status", 200), content_type="application/json",
                                     bytes=len(body), duration_ms=round((time.perf_counter() - started) * 1000))
            return json.loads(body.decode("utf-8"))
        except Exception as exc:
            _record_endpoint(url, error_type=type(exc).__name__, error=redact(exc))
            retry = attempt < attempts - 1 and _is_retryable_fetch_error(exc)
            delay = _retry_delay(exc, attempt) if retry else 0
            _close_http_error(exc)
            if not retry:
                raise
            _sleep_for_retry(delay, deadline)
    raise RuntimeError("post_form retry loop exhausted unexpectedly")  # pragma: no cover


def extract_json_array(text: str) -> list:
    """Best-effort parse of a JSON array from LLM/search output."""
    if not text:
        return []
    candidates = [text]
    for m in re.finditer(r"```(?:json)?\s*(.*?)```", text, re.S | re.I):
        candidates.append(m.group(1))
    arr_match = re.search(r"\[[\s\S]*\]", text)
    if arr_match:
        candidates.append(arr_match.group(0))
    last_error = None
    for candidate in candidates:
        try:
            parsed = json.loads(candidate.strip())
            return parsed if isinstance(parsed, list) else []
        except json.JSONDecodeError as exc:
            last_error = exc
            continue
    if last_error is not None:
        log_source_error("Search JSON response", last_error)
    return []


# ── HTML / text ─────────────────────────────────────────────────────

def clean_html(text: str) -> str:
    """Strip tags/entities and collapse whitespace."""
    text = unescape(text or "")
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.S)
    text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


# Tags whose boundary the author used to separate thoughts. Everything else
# (``<strong>``, ``<a>``, ``<span>`` …) is inline and must not break a sentence.
_BLOCK_TAG_PATTERN = re.compile(
    r"</?(?:p|div|section|article|header|footer|ul|ol|dl|table|"
    r"h[1-6]|blockquote|figure|figcaption|pre|hr)\b[^>]*>",
    re.I,
)
# One entry per line, but a list is one block: a blank line between every bullet
# reads as a series of one-line paragraphs. Only the opening tag breaks, so
# "</li><li>" stays a single break rather than becoming a paragraph gap.
_LIST_ITEM_OPEN_PATTERN = re.compile(r"<(?:li|dt|dd|tr)\b[^>]*>", re.I)
_LIST_ITEM_CLOSE_PATTERN = re.compile(r"</(?:li|dt|dd|tr)\s*>", re.I)
_LINE_BREAK_TAG_PATTERN = re.compile(r"<br\b[^>]*>", re.I)


def clean_html_blocks(text: str) -> str:
    """Strip tags but keep the author's paragraph structure.

    ``clean_html`` flattens everything to one line, which is right for a title,
    a venue or a price. Event copy is prose: the source wrote paragraphs and
    lists, and collapsing them produced a single unreadable wall of text on the
    detail page. Block boundaries become a blank line, ``<br>`` a single one,
    and only horizontal whitespace is collapsed.
    """
    text = unescape(text or "")
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.S)
    text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", text, flags=re.S | re.I)
    text = _LINE_BREAK_TAG_PATTERN.sub("\n", text)
    text = _LIST_ITEM_CLOSE_PATTERN.sub("", text)
    text = _LIST_ITEM_OPEN_PATTERN.sub("\n", text)
    text = _BLOCK_TAG_PATTERN.sub("\n\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    return normalize_block_text(text)


def normalize_block_text(text: str) -> str:
    """Collapse horizontal runs and stray blank lines, keeping paragraph breaks."""
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[^\S\n]+", " ", text)
    # Stripping an inline tag leaves a space where the markup was, so copy that
    # ends a sentence inside a link reads as "willkommen ." once the tag is gone.
    text = re.sub(r" +([,.;:!?])", r"\1", text)
    text = re.sub(r" *\n *", "\n", text)
    # Three or more breaks are layout padding, not a third kind of separator.
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def normalize_url(url: str) -> str:
    """Decode HTML entities and make internationalized hostnames link-safe."""
    url = unescape(url or "").strip()
    # Municipal calendars occasionally publish Windows-style separators
    # ("http:\\example.de"). Browsers repair those, urlsplit does not, so the
    # link would otherwise reach the site unusable.
    if "\\" in url:
        url = url.replace("\\", "/")
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        return url

    try:
        host = parts.hostname.encode("idna").decode("ascii")
    except UnicodeError:
        return url

    userinfo = ""
    if "@" in parts.netloc:
        userinfo = parts.netloc.rsplit("@", 1)[0] + "@"

    try:
        port = f":{parts.port}" if parts.port else ""
    except ValueError:
        port = ""

    if ":" in host and not host.startswith("["):
        host = f"[{host}]"

    return urllib.parse.urlunsplit((parts.scheme, f"{userinfo}{host}{port}", parts.path, parts.query, parts.fragment))


def is_raw_api_url(url: str) -> bool:
    """True when an event link points to machine data rather than a human page."""
    parts = urllib.parse.urlsplit(url or "")
    path = (parts.path or "").lower()
    query = (parts.query or "").lower()
    if path.endswith((".json", ".xml")):
        return True
    if "/api/" in path or path.startswith("/api"):
        return True
    if path in {"", "/"} and query and any(bit in query for bit in ("format=json", "output=json", "type=json", "eventid=")):
        return True
    return False


def normalize_venue_name(value: str, city: str = "") -> str:
    """Return a registry display name or a conservatively cleaned source name."""
    return resolve_venue(clean_html(value)[:300], city).venue


class GeneratedDescription(str):
    """String marker for copy synthesized by the importer rather than a source."""


def description_source_for(value: str) -> str:
    """Return the public provenance label for event description copy."""
    return "generated" if isinstance(value, GeneratedDescription) else "scraped"


_NON_TERMINAL_ABBREVIATIONS = frozenset({
    "abb", "bsp", "bzw", "ca", "d.h", "dr", "etc", "ggf", "inkl", "nr",
    "prof", "sog", "str", "u.a", "usw", "vgl", "z.b", "zzgl",
})


def _is_sentence_boundary(text: str, match: re.Match) -> bool:
    """Reject periods that belong to common abbreviations, initials, or ordinals."""
    if match.group(0)[0] != ".":
        return True
    token_match = re.search(r"([\wÄÖÜäöüß.]+)$", text[:match.start()])
    token = token_match.group(1) if token_match else ""
    normalized = token.casefold().strip(".")
    return not (
        token.isdigit()
        or len(normalized) == 1
        or normalized in _NON_TERMINAL_ABBREVIATIONS
    )


def concise_description(value: str, max_chars: int | None = None) -> str:
    """Return cleaned event copy sized for reports and downstream cards."""
    generated = isinstance(value, GeneratedDescription)
    cleaned = clean_html_blocks(value)
    # Feeds that serialize their copy into a JSON string carry the break as the
    # two characters "\" and "n"; it means the same thing the tag did. Only
    # unescape when the text plausibly came through such double-encoding: no
    # real newlines, literal "\n"/"\r\n" sequences present, and no Windows
    # path or UNC share (e.g. "C:\neu", "\\server") whose backslashes would
    # otherwise be split mid-word.
    # ponytail: heuristic gate, not a JSON round-trip proof; refine per-feed
    # in the source adapters if a feed ever mixes paths with escaped breaks.
    if (
        "\n" not in cleaned
        and "\\n" in cleaned
        and not re.search(r"(?<!\w)[A-Za-z]:\\|\\\\", cleaned)
    ):
        cleaned = re.sub(r"\\r\\n|\\[rn]", "\n", cleaned)
    cleaned = normalize_block_text(cleaned)
    limit = _runtime_state().settings.description_max_chars if max_chars is None else max_chars
    if not limit or len(cleaned) <= limit:
        shortened = cleaned
    else:
        sentence_ends = [
            match
            for match in re.finditer(r'''[.!?](?:["'“”’»\)\]]*)(?=\s|$)''', cleaned[:limit])
            if _is_sentence_boundary(cleaned, match)
        ]
        if sentence_ends:
            shortened = cleaned[:sentence_ends[-1].end()].rstrip()
        else:
            prefix = cleaned[:max(0, limit - 1)]
            # A cut may land mid-paragraph; break on the last whitespace of any
            # kind so the truncation never glues two paragraphs together.
            shortened = re.split(r"\s(?=\S*$)", prefix)[0].rstrip(" ,;:\n")
            shortened = f"{shortened}…" if shortened else "…"[:limit]
    shortened = normalize_block_text(shortened)
    return GeneratedDescription(shortened) if generated else shortened


def factual_event_description(
    title: str,
    *,
    date_value: Any = None,
    end_date_value: Any = None,
    time_text: str = "",
    end_time_text: str = "",
    venue: str = "",
    city: str = "",
    calendar_name: str = "",
    categories: tuple[Any, ...] = (),
) -> str:
    """Build useful minimum copy when an upstream listing has no description."""
    clean_title = clean_html(title)
    date_text = (
        date_value.strftime("%d.%m.%Y")
        if hasattr(date_value, "strftime")
        else clean_html(str(date_value or ""))
    )
    clean_time = sanitize_time_text(time_text).removesuffix(" Uhr")
    end_date_text = (
        end_date_value.strftime("%d.%m.%Y")
        if hasattr(end_date_value, "strftime")
        else clean_html(str(end_date_value or ""))
    )
    when = (
        f" vom {date_text} bis {end_date_text}"
        if date_text and end_date_text and end_date_text != date_text
        else (f" am {date_text}" if date_text else "")
    )
    times = re.findall(r"\d{1,2}:\d{2}", f"{clean_time} {end_time_text}")
    if len(times) >= 2:
        when += f" von {times[0]} bis {times[1]} Uhr"
    elif times:
        when += f" um {times[0]} Uhr"
    description = f"„{clean_title}“ findet{when} statt."

    place_parts: list[str] = []
    for index, value in enumerate((venue, city)):
        cleaned = clean_html(value)
        if cleaned and not any(
            cleaned.casefold() == part.casefold()
            or (index == 1 and cleaned.casefold() in part.casefold())
            for part in place_parts
        ):
            place_parts.append(cleaned)
    if place_parts:
        description += f" Veranstaltungsort: {', '.join(place_parts)}."
    clean_categories = [clean_html(str(value)) for value in categories or ()]
    clean_categories = [value for value in clean_categories if value]
    clean_calendar = clean_html(calendar_name)
    if clean_calendar:
        description += f" Quelle: Veranstaltungskalender {clean_calendar}."
    if clean_categories:
        description += f" Themen: {', '.join(dict.fromkeys(clean_categories))}."
    return GeneratedDescription(concise_description(description))


def keep_only_event_master_data(event: RawEvent) -> RawEvent:
    """Replace publisher prose with a sentence generated only from event facts.

    This is for discovery platforms and directories whose descriptive copy must
    not be republished. Classification, admission and status extraction happen
    before this helper is called; the public description then contains only the
    allowed title, date, time and place fields already present on the record.
    """
    start = parse_iso_date(event.get("start_date") or event.get("date") or "")
    end = parse_iso_date(event.get("end_date") or "")
    description = factual_event_description(
        event.get("title", ""),
        date_value=start,
        end_date_value=end,
        time_text=event.get("time", ""),
        venue=event.get("venue", ""),
        city=event.get("city", ""),
    )
    event["description"] = description
    event["description_html"] = richtext.from_plain_text(description)
    event["description_source"] = "generated"
    return event


_CANCELLED_STATUS_WORDS = (
    r"abgesagt(?:\s+(?:werden|wird|wurde))?|entfällt|entfaellt|"
    r"fällt\s+(?:leider\s+)?aus|faellt\s+(?:leider\s+)?aus|"
    r"findet\s+(?:leider\s+)?nicht\s+statt|verschoben"
)
_CANCELLED_STATUS_SUBJECTS = (
    r"veranstaltung|termin|event|konzert|lesung|theaterabend|show|kurs|workshop|"
    r"führung|fuehrung|rundgang|programm|kabarettprogramm"
)
_CANCELLED_TITLE_PATTERN = re.compile(
    rf"^\s*[-–—:()]*\s*(?:{_CANCELLED_STATUS_WORDS})\b"
    rf"|\b(?:{_CANCELLED_STATUS_WORDS})\b\s*[-–—:()]*$",
    re.IGNORECASE,
)
_CANCELLED_CONTEXT_PATTERN = re.compile(
    rf"\b(?:{_CANCELLED_STATUS_SUBJECTS})\b[^\n.!?]{{0,80}}\b(?:{_CANCELLED_STATUS_WORDS})\b"
    rf"|\b(?:{_CANCELLED_STATUS_WORDS})\b[^\n.!?]{{0,80}}\b(?:krankheitsbedingt|neuer\s+termin|nachgeholt)\b",
    re.IGNORECASE,
)
_POSTPONED_VERLEGT_TITLE_PATTERN = re.compile(
    r"^\s*[-–—:()]*\s*verlegt\b|\bverlegt\s*[-–—:()]*$",
    re.IGNORECASE,
)
_POSTPONED_VERLEGT_CONTEXT_PATTERN = re.compile(
    rf"\b(?:{_CANCELLED_STATUS_SUBJECTS})\b\s+(?:wurde|wird|ist)\b"
    r"[^\n.!?]{0,80}\bverlegt\b"
    r"|\bverlegt\b[^\n.!?]{0,80}\b(?:vom|auf|neuer\s+termin|neues\s+datum)\b",
    re.IGNORECASE,
)


def has_cancelled_status(title: str, description: str) -> bool:
    """True when text marks this event as cancelled/postponed."""
    combined = " ".join([title or "", description or ""])
    return bool(
        _CANCELLED_TITLE_PATTERN.search(title or "")
        or _CANCELLED_CONTEXT_PATTERN.search(combined)
        or _POSTPONED_VERLEGT_TITLE_PATTERN.search(title or "")
        or _POSTPONED_VERLEGT_CONTEXT_PATTERN.search(combined)
    )


def event_status(title: str, description: str) -> str:
    """Return a normalized source-independent schedule status."""
    text = " ".join([title or "", description or ""])
    if has_cancelled_status(title, description):
        return (
            "postponed"
            if re.search(r"\b(?:verschoben|verlegt)\b|neuer\s+termin", text, re.IGNORECASE)
            else "cancelled"
        )
    return "scheduled"


# ── Date parsing ────────────────────────────────────────────────────

def extract_dates(text: str) -> list:
    """Extract parseable dates from free text (for search-result filtering)."""
    text = text or ""
    dates = []
    patterns = [
        r"20\d{2}-\d{2}-\d{2}",
        r"\d{1,2}\.\d{1,2}\.20\d{2}",
        r"\d{1,2}\.\d{1,2}\.\d{2}\b",
        r"\d{1,2}\.\s*(?:Januar|Jan|Februar|Feb|März|Maerz|Mär|Mae|April|Apr|Mai|Juni|Jun|Juli|Jul|August|Aug|September|Sep|Oktober|Okt|November|Nov|Dezember|Dez)\s*20\d{2}",
    ]
    for pat in patterns:
        for m in re.finditer(pat, text, re.I):
            dt = parse_date(m.group(0))
            if dt:
                dates.append(dt)
    return dates


def date_range_overlaps(dates: list) -> bool:
    """True if any extracted date is inside the window; empty list = unknown = include."""
    if not dates:
        return True
    return any(window_contains(dt) for dt in dates)


def in_date_range(date_str: str) -> bool:
    """True if a date string is in-window, or unparseable (include-when-unknown)."""
    dt = parse_date(date_str)
    if dt is None:
        return True
    return window_contains(dt)


def window_contains(start_dt: Optional[datetime], end_dt: Optional[datetime] = None) -> bool:
    """Return whether a dated event overlaps the inclusive report window."""
    if start_dt is None:
        return False
    window_end = END_DATE.replace(hour=23, minute=59, second=59, microsecond=999999)
    effective_end = end_dt or start_dt
    return effective_end >= TODAY and start_dt <= window_end


def event_in_window(event: dict) -> bool:
    """Return whether a parsed event overlaps the inclusive report window."""
    start = parse_iso_date(event.get("start_date", ""))
    end = parse_iso_date(event.get("end_date", "")) or start
    if not start:
        date_text = event.get("date", "")
        if "–" in date_text:
            start_text, end_text = date_text.split("–", 1)
            start, end = parse_date(start_text), parse_date(end_text)
        else:
            start = parse_date(date_text)
            end = start
    return True if not start else window_contains(start, end)


def event_in_window_and_radius(
    start_dt: Optional[datetime], end_dt: Optional[datetime], city: str,
    coords: Optional[tuple] = None,
) -> bool:
    """Cheap preflight for detail-page fan-out before full event construction."""
    if not window_contains(start_dt, end_dt):
        return False
    resolved_coords, _, _ = resolve_location(city, coords)
    if not resolved_coords:
        return True
    return haversine(BONN_LAT, BONN_LON, *resolved_coords) <= runtime_radius_km()


_SIMPLE_TIME_PATTERN = re.compile(
    r"^\s*(?P<prefix>ab\s+)?"
    r"(?P<start_hour>\d{1,2})(?:[.:](?P<start_minute>\d{2}))?\s*(?:Uhr)?"
    r"(?:\s*(?:bis|[-–—])\s*"
    r"(?P<end_hour>\d{1,2})(?:[.:](?P<end_minute>\d{2}))?\s*(?:Uhr)?)?\s*$",
    re.IGNORECASE,
)


def _round_time_to_quarter(hour: int, minute: int) -> tuple[int, int]:
    total = hour * 60 + minute
    rounded = min(int(round(total / 15) * 15), 23 * 60 + 45)
    return divmod(rounded, 60)


def _format_hhmm(hour: int, minute: int) -> str:
    return f"{hour:02d}:{minute:02d}"


def normalize_time_fields(time_text: str) -> tuple[str, str]:
    """Split a source time into a strict clock value and lossless display note."""
    text = (time_text or "").strip()
    if not text:
        return "", ""
    match = _SIMPLE_TIME_PATTERN.fullmatch(text)
    if not match:
        return "", text

    start_hour = int(match.group("start_hour"))
    start_minute = int(match.group("start_minute") or 0)
    if start_hour > 23 or start_minute > 59:
        return "", text

    rounded_start = (start_hour, start_minute)
    end_hour_text = match.group("end_hour")
    if end_hour_text is not None:
        end_hour = int(end_hour_text)
        end_minute = int(match.group("end_minute") or 0)
        if end_hour > 23 or end_minute > 59:
            return "", text
        start_total = start_hour * 60 + start_minute
        end_total = end_hour * 60 + end_minute
        if end_total < start_total:
            end_total += 24 * 60
        duration = end_total - start_total
        artifact_range = (end_hour, end_minute) in {(23, 59), (0, 0)} or duration < 20
        if artifact_range:
            if start_minute % 5 != 0:
                rounded_start = _round_time_to_quarter(start_hour, start_minute)
            return _format_hhmm(*rounded_start), ""
        return (
            f"{_format_hhmm(*rounded_start)}–{_format_hhmm(end_hour, end_minute)}",
            text if match.group("prefix") else "",
        )

    return _format_hhmm(*rounded_start), text if match.group("prefix") else ""


def sanitize_time_text(time_text: str) -> str:
    """Return a canonical simple time, retaining complex legacy input unchanged."""
    canonical, note = normalize_time_fields(time_text)
    return canonical or note


def combine_time_notes(existing: str, inferred: str) -> str:
    """Preserve distinct source qualifiers without duplicating identical copy."""
    existing = (existing or "").strip()
    inferred = (inferred or "").strip()
    if not existing:
        return inferred
    if not inferred or inferred in existing:
        return existing
    return f"{existing}; {inferred}"


# ── Event construction + junk filter ────────────────────────────────

_FREE_ADMISSION_PATTERNS = (
    r"\b(?:kosten|preis|teilnahmegebühr|teilnahmegebuehr)\s*:\s*(?:frei|kostenlos|kostenfrei)\b",
    r"\beinlass\s*:?\s*(?:gratis|frei|kostenlos|kostenfrei)\b",
    r"\beintritt\s*:?\s*(?:frei|kostenlos|kostenfrei)\b",
    r"\beintritt\s+(?:ist|bleibt)\s+(?:(?:nach\s+wie\s+vor|weiterhin|auch|natürlich|natuerlich|"
    r"wieder|wie\s+immer|(?:für|fuer|zu)\s+alle(?:n)?\s+(?:veranstaltungen|angebote|termine))\s+)*"
    r"(?:frei|kostenlos|kostenfrei)\b",
    r"\beintirtt\s+(?:ist|bleibt)\s+(?:frei|kostenlos|kostenfrei)\b",
    r"\beintritt\s+(?:(?:natürlich|natuerlich|weiterhin|wieder|nach\s+wie\s+vor|"
    r"wie\s+immer)\s+)+(?:frei|kostenlos|kostenfrei)\b",
    r"\beintritt\s+(?:auch\s+)?(?:zu|für|fuer)\s+[^.]{1,60}\s+"
    r"(?:ist|bleibt)\s+(?:frei|kostenlos|kostenfrei)\b",
    r"\bfreier\s+eintritt\b",
    r"\b(?:kostenloser|kostenfreier)\s+eintritt\b",
    r"\b(?:bei|mit)\s+frei(?:em|en)\s+eintritt\b",
    r"\b(?:teilnahme|veranstaltung|ausstellung|ferienprogramm|performance|workshop|angebote?|programm|sportangebot|termin|event)"
    r"\s+.{0,90}\b(?:ist|sind)\s+(?:kostenlos|kostenfrei)\b",
    r"\b(?:kostenlos(?:e[rsn]?|em|en|es)?|kostenfrei(?:e[rsn]?|em|en|es)?)[,\s–-]+"
    r"(?:[a-zäöüß-]+[,\s]+){0,2}(?:teilnahme|veranstaltung|angebot|programm|sportangebot|"
    r"[a-zäöüß-]*(?:workshop|kurs|konzert|führung|fuehrung|tour|training)|termin|event|"
    r"filmvorführung|filmvorfuehrung)\b",
    r"\b(?:workshop|veranstaltung|sonder-veranstaltung|führung|fuehrung|offene werkstatt)"
    r"[^.]{0,80}\b(?:kostenlos|kostenfrei)\b",
    r"\b(?:kostenlos|kostenfrei)\s*(?:[-–]\s*)?(?:und\s+)?"
    r"(?:keine anmeldung|anmeldung erforderlich|ohne anmeldung)\b",
    r"\b(?:kostenlos|kostenfrei)\s+und\s+(?:draußen|draussen)\s*[-–,]?\s*"
    r"(?:keine anmeldung|ohne anmeldung)\b",
    r"(?:^|[.!?]\s*)kostenlos\s+und\s+unverbindlich\b",
    r"\b(?:kostenlos|kostenfrei)\s+ab\s+\d+\b",
    r"\b(?:du\s+kannst|kannst\s+du|ihr\s+(?:könnt|koennt)|(?:könnt|koennt)\s+ihr|"
    r"sie\s+(?:können|koennen)|(?:können|koennen)\s+sie|man\s+kann)\b"
    r"[^.]{0,140}\b(?:kostenlos|kostenfrei)\b"
    r"[^.]{0,60}\b(?:anhören|anhoeren|besuchen|teilnehmen|mitmachen)\b",
)
_FREE_TITLE_PATTERN = re.compile(r"^\s*(?:kostenlos|kostenfrei)\s+", re.IGNORECASE)
# Municipal calendar templates often place the admission value on its own
# paragraph instead of exposing a dedicated price field. Require a whole block;
# ordinary prose such as "Anmeldung nicht erforderlich: frei" is not evidence.
_FREE_DESCRIPTION_BLOCK_PATTERN = re.compile(
    r"(?im)^\s*(?:"
    r"(?:kostenlos|kostenfrei)(?:\s+natürlich|\s+natuerlich)?\s*"
    r"(?:[.!][ \t]*(?=\n|$)|$)"
    r"|frei\s*(?:[.!]?\s*$|,\s*(?:es\s+geht\s+der\s+hut\s+rum|hutspenden?\b|spenden?\b).*$)"
    r")",
)
_FREE_PRICE_PATTERN = re.compile(
    r"^(?:(?:eintritt|kosten|preis|teilnahmegebühr|teilnahmegebuehr)\s*:?\s*)?"
    r"(?:(?:frei|kostenlos|kostenfrei|free)"
    # Calendar templates append their currency unconditionally, so a free event
    # arrives as "Eintritt: frei€" or "Eintritt: frei 0 €". Without this the
    # whole string fails the match, the price is treated as a real amount and
    # the event is published as paid. The trailing group stays anchored so
    # "Eintritt: freitags 10 €" is still not free.
    r"(?:\s*[,;/(-].*|(?:\s*0(?:[,.]00)?)?\s*(?:€|eur|euro))?"
    r"|0(?:[,.]00)?\s*(?:€|eur|euro))$",
    re.IGNORECASE,
)
_LIMITED_FREE_WITH_PAID_PATTERN = re.compile(
    r"\b(?:kosten|preise?|eintritt|teilnahme|gebühr|gebuehr|führungen?|fuehrungen?|"
    r"erwachsene|ermäßigt|ermaessigt)\b[^.]{0,100}\b\d+[,.]?\d*\s*(?:€|eur|euro)(?!\w)",
    re.IGNORECASE,
)
_LIMITED_FREE_CONTEXT_PATTERNS = (
    r"\beintritt\s+in\s+(?:den|die|das)\s+[^.]{0,50}\s+ist\s+frei\b",
    r"\bkinder(?:n)?\s+bis\s+\d+[^.]{0,40}\s+(?:kostenlos|frei)\b",
    r"\b(?:kostenlos|frei)[^.]{0,40}\bkinder(?:n)?\s+bis\s+\d+\b",
)
_LIMITED_FREE_TRIAL_PATTERN = re.compile(
    r"\b(?:erste|ersten|erstes|erstmalige|einmalige)\b[^.]{0,80}"
    r"\b(?:kostenlos|kostenfrei)(?:e[rsn]?|em|en|es)?\s+probe(?:stunde|training|termin)\b",
    re.IGNORECASE,
)
_CONDITIONAL_FREE_VISITOR_GROUP_PATTERN = (
    r"(?:kind(?:er(?:n)?)?|jugendlich(?:e|en|er|es)|person(?:en)?|mensch(?:en)?|"
    r"mitglied(?:er(?:n)?)?|begleitperson(?:en)?)"
)
_CONDITIONAL_FREE_ADMISSION_PATTERN = re.compile(
    r"\b(?:freier|kostenloser|kostenfreier)\s+eintritt\b[^.!?]{0,100}"
    r"(?:\bam\s+eröffnungsabend\b|\ban\s+(?:jedem\s+)?(?:ersten\s+)?sonntag\b|"
    rf"\bnur\b|\bfür\s+(?!alle\b){_CONDITIONAL_FREE_VISITOR_GROUP_PATTERN}\b)"
    rf"|\b{_CONDITIONAL_FREE_VISITOR_GROUP_PATTERN}\b[^.!?]{{0,100}}"
    r"\b(?:freien\s+eintritt|eintritt\s+(?:ist\s+)?frei|"
    r"kostenlos(?:e(?:n|r|m|s)?\s+eintritt)?)\b"
    r"|\b(?:am\s+eröffnungsabend|an\s+(?:jedem\s+)?(?:ersten\s+)?sonntag)\b"
    r"[^.!?]{0,100}\b(?:eintritt\b[^.!?]{0,30}\bfrei|freier\s+eintritt)\b",
    re.IGNORECASE,
)


def has_conditional_free_admission(value: str) -> bool:
    """Return whether free access is limited to a date or visitor group."""
    return bool(_CONDITIONAL_FREE_ADMISSION_PATTERN.search(clean_html(value or "")))

# These event types normally have no visitor admission even when the source only
# publishes ancillary charges or leaves the price field empty. Keep the list
# narrow: ticketed design/night/indoor markets are deliberately excluded.
_IMPLICIT_FREE_TITLE_PATTERN = re.compile(
    r"\b(?:flohmarkt|trödelmarkt|troedelmarkt|hofflohmarkt|hausflohmarkt|"
    r"straßenflohmarkt|strassenflohmarkt|stadtflohmarkt|büchermarkt|buechermarkt|"
    r"stadtteilfest|straßenfest|strassenfest|veedelsfest|dorffest|"
    r"nachbarschaftsfest|tag\s+der\s+offenen\s+tür|tag\s+der\s+offenen\s+tuer|"
    r"repair[-\s]?caf[ée]|reparaturcaf[ée])\b",
    re.IGNORECASE,
)
_IMPLICIT_FREE_EXCLUSION_PATTERN = re.compile(
    r"\b(?:nachtflohmarkt|indoor[-\s]?(?:floh|trödel|troedel)?markt|messe|"
    r"stadthalle|eventhalle|ticket(?:s|preis)?|besucher(?:eintritt|preis))\b",
    re.IGNORECASE,
)
_VISITOR_ADMISSION_AMOUNT_PATTERN = re.compile(
    r"\b(?:(?:eintritt|besucher(?:preis|eintritt)|ticket(?:preis)?|"
    r"teilnahme(?:gebühr|gebuehr|kosten)|teilnehmergebühr|teilnehmergebuehr|"
    r"kostenbeitrag|kursgebühr|kursgebuehr|workshopgebühr|workshopgebuehr)\b[^.]{0,60}|"
    r"(?:gäste|gaeste|erwachsene)\s+(?:zahlen|bezahlen|kosten)\s*)"
    # `\b` after `€` would never match at end of string: use a word-char guard so
    # the common German notation ("Eintritt: 4,50 €") is recognised.
    r"\b\d+[,.]?\d*\s*(?:€|eur|euro)(?!\w)",
    re.IGNORECASE,
)
_SELLER_FEE_PATTERN = re.compile(
    r"\b(?:standgebühr|standgebuehr|standpreis|standfläche\s+kostet|"
    r"standflaeche\s+kostet|lfdm|laufend(?:e|er|en)?\s+(?:front)?meter|"
    r"reinigungskaution|verkäufergebühr|verkaeufergebuehr|händlergebühr|"
    r"haendlergebuehr)\b",
    re.IGNORECASE,
)


def has_seller_fee(value: str) -> bool:
    """Return whether copy names a vendor charge rather than visitor admission."""
    return bool(_SELLER_FEE_PATTERN.search(clean_html(value or "")))

def infer_admission(
    title: str,
    description: str,
    price: str = "",
    *,
    admission: AdmissionDefault | None = None,
    admission_basis: str = "",
) -> tuple[str, str]:
    """Infer admission from event copy, price, or a declared source default."""
    # Transport metadata is not admission evidence. A venue or URL containing
    # "Eintritt frei" must not silently turn a paid event into a free one.
    raw = " ".join([title or "", description or "", price or ""])
    text = clean_html(raw).lower()
    # Some upstream WordPress copy glues adjacent logistics labels together
    # ("16:30 UhrEintritt frei"). Repair only this explicit boundary rather
    # than inserting spaces inside arbitrary camel-cased words.
    text = re.sub(r"\buhr(?=eintritt\b)", "uhr ", text, flags=re.IGNORECASE)
    text = re.sub(r"\b(kostenfrei|kostenlos)(?=ab\s+\d)", r"\1 ", text)
    text = re.sub(r"\s+", " ", text)
    price_text = clean_html(price or "").lower().strip()

    # A broad calendar tag such as "Kostenlos" can coexist with prose that
    # limits free access to an opening night, a monthly museum day, children or
    # members.  The conditional prose is the stronger fact; do not publish the
    # occurrence as free for every visitor.  A separate, unqualified sentence
    # ("Der Eintritt ist frei.") still wins because it is explicit whole-event
    # evidence rather than the same qualified offer.
    description_text = clean_html(description or "")
    conditional_free = has_conditional_free_admission(description_text)
    unconditional_description = _CONDITIONAL_FREE_ADMISSION_PATTERN.sub(
        " ", description_text,
    )
    unconditional_free = (
        bool(_FREE_DESCRIPTION_BLOCK_PATTERN.search(clean_html_blocks(unconditional_description)))
        or any(
            re.search(pattern, unconditional_description, re.IGNORECASE)
            for pattern in _FREE_ADMISSION_PATTERNS
        )
    )
    if conditional_free and not unconditional_free:
        return "", ""

    visitor_charge = bool(_VISITOR_ADMISSION_AMOUNT_PATTERN.search(text))
    seller_fee = has_seller_fee(text)
    price_has_amount = bool(re.search(
        r"(?<!\d)\d+(?:[.,]\d{1,2})?\s*(?:€|eur\b|euro\b)",
        price_text,
        re.IGNORECASE,
    ))
    price_states_whole_event_is_free = (
        bool(_FREE_DESCRIPTION_BLOCK_PATTERN.search(price_text))
        or any(
            re.search(pattern, price_text, re.IGNORECASE)
            for pattern in _FREE_ADMISSION_PATTERNS
        )
    )
    if admission_basis == "implicit" and (visitor_charge or seller_fee):
        return "", ""
    if _FREE_PRICE_PATTERN.fullmatch(price_text):
        return "kostenlos", admission_basis or "explicit"
    # Structured municipal calendars frequently expose a complete sentence in
    # their price field (for example, "Die Teilnahme ist kostenlos.").  Treat
    # that as explicit whole-event evidence only when the same field contains
    # no monetary amount; conditional free tiers and paid add-ons must remain
    # paid.
    if price_states_whole_event_is_free and not price_has_amount:
        return "kostenlos", admission_basis or "explicit"
    if price_text:
        return "", "explicit"
    if visitor_charge:
        return "", ""
    if _LIMITED_FREE_WITH_PAID_PATTERN.search(text) and any(re.search(pattern, text, re.IGNORECASE) for pattern in _LIMITED_FREE_CONTEXT_PATTERNS):
        return "", ""
    if _LIMITED_FREE_TRIAL_PATTERN.search(clean_html(description or "")):
        return "", ""
    if admission == AdmissionDefault.SOURCE_CONFIRMED_FREE:
        return "kostenlos", "explicit"
    if _FREE_TITLE_PATTERN.search(clean_html(title or "")):
        return "kostenlos", "inferred"
    if _FREE_DESCRIPTION_BLOCK_PATTERN.search(clean_html_blocks(description or "")):
        return "kostenlos", "inferred"
    if any(re.search(pattern, text, re.IGNORECASE) for pattern in _FREE_ADMISSION_PATTERNS):
        return "kostenlos", "inferred"
    clean_title = clean_html(title or "")
    if (
        _IMPLICIT_FREE_TITLE_PATTERN.search(clean_title)
        and not price_text
        and not _IMPLICIT_FREE_EXCLUSION_PATTERN.search(text)
        and not visitor_charge
        and not seller_fee
    ):
        return "kostenlos", "implicit"
    if admission == AdmissionDefault.FREE_BY_NATURE and not visitor_charge:
        return "kostenlos", "implicit"
    return "", ""


def infer_free_admission_price(
    title: str,
    description: str,
    price: str = "",
    *,
    admission: AdmissionDefault | None = None,
) -> str:
    """Return a normalized free-admission label from explicit or safe implicit evidence."""
    return infer_admission(
        title, description, price, admission=admission,
    )[0]


def _event_time_fields(
    start: datetime | None,
    end: datetime | None,
    time_text: str,
    time_note: str,
    all_day: bool | None,
) -> tuple[str, str, bool]:
    if not time_text and start and (start.hour or start.minute):
        time_text = start.strftime("%H:%M")
        if end and (end.hour or end.minute):
            time_text += "–" + end.strftime("%H:%M")
    canonical_time, inferred_note = normalize_time_fields(time_text)
    if not canonical_time and start and (start.hour or start.minute):
        derived = start.strftime("%H:%M")
        if end and (end.hour or end.minute):
            derived += "–" + end.strftime("%H:%M")
        canonical_time, _ = normalize_time_fields(derived)
    combined_note = combine_time_notes(time_note, inferred_note)
    if all_day is None:
        all_day = not canonical_time and not combined_note and not (
            start and (start.hour or start.minute)
        )
    return canonical_time, combined_note, all_day


def _event_location(city: str, venue: str, coords: tuple | None):
    canonical_venue = resolve_venue(venue, city)
    registry_coords = (
        (canonical_venue.venue_latitude, canonical_venue.venue_longitude)
        if canonical_venue.venue_latitude is not None
        and canonical_venue.venue_longitude is not None
        else None
    )
    resolved, confidence, source = resolve_location(
        city, coords if coords is not None else registry_coords,
    )
    if coords is None and registry_coords is not None:
        source = "venue_registry"
    distance = haversine(BONN_LAT, BONN_LON, *resolved) if resolved else None
    return canonical_venue, distance, confidence, source


def build_event(draft: EventDraft) -> RawEvent | None:
    """Normalize one bundled event draft and apply radius and quality checks.

    ``coords`` optionally pins the event to an explicit (lat, lon) — e.g. a venue
    point — instead of deriving it from ``city`` via :func:`coords_for_city`.
    """
    title, start_dt, end_dt = draft.title, draft.start, draft.end
    venue, city, description = draft.venue, draft.city, draft.description
    link, source, category, trust = draft.link, draft.source, draft.category, draft.trust
    time_text, coords, all_day = draft.time_text, draft.coords, draft.all_day
    timezone_name, source_id = draft.timezone_name, draft.source_id
    source_role, discovered_via, link_kind = (
        draft.source_role, draft.discovered_via, draft.link_kind,
    )
    description_source, admission = draft.description_source, draft.admission
    time_note = draft.time_note
    default_category_key, category_locked = draft.default_category_key, draft.category_locked
    if not title or (start_dt is None and end_dt is not None):
        return None
    title = normalize_event_title(title, start=start_dt, end=end_dt, source=source)
    # Most sources only ever report "Bonn". Resolve the district centrally from
    # the venue so every source benefits instead of each repeating the lookup.
    city = canonicalize_city(city)
    city = refine_bonn_location(city, f"{venue} {city}")
    outside_window = bool(start_dt is not None and not window_contains(start_dt, end_dt))
    _record_parser_candidate(out_of_window=outside_window)
    canonical_venue, km, location_confidence, location_source = _event_location(city, venue, coords)
    date_text = start_dt.strftime("%Y-%m-%d") if start_dt else ""
    ongoing = bool(start_dt and end_dt and start_dt < TODAY <= end_dt)
    time_text, time_note, all_day = _event_time_fields(
        start_dt, end_dt, time_text, time_note, all_day,
    )
    full_text = f"{title} {venue} {city} {description} {category}"
    # URLs encode venue slugs and other implementation detail (for example
    # ``alte-vhs`` in an aggregator concert URL). They are not event content and
    # must not affect the display category.
    canonical_category = category_taxonomy.categorize_event(
        category,
        title,
        description,
        venue=venue,
        source=source,
        source_id=source_id,
        default_category_key=default_category_key,
        category_locked=category_locked,
    )
    event_link = normalize_url(link)
    if is_raw_api_url(event_link):
        event_link = ""
    status = event_status(title, description)
    start_date = start_dt.strftime("%Y-%m-%d") if start_dt else ""
    final_end = end_dt or start_dt
    end_date = final_end.strftime("%Y-%m-%d") if final_end else ""
    local_zone = ZoneInfo(timezone_name)
    start_at = "" if all_day or not start_dt else start_dt.replace(tzinfo=local_zone).isoformat(timespec="minutes")
    end_at = "" if all_day or not end_dt else end_dt.replace(tzinfo=local_zone).isoformat(timespec="minutes")
    price, admission_basis = infer_admission(title, description, admission=admission)
    ev: RawEvent = {
        "title": clean_html(title),
        "date": date_text,
        "time": time_text,
        "time_note": time_note,
        "venue": canonical_venue.venue,
        "venue_id": canonical_venue.venue_id,
        "venue_address": canonical_venue.venue_address,
        "venue_district": canonical_venue.venue_district,
        "venue_type": canonical_venue.venue_type,
        "venue_latitude": canonical_venue.venue_latitude,
        "venue_longitude": canonical_venue.venue_longitude,
        "city": clean_html(city).title(),
        "description": concise_description(description),
        # Every event carries renderable markup. A source that kept the raw
        # HTML overwrites this with the real headings and lists afterwards.
        "description_html": richtext.from_plain_text(concise_description(description)),
        "description_source": description_source or description_source_for(description),
        "price": price,
        "admission_basis": admission_basis,
        "link": event_link,
        "distance_km": round(km, 1) if km is not None else None,
        "location_confidence": location_confidence,
        "location_source": location_source,
        "score": round(distance_score(km, runtime_radius_km()) * category_score(full_text) * trust, 2) if km is not None
                 else round(0.3 * category_score(full_text) * trust, 2),
        "source": source,
        "source_id": source_id,
        "source_role": source_role,
        "discovered_via": list(discovered_via),
        "link_kind": link_kind,
        "status": status,
        "start_at": start_at,
        "end_at": end_at,
        "start_date": start_date,
        "end_date": end_date,
        "all_day": all_day,
        "ongoing": ongoing,
        "timezone": timezone_name,
        "category": category,
        "category_key": canonical_category["key"],
        "category_label": canonical_category["label"],
        "category_confidence": canonical_category.get("confidence", 0),
        "category_reason": canonical_category.get("reason", ""),
    }
    if status == "postponed":
        replacement_dates = [
            candidate for candidate in extract_dates(f"{title} {description}")
            if not start_dt or candidate.date() != start_dt.date()
        ]
        if replacement_dates:
            ev["replacement_start_date"] = replacement_dates[0].strftime("%Y-%m-%d")
    if status in {"cancelled", "postponed"}:
        # Preserve schedule changes as first-class candidates. The runner binds
        # them to the scheduled occurrence after source-authority deduplication.
        result = getattr(_SOURCE_CONTEXT, "result", None)
        if result is not None:
            result.cancelled_events.append(ev)
    decision = evaluate_event_quality(ev)
    if decision.should_drop:
        if not outside_window:
            log_source_quality_skip(source, decision.rule_id)
        return None
    return ev


def make_event(title: str, start_dt: Optional[datetime], end_dt: Optional[datetime],
               venue: str, city: str, description: str, link: str, source: str,
               category: str, trust: float = 1.0, time_text: str = "",
               coords: Optional[tuple] = None, all_day: Optional[bool] = None,
               timezone_name: str = "Europe/Berlin", source_id: str = "",
               description_source: str = "",
               admission: AdmissionDefault | None = None,
               time_note: str = "",
               default_category_key: str = "",
               category_locked: bool = False,
               source_role: str = "primary",
               discovered_via: tuple[str, ...] = (),
               link_kind: str = "") -> RawEvent | None:
    """Compatibility adapter for source modules migrating to :class:`EventDraft`."""
    return build_event(EventDraft(
        title=title, start=start_dt, end=end_dt, venue=venue, city=city,
        description=description, link=link, source=source, category=category,
        trust=trust, time_text=time_text, coords=coords, all_day=all_day,
        timezone_name=timezone_name, source_id=source_id,
        description_source=description_source, admission=admission,
        time_note=time_note, default_category_key=default_category_key,
        category_locked=category_locked, source_role=source_role,
        discovered_via=discovered_via, link_kind=link_kind,
    ))


def _legacy_is_junk_event(ev: dict) -> bool:
    """Compatibility boolean for callers that have not migrated to decisions."""
    return legacy_junk_decision(ev) is not None


def is_junk_event(ev: dict) -> bool:
    """Compatibility wrapper for callers that only need the boolean policy."""
    return evaluate_event_quality(ev).should_drop


# ── JSON-LD (schema.org) ────────────────────────────────────────────

def jsonld_event_items(html: str) -> list[dict[str, Any]]:
    """Extract schema.org Event objects from JSON-LD blobs."""
    items: list[dict[str, Any]] = []

    def walk(obj: Any) -> None:
        if isinstance(obj, list):
            for x in obj:
                walk(x)
        elif isinstance(obj, dict):
            typ = obj.get("@type")
            types = typ if isinstance(typ, list) else [typ]
            if any(t and "Event" in str(t) for t in types):
                items.append(obj)
            for key in ("@graph", "itemListElement", "item"):
                if key in obj:
                    walk(obj[key])

    for m in re.finditer(r"<script[^>]+application/ld\+json[^>]*>(.*?)</script>", html, re.S | re.I):
        raw = m.group(1).strip()
        # Some consent plugins incorrectly label executable JavaScript as
        # application/ld+json. JSON-LD roots must be objects or arrays, so
        # these blocks are not parse failures and should not create warnings.
        if not raw or raw[0] not in "[{":
            continue
        try:
            # Real publisher pages occasionally contain literal newlines or
            # tabs inside JSON strings. Browsers accept these blocks, and the
            # rest of the document remains useful, so parse them permissively.
            walk(json.loads(raw, strict=False))
        except json.JSONDecodeError as exc:
            log_source_error("JSON-LD", exc)
            continue
    return items


def _jsonld_location(loc: Any) -> tuple[str, str]:
    """Return (venue_name, city) from a schema.org location that may be a dict or list."""
    if isinstance(loc, list):
        loc = loc[0] if loc else {}
    if not isinstance(loc, dict):
        return "", ""
    venue = loc.get("name", "") or ""
    address = loc.get("address", {})
    city = ""
    if isinstance(address, dict):
        city = address.get("addressLocality") or ""
    city = re.sub(r"^\d{5}\s+", "", str(city)).strip()
    return venue, city


def _jsonld_schema_token(value: Any, allowed: tuple[str, ...]) -> str:
    """Return a recognized bare or schema.org vocabulary token."""
    raw = str(value or "").strip().rstrip("/")
    if raw in allowed:
        return raw
    parsed = urllib.parse.urlsplit(raw)
    if (
        parsed.scheme in {"http", "https"}
        and parsed.netloc.casefold() in {"schema.org", "www.schema.org"}
    ):
        token = parsed.path.rsplit("/", 1)[-1]
        return token if token in allowed else ""
    return ""


def _jsonld_entity_names(value: Any, *, max_length: int = 500) -> str:
    """Return bounded schema.org person or organization names in source order."""
    candidates = value if isinstance(value, list) else [value]
    names: list[str] = []
    for candidate in candidates:
        if isinstance(candidate, dict):
            entity_type = candidate.get("@type")
            entity_types = entity_type if isinstance(entity_type, list) else [entity_type]
            explicit_types = [value for value in entity_types if value]
            if explicit_types and not any(
                _jsonld_schema_token(value, ("Organization", "Person"))
                for value in explicit_types
            ):
                continue
            candidate = candidate.get("name", "")
        if not isinstance(candidate, str):
            continue
        name = clean_html(candidate).strip()
        if not name or name in names:
            continue
        joined_length = sum(map(len, names)) + 2 * len(names) + len(name)
        if joined_length <= max_length:
            names.append(name)
    return "; ".join(names)


def _apply_jsonld_provenance(
    event: RawEvent, *, organizer: str, admission_price: Optional[str], availability: str,
) -> None:
    """Attach optional source evidence without duplicating occurrence paths."""
    if organizer:
        event["organizer"] = organizer
    if admission_price is not None:
        event["price"] = admission_price
        event["admission_basis"] = "explicit"
    if availability:
        event["availability"] = availability


def _jsonld_schedule_items(schedule: Any) -> list[dict[str, Any]]:
    """Return schema.org Schedule objects as a list, preserving source order."""
    if isinstance(schedule, list):
        return [s for s in schedule if isinstance(s, dict)]
    if isinstance(schedule, dict):
        return [schedule]
    return []


def _jsonld_schedule_dt(schedule: dict, date_key: str, time_key: str = "") -> Optional[datetime]:
    """Parse a Schedule date and optional time into a naive datetime."""
    dt = parse_iso_date(schedule.get(date_key, ""))
    if not dt:
        return None
    time_value = (schedule.get(time_key, "") if time_key else "") or ""
    m = re.match(r"^(\d{1,2}):(\d{2})", str(time_value).strip())
    if m:
        hour, minute = map(int, m.groups())
        dt = dt.replace(hour=hour, minute=minute)
    return dt


def _jsonld_schedule_time_text(schedule: dict) -> str:
    """Return a compact display time from schema.org Schedule start/end times."""
    start = str(schedule.get("startTime", "") or "").strip()
    end = str(schedule.get("endTime", "") or "").strip()
    if start and end:
        return f"{start}–{end}"
    return start or end


def _jsonld_accessible_for_free(value: Any) -> Optional[bool]:
    """Parse schema.org Boolean values without treating arbitrary strings as true."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in {"true", "1"}:
            return True
        if normalized in {"false", "0"}:
            return False
    return None


def _jsonld_offer_price(offers: Any) -> Optional[str]:
    """Return a conservative schema.org Offer price as the legacy display string."""
    if isinstance(offers, dict):
        candidates = [offers]
    elif isinstance(offers, list):
        candidates = [offer for offer in offers if isinstance(offer, dict)]
    else:
        candidates = []
    has_explicitly_free_offer = False
    for offer in candidates:
        amount = offer.get("price")
        if amount in (None, "") or isinstance(amount, (dict, list, bool)):
            continue
        amount_text = clean_html(str(amount)).strip()
        if not amount_text:
            continue
        currency = offer.get("priceCurrency")
        currency_text = (
            "" if isinstance(currency, (dict, list)) else clean_html(str(currency or "")).strip()
        )
        if _FREE_PRICE_PATTERN.fullmatch(amount_text):
            has_explicitly_free_offer = True
            continue
        if re.fullmatch(r"0+(?:[.,]0+)?", amount_text):
            # A bare zero without a currency is what many calendar plugins emit for
            # "no price maintained". Only trust it as free when the source states a
            # currency, otherwise leave it to the remaining offers or text inference.
            if currency_text:
                has_explicitly_free_offer = True
            continue
        return " ".join(part for part in (amount_text, currency_text) if part)
    return "kostenlos" if has_explicitly_free_offer else None


def _jsonld_offer_availability(offers: Any) -> str:
    """Return an explicit schema.org availability, preferring a purchasable tier."""
    candidates = offers if isinstance(offers, list) else [offers]
    recognized: list[str] = []
    allowed = ("InStock", "LimitedAvailability", "PreOrder", "SoldOut")
    for offer in candidates:
        if not isinstance(offer, dict):
            continue
        value = _jsonld_schema_token(offer.get("availability"), allowed)
        if value in allowed:
            recognized.append(value)
    for value in allowed:
        if value in recognized:
            return value
    return ""


def _jsonld_admission_price(item: dict) -> Optional[str]:
    """Resolve structured admission, with the direct free-access flag authoritative."""
    accessible_for_free = _jsonld_accessible_for_free(item.get("isAccessibleForFree"))
    offer_price = _jsonld_offer_price(item.get("offers"))
    if accessible_for_free is True:
        return "kostenlos"
    if accessible_for_free is False:
        return offer_price if offer_price and offer_price != "kostenlos" else "kostenpflichtig"
    return offer_price


_VISIBLE_PAID_ADMISSION_RE = re.compile(
    r"\b(?:eintritt|teilnahme|ticket(?:s)?|kostenbeitrag|teilnahmebeitrag|teilnahmegeb(?:u|ü)hr)"
    r"\s+(?:kostet|kosten|betr(?:a|ä)gt|betragen)\s+"
    r"(?P<amount>\d+(?:[.,]\d{1,2})?)\s*(?P<currency>€|eur\b|euro\b)",
    re.IGNORECASE,
)


def _visible_paid_admission_price(text: str) -> Optional[str]:
    """Return an explicit visitor price from prose, without guessing fees."""
    match = _VISIBLE_PAID_ADMISSION_RE.search(text or "")
    if not match:
        return None
    currency = match.group("currency")
    if currency.casefold() == "eur":
        currency = "Euro"
    return f'{match.group("amount")} {currency}'


def events_from_jsonld(html: str, source: str, default_city: str, category: str,
                       trust: float, default_link: str, source_id: str = "",
                       admission: AdmissionDefault | None = None,
                       default_category_key: str = "",
                       category_locked: bool = False) -> list:
    """Build events from every schema.org Event in a page's JSON-LD."""
    events = []
    for item in jsonld_event_items(html):
        title = item.get("name", "")
        start_dt = parse_iso_date(item.get("startDate", ""))
        end_dt = parse_iso_date(item.get("endDate", "")) or start_dt
        venue, city = _jsonld_location(item.get("location"))
        city = city or default_city
        desc = item.get("description", "")
        link = item.get("url") or default_link
        admission_price = _jsonld_admission_price(item)
        # Calendar plugins often publish a default ``price: 0`` even when the
        # visible event copy names a visitor fee. A narrowly phrased amount in
        # that copy is stronger evidence than this structured placeholder.
        if (
            admission_price == "kostenlos"
            and _jsonld_accessible_for_free(item.get("isAccessibleForFree")) is not True
        ):
            admission_price = _visible_paid_admission_price(desc) or admission_price
        organizer = _jsonld_entity_names(item.get("organizer"))
        availability = _jsonld_offer_availability(item.get("offers"))

        schedules = _jsonld_schedule_items(item.get("eventSchedule"))
        if schedules:
            for schedule in schedules:
                sched_start = _jsonld_schedule_dt(schedule, "startDate", "startTime")
                sched_end = _jsonld_schedule_dt(schedule, "endDate", "endTime") or sched_start
                ev = make_event(
                    title, sched_start, sched_end, venue, city, desc, link, source,
                    category, trust, time_text=_jsonld_schedule_time_text(schedule),
                    source_id=source_id, admission=admission,
                    default_category_key=default_category_key,
                    category_locked=category_locked,
                )
                if ev:
                    _apply_jsonld_provenance(
                        ev,
                        organizer=organizer,
                        admission_price=admission_price,
                        availability=availability,
                    )
                    events.append(ev)
            # Explicit schedule entries are the real appointments. The top-level
            # start/end often describes only a season span, e.g. Rheinauen-Flohmarkt
            # April→October, and must not be emitted as a stale appointment.
            continue

        ev = make_event(
            title, start_dt, end_dt, venue, city, desc, link, source, category, trust,
            source_id=source_id, admission=admission,
            default_category_key=default_category_key,
            category_locked=category_locked,
        )
        if ev:
            _apply_jsonld_provenance(
                ev,
                organizer=organizer,
                admission_price=admission_price,
                availability=availability,
            )
            events.append(ev)
    return events


# ── iCal (RFC 5545) ─────────────────────────────────────────────────
# Many German venues run WordPress + "The Events Calendar" (Tribe), exposing a
# clean .ics feed at ?post_type=tribe_events&ical=1. iCal beats HTML scraping.

def _ical_unfold(text: str) -> str:
    """RFC 5545 line unfolding: CRLF + space/tab continues the previous line."""
    return re.sub(r"\r?\n[ \t]", "", text)


def _ical_unescape(text: str, *, preserve_breaks: bool = False) -> str:
    """Decode RFC 5545 escapes.

    ``\\n`` is the only way an iCal feed can express a paragraph, so DESCRIPTION
    keeps it; a SUMMARY or URL stays on one line.
    """
    break_replacement = "\n" if preserve_breaks else " "
    return (text.replace("\\n", break_replacement).replace("\\N", break_replacement)
                .replace('\\"', '"')
                .replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\")).strip()


def events_from_time_listing(html: str, source: str, default_city: str, category: str,
                             trust: float, base_url: str, min_title: int = 6,
                             max_chars: int = 900, anchor_pattern: Optional[str] = None) -> list:
    """Scrape a server-rendered listing that pairs ``<time datetime="…">`` tags with
    nearby title links — common in TYPO3 ``tx_news`` / municipal calendars that
    expose no iCal or JSON-LD feed. Each ``<time>`` is matched to the closest
    in-document anchor (within ``max_chars``) whose text looks like a real title.

    By default every ``<a>`` is a title candidate, filtered by a denylist of
    navigation labels. Pass ``anchor_pattern`` (a regex capturing href + inner
    text) to scope candidates to a CMS-specific title wrapper instead, e.g.
    ``result-list_object-title…<a href="(…)">(…)</a>``. Fails soft on unexpected
    markup (returns the events it could pair, or []).
    """
    times = [(m.start(), m.group(1)) for m in re.finditer(r'<time[^>]*datetime="([^"]+)"', html)]
    pattern = anchor_pattern or r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>'
    anchors = [(m.start(), m.group(1), clean_html(m.group(2)))
               for m in re.finditer(pattern, html, re.S | re.I)]
    # A scoped title pattern already excludes nav links; only the broad default
    # needs the denylist.
    bad = () if anchor_pattern else (
        "drucken", "session.", "weiterlesen", "mehr ", "mehr:", "details",
        "zum kalender", "veranstaltungsliste", "impressum", "anmelden", "suche")
    events, seen = [], set()
    for tp, dt in times:
        candidate = min(
            (
                (abs(ap - tp), href, title)
                for ap, href, title in anchors
                if abs(ap - tp) < max_chars and len(title) >= min_title
                and not any(marker in title.lower() for marker in bad)
            ),
            default=None,
        )
        if candidate is None:
            continue
        _, href, title = candidate
        key = (title.lower(), dt[:10])
        if key in seen:
            continue
        seen.add(key)
        start = parse_iso_date(dt)
        link = urllib.parse.urljoin(base_url, href)
        ev = make_event(title, start, None, "", default_city, "", link, source, category, trust)
        if ev:
            events.append(ev)
    return events


def events_from_ecmaps_tiles(html: str, source: str, default_city: str, category: str,
                             trust: float, base_url: str) -> list:
    """Parse destination.one / ECMaps tile listings with date, title, and venue.

    Used by regional tourism calendars such as Naturregion Sieg. The markup is
    server-rendered but minified, so this intentionally pairs fields inside the
    tile anchor instead of relying on line structure.
    """
    events, seen = [], set()
    for m in re.finditer(r'<a[^>]+href="(?P<href>[^"]+)"[^>]*class="[^"]*tile__link[^"]*"[^>]*>(?P<body>.*?)</a>',
                         html, re.S | re.I):
        href = m.group("href")
        body = m.group("body")
        if "${" in href or "${" in body:
            continue
        date_m = re.search(r'tile__label-text[^>]*>\s*(.*?)\s*</span>', body, re.S | re.I)
        title_m = re.search(r'header__head[^>]*>\s*(.*?)\s*</p>', body, re.S | re.I)
        venue_m = re.search(r'icontext__text[^>]*>\s*(.*?)\s*</span>', body, re.S | re.I)
        if not (date_m and title_m):
            continue
        title = clean_html(title_m.group(1))
        date_text = clean_html(date_m.group(1))
        venue = clean_html(venue_m.group(1) if venue_m else "")
        start = parse_date(date_text)
        city = guess_city_from_text(venue) or default_city
        key = (title.lower(), start.strftime("%Y-%m-%d") if start else date_text)
        if key in seen:
            continue
        seen.add(key)
        ev = make_event(
            title, start, start, venue, city, "", urllib.parse.urljoin(base_url, href),
            source, category, trust,
        )
        if ev:
            events.append(ev)
    return events


def _wp_event_manager_datetimes(text: str) -> tuple:
    text = re.sub(r"\s+", " ", clean_html(text))
    m = re.search(
        r"(?P<start>\d{1,2}\.\d{1,2}\.20\d{2})(?:\s*@\s*(?P<stime>\d{1,2}:\d{2}))?"
        r"(?:\s*-\s*(?P<end>\d{1,2}\.\d{1,2}\.20\d{2})?\s*@?\s*(?P<etime>\d{1,2}:\d{2})?)?",
        text,
    )
    if not m:
        return None, None, ""
    start = parse_date(m.group("start"))
    end = parse_date(m.group("end") or m.group("start"))
    stime, etime = m.group("stime"), m.group("etime")
    if start and stime:
        hour, minute = map(int, stime.split(":"))
        start = start.replace(hour=hour, minute=minute)
    if end and etime:
        hour, minute = map(int, etime.split(":"))
        end = end.replace(hour=hour, minute=minute)
    time_text = f"{stime}-{etime}" if stime and etime else (stime or "")
    return start, end, time_text


def events_from_wp_event_manager_listing(html: str, source: str, category: str, trust: float) -> list:
    """Parse WP Event Manager list cards, skipping locations outside known towns."""
    events, seen = [], set()
    for m in re.finditer(r'<div class="event_listing\b(?P<body>.*?)</a>', html, re.S | re.I):
        body = m.group("body")
        href_m = re.search(r'<a[^>]+href="([^"]+)"', body, re.S | re.I)
        title_m = re.search(r'wpem-event-title.*?<h3[^>]*>(.*?)</h3>', body, re.S | re.I)
        date_m = re.search(r'wpem-event-date-time.*?<span[^>]*>(.*?)</span>', body, re.S | re.I)
        loc_m = re.search(r'wpem-event-location.*?<span[^>]*>(.*?)</span>', body, re.S | re.I)
        if not (href_m and title_m and date_m and loc_m):
            continue
        title = clean_html(title_m.group(1))
        location = clean_html(loc_m.group(1))
        city = guess_city_from_text(location)
        if not city:
            continue
        start, end, time_text = _wp_event_manager_datetimes(date_m.group(1))
        key = (title.lower(), start.strftime("%Y-%m-%d") if start else "")
        if key in seen:
            continue
        seen.add(key)
        ev = make_event(
            title, start, end, location, city, "", href_m.group(1),
            source, category, trust, time_text=time_text,
        )
        if ev:
            events.append(ev)
    return events


def _ical_content_line(line: str) -> tuple:
    """Split an iCal content line at the first colon outside quoted params."""
    in_quote = False
    for idx, char in enumerate(line):
        if char == '"':
            in_quote = not in_quote
        elif char == ":" and not in_quote:
            return line[:idx], line[idx + 1:]
    return line, ""


def _ical_parse_dt(value: str, property_key: str = "") -> Optional[datetime]:
    v = (value or "").strip()
    is_utc = v.endswith("Z")
    if re.match(r"^\d{8}T\d{6}Z?$", v):
        parsed = datetime.strptime(v[:15], "%Y%m%dT%H%M%S")
    elif re.match(r"^\d{8}T\d{4}Z?$", v):
        parsed = datetime.strptime(v[:13], "%Y%m%dT%H%M")
    else:
        parsed = None
    if parsed is not None:
        if is_utc:
            return parsed.replace(tzinfo=timezone.utc).astimezone(LOCAL_TIMEZONE).replace(tzinfo=None)
        tzid = re.search(r"(?:^|;)TZID=([^;:]+)", property_key, re.IGNORECASE)
        if tzid:
            try:
                return parsed.replace(tzinfo=ZoneInfo(tzid.group(1))).astimezone(LOCAL_TIMEZONE).replace(tzinfo=None)
            except (ValueError, ZoneInfoNotFoundError) as exc:
                log_source_error("iCal timezone", exc)
        return parsed
    if re.match(r"^\d{8}$", v):
        return datetime.strptime(v, "%Y%m%d")
    return parse_iso_date(v)


def _ical_attach_event_page(value: str) -> str:
    """Return a human event-detail page derived from an iCal ATTACH URL.

    Some municipal IONAS feeds put the organizer homepage in ``URL`` but include
    an image attachment whose path lives under the real event detail page, e.g.
    ``.../2026-06-12-jazzig-in-die-ferne-swingen/poster.jpg?cid=...``. The image
    itself is a bad event link; its parent directory is the readable event page.
    """
    raw = _ical_unescape(value or "").strip()
    if not raw.startswith(("http://", "https://")):
        return ""
    parsed = urllib.parse.urlparse(raw)
    path = parsed.path or ""
    if "/kalender/" not in path:
        return ""
    if path.rstrip("/").split("/")[-1].lower().endswith((
        ".jpg", ".jpeg", ".png", ".gif", ".webp", ".pdf", ".ics",
    )):
        path = path.rsplit("/", 1)[0] + "/"
    elif not path.endswith("/"):
        path += "/"
    return urllib.parse.urlunparse((parsed.scheme, parsed.netloc, path, "", "", ""))


def _ical_feed_page(url: str) -> str:
    """Convert an iCal export URL to its human calendar page fallback."""
    parsed = urllib.parse.urlparse(url or "")
    path = parsed.path or ""
    if path.endswith("/event.ics"):
        path = path.rsplit("/", 1)[0] + "/"
        return urllib.parse.urlunparse((parsed.scheme, parsed.netloc, path, "", "", ""))
    return url


def _ical_best_link(props: dict, feed_url: str) -> str:
    """Choose the most useful human URL for an iCal event."""
    attach_page = _ical_attach_event_page(props.get("ATTACH", ""))
    if attach_page:
        return attach_page
    return (props.get("URL", "") or _ical_feed_page(feed_url)).strip()


_ICAL_WEEKDAYS = {name: index for index, name in enumerate(
    ("MO", "TU", "WE", "TH", "FR", "SA", "SU")
)}
_SUPPORTED_RRULE_PARTS = {"FREQ", "INTERVAL", "BYDAY", "UNTIL", "COUNT"}


def _ical_date_list(values: list[tuple[str, str]]) -> list[datetime]:
    parsed = []
    for property_key, raw in values:
        for value in raw.split(","):
            dt = _ical_parse_dt(value, property_key)
            if dt is not None:
                parsed.append(dt)
    return parsed


def _ical_date_only_days(values: list[tuple[str, str]]) -> set[date]:
    """Calendar days named by date-only values (e.g. ``EXDATE;VALUE=DATE``).

    A date-only exclusion carries no clock time, so it must exclude the whole
    day; comparing its midnight parse against timed occurrences never matches.
    """
    return {
        datetime.strptime(value, "%Y%m%d").date()
        for _property_key, raw in values
        for value in (part.strip() for part in raw.split(","))
        if re.match(r"^\d{8}$", value)
    }


def _ical_recurrence_starts(
    start: datetime,
    rrule: str,
    rdates: list[datetime],
    exdates: list[datetime],
    exdate_days: set[date] | None = None,
) -> tuple[list[datetime], str]:
    """Expand a bounded stdlib-only subset of RFC 5545 recurrence rules."""
    excluded_days = exdate_days or set()
    if not rrule:
        starts = [start, *rdates]
        excluded = set(exdates)
        return sorted({
            value for value in starts
            if value not in excluded and value.date() not in excluded_days
        }), ""

    parts = {}
    for raw_part in rrule.split(";"):
        if "=" not in raw_part:
            return [start], f"unsupported RRULE fragment {raw_part!r}"
        key, value = raw_part.split("=", 1)
        parts[key.upper()] = value.upper()
    unsupported = set(parts) - _SUPPORTED_RRULE_PARTS
    if unsupported:
        return [start], f"unsupported RRULE parts: {', '.join(sorted(unsupported))}"

    freq = parts.get("FREQ", "")
    if freq not in {"DAILY", "WEEKLY", "MONTHLY"}:
        return [start], f"unsupported RRULE frequency: {freq or 'missing'}"
    try:
        interval = max(int(parts.get("INTERVAL", "1")), 1)
        count = int(parts["COUNT"]) if "COUNT" in parts else None
    except ValueError:
        return [start], "invalid RRULE INTERVAL or COUNT"
    until = _ical_parse_dt(parts.get("UNTIL", "")) if parts.get("UNTIL") else None
    byday_tokens = [token for token in parts.get("BYDAY", "").split(",") if token]
    if any(token not in _ICAL_WEEKDAYS for token in byday_tokens):
        return [start], "unsupported ordinal or invalid RRULE BYDAY"
    weekdays = {_ICAL_WEEKDAYS[token] for token in byday_tokens}

    window_end = END_DATE.replace(hour=23, minute=59, second=59, microsecond=999999)
    hard_end = min(window_end, until) if until else window_end
    cursor = start
    starts = []
    generated = 0
    iterations = 0
    start_week = start.date() - timedelta(days=start.weekday())
    while cursor <= hard_end and iterations < 100_000:
        iterations += 1
        days_since = (cursor.date() - start.date()).days
        include = False
        if freq == "DAILY":
            include = days_since % interval == 0
        elif freq == "WEEKLY":
            week = (cursor.date() - start_week).days // 7
            allowed_days = weekdays or {start.weekday()}
            include = week % interval == 0 and cursor.weekday() in allowed_days
        else:
            months_since = (cursor.year - start.year) * 12 + cursor.month - start.month
            if months_since % interval == 0:
                include = cursor.weekday() in weekdays if weekdays else cursor.day == start.day
        if include:
            generated += 1
            if cursor >= start and (count is None or generated <= count):
                starts.append(cursor)
            if count is not None and generated >= count:
                break
        cursor += timedelta(days=1)

    starts.extend(rdates)
    excluded = set(exdates)
    return sorted({
        value for value in starts
        if value not in excluded and value.date() not in excluded_days
    }), ""


def fetch_ical(url: str, source: str, default_city: str, category: str = "",
               trust: float = 1.0, source_id: str = "",
               event_filter: Callable[[dict[str, str], datetime, datetime], bool] | None = None,
               city_resolver: Callable[[str], str] | None = None,
               fetcher: Callable[..., str] | None = None,
               admission: AdmissionDefault | None = None,
               default_category_key: str = "",
               category_locked: bool = False,
               empty_calendar_is_valid: bool = False,
               description_max_chars: int | None = None) -> list[RawEvent]:
    """Generic RFC 5545 iCal/.ics fetcher (Tribe Events, webcal, Meetup feeds).

    ``fetcher`` optionally replaces the plain HTTP read with a ``(url, **kwargs) ->
    str`` callable. Sources that must request one small calendar *per event* use it
    to route through the persistent TTL cache, so a repeat run costs no requests.
    """
    read = fetcher or fetch_url
    raw = _ical_unfold(read(
        url,
        timeout=20,
        accept="text/calendar,application/calendar+json;q=0.9,*/*;q=0.8",
        sec_fetch_mode="no-cors",
        sec_fetch_dest="empty",
    ))
    events: list[RawEvent] = []
    blocks = re.findall(r"BEGIN:VEVENT(.*?)END:VEVENT", raw, re.S)
    for block in blocks:
        props: dict[str, str] = {}
        property_keys: dict[str, str] = {}
        multi_props: dict[str, list[tuple[str, str]]] = {}
        for line in block.splitlines():
            if ":" not in line:
                continue
            key, val = _ical_content_line(line)
            if not val:
                continue
            name = key.split(";")[0].strip().upper()
            if name in (
                "SUMMARY", "DTSTART", "DTEND", "DESCRIPTION", "LOCATION", "URL",
                "CATEGORIES", "ATTACH", "RRULE", "RDATE", "EXDATE",
            ):
                props.setdefault(name, val)
                property_keys.setdefault(name, key)
                multi_props.setdefault(name, []).append((key, val))
        if not props.get("SUMMARY"):
            continue
        start_dt = _ical_parse_dt(props.get("DTSTART", ""), property_keys.get("DTSTART", ""))
        raw_end_dt = _ical_parse_dt(
            props.get("DTEND", ""), property_keys.get("DTEND", "")
        ) or start_dt
        all_day = bool(re.match(r"^\d{8}$", props.get("DTSTART", "").strip()))
        if start_dt is None:
            continue
        duration = (raw_end_dt - start_dt) if raw_end_dt else timedelta(0)
        starts, recurrence_warning = _ical_recurrence_starts(
            start_dt,
            props.get("RRULE", ""),
            _ical_date_list(multi_props.get("RDATE", [])),
            _ical_date_list(multi_props.get("EXDATE", [])),
            _ical_date_only_days(multi_props.get("EXDATE", [])),
        )
        if recurrence_warning:
            log_source_error(f"{source} recurrence", ValueError(recurrence_warning))
        # Keep feed-level and event-level signals separate. CATEGORIES describes
        # this VEVENT and is therefore preferred when present; the static hint is
        # only a fallback. Concatenating both can create an artificial broad bag
        # with more than two category intents, which the taxonomy deliberately
        # rejects as untrustworthy.
        event_categories = _ical_unescape(props.get("CATEGORIES", "")).strip()
        cat = event_categories or (category or "").strip()
        for occurrence_start in starts:
            occurrence_end = occurrence_start + duration
            # RFC 5545 all-day DTEND is exclusive. Present the inclusive last day.
            if all_day and duration > timedelta(0):
                occurrence_end -= timedelta(days=1)
            if event_filter and not event_filter(props, occurrence_start, occurrence_end):
                continue
            location = _ical_unescape(props.get("LOCATION", ""))
            city = city_resolver(location) if city_resolver else default_city
            if not city:
                continue
            full_description = _ical_unescape(
                props.get("DESCRIPTION", ""), preserve_breaks=True
            )
            ev = make_event(
                _ical_unescape(props["SUMMARY"]),
                occurrence_start, occurrence_end,
                location,
                city,
                full_description,
                _ical_best_link(props, url),
                source, cat, trust,
                all_day=all_day,
                source_id=source_id,
                admission=admission,
                default_category_key=default_category_key,
                category_locked=category_locked,
            )
            if ev:
                if description_max_chars is not None:
                    ev["description"] = concise_description(
                        full_description, max_chars=description_max_chars
                    )
                    ev["description_html"] = richtext.from_plain_text(ev["description"])
                events.append(ev)
    valid_empty_calendar = bool(
        empty_calendar_is_valid
        and re.search(r"(?mi)^BEGIN:VCALENDAR\s*$", raw)
        and re.search(r"(?mi)^END:VCALENDAR\s*$", raw)
        # A VEVENT marker that produced no block means the component is
        # truncated or unbalanced — that is drift, not an inactive group.
        and not re.search(r"(?mi)^BEGIN:VEVENT", raw)
    )
    _record_endpoint(
        url,
        parser_type="ical",
        candidate_count=len(blocks),
        parsed_event_count=len(events),
        parser_empty=not bool(blocks) and not valid_empty_calendar,
    )
    return events


# ── Web-search helper (shared by Exa + Grok) ────────────────────────

def search_result_event(title: str, link: str, desc: str, source: str, trust: float) -> RawEvent | None:
    """Convert a search result through the same canonical draft pipeline as adapters."""
    full_text = f"{title} {desc} {link}"
    extracted_dates = extract_dates(full_text)
    if not extracted_dates:
        return None
    if not date_range_overlaps(extracted_dates):
        return None
    city_guess = guess_city_from_text(full_text)
    if not city_guess:
        return None
    start = extracted_dates[0]
    return build_event(EventDraft(
        title=unescape(clean_html(title)),
        start=start,
        end=start,
        venue="",
        city=city_guess,
        description=clean_html(desc),
        link=link,
        source=source,
        category="search fallback",
        trust=trust,
        all_day=True,
    ))


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
