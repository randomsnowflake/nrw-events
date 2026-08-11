"""Narrow policy for verified events announced beyond the planner window."""

from __future__ import annotations

from datetime import timedelta
from typing import Mapping
from urllib.parse import urlsplit, urlunsplit

from . import common
from .normalization import comparison_text


PUETZCHENS_MARKT_SOURCE_ID = "tourismus-nrw-puetzchens-markt"
PUETZCHENS_MARKT_URL = "https://www.nrw-tourismus.de/events/puetzchens-markt"
MAX_EARLY_DAYS = 370


def _url_key(value: object) -> str:
    parsed = urlsplit(str(value or "").strip())
    host = (parsed.hostname or "").casefold().removeprefix("www.")
    path = parsed.path.rstrip("/") or "/"
    return urlunsplit((parsed.scheme.casefold(), host, path, "", ""))


def is_eligible(event: Mapping[str, object]) -> bool:
    """Return whether an outside-window record may become an early detail page.

    The adapter flag is necessary but never sufficient. The canonical source,
    exact public URL, title and a bounded future date must all agree so another
    source cannot widen the site's 28-day contract by setting one boolean.
    """
    if event.get("early_publication") is not True:
        return False
    if str(event.get("source_id") or "").casefold() != PUETZCHENS_MARKT_SOURCE_ID:
        return False
    if _url_key(event.get("link")) != _url_key(PUETZCHENS_MARKT_URL):
        return False
    if comparison_text(str(event.get("title") or "")) != "puetzchens markt":
        return False

    start = common.parse_iso_date(str(event.get("start_date") or event.get("date") or ""))
    end = common.parse_iso_date(str(event.get("end_date") or "")) or start
    if not start or not end or end < common.TODAY:
        return False
    if common.window_contains(start, end):
        return False
    return start <= common.TODAY + timedelta(days=MAX_EARLY_DAYS)
