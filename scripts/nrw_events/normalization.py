"""Shared text normalization for stable comparison keys."""

from __future__ import annotations

import re
import unicodedata

_GERMAN_TRANSLITERATION = str.maketrans({
    "ä": "ae",
    "ö": "oe",
    "ü": "ue",
    "ß": "ss",
})


def comparison_text(value: str, *, separator: str = " ") -> str:
    """Casefold and transliterate text into a punctuation-insensitive key."""
    folded = (value or "").casefold().translate(_GERMAN_TRANSLITERATION)
    ascii_text = unicodedata.normalize("NFKD", folded).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", separator, ascii_text).strip(separator)
