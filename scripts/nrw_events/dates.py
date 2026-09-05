"""Pure source-independent date parsing utilities."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo

from .runtime import ACTIVE_RUNTIME

LOCAL_TIMEZONE = ZoneInfo("Europe/Berlin")
_REFERENCE_DATE: datetime | None = None
YEARLESS_GRACE_DAYS = 30


@dataclass(frozen=True, slots=True)
class YearlessDateResolution:
    """A resolved day/month plus provenance for the inferred year choice."""

    value: datetime
    basis: str


def _local_naive(value: datetime) -> datetime:
    """Normalize aware source timestamps to naive Europe/Berlin datetimes."""
    if value.tzinfo is not None:
        value = value.astimezone(LOCAL_TIMEZONE).replace(tzinfo=None)
    return value


def configure_reference_date(value: datetime) -> None:
    """Set the report-window date used for yearless source values."""
    global _REFERENCE_DATE
    _REFERENCE_DATE = _local_naive(value)


def runtime_reference_date() -> datetime:
    state = ACTIVE_RUNTIME.get()
    if state is not None and state.window is not None:
        return state.window.start
    return _REFERENCE_DATE or datetime.now(LOCAL_TIMEZONE)


MONTH_DE = {
    "januar": 1, "jan": 1, "februar": 2, "feb": 2, "märz": 3, "maerz": 3,
    "mär": 3, "april": 4, "apr": 4, "mai": 5, "juni": 6, "jun": 6,
    "juli": 7, "jul": 7, "august": 8, "aug": 8, "september": 9, "sep": 9,
    "oktober": 10, "okt": 10, "november": 11, "nov": 11, "dezember": 12, "dez": 12,
}
MONTH_EN = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12,
}
MONTH_ALIASES = {"mar": 3, "sept": 9, "oct": 10, "dec": 12}
MONTH_ALL = {**MONTH_DE, **MONTH_EN, **MONTH_ALIASES}
DATE_FORMATS = (
    (r"^\d{4}-\d{2}-\d{2}", "%Y-%m-%d"),
    (r"^\d{1,2}\.\d{1,2}\.\d{4}", "%d.%m.%Y"),
    (r"^\d{1,2}\.\d{1,2}\.\d{2}", "%d.%m.%y"),
    (
        r"^[A-Za-z]{3}, \d{1,2} [A-Za-z]{3} \d{4} \d{2}:\d{2}:\d{2} [+-]\d{4}",
        "%a, %d %b %Y %H:%M:%S %z",
    ),
)


def parse_iso_date(text: str) -> datetime | None:
    if not text:
        return None
    try:
        return _local_naive(datetime.fromisoformat(text.replace("Z", "+00:00")))
    except ValueError:
        try:
            return datetime.strptime(text[:10], "%Y-%m-%d")
        except ValueError:
            return None


def resolve_yearless_date(
    day: int,
    month: int,
    reference_date: datetime,
    *,
    grace_days: int = YEARLESS_GRACE_DAYS,
) -> YearlessDateResolution | None:
    """Resolve a day/month near the report date and record why its year won."""
    reference_date = _local_naive(reference_date)
    lower_bound = reference_date - timedelta(days=grace_days)
    for year in range(reference_date.year - 1, reference_date.year + 9):
        try:
            candidate = datetime(year, month, day)
        except ValueError:
            continue
        if candidate.date() >= lower_bound.date():
            basis = "grace-window" if candidate.date() < reference_date.date() else "upcoming"
            return YearlessDateResolution(candidate, basis)
    return None


def _range_start(text: str) -> str:
    """Return a range's start while inheriting missing month/year context."""
    parts = re.split(r"\s*(?:[–—]|\bbis(?:\s+zum)?\b|\s-\s)\s*", text, maxsplit=1, flags=re.I)
    if len(parts) == 1:
        return text
    start, end = (part.strip() for part in parts)
    year_match = re.search(r"\b(20\d{2})\b", end)
    year = int(year_match.group(1)) if year_match else None
    numeric_end_month = re.search(r"\b\d{1,2}\.(\d{1,2})\.?(?:20\d{2})?\b", end)
    word_end_month = re.search(r"\b([A-Za-zäöüÄÖÜ]+)\b", end)
    end_month = int(numeric_end_month.group(1)) if numeric_end_month else None
    if end_month is None and word_end_month:
        key = word_end_month.group(1).lower().rstrip(".")
        end_month = MONTH_ALL.get(key)

    def inherited_year(start_month: int | None) -> str:
        if year is None:
            return ""
        return str(year - 1 if start_month and end_month and start_month > end_month else year)

    if re.fullmatch(r"\d{1,2}\.\d{1,2}\.?", start) and year:
        start_month_number = int(start.split(".")[1])
        separator = "" if start.endswith(".") else "."
        return f"{start}{separator}{inherited_year(start_month_number)}"
    if re.fullmatch(r"\d{1,2}\.?", start) and end_month:
        day = start.rstrip(".")
        suffix = inherited_year(end_month)
        return f"{day}.{end_month:02d}.{suffix}".rstrip(".")
    if year and not re.search(r"\b20\d{2}\b", start):
        word_start_month = re.search(r"\b([A-Za-zäöüÄÖÜ]+)\b", start)
        start_month = None
        if word_start_month:
            key = word_start_month.group(1).lower().rstrip(".")
            start_month = MONTH_ALL.get(key)
        return f"{start} {inherited_year(start_month)}"
    return start


