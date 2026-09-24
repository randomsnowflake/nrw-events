"""Owning implementation of detail cache; core is a compatibility facade."""

from __future__ import annotations

import base64
import gzip
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
from collections.abc import Callable
from contextlib import suppress
from hashlib import sha256
from pathlib import Path
from typing import Any, TypedDict

from . import botgate as _botgate
from . import http as _impl_http
from . import performance
from . import run_state as _impl_run_state

_DETAIL_PAGE_CACHE_VERSION = 1


_DETAIL_PAGE_CACHE_DEFAULT_MAX_ENTRIES = 5000


_DETAIL_PAGE_CACHE_DEFAULT_MAX_BYTES = 25 * 1024 * 1024


_DETAIL_PAGE_CACHE_MAX_BYTES_BY_NAMESPACE = {
    "bonn-detail": 50 * 1024 * 1024,
}


_DETAIL_PAGE_CACHE_LOCK = threading.RLock()


class _DetailCacheEntryRequired(TypedDict):
    fetched_at: float
    accessed_at: float
    body: str


class DetailCacheEntry(_DetailCacheEntryRequired, total=False):
    # Disk form of ``body`` (base64 gzip); kept in memory once computed so a
    # size-capped persist never recompresses unchanged pages.
    body_gz: str
    # A remembered 4xx refusal: re-raised from cache instead of re-requested.
    failed_status: int


class CachedDetailFailureError(RuntimeError):
    """A detail page recently refused with a permanent 4xx; not re-requested."""


class DetailHostRefusedError(RuntimeError):
    """A host refused several detail requests this run; skip the rest politely."""


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


def _env_hours(name: str, default: float) -> float:
    try:
        return max(float(os.environ.get(name, str(default))), 0) * 60 * 60
    except (TypeError, ValueError):
        return default * 60 * 60


# Detail pages are fetched once. Listings, feeds and APIs carry the dates,
# times and removals and are read on every import; a detail page only adds
# description, admission and venue facts, which rarely change. A page stays
# cached for up to 60 days while events keep referencing it, and is dropped
# after 14 days without use (the event is gone). Accepted trade-off: a later
# edit on an organizer's detail page is not picked up within that window.
def _detail_page_cache_ttl_seconds() -> float:
    return _env_hours("NRW_EVENTS_DETAIL_CACHE_TTL_HOURS", 60 * 24)


def _detail_page_cache_idle_seconds() -> float:
    return _env_hours("NRW_EVENTS_DETAIL_CACHE_IDLE_HOURS", 14 * 24)


def _detail_failure_ttl_seconds() -> float:
    return _env_hours("NRW_EVENTS_DETAIL_FAILURE_CACHE_HOURS", 7 * 24)


# Permanent client errors worth remembering. 408/425/429 are transient.
_REMEMBERED_FAILURE_STATUSES = frozenset({400, 401, 403, 404, 405, 406, 410, 451})
# Statuses meaning "this host does not want our detail requests right now".
_HOST_REFUSAL_STATUSES = frozenset({401, 403, 429})
_HOST_REFUSAL_LIMIT = 3
_HOST_REFUSALS: dict[str, int] = {}
_HOST_REFUSALS_LOCK = threading.Lock()


def _refusal_host(url: str) -> str:
    return _botgate.gated_bucket(url) or (urllib.parse.urlsplit(url).hostname or "").lower()


def _compress_body(body: str) -> str:
    return base64.b64encode(gzip.compress(body.encode("utf-8"), compresslevel=6)).decode("ascii")


def _decompress_body(value: str) -> str:
    return gzip.decompress(base64.b64decode(value.encode("ascii"))).decode("utf-8")


def _disk_entry(entry: DetailCacheEntry) -> dict[str, Any]:
    disk: dict[str, Any] = {"fetched_at": entry["fetched_at"], "accessed_at": entry["accessed_at"]}
    if "failed_status" in entry:
        disk["failed_status"] = entry["failed_status"]
    if entry["body"]:
        if "body_gz" not in entry:
            entry["body_gz"] = _compress_body(entry["body"])
        disk["body_gz"] = entry["body_gz"]
    else:
        disk["body"] = ""
    return disk


