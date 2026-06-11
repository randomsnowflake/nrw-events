"""Canonical event category taxonomy and keyword classifier.

The scraper still preserves the source-provided ``category`` text, but it also
emits a stable ``category_key``/``category_label`` pair so downstream sites do
not have to duplicate category rules in TypeScript.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, TypedDict


class Category(TypedDict):
    key: str
    label: str


@dataclass(frozen=True)
class Keyword:
    value: str
    title_only: bool = False
    word: bool = False


@dataclass(frozen=True)
class Rule:
    key: str
    priority: int
    keywords: tuple[str | Keyword, ...]


CATEGORIES: list[Category] = [
    {"key": "concert", "label": "Konzert"},
    {"key": "nightlife", "label": "Nachtleben & Party"},
    {"key": "stage", "label": "Theater & Bühne"},
    {"key": "cinema", "label": "Kino & Film"},
    {"key": "exhibition", "label": "Ausstellung"},
    {"key": "festival", "label": "Feste & Stadtleben"},
    {"key": "market", "label": "Märkte & Flohmarkt"},
    {"key": "food", "label": "Food & Genuss"},
    {"key": "outdoor", "label": "Führungen & Outdoor"},
    {"key": "sports", "label": "Sport & Bewegung"},
    {"key": "talk", "label": "Vorträge & Lesungen"},
    {"key": "workshop", "label": "Workshop & Kurse"},
    {"key": "kids", "label": "Familie & Kinder"},
    {"key": "other", "label": "Sonstiges"},
]

CATEGORY_BY_KEY = {category["key"]: category for category in CATEGORIES}


def word(value: str) -> Keyword:
    return Keyword(value=value, word=True)


def title_only(value: str) -> Keyword:
    return Keyword(value=value, title_only=True)


RULES: tuple[Rule, ...] = (
    Rule(
        "kids",
        13,
        (
            "kinder", "kinderbuch", "kinderbücher", "kids", "familie", "family", "jugend",
            "mitmach", "märchen", "maerchen", "puppentheater", "kasper", "vorlesen",
            "bambini", "krabbel", "ferienprogramm", "lego", "zauberwürfel", "zauberwuerfel",
            "storytime", "dino",
        ),
    ),
    Rule("workshop", 12, ("workshop", "werkstatt", "kurs", "seminar", "training", "repair", "sprechstunde", "weiterbildung", "vhs", "basteln", "keramik")),
    Rule("talk", 11, ("lesung", "lesekreis", "vorlesung", "vortrag", "lecture", "diskussion", "tagung", "kongress", "symposium", "podium", "bildung", "informationsveranstaltung", "chatgpt", "canva", "digital", "3d-druck", "digi:snack", "cloud tech", "azure", "gespräch", "gespraech", "politik", word("talk"), title_only("meetup"))),
    Rule("sports", 10, ("sport", "lauf", "rennen", "marathon", "yoga", "fitness", "tanzen", "tanzkurs", "radtour", "fahrrad", "rennrad", "stadtradeln", "radeln", "klettern", "schwimmen")),
    Rule("cinema", 9, ("kino", "film", "movie", "cinema", "open-air kino", "open air kino", "filmabend", "screening")),
    Rule("concert", 8, ("konzert", "concert", "musik", "music", "songkick", "jazz", "orchester", "sinfonie", "symphon", "klavier", "recital", "dirigent", "flöte", "floete", word("chor"), word("band"), word("live"))),
    Rule("nightlife", 7, ("techno", "electronic", "elektro", "party", "club", "dj", "nightlife", "rave", "disco", "beats", "lounge")),
    Rule("stage", 6, ("theater", "bühne", "buehne", "kabarett", "comedy", "variete", "varieté", "revue", "tanz", "dance", "musical", "show", "improtheater", word("oper"), word("stage"), word("slam"))),
    Rule("exhibition", 5, ("ausstellung", "exhibition", "museum", "galerie", "gallery", "kunst", "vernissage", "atelier", "installation", word("art"))),
    Rule("food", 4, ("street food", "foodtruck", "kulinar", "genuss", "wein", "wine", "winzer", "weinprobe", "weinfest", "bier", "tasting")),
    Rule("market", 3, ("flohmarkt", "trödel", "troedel", "wochenmarkt", "kunstmarkt", "spezialmarkt", title_only("markt"))),
    Rule("outdoor", 2, ("outdoor", "draußen", "draussen", "führung", "fuehrung", "tour", "wander", "spaziergang", "rundgang", "rundfahrt", "natur", "garten", "exkursion", "ausflug", "park", "streuobst", "wildkräuter", "wildkraeuter", "straßenbäume", "strassenbaeume", "stolpersteine", "freiluga")),
    Rule("festival", 1, ("fest", "kirmes", "kerb", "meile", "public viewing", "tag der offenen tür", "tag der offenen tuer", "stadtteilfest", "straßenfest", "strassenfest", "dorffest")),
)

_NON_WORD = r"[^\wäöüÄÖÜß]"


def normalize_text(value: str) -> str:
    value = (value or "").lower()
    value = value.replace("&amp;", "&")
    return re.sub(r"\s+", " ", value).strip()


def _contains_word(text: str, needle: str) -> bool:
    escaped = re.escape(normalize_text(needle))
    return re.search(rf"(^|{_NON_WORD}){escaped}($|{_NON_WORD})", text) is not None


def _matches(text: str, keyword: str | Keyword, *, is_title: bool) -> bool:
    if isinstance(keyword, str):
        return keyword in text
    if keyword.title_only and not is_title:
        return False
    if keyword.word:
        return _contains_word(text, keyword.value)
    return keyword.value in text


def _count_hits(text: str, keywords: Iterable[str | Keyword], *, is_title: bool) -> int:
    return sum(1 for keyword in keywords if _matches(text, keyword, is_title=is_title))


def _has_hit(text: str, keywords: Iterable[str | Keyword]) -> bool:
    return any(_matches(text, keyword, is_title=False) for keyword in keywords)


def categorize_event(source_category: str, title: str, description: str = "") -> Category:
    """Return the canonical category for an event.

    Titles are the strongest signal, descriptions are moderate, and source
    category bags are intentionally weak because many municipal sources attach a
    generic all-purpose bag to every record.
    """

    title_text = normalize_text(title)
    hint_text = normalize_text(source_category)
    description_text = normalize_text(description)

    best_key = "other"
    best_score = 0
    best_priority = -1
    for rule in RULES:
        score = 3 * _count_hits(title_text, rule.keywords, is_title=True)
        score += 2 * _count_hits(description_text, rule.keywords, is_title=False)
        score += 1 if _has_hit(hint_text, rule.keywords) else 0
        if score == 0:
            continue
        if score > best_score or (score == best_score and rule.priority > best_priority):
            best_key = rule.key
            best_score = score
            best_priority = rule.priority

    return CATEGORY_BY_KEY[best_key]
