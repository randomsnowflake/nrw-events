"""Owning implementation of detail cache; core is a compatibility facade."""

from __future__ import annotations

import json
import os
import re
import threading
import time
from collections.abc import Callable
from contextlib import suppress
from hashlib import sha256
from pathlib import Path
from typing import Any, TypedDict

from . import http as _impl_http
from . import performance
from . import run_state as _impl_run_state

_DETAIL_PAGE_CACHE_VERSION = 1


_DETAIL_PAGE_CACHE_DEFAULT_MAX_ENTRIES = 500


_DETAIL_PAGE_CACHE_DEFAULT_MAX_BYTES = 25 * 1024 * 1024


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
    retained: dict[str, DetailCacheEntry] = {}
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
            performance.count("detail_cache_hits")
            # Access-only LRU bump: kept in memory only, so a fully cached run
            # never rewrites multi-MB namespace files. The bump is persisted
            # alongside the next insertion in this namespace, which is the only
            # time LRU precision matters (eviction happens during persist).
            cached["accessed_at"] = time.time()
            return cached["body"]
        state["entries"].pop(cache_key, None)

    performance.count("detail_cache_misses")
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