def _entry_expired(entry: DetailCacheEntry, *, ttl_seconds: float, now: float) -> bool:
    max_age = _detail_failure_ttl_seconds() if "failed_status" in entry else ttl_seconds
    if now - entry["fetched_at"] > max_age:
        return True
    idle = _detail_page_cache_idle_seconds()
    return bool(idle) and now - entry["accessed_at"] > idle


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
    entries: dict[str, Any],
    *,
    namespace: str,
    ttl_seconds: float,
    now: float,
) -> dict[str, DetailCacheEntry]:
    """Drop expired entries, then retain the newest entries within both caps."""
    valid: list[tuple[str, DetailCacheEntry]] = []
    for url, entry in entries.items():
        if not isinstance(url, str) or not isinstance(entry, dict):
            continue
        try:
            fetched_at = float(entry.get("fetched_at", 0))
            accessed_at = float(entry.get("accessed_at", fetched_at))
        except (TypeError, ValueError):
            continue
        body = entry.get("body")
        body_gz = entry.get("body_gz")
        if not isinstance(body, str):
            if not isinstance(body_gz, str):
                continue
            try:
                body = _decompress_body(body_gz)
            except (ValueError, OSError, EOFError, UnicodeDecodeError):
                continue
        normalized: DetailCacheEntry = {
            "fetched_at": fetched_at,
            "accessed_at": accessed_at,
            "body": body,
        }
        if isinstance(body_gz, str):
            normalized["body_gz"] = body_gz
        failed_status = entry.get("failed_status")
        if isinstance(failed_status, int) and not isinstance(failed_status, bool):
            normalized["failed_status"] = failed_status
        if _entry_expired(normalized, ttl_seconds=ttl_seconds, now=now):
            continue
        valid.append((url, normalized))

    max_entries = _detail_page_cache_limit(
        "NRW_EVENTS_DETAIL_CACHE_MAX_ENTRIES", _DETAIL_PAGE_CACHE_DEFAULT_MAX_ENTRIES,
    )
    max_bytes = _detail_page_cache_max_bytes(namespace)
    valid.sort(key=lambda item: (item[1]["accessed_at"], item[1]["fetched_at"], item[0]), reverse=True)
    empty_payload_size = len(json.dumps(
        {"version": _DETAIL_PAGE_CACHE_VERSION, "namespace": namespace, "entries": {}},
        ensure_ascii=False, separators=(",", ":"),
    ).encode("utf-8"))
    retained: dict[str, DetailCacheEntry] = {}
    serialized_size = empty_payload_size
    for url, entry in valid:
        if len(retained) >= max_entries:
            break
        fragment_size = len(json.dumps(
            {url: _disk_entry(entry)}, ensure_ascii=False, separators=(",", ":"),
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
            with _HOST_REFUSALS_LOCK:
                _HOST_REFUSALS.clear()
        else:
            _DETAIL_PAGE_CACHE_STATES.pop(_detail_page_cache_slug(namespace), None)


@performance.measured("detail_cache.load")
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
        raw_entries = payload.get("entries") or {}
        if not isinstance(raw_entries, dict):
            raw_entries = {}
        entries = _prune_detail_page_cache_entries(
            raw_entries, namespace=slug,
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
                    "entries": {url: _disk_entry(entry) for url, entry in entries.items()},
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
        _impl_run_state.log_source_error(f"{state['namespace']} detail cache", exc)
        with suppress(OSError):
            temporary.unlink(missing_ok=True)
        return {
            "source": "detail-cache",
            "error_type": type(exc).__name__,
            "error": f"failed to persist {state['namespace']} detail cache: {exc}",
        }


@performance.measured("detail_cache.flush")
def flush_detail_page_caches(namespace: str | None = None) -> list[dict[str, str]]:
    """Persist dirty cache namespaces once at a source-run boundary."""
    warnings: list[dict[str, str]] = []
    with _DETAIL_PAGE_CACHE_LOCK:
        slug = _detail_page_cache_slug(namespace) if namespace else None
        for key, state in list(_DETAIL_PAGE_CACHE_STATES.items()):
            if (
                state.get("dirty")
                and (slug is None or key == slug)
                and (warning := _persist_detail_page_cache(state))
            ):
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
    retry_attempts: int | None = None,
    **fetch_kwargs: Any,
) -> str:
    """Fetch a public event detail page through the persistent TTL cache.

    Successful responses are cached by default and effectively fetched once
    (see ``_detail_page_cache_ttl_seconds``). Permanent 4xx refusals are
    remembered for a week and re-raised as ``CachedDetailFailureError``; after
    three 401/403/429 answers from one host the rest of the run's detail
    requests to it raise ``DetailHostRefusedError`` without a request. Sources
    that must enforce a strict request ceiling can set ``cache_failures=True``;
    any failed attempt is then represented by an empty cached body instead. Set
    ``retry_attempts`` controls transport behavior without changing the cached
    representation's identity. Set ``NRW_EVENTS_DETAIL_CACHE_TTL_HOURS=0`` to
    bypass both memory and disk.
    """
    if brightdata and brightdata_fallback:
        raise ValueError("brightdata and brightdata_fallback are mutually exclusive")
    if brightdata:
        fetcher: Callable[..., str] = _impl_http.fetch_url_with_brightdata
        transport = "brightdata"
    elif brightdata_fallback:
        fetcher = _impl_http.fetch_url_with_brightdata_fallback
        transport = "direct-with-brightdata-fallback"
    else:
        fetcher = _impl_http.fetch_url
        transport = "direct"
    transport_kwargs = dict(fetch_kwargs)
    if retry_attempts is not None:
        transport_kwargs["retry_attempts"] = retry_attempts
    ttl_seconds = _detail_page_cache_ttl_seconds()
    if not ttl_seconds:
        performance.count("detail_cache_bypasses")
        return fetcher(url, timeout=timeout, **transport_kwargs)
    host = _refusal_host(url)
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
        now = time.time()
        if cached is not None and not _entry_expired(cached, ttl_seconds=ttl_seconds, now=now):
            performance.count("detail_cache_hits")
            # Access bumps keep still-referenced pages from idle expiry. Persist
            # them at most daily so a fully cached run rarely rewrites files.
            if now - cached["accessed_at"] > 24 * 60 * 60:
                state["dirty"] = True
            cached["accessed_at"] = now
            if "failed_status" in cached:
                raise CachedDetailFailureError(
                    f"HTTP {cached['failed_status']} remembered for {url}; not re-requested"
                )
            return cached["body"]
        state["entries"].pop(cache_key, None)

    with _HOST_REFUSALS_LOCK:
        refused = _HOST_REFUSALS.get(host, 0) >= _HOST_REFUSAL_LIMIT
    if refused:
        performance.count("detail_host_refusal_skips")
        raise DetailHostRefusedError(f"{host} refused {_HOST_REFUSAL_LIMIT} detail requests this run")

    performance.count("detail_cache_misses")
    try:
        body = fetcher(url, timeout=timeout, **transport_kwargs)
    except Exception as exc:
        status = exc.code if isinstance(exc, urllib.error.HTTPError) else None
        if status in _HOST_REFUSAL_STATUSES:
            with _HOST_REFUSALS_LOCK:
                _HOST_REFUSALS[host] = _HOST_REFUSALS.get(host, 0) + 1
        if status in _REMEMBERED_FAILURE_STATUSES and not cache_failures:
            with _DETAIL_PAGE_CACHE_LOCK:
                state = _load_detail_page_cache(cache_namespace, ttl_seconds)
                failed_at = time.time()
                state["entries"][cache_key] = {
                    "fetched_at": failed_at, "accessed_at": failed_at, "body": "",
                    "failed_status": int(status),
                }
                state["dirty"] = True
        if cache_failures:
            with _DETAIL_PAGE_CACHE_LOCK:
                state = _load_detail_page_cache(cache_namespace, ttl_seconds)
                fetched_at = time.time()
                state["entries"][cache_key] = {
                    "fetched_at": fetched_at, "accessed_at": fetched_at, "body": "",
                }
                state["dirty"] = True
        raise
    with _HOST_REFUSALS_LOCK:
        _HOST_REFUSALS.pop(host, None)
    with _DETAIL_PAGE_CACHE_LOCK:
        state = _load_detail_page_cache(cache_namespace, ttl_seconds)
        fetched_at = time.time()
        state["entries"][cache_key] = {
            "fetched_at": fetched_at, "accessed_at": fetched_at, "body": body,
        }
        state["dirty"] = True
    return body
