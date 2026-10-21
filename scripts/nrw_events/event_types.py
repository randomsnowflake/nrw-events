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

EVENT_TYPES = frozenset({"carnival", "christmas-market", "funfair", "halloween", "st-martin"})

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
    r"|abend(?:e|en|s)?|action|stimmung|atmosphaere(?:n)?|motive\w*|familie\w*)\b"
    r"|\b(?:bald|passend\s+an|einstimmung\s+auf)\s+halloween\b"
    r"|\b(?:wissen|fragen|quiz|raetsel)\b.{0,40}\b(?:ueber|zu)\s+halloween\b"
    r"|\bhalloween\s+feiern\b"
    r"|\bhalloween\s*monster\w*\b.{0,60}\bbastel\w*\b"
    r"|\bbastel\w*\b.{0,60}\bhalloween\s*monster\w*\b"
)
_HALLOWEEN_INCIDENTAL = re.compile(
    r"\b(?:kein\w*|nicht|ohne|statt|vorjahr|letztes\s+jahr|vergangenen\s+jahr"
    r"|bekannt\s+fuer|frueher|normalerweise)\b.{0,100}\bhalloween"
)


# Titles are matched after dashes became spaces, as on the website, so
# "Sessions-Start" and "St. Martin-Umzug" read like their spaced spellings.
_DASHES = re.compile(r"[-\u2010-\u2015]")

# Carnival names itself through its season, its sessions and its royalty. Only
# carnival sitting compounds count: a Ratssitzung is not a Prunksitzung, and
# "Der kleine Prinz" is no Prinzenpaar. Saint-Saens' "Karneval der Tiere" and
# a "Karneval der Kulturen" are figurative and say nothing about the season.
_CARNIVAL_TITLE = re.compile(
    r"(?<!\w)(?:\w*karneval\w*|\w*fastnacht\w*|\w*fasching\w*|fastelovend\w*"
    r"|rosenmontag\w*|veilchendienstag\w*"
    r"|\w*proklamation\w*"
    r"|sessions?\s?eroef+nung\w*|sessions\s?(?:start|auftakt)"
    r"|(?:prunk|damen|herren|kostuem|kneipen|maedchen|maedels|fremden|kinder|karnevals"
    r"|narren|jecken|pyjama|gala|sing|mitmach)\s?sitzung\w*"
    r"|veedels?\s?zoe?ch\w*|\w*prinzenpaar\w*|\w*dreigestirn\w*|prinzenempfang\w*"
    r"|waescherprinzess\w*|gardetanz\w*|garde\s?(?:und|&)\s?showtanz\w*)(?!\w)",
)
_FIGURATIVE_CARNIVAL = re.compile(r"\bkarneval\s+der\s+(?:tiere|kulturen)\b")
_CARNIVAL_SOURCES = frozenset({"karneval-in-bonn"})

# St. Martin is also the patron of many churches ("Konzert in St. Martin",
# "Pfarrei St. Martin"), so the name alone needs the children's category. The
# parade, its fire, its lanterns and its songs name the custom by themselves.
# Martinsmarkt, Martinsgans and Martini markets are seasonal trade, not the custom.
_ST_MARTIN_TITLE = re.compile(
    r"(?<!\w)(?:martins?\s?(?:zug|zuege|umzug|umzuege)|martins\s?(?:feuer\w*|fest|feier\w*)"
    r"|martinslied\w*|martinslatern\w*|laternen\s?(?:umzug|umzuege))(?!\w)",
)
_ST_MARTIN_NAME = re.compile(r"(?<!\w)(?:sankt|st\.?)\s?martin(?!\w)")
_ST_MARTIN_SOURCES = frozenset({"bonn-martinszuege"})


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
    spaced_title = re.sub(r"\s+", " ", _DASHES.sub(" ", title))
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
    if source_id in _CARNIVAL_SOURCES or _CARNIVAL_TITLE.search(
        _FIGURATIVE_CARNIVAL.sub(" ", spaced_title)
    ):
        event_types.add("carnival")
    if (
        source_id in _ST_MARTIN_SOURCES
        or _ST_MARTIN_TITLE.search(spaced_title)
        or (event.get("category_key") == "kids" and _ST_MARTIN_NAME.search(spaced_title))
    ):
        event_types.add("st-martin")

    return sorted(event_types)
