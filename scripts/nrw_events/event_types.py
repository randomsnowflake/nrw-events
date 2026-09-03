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

EVENT_TYPES = frozenset({"funfair"})

# Kirmes compounds end in "kirmes" (Herbstkirmes, Rochuskirmes). Rummel may
# likewise be a compound or occur as Rummelplatz. Do not match Kirmesabend or a
# market whose category is not festival.
_FUNFAIR_TITLE = re.compile(
    r"(?<!\w)(?:\w*kirmes|\w*rummel(?:platz)?|kerb)(?!\w)",
    re.IGNORECASE,
)


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

    return sorted(event_types)