def _next_yearless_occurrence(day: int, month: int, reference_date: datetime) -> datetime | None:
    """Compatibility wrapper returning only the resolved calendar occurrence."""
    resolution = resolve_yearless_date(day, month, reference_date)
    return resolution.value if resolution else None


def parse_date(text: str, *, reference_date: datetime | None = None) -> datetime | None:
    """Parse common ISO, numeric, English, and German event dates."""
    text = (text or "").strip()
    if not text:
        return None
    text = re.sub(
        r"^(?:montag|dienstag|mittwoch|donnerstag|freitag|samstag|sonntag|mo|di|mi|do|fr|sa|so)\b\.?,?\s*",
        "", text, flags=re.I,
    )
    text = _range_start(text)
    if "," in text:
        try:
            parsed = parsedate_to_datetime(text)
            if parsed is not None:
                return _local_naive(parsed)
        except (TypeError, ValueError, OverflowError):
            pass
    try:
        return _local_naive(datetime.fromisoformat(text.replace("Z", "+00:00")))
    except (ValueError, TypeError):
        pass
    for pattern, fmt in DATE_FORMATS:
        match = re.match(pattern, text)
        if not match:
            continue
        try:
            return _local_naive(datetime.strptime(match.group(0), fmt))
        except ValueError:
            continue
    match = re.match(r"(\d{1,2})\.(\d{1,2})\.(20\d{2})", text)
    if match:
        try:
            day, month, year = map(int, match.groups())
            return datetime(year, month, day)
        except ValueError:
            return None
    match = re.match(r"^(\d{1,2})\.(\d{1,2})\.?(?!\d)", text)
    if match:
        reference = reference_date or runtime_reference_date()
        return _next_yearless_occurrence(
            int(match.group(1)), int(match.group(2)), reference,
        )
    match = re.search(r"(\d{1,2})\.?\s+([A-Za-zäöüÄÖÜ]+)\b\.?\s*(20\d{2})", text)
    if match:
        day_text, month_text, year_text = match.groups()
        key = month_text.lower().rstrip(".")
        month_number = MONTH_ALL.get(key)
        if month_number:
            return datetime(int(year_text), month_number, int(day_text))
    match = re.search(r"(\d{1,2})\.?\s+([A-Za-zäöüÄÖÜ]+)\b\.?", text)
    if match:
        day_text, month_text = match.groups()
        key = month_text.lower().rstrip(".")
        month_number = MONTH_ALL.get(key)
        if month_number:
            reference = reference_date or runtime_reference_date()
            return _next_yearless_occurrence(int(day_text), month_number, reference)
    return None
