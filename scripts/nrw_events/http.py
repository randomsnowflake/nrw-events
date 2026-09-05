"""Owning implementation of http; core is a compatibility facade."""

from __future__ import annotations

import inspect
import json
import os
import random
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator
from contextlib import closing, contextmanager
from typing import Any, NoReturn

from . import performance
from . import run_state as _impl_run_state
from .observability import redact
from .runtime import ACTIVE_RUNTIME as _RUNTIME_STATE

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


_BRIGHT_DATA_API_URL = "https://api.brightdata.com/request"


_HOST_FETCH_LOCK = threading.Lock()


_HOST_LAST_FETCH_AT: dict[str, float] = {}


_HOST_SLOT_LOCK = threading.Lock()


_HOST_SLOTS: dict[str, threading.Lock] = {}


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
    cancel_event = getattr(_impl_run_state._SOURCE_CONTEXT, "cancel_event", None)
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
        if not isinstance(chunk, bytes | bytearray):
            raise TypeError("HTTP response body reader returned non-bytes data")
        body.extend(chunk)
        if len(body) > max_bytes > 0:
            raise ResponseTooLargeError(f"response exceeds {max_bytes} bytes")
    _response_read_timeout(deadline)
    missing_bytes = getattr(response, "length", None)
    if isinstance(missing_bytes, int) and missing_bytes > 0:
        raise ConnectionError(f"response truncated, {missing_bytes} bytes missing")
    return bytes(body)


class UnexpectedContentTypeError(ValueError):
    pass


def _record_endpoint(url: str, **details: Any) -> None:
    result = getattr(_impl_run_state._SOURCE_CONTEXT, "result", None)
    if result is not None:
        result.endpoint(redact(url), **details)
    status = details.get("status")
    timeout_seconds = getattr(_impl_run_state._SOURCE_CONTEXT, "timeout_seconds", None)
    if isinstance(status, int) and 200 <= status < 400 and timeout_seconds is not None:
        hard_deadline = getattr(_impl_run_state._SOURCE_CONTEXT, "hard_deadline", None)
        renewed_deadline = time.perf_counter() + timeout_seconds
        _impl_run_state._SOURCE_CONTEXT.deadline = min(renewed_deadline, hard_deadline) if hard_deadline else renewed_deadline


