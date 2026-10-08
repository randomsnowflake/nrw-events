"""Conservative, source-aware normalization for visitor-facing event titles."""

from __future__ import annotations

import re
from datetime import datetime

from .normalization import comparison_text

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
    "ohne", "und", "vom", "von", "vor", "zu", "zum", "zur", "dem", "den", "ins", "beim",
})
_KNOWN_ACRONYMS = frozenset({
    "AI", "ARD", "DJ", "FLINTA", "KI", "LGBTQ", "LVR", "NRW", "UNESCO", "UNICEF", "VHS", "WDR", "ZDF",
})


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


_INLINE_DATE = re.compile(
    r"(?P<lead>\s*(?:[,;|]|\s[-–—])?\s*)(?:\b(?:am|vom)\s+)?"
    r"(?P<date>(?<!\d)\d{1,2}\.\d{1,2}\.(?:\d{2}|20\d{2})(?!\d))"
    r"(?P<trail>\s*(?::|[-–—](?=\s))?\s*)",
    re.IGNORECASE,
)


def _strip_redundant_inline_dates(title: str, start: datetime | None, end: datetime | None) -> str:
    """Remove a date inside the title when it only repeats the structured date.

    "Quintetto Encuentro am 12.09.2026 im Löhrerhof" becomes "Quintetto
    Encuentro im Löhrerhof". A different date ("verlegt auf 28.10.2027") is
    information and stays.
    """
    if not start:
        return title
    structured = {start.strftime("%Y-%m-%d")} | ({end.strftime("%Y-%m-%d")} if end else set())

    def replace(match: re.Match[str]) -> str:
        token = _DATE_TOKEN.fullmatch(match.group("date"))
        if not token or _date_value(token) not in structured:
            return match.group(0)
        separated = match.group("lead").strip() and match.group("trail").strip()
        return " – " if separated else " "

    replaced = _INLINE_DATE.sub(replace, title)
    if replaced == title:
        return title
    cleaned = re.sub(r"\s+", " ", replaced).strip(" ,;|–—-:")
    return cleaned or title


_STATUS_WORDS = (
    r"abgesagt|entfällt|entfaellt|fällt\s+(?:leider\s+)?aus|faellt\s+(?:leider\s+)?aus|"
    r"verlegt|verschoben|ausgefallen"
)
_LEADING_STATUS = re.compile(rf"^\s*[-–—:(\[]*\s*(?:{_STATUS_WORDS})\b\s*[)\]!]*\s*[-–—:]*\s*", re.IGNORECASE)
_TRAILING_STATUS = re.compile(rf"\s*[-–—:(\[]*\s*\b(?:{_STATUS_WORDS})\s*[)\]!]*\s*$", re.IGNORECASE)
_TRAILING_RESCHEDULE_CLAUSE = re.compile(
    r"\s+[-–—]\s+[^-–—]*\b(?:verlegt|verschoben)\b.*$", re.IGNORECASE,
)


def strip_status_markers(title: str, status: str) -> str:
    """Drop schedule-status words once ``status`` carries them.

    Sources announce a cancellation by rewriting the title ("ABGESAGT: …",
    "… entfällt"); the badge already says so, and the clean title keeps the
    public identity stable across the change.
    """
    if status not in {"cancelled", "postponed"}:
        return title
    cleaned = _LEADING_STATUS.sub("", title)
    cleaned = _TRAILING_STATUS.sub("", cleaned)
    if status == "postponed":
        cleaned = _TRAILING_RESCHEDULE_CLAUSE.sub("", cleaned)
    cleaned = cleaned.strip(" ,;|–—-:")
    return cleaned if len(cleaned) >= 3 else title


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


_LETTER_RUN = re.compile(r"[^\W\d_]+")
_ROMAN_NUMERAL = re.compile(r"X{0,3}(?:IX|IV|VI{0,3}|I{1,3})")


def _keeps_capitals(token: str) -> bool:
    return token in _KNOWN_ACRONYMS or bool(_ROMAN_NUMERAL.fullmatch(token))


