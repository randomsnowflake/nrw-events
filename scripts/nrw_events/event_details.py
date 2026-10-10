"""Visitor facts that a source publishes as labelled data.

Adapters fill ``event["details"]`` only from explicit source fields or labelled
page elements, never from prose or AI. This module is the canonical boundary:
unknown keys, wrong types and unsafe URLs are dropped, empty values omitted.
"""

from __future__ import annotations

import urllib.parse
from typing import Any

from . import text as _text

PERFORMANCE_NOTES = frozenset({"premiere", "revival", "final"})
_TEXT_LIMITS = {"age": 80, "language": 200}
_LIST_LIMITS = {"performers": (20, 160), "programme": (20, 160)}
_URL_KEYS = ("video_url",)


def _clean(value: Any, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    cleaned = " ".join(_text.clean_html(value).split())
    return cleaned if len(cleaned) <= limit else ""


def _public_url(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    url = value.strip()
    try:
        parsed = urllib.parse.urlsplit(url)
    except ValueError:  # e.g. "https://[invalid": drop the field, keep the event
        return ""
    return url if parsed.scheme == "https" and parsed.netloc and len(url) <= 2048 else ""


def canonical_details(value: Any) -> dict[str, Any]:
    """Return the publishable subset of an adapter's ``details`` record."""
    if not isinstance(value, dict):
        return {}
    result: dict[str, Any] = {}
    performance = value.get("performance")
    if isinstance(performance, str) and performance in PERFORMANCE_NOTES:
        result["performance"] = performance
    for key, limit in _TEXT_LIMITS.items():
        if cleaned := _clean(value.get(key), limit):
            result[key] = cleaned
    for key, (max_items, limit) in _LIST_LIMITS.items():
        raw = value.get(key)
        if not isinstance(raw, list):
            continue
        items = list(dict.fromkeys(item for item in (_clean(entry, limit) for entry in raw) if item))
        if items:
            result[key] = items[:max_items]
    for key in _URL_KEYS:
        if url := _public_url(value.get(key)):
            result[key] = url
    return result
