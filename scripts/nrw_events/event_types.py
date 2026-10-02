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

# A title is explicit evidence. A seasonal source description can also establish
# the programme; spooky words alone or an artist's past gigs cannot.
_HALLOWEEN_TITLE = re.compile(r"(?<!\w)\w*halloween\w*(?!\w)", re.IGNORECASE)
_HALLOWEEN_PROGRAMME = re.compile(
    r"\bhalloween\s*(?:party\w*|parties|disco\w*|dragshow\w*|workshop\w*"
    r"|action|stimmung|motive\w*|familie\w*)\b"
    r"|\b(?:bald|passend\s+an|einstimmung\s+auf)\s+halloween\b"
    r"|\b(?:wissen|fragen|quiz|raetsel)\b.{0,40}\b(?:ueber|zu)\s+halloween\b"
    r"|\bhalloween\s+feiern\b"
)
_HALLOWEEN_INCIDENTAL = re.compile(
    r"\b(?:kein\w*|nicht|ohne|statt|vorjahr|letztes\s+jahr|vergangenen\s+jahr"
    r"|bekannt\s+fuer|frueher|normalerweise)\b.{0,100}\bhalloween"
)


def _description_is_halloween_programme(event: Mapping[str, Any]) -> bool:
    if event.get("description_source") == "generated":
        return False
    start = str(event.get("start_date") or event.get("date") or "")[:10]
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", start) or not "09-15" <= start[5:] <= "11-02":
        return False
    for sentence in re.split(r"[.!?\n]+", str(event.get("description") or "")):
        text = re.sub(r"[-\u2010-\u2015]", " ", comparison_text(sentence))
        if _HALLOWEEN_INCIDENTAL.search(text):
            continue
        if any(year != start[:4] for year in re.findall(r"\b20\d{2}\b", text)):
            continue
        if _HALLOWEEN_PROGRAMME.search(text):
            return True
    return False


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
    if _HALLOWEEN_TITLE.search(title) or _description_is_halloween_programme(event):
        event_types.add("halloween")

    return sorted(event_types)