def _soften_shouted_runs(title: str) -> str:
    """Title-case uppercase stretches inside an otherwise mixed-case title.

    "NightWash Live - COMEDY AT ITS BEST" and "MACBETH - William Shakespeare"
    shout a name or tagline. Adjacent uppercase words form one run; a run is
    softened only when it holds a word of five or more letters or at least two
    words with seven letters together, so short acronyms (ADFC, ZWAR, REWE)
    standing next to ordinary words stay untouched.
    """
    runs: list[list[re.Match[str]]] = []
    previous_end = -1
    for match in _LETTER_RUN.finditer(title):
        word = match.group()
        if not word.isupper():
            previous_end = -1
            continue
        gap = title[previous_end:match.start()] if previous_end >= 0 else "x"
        if runs and previous_end >= 0 and not any(char.isalnum() for char in gap):
            runs[-1].append(match)
        else:
            runs.append([match])
        previous_end = match.end()
    pieces: list[str] = []
    cursor = 0
    for run in runs:
        shouted = [match.group() for match in run if not _keeps_capitals(match.group())]
        if not any(len(word) >= 5 for word in shouted) and (
            len(shouted) < 2 or sum(len(word) for word in shouted) < 7
        ):
            continue
        for position, match in enumerate(run):
            word = match.group()
            if _keeps_capitals(word):
                continue
            lowered = word.casefold()
            replacement = (
                lowered if position and lowered in _SMALL_GERMAN_WORDS
                else lowered[:1].upper() + lowered[1:]
            )
            pieces.extend((title[cursor:match.start()], replacement))
            cursor = match.end()
    return "".join(pieces) + title[cursor:] if pieces else title


def _normalize_all_lowercase(title: str) -> str:
    """Give a multi-word title without any capital letter a capital start.

    A lone lowercase word may be a stylized name ("zeitguised") and a
    mixed-case start ("dein Mädelsflohmarkt") is a deliberate brand.
    """
    if any(char.isupper() for char in title) or len(title.split()) < 2:
        return title
    return title[:1].upper() + title[1:]


_TRAILING_QUOTED_SUBTITLE = re.compile(r"(?:\s+[-–—])?\s+(?:«([^«»]+)»|>>\s*([^<>«»]+))$")


def _normalize_quoted_subtitle(title: str) -> str:
    """Render a trailing «…» or unclosed ">>…" subtitle as a plain dash segment.

    One programme writes the same festival suffix three ways ("A - «F - TV»",
    "B «F - TV»", "C - >>F - TV"); every night now reads "A – F – TV".
    """
    match = _TRAILING_QUOTED_SUBTITLE.search(title)
    if not match or not title[:match.start()].strip():
        return title
    subtitle = re.sub(r"\s+[-–—]\s+", " – ", (match.group(1) or match.group(2)).strip())
    return f"{title[:match.start()].rstrip()} – {subtitle}"


def _strip_redundant_place(title: str, venue: str, city: str) -> str:
    """Drop a ", <venue>" suffix or "<city>, " prefix that repeats structured fields.

    The remainder must still be a title of its own: at least two words, and a
    capitalized start after a removed city ("Bonn, ich …" keeps its sense).
    """
    venue_key = comparison_text(venue)
    head, separator, tail = title.rpartition(", ")
    if separator and venue_key and comparison_text(tail) == venue_key and len(head.split()) >= 2:
        title = head.rstrip()
    city_keys = {comparison_text(city), comparison_text(city.split("-", 1)[0])} - {""}
    prefix, separator, rest = title.partition(", ")
    if separator and comparison_text(prefix) in city_keys and len(rest.split()) >= 2 and rest[:1].isupper():
        title = rest.lstrip()
    return title


def normalize_event_title(
    title: str,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
    source: str = "",
    venue: str = "",
    city: str = "",
) -> str:
    """Return a clean title without duplicating structured event metadata."""
    normalized = re.sub(r"\s+", " ", title or "").strip()
    if source.casefold() == "bundeskunsthalle":
        # This source once separated decorative initial spans from the rest of
        # a word. Keep the repair source-bound so ordinary phrases stay intact.
        normalized = re.sub(r"\b([A-ZÄÖÜ])\s+([a-zäöüß]{2,})\b", r"\1\2", normalized)
    normalized = _strip_redundant_place(normalized, venue, city)
    normalized = _strip_redundant_date_suffix(normalized, start, end)
    normalized = _strip_redundant_inline_dates(normalized, start, end)
    normalized = _normalize_quoted_subtitle(normalized)
    normalized = _normalize_all_caps(normalized)
    normalized = _soften_shouted_runs(normalized)
    normalized = _normalize_all_lowercase(normalized)
    return normalized


def title_looks_truncated(title: str, *, source: str = "") -> bool:
    """Detect source teaser endings that should be inspected, not invented."""
    normalized = re.sub(r"\s+", " ", title or "").strip()
    if len(normalized) < 24:
        return False
    if _INCOMPLETE_WORD_ENDING.search(normalized):
        return True
    return source.casefold() == "marktcom" and bool(_ELLIPSIS_ENDING.search(normalized))
