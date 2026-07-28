"""Shared text normalization for stable comparison keys."""

from __future__ import annotations

import re
import unicodedata
from typing import Mapping

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


def canonical_venue_id(event: Mapping[str, object]) -> str:
    """Return a stable identity for high-confidence recurring venue aliases.

    Sources describe the same place as a district, plaza collection, street
    address, or colloquial venue name. Keep this deliberately small and
    auditable: explicit source-provided identities win, followed by aliases
    backed by verified recurring event records.
    """
    explicit = str(event.get("venue_id") or "").strip()
    if explicit:
        return explicit

    title = comparison_text(str(event.get("title") or ""), separator="")
    venue = comparison_text(str(event.get("venue") or ""), separator="")
    city = comparison_text(str(event.get("city") or ""), separator="")
    description = comparison_text(
        str(event.get("description") or ""),
        separator="",
    )
    text = f"{title}{venue}{city}{description}"

    # The Rigal'sche Wiese is inside Bad Godesberg, but it is not the generic
    # Innenstadt market area and therefore must be resolved first.
    if (
        (
            city in {"bonn", "bonnbadgodesberg", "badgodesberg"}
            or "badgodesberg" in text
        )
        and (
            "rigal" in text
            or "friedrichebertstrasse32" in text
        )
    ):
        return "rigalsche-wiese-bad-godesberg"

    if (
        "badgodesberg" in text
        and ("antik" in title or "troedelmarkt" in title)
        and any(
            marker in text
            for marker in (
                "badgodesbergerinnenstadt",
                "theaterplatz",
                "amfronhof",
                "michaelshof",
                "fussgaengerzone",
            )
        )
    ):
        return "bad-godesberg-innenstadt"

    if "friedensplatz" in venue and city == "bonn":
        return "friedensplatz-bonn"

    if (
        city == "troisdorf"
        and "hitmarkt" in text
        and any(
            marker in text
            for marker in ("rottersee", "spicherstrasse101", "hitmarkt")
        )
    ):
        return "hit-markt-rotter-see"

    if (
        city == "linzamrhein"
        and "antik" in title
        and "markt" in title
    ):
        return "innenstadt-linz"

    return ""
