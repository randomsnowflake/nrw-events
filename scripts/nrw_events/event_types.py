"""Small, public event-type taxonomy for cross-category topic pages.

Categories answer what the primary programme is. Event types are additive:
one occurrence may belong to a named series and also to topic collections such
as funfairs or Christmas markets. Keep inference deliberately narrow because a
false topic assignment is more visible than an unknown type.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from .category_taxonomy import comparison_text

EVENT_TYPES = frozenset({"funfair", "christmas-market", "halloween"})

# Kirmes compounds end in "kirmes" (Herbstkirmes, Rochuskirmes). Rummel may
# likewise be a compound or occur as Rummelplatz. Do not match Kirmesabend or a
# market whose category is not festival.
_FUNFAIR_TITLE = re.compile(
    r"(?<!\w)(?:\w*kirmes|\w*rummel(?:platz)?|kerb)(?!\w)",
    re.IGNORECASE,
)

# Only the market nouns themselves. A Weihnachtskonzert is not a market, and
# "Weihnachtsmarkt-Konzert" in a concert category stays a concert.
_CHRISTMAS_MARKET_TITLE = re.compile(
    r"(?<!\w)(?:\w*weihnachtsmarkt|\w*weihnachtsmaerkte|\w*adventsmarkt"
    r"|christkindl(?:es)?markt|nikolausmarkt)(?!\w)",
    re.IGNORECASE,
)
_CHRISTMAS_MARKET_CATEGORIES = frozenset({"market", "festival"})

# Halloween names itself. Gruselnacht, Kuerbisfest and the like stay out until a
# source proves the occasion instead of the mood. "St. Martin" is a church name
# before it is a lantern parade, so no season word infers a type on its own.
_HALLOWEEN_TITLE = re.compile(r"(?<!\w)\w*halloween\w*(?!\w)", re.IGNORECASE)


def classify_event_types(event: Mapping[str, Any]) -> list[str]:
    """Return validated explicit types plus conservative shared inference."""
    raw_types = event.get("event_types") or []
    if not isinstance(raw_types, list | tuple):
        raise ValueError("event_types_type")

    event_types: set[str] = set()
    for value in raw_types:
        if not isinstance(value, str) or value not in EVENT_TYPES:
            raise ValueError("event_types_item_invalid")
        event_types.add(value)

    source_id = str(event.get("source_id") or "").casefold()
    title = comparison_text(
        " ".join(
            str(event.get(field) or "")
            for field in ("title", "series_title")
        )
    )
    if source_id == "bonnkirmes" or (
        event.get("category_key") == "festival" and _FUNFAIR_TITLE.search(title)
    ):
        event_types.add("funfair")
    if event.get(
        "category_key"
    ) in _CHRISTMAS_MARKET_CATEGORIES and _CHRISTMAS_MARKET_TITLE.search(title):
        event_types.add("christmas-market")
    if _HALLOWEEN_TITLE.search(title):
        event_types.add("halloween")

    return sorted(event_types)