def _throttle_bucket(url: str) -> tuple[str, float] | tuple[None, float]:
    hostname = (urllib.parse.urlsplit(url).hostname or "").lower()
    state = _RUNTIME_STATE.get()
    delays = (
        {"bonn.de": state.settings.bonn_de_delay_seconds}
        if state is not None else _impl_run_state._HOST_THROTTLE_SECONDS_BY_SUFFIX
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
    cancel_event = getattr(_impl_run_state._SOURCE_CONTEXT, "cancel_event", None)
    if cancel_event is not None and cancel_event.is_set():
        raise TimeoutError("source wall-clock budget exhausted")
    deadline = time.perf_counter() + _impl_run_state._runtime_state().settings.http_request_budget_seconds
    source_deadline = getattr(_impl_run_state._SOURCE_CONTEXT, "deadline", None)
    hard_deadline = getattr(_impl_run_state._SOURCE_CONTEXT, "hard_deadline", None)
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
    with performance.span("http.host_slot_wait"):
        if not slot.acquire(timeout=wait):
            raise TimeoutError(f"timed out waiting for request slot on {hostname}")
    try:
        with performance.span("http.throttle_wait"):
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
    settings = _impl_run_state._runtime_state().settings
    base = settings.http_retry_base_seconds
    jitter = random.SystemRandom().uniform(0, base / 2) if base else 0.0
    return min(base * (2 ** attempt_index) + jitter, settings.http_retry_max_delay_seconds)


def _is_retryable_fetch_error(exc: Exception) -> bool:
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code in _TRANSIENT_HTTP_STATUSES
    return isinstance(exc, urllib.error.URLError | TimeoutError | ConnectionError)


def _close_http_error(exc: Exception) -> None:
    """Close urllib's response-shaped HTTPError on both retry and raise paths."""
    if isinstance(exc, urllib.error.HTTPError):
        exc.close()


def browser_headers(
    *,
    accept: str,
    sec_fetch_mode: str,
    sec_fetch_dest: str,
    extra: dict | None = None,
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


@performance.measured("http.fetch_including_slot_and_retries")
def fetch_url(
    url: str,
    timeout: int = 15,
    headers: dict | None = None,
    accept: str = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    sec_fetch_mode: str = "navigate",
    sec_fetch_dest: str = "document",
    expected_content_types: tuple | None = None,
    retry_attempts: int | None = None,
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
    settings = _impl_run_state._runtime_state().settings
    attempts = (
        settings.http_retry_attempts
        if retry_attempts is None
        else max(int(retry_attempts), 1)
    )
    for attempt in range(attempts):
        performance.count("http_attempts")
        try:
            started = time.perf_counter()
            req = urllib.request.Request(url, headers=hdrs)
            with _host_request_slot(url, deadline), closing(
                urllib.request.urlopen(req, timeout=_remaining_timeout(deadline, timeout))
            ) as resp:
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
                    performance.count("http_bytes", len(body))
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
                encoding = "utf-8-sig" if not charset or charset.casefold() == "utf-8" else charset
                return body.decode(encoding)
            except (UnicodeDecodeError, LookupError):
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


def fetch_json(url: str, timeout: int = 15, headers: dict | None = None) -> Any:
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
    settings = _impl_run_state._runtime_state().settings
    try:
        with _host_request_slot(_BRIGHT_DATA_API_URL, deadline), closing(urllib.request.urlopen(
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

    if not isinstance(target_status, int | str):
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
        cancel_event = getattr(_impl_run_state._SOURCE_CONTEXT, "cancel_event", None)
        if cancel_event is not None and cancel_event.is_set():
            raise
        # A proxy fallback is a fresh network request. Never begin it after the
        # source's hard deadline merely because the watchdog has not set its
        # cooperative cancellation flag yet.
        hard_deadline = getattr(_impl_run_state._SOURCE_CONTEXT, "hard_deadline", None)
        _remaining_timeout(
            hard_deadline if hard_deadline is not None else _request_deadline(),
            timeout,
        )
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


def post_json(url: str, payload: dict[str, Any], timeout: int = 45,
              headers: dict[str, str] | None = None,
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
    settings = _impl_run_state._runtime_state().settings
    attempts = settings.http_retry_attempts if retry_safe else 1
    deadline = _request_deadline()
    for attempt in range(attempts):
        try:
            started = time.perf_counter()
            with _host_request_slot(url, deadline), closing(
                urllib.request.urlopen(req, timeout=_remaining_timeout(deadline, timeout))
            ) as resp:
                    body = _read_response_body(
                        resp, settings.http_max_response_bytes, deadline=deadline,
                    )
                    _record_endpoint(url, status=getattr(resp, "status", 200), content_type="application/json",
                                     bytes=len(body), duration_ms=round((time.perf_counter() - started) * 1000))
            return json.loads(body.decode("utf-8"))
        except Exception as exc:  # noqa: PERF203 - retry attempts must isolate transport failures
            _record_endpoint(url, error_type=type(exc).__name__, error=redact(exc))
            retry = attempt < attempts - 1 and _is_retryable_fetch_error(exc)
            delay = _retry_delay(exc, attempt) if retry else 0
            _close_http_error(exc)
            if not retry:
                raise
            _sleep_for_retry(delay, deadline)
    raise RuntimeError("post_json retry loop exhausted unexpectedly")  # pragma: no cover


def post_form(url: str, fields: Any, timeout: int = 45,
              headers: dict[str, str] | None = None,
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
    settings = _impl_run_state._runtime_state().settings
    attempts = settings.http_retry_attempts if retry_safe else 1
    deadline = _request_deadline()
    for attempt in range(attempts):
        try:
            started = time.perf_counter()
            with _host_request_slot(url, deadline), closing(
                urllib.request.urlopen(req, timeout=_remaining_timeout(deadline, timeout))
            ) as resp:
                    body = _read_response_body(
                        resp, settings.http_max_response_bytes, deadline=deadline,
                    )
                    _record_endpoint(url, status=getattr(resp, "status", 200), content_type="application/json",
                                     bytes=len(body), duration_ms=round((time.perf_counter() - started) * 1000))
            return json.loads(body.decode("utf-8"))
        except Exception as exc:  # noqa: PERF203 - retry attempts must isolate transport failures
            _record_endpoint(url, error_type=type(exc).__name__, error=redact(exc))
            retry = attempt < attempts - 1 and _is_retryable_fetch_error(exc)
            delay = _retry_delay(exc, attempt) if retry else 0
            _close_http_error(exc)
            if not retry:
                raise
            _sleep_for_retry(delay, deadline)
    raise RuntimeError("post_form retry loop exhausted unexpectedly")  # pragma: no cover
