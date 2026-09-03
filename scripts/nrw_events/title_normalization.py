"""Conservative, source-aware normalization for visitor-facing event titles."""

from __future__ import annotations

import re
from datetime import datetime

_DATE_TOKEN = re.compile(r"(?<!\d)(\d{1,2})[.\-/](\d{1,2})[.\-/](\d{2}|20\d{2})(?!\d)")
_DATE_SUFFIX = re.compile(
    r"\s*(?:[,;|]|\s[-–—]\s)\s*(?:(?:am|vom)\s+)?"
    r"\d{1,2}[.\-/]\d{1,2}[.\-/](?:\d{2}|20\d{2})"
    r"(?:\s*(?:bis|[-–—])\s*\d{1,2}[.\-/]\d{1,2}[.\-/](?:\d{2}|20\d{2}))?"
    r"\s*$",
    re.IGNORECASE,
)
_INCOMPLETE_WORD_ENDING = re.compile(r"\b(?:u|un|und)\s*$", re.IGNORECASE)
_ELLIPSIS_ENDING = re.compile(r"(?:\.{3}|…)\s*$")
_SMALL_GERMAN_WORDS = frozenset({
    "am", "an", "auf", "aus", "bei", "bis", "das", "der", "des", "die", "ein",
    "eine", "einer", "eines", "für", "fuer", "im", "in", "mit", "nach", "oder",
    "ohne", "und", "vom", "von", "vor", "zu", "zum", "zur",
})
_KNOWN_ACRONYMS = frozenset({"AI", "ARD", "DJ", "KI", "LVR", "NRW", "VHS", "WDR", "ZDF"})


def _date_value(match: re.Match[str]) -> str:
    day, month, year = match.groups()
    year = f"20{year}" if len(year) == 2 else year
    return f"{year}-{int(month):02d}-{int(day):02d}"


def _strip_redundant_date_suffix(title: str, start: datetime | None, end: datetime | None) -> str:
    if not start:
        return title
    suffix = _DATE_SUFFIX.search(title)
    if not suffix:
        return title
    dates = {_date_value(match) for match in _DATE_TOKEN.finditer(suffix.group(0))}
    structured = {start.strftime("%Y-%m-%d")}
    if end:
        structured.add(end.strftime("%Y-%m-%d"))
    if not dates or not dates.issubset(structured) or start.strftime("%Y-%m-%d") not in dates:
        return title
    return title[:suffix.start()].rstrip(" ,;|–—-")


def _title_case_word(word: str, *, first: bool) -> str:
    bare = word.strip("()[]{}\"'„“”‚‘’«».,:;!?")
    if not bare:
        return word
    if bare in _KNOWN_ACRONYMS or any(char.isdigit() for char in bare) or "." in bare:
        return word
    lowered = bare.casefold()
    replacement = lowered if not first and lowered in _SMALL_GERMAN_WORDS else lowered[:1].upper() + lowered[1:]
    start = word.find(bare)
    return word[:start] + replacement + word[start + len(bare):]


def _normalize_all_caps(title: str) -> str:
    letters = [char for char in title if char.isalpha()]
    if len(letters) < 6 or not all(char.isupper() for char in letters):
        return title
    words = title.split()
    return " ".join(_title_case_word(word, first=index == 0) for index, word in enumerate(words))


def normalize_event_title(
    title: str,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
    source: str = "",
) -> str:
    """Return a clean title without duplicating structured event metadata."""
    normalized = re.sub(r"\s+", " ", title or "").strip()
    if source.casefold() == "bundeskunsthalle":
        # This source once separated decorative initial spans from the rest of
        # a word. Keep the repair source-bound so ordinary phrases stay intact.
        normalized = re.sub(r"\b([A-ZÄÖÜ])\s+([a-zäöüß]{2,})\b", r"\1\2", normalized)
    normalized = _strip_redundant_date_suffix(normalized, start, end)
    normalized = _normalize_all_caps(normalized)
    return normalized


def title_looks_truncated(title: str, *, source: str = "") -> bool:
    """Detect source teaser endings that should be inspected, not invented."""
    normalized = re.sub(r"\s+", " ", title or "").strip()
    if len(normalized) < 24:
        return False
    if _INCOMPLETE_WORD_ENDING.search(normalized):
        return True
    return source.casefold() == "marktcom" and bool(_ELLIPSIS_ENDING.search(normalized))
