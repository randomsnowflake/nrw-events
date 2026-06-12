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


class CategoryResult(Category, total=False):
    confidence: float
    reason: str


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


FORCED_CATEGORY_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    # Public health / expert livestream formats tend to carry generic source
    # bags like "Kultur Konzert". The title is the useful signal here: it is a
    # talk, not a concert just because it contains "live".
    ("talk", ("livetalk", "live-talk")),
    # Walking/listening formats are tours even when a place name contains
    # "markt" (e.g. Cologne's Waidmarkt) or the source uses a broad culture bag.
    ("outdoor", ("soundwalk",)),
    # Basic movement/gymnastics terms should not be mistaken for talks
    # ("Rückbildung") or exhibitions (English "art" inside German prose).
    ("sports", ("gymnastik", "pilates", "hatha yoga")),
    # Meetups around vehicles are destination-style local gatherings, but not
    # concerts just because the description mentions live music later in the day.
    ("festival", ("biker-treffen", "fiat-treffen")),
)

LOW_VALUE_TITLE_CONTEXT = (
    "treff", "frühstück", "fruehstueck", "senioren", "cafe", "café",
    "sprachkurs", "deutschkurs", "english club", "sprechstunde", "beratung",
)

DESTINATION_TITLE_CONTEXT = (
    "festival", "flohmarkt", "konzert", "theater", "kino", "ausstellung", "vernissage",
    "führung", "fuehrung", "tour", "soundwalk", "tag der offenen tür", "tag der offenen tuer",
    "repair café", "repair cafe", "biker-treffen",
)


