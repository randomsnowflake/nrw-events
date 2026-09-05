"""Owning implementation of text; core is a compatibility facade."""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from html import unescape
from typing import Any

from . import run_state as _impl_run_state
from .normalization import resolve_venue


def parse_float(value: Any, default: float = 0.0) -> float:
    """Parse source-provided numeric values, accepting German decimal commas."""
    if value is None or value == "":
        return default
    try:
        return float(str(value).strip().replace(",", "."))
    except (TypeError, ValueError):
        return default


def extract_json_array(text: str) -> list:
    """Best-effort parse of a JSON array from LLM/search output."""
    if not text:
        return []
    candidates = [text]
    candidates.extend(m.group(1) for m in re.finditer(r"```(?:json)?\s*(.*?)```", text, re.S | re.I))
    arr_match = re.search(r"\[[\s\S]*\]", text)
    if arr_match:
        candidates.append(arr_match.group(0))
    last_error = None
    for candidate in candidates:
        try:
            parsed = json.loads(candidate.strip())
            return parsed if isinstance(parsed, list) else []
        except json.JSONDecodeError as exc:  # noqa: PERF203 - candidates are independent JSON envelopes
            last_error = exc
            continue
    if last_error is not None:
        _impl_run_state.log_source_error("Search JSON response", last_error)
    return []


def clean_html(text: str) -> str:
    """Strip tags/entities and collapse whitespace."""
    text = text or ""
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.S)
    text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", text, flags=re.S | re.I)
    text = re.sub(r"</?[A-Za-z][^>]*>", " ", text)
    text = unescape(text)
    return re.sub(r"\s+", " ", text).strip()


_BLOCK_TAG_PATTERN = re.compile(
    r"</?(?:p|div|section|article|header|footer|ul|ol|dl|table|"
    r"h[1-6]|blockquote|figure|figcaption|pre|hr)\b[^>]*>",
    re.I,
)


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
    text = text or ""
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.S)
    text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", text, flags=re.S | re.I)
    text = _LINE_BREAK_TAG_PATTERN.sub("\n", text)
    text = _LIST_ITEM_CLOSE_PATTERN.sub("", text)
    text = _LIST_ITEM_OPEN_PATTERN.sub("\n", text)
    text = _BLOCK_TAG_PATTERN.sub("\n\n", text)
    text = re.sub(r"</?[A-Za-z][^>]*>", " ", text)
    text = unescape(text)
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
    return bool(path in {"", "/"} and query and any(bit in query for bit in ("format=json", "output=json", "type=json", "eventid=")))


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
    limit = _impl_run_state._runtime_state().settings.description_max_chars if max_chars is None else max_chars
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
