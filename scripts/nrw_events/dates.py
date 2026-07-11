"""Pure source-independent date parsing utilities."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Optional

MONTH_DE = {
    "januar": 1, "jan": 1, "februar": 2, "feb": 2, "märz": 3, "maerz": 3,
    "mär": 3, "mae": 3, "april": 4, "apr": 4, "mai": 5, "juni": 6, "jun": 6,
    "juli": 7, "jul": 7, "august": 8, "aug": 8, "september": 9, "sep": 9,
    "oktober": 10, "okt": 10, "november": 11, "nov": 11, "dezember": 12, "dez": 12,
}
MONTH_EN = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12,
}


def parse_iso_date(text: str) -> Optional[datetime]:
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        try:
            return datetime.strptime(text[:10], "%Y-%m-%d")
        except ValueError:
            return None


def parse_date(text: str) -> Optional[datetime]:
    """Parse common ISO, numeric, English, and German event dates."""
    text = (text or "").strip()
    if not text:
        return None
    text = re.split(r"\s*(?:–|\bbis\b)\s*", text, maxsplit=1)[0].strip()
    text = re.sub(r"^(?:mo|di|mi|do|fr|sa|so|montag|dienstag|mittwoch|donnerstag|freitag|samstag|sonntag)\.?,?\s*", "", text, flags=re.I)
    for fmt in ["%Y-%m-%d", "%d.%m.%Y", "%d.%m.%y", "%a, %d %b %Y %H:%M:%S %z"]:
        try:
            return datetime.strptime(text[:len(fmt) + 5], fmt).replace(tzinfo=None)
        except (ValueError, IndexError):
            continue
    match = re.search(r"(\d{1,2})\.(\d{1,2})\.(20\d{2})", text)
    if match:
        try:
            day, month, year = map(int, match.groups())
            return datetime(year, month, day)
        except ValueError:
            return None
    match = re.search(r"(\d{1,2})\.?\s+([A-Za-zäöüÄÖÜ]+)\s*(20\d{2})", text)
    if match:
        day, month, year = match.groups()
        key = month.lower().rstrip(".")
        month_number = MONTH_DE.get(key) or MONTH_EN.get(key) or {
            "mar": 3, "sept": 9, "oct": 10, "dec": 12,
        }.get(key)
        if month_number:
            return datetime(int(year), month_number, int(day))
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).replace(tzinfo=None)
    except (ValueError, TypeError):
        return None