# The priority only breaks equal scores. More specific commercial/category intent
# should beat broad family/culture words in ties: e.g. "Kinderbücher-Flohmarkt"
# is a flea market first, not a generic family event.
RULES: tuple[Rule, ...] = (
    Rule("market", 14, ("flohmarkt", "trödel", "troedel", "wochenmarkt", "kunstmarkt", "spezialmarkt", "antikmarkt", "kreativmarkt", "lebenskunstmarkt", Keyword("markt", title_only=True, word=True))),
    Rule("food", 13, ("streetfood-festival", "streetfood", "street food", "foodtruck", "kulinar", "genuss", "wein", "wine", "winzer", "weinprobe", "weinfest", "bier", "tasting")),
    Rule(
        "kids",
        12,
        (
            "kinder", "kids", "familie", "family", "jugend",
            "mitmach", "märchen", "maerchen", "puppentheater", "kasper", "vorlesen",
            "bambini", "krabbel", "ferienprogramm", "lego", "zauberwürfel", "zauberwuerfel",
            "storytime", word("dino"),
        ),
    ),
    Rule("workshop", 11, ("workshop", "werkstatt", "kurs", "seminar", "training", "repair", "sprechstunde", "weiterbildung", "vhs", "basteln", "keramik")),
    Rule("talk", 10, ("lesung", "lesekreis", "vorlesung", "vortrag", "lecture", "diskussion", "tagung", "kongress", "symposium", "podium", word("bildung"), "informationsveranstaltung", "chatgpt", "canva", "digital", "3d-druck", "digi:snack", "cloud tech", "azure", "gespräch", "gespraech", "politik", word("talk"), title_only("meetup"))),
    Rule("sports", 9, (word("sport"), "sportveranstaltung", "lauf", "joggen", "running", "rennen", "marathon", "handball", "final4", "yoga", "fitness", "tanzen", "tanzkurs", "radtour", "fahrrad", "rennrad", "stadtradeln", "radeln", "pedelec", "klettern", "schwimmen")),
    Rule("cinema", 8, ("kino", "film", "movie", "cinema", "open-air kino", "open air kino", "filmabend", "screening")),
    Rule("concert", 7, ("konzert", "concert", "livemusik", "live-musik", "live musik", "livekonzert", "live-konzert", "musik", "music", "songkick", "jazz", "orchester", "sinfonie", "symphon", "klavier", "recital", "dirigent", "flöte", "floete", word("chor"), word("band"))),
    Rule("nightlife", 6, ("techno", "electronic", "elektro", "party", "clubnacht", "clubabend", "club party", "dj", "nightlife", "rave", "disco", "beats", "lounge")),
    Rule("stage", 5, ("theater", "bühne", "buehne", "kabarett", "comedy", "variete", "varieté", "revue", "tanz", "dance", "musical", "show", "improtheater", word("oper"), word("stage"), word("slam"))),
    Rule("exhibition", 4, ("ausstellung", "exhibition", "museum", "galerie", "gallery", "kunst", "vernissage", "atelier", "installation")),
    Rule("outdoor", 2, ("outdoor", "draußen", "draussen", "führung", "fuehrung", "tour", "wander", "spaziergang", "rundgang", "rundfahrt", "natur", "garten", "exkursion", "ausflug", "hohes venn", "park", "streuobst", "wildkräuter", "wildkraeuter", "straßenbäume", "strassenbaeume", "stolpersteine", "freiluga")),
    Rule("festival", 1, ("fest", "festival", "kirmes", "kerb", "meile", "public viewing", "tag der offenen tür", "tag der offenen tuer", "stadtteilfest", "straßenfest", "strassenfest", "dorffest")),
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


def _matched_values(text: str, keywords: Iterable[str | Keyword], *, is_title: bool) -> list[str]:
    values: list[str] = []
    for keyword in keywords:
        if _matches(text, keyword, is_title=is_title):
            values.append(keyword if isinstance(keyword, str) else keyword.value)
    return values


def categorize_event(source_category: str, title: str, description: str = "") -> CategoryResult:
    """Return the canonical category for an event.

    Titles are the strongest signal, descriptions are moderate, and source
    category bags are intentionally weak because many municipal sources attach a
    generic all-purpose bag to every record.
    """

    title_text = normalize_text(title)
    hint_text = normalize_text(source_category)
    description_text = normalize_text(description)

    if (any(bit in title_text for bit in LOW_VALUE_TITLE_CONTEXT)
            and not any(bit in title_text for bit in DESTINATION_TITLE_CONTEXT)):
        # Municipal sources often attach broad all-purpose category bags like
        # "Kultur Konzert" to routine meetups/courses. For those low-value title
        # shapes, only classify from the actual title/description.
        hint_text = ""

    combined_text = f"{title_text} {description_text} {hint_text}"
    for forced_key, needles in FORCED_CATEGORY_RULES:
        if any(needle in combined_text for needle in needles):
            category = CATEGORY_BY_KEY[forced_key]
            return {
                "key": category["key"],
                "label": category["label"],
                "confidence": 1.0,
                "reason": f"forced:{forced_key}",
            }

    best_key = "other"
    best_score = 0
    best_priority = -1
    best_reason = "other:no-match"
    for rule in RULES:
        title_matches = _matched_values(title_text, rule.keywords, is_title=True)
        description_matches = _matched_values(description_text, rule.keywords, is_title=False)
        hint_matches = _matched_values(hint_text, rule.keywords, is_title=False)
        score = 3 * len(title_matches)
        score += 2 * len(description_matches)
        # Source categories are weak fallbacks. They should classify otherwise
        # ambiguous records, but must not outvote a competing title/description
        # signal from another rule.
        score += 1 if hint_matches and not title_matches and not description_matches else 0
        if score == 0:
            continue
        if score > best_score or (score == best_score and rule.priority > best_priority):
            best_key = rule.key
            best_score = score
            best_priority = rule.priority
            bits = []
            if title_matches:
                bits.append("title=" + ",".join(title_matches[:3]))
            if description_matches:
                bits.append("description=" + ",".join(description_matches[:3]))
            if hint_matches:
                bits.append("source_category=" + ",".join(hint_matches[:3]))
            best_reason = f"{rule.key}:" + ";".join(bits)

    category = CATEGORY_BY_KEY[best_key]
    return {
        "key": category["key"],
        "label": category["label"],
        "confidence": round(min(1.0, best_score / 6), 2),
        "reason": best_reason,
    }
