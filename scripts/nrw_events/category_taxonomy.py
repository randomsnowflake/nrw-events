"""Canonical event category taxonomy and keyword classifier.

The scraper still preserves the source-provided ``category`` text, but it also
emits a stable ``category_key``/``category_label`` pair so downstream sites do
not have to duplicate category rules in TypeScript.
"""

from __future__ import annotations

import re
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, TypedDict

from .models import normalize_source_id


_GERMAN_SPELLING_TRANSLATION = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})
_WORD_PATTERN = re.compile(r"\w+")


def normalize_text(value: str) -> str:
    value = (value or "").lower()
    value = value.replace("&amp;", "&")
    return re.sub(r"\s+", " ", value).strip()


def _comparison_from_normalized(value: str) -> str:
    return value.translate(_GERMAN_SPELLING_TRANSLATION)


def comparison_text(value: str) -> str:
    """Normalize German spelling variants without rebuilding translation data."""
    return _comparison_from_normalized(normalize_text(value))


def _compile_keyword_pattern(value: str, mode: str) -> re.Pattern[str]:
    """Compile a policy keyword once while loading the category policy."""
    escaped = re.escape(value)
    if mode == "word_prefix":
        expression = rf"(?<!\w){escaped}\w*(?!\w)"
    elif mode == "word_suffix":
        expression = rf"(?<!\w)\w*{escaped}(?!\w)"
    elif mode == "compound_word":
        expression = rf"(?<!\w)\w*{escaped}\w*(?!\w)"
    else:
        expression = rf"(?<!\w){escaped}(?!\w)"
    return re.compile(expression)


class Category(TypedDict):
    key: str
    label: str


class CategoryResult(Category, total=False):
    confidence: float
    reason: str


@dataclass(frozen=True)
class Keyword:
    value: str
    normalized_value: str
    pattern: re.Pattern[str]
    title_only: bool = False
    word: bool = False
    word_prefix: bool = False
    word_suffix: bool = False
    compound_word: bool = False
    weak: bool = False


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
    {"key": "market", "label": "Märkte & Flohmärkte"},
    {"key": "food", "label": "Food & Genuss"},
    {"key": "outdoor", "label": "Führungen & Outdoor"},
    {"key": "sports", "label": "Sport & Bewegung"},
    {"key": "talk", "label": "Vorträge & Lesungen"},
    {"key": "workshop", "label": "Workshops & Kurse"},
    {"key": "kids", "label": "Familie & Kinder"},
    {"key": "activities", "label": "Aktivitäten & Treffen"},
    {"key": "other", "label": "Sonstiges"},
]

CATEGORY_BY_KEY = {category["key"]: category for category in CATEGORIES}
_POLICY_PATH = Path(__file__).with_name("categories.json")
_MATCH_MODES = frozenset({"word", "word_prefix", "word_suffix", "compound_word"})


def _keyword_from_spec(raw: object) -> Keyword:
    """Validate and compile one data-owned keyword specification."""
    required = {"value", "match_mode", "scope", "weight", "comment"}
    if not isinstance(raw, dict) or not required <= raw.keys():
        raise ValueError(f"category keyword must define {sorted(required)}")
    value = raw["value"]
    mode = raw["match_mode"]
    scope = raw["scope"]
    weight = raw["weight"]
    if not isinstance(value, str) or not value.strip():
        raise ValueError("category keyword value must be a non-empty string")
    if mode not in _MATCH_MODES:
        raise ValueError(f"unsupported category keyword match_mode {mode!r}")
    if scope not in {"all", "title"}:
        raise ValueError(f"unsupported category keyword scope {scope!r}")
    if not isinstance(weight, (int, float)) or not 0 < weight <= 1:
        raise ValueError("category keyword weight must be greater than zero and at most one")
    if not isinstance(raw["comment"], str):
        raise ValueError("category keyword comment must be a string")
    normalized_value = comparison_text(value)
    return Keyword(
        value=value,
        normalized_value=normalized_value,
        pattern=_compile_keyword_pattern(normalized_value, mode),
        title_only=scope == "title",
        word=mode == "word",
        word_prefix=mode == "word_prefix",
        word_suffix=mode == "word_suffix",
        compound_word=mode == "compound_word",
        weak=weight < 1,
    )


def _load_policy() -> dict:
    payload = json.loads(_POLICY_PATH.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("version") != 1:
        raise ValueError("category policy must use schema version 1")
    for field in ("forced_rules", "contexts", "rules"):
        if field not in payload:
            raise ValueError(f"category policy is missing {field!r}")
    return payload


_CATEGORY_POLICY = _load_policy()
HEURISTIC_CONFIDENCE_THRESHOLD = float(_CATEGORY_POLICY["heuristic_confidence_threshold"])
FORCED_CATEGORY_RULES = tuple(
    (entry["key"], tuple(_keyword_from_spec(keyword) for keyword in entry["keywords"]))
    for entry in _CATEGORY_POLICY["forced_rules"]
)
LOW_VALUE_TITLE_CONTEXT = tuple(
    _keyword_from_spec(keyword) for keyword in _CATEGORY_POLICY["contexts"]["low_value_title"]
)
DESTINATION_TITLE_CONTEXT = tuple(
    _keyword_from_spec(keyword) for keyword in _CATEGORY_POLICY["contexts"]["destination_title"]
)
STRONG_MARKET_TITLE_CONTEXT = tuple(
    comparison_text(keyword["value"]) for keyword in _CATEGORY_POLICY["contexts"]["strong_market_title"]
)
RULES = tuple(sorted(
    (
        Rule(
            entry["key"],
            int(entry["priority"]),
            tuple(_keyword_from_spec(keyword) for keyword in entry["keywords"]),
        )
        for entry in _CATEGORY_POLICY["rules"]
    ),
    key=lambda rule: rule.priority,
    reverse=True,
))
_FALLBACK_CACHE: dict[str, CategoryResult] = {}


def category_cache_key(source_id: str, title: str) -> str:
    """Return the stable series key used by an optional reviewed fallback cache."""
    return f"{normalize_source_id(source_id)}|{normalize_text(title)}"


def configure_fallback_cache(path: str = "") -> None:
    """Load reviewed fallback classifications without invoking any external service."""
    global _FALLBACK_CACHE
    if not path:
        _FALLBACK_CACHE = {}
        return
    payload = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    entries = payload.get("entries") if isinstance(payload, dict) else None
    if not isinstance(payload, dict) or payload.get("version") != 1 or not isinstance(entries, dict):
        raise ValueError("category fallback cache must use schema version 1 with an entries object")
    loaded: dict[str, CategoryResult] = {}
    for cache_key, raw in entries.items():
        if not isinstance(cache_key, str) or not isinstance(raw, dict):
            raise ValueError("category fallback cache entries must be keyed objects")
        key = raw.get("key")
        confidence = raw.get("confidence", 0.9)
        if key not in CATEGORY_BY_KEY or key == "other":
            raise ValueError(f"category fallback cache contains invalid category {key!r}")
        if not isinstance(confidence, (int, float)) or not HEURISTIC_CONFIDENCE_THRESHOLD <= confidence <= 1:
            raise ValueError("category fallback cache confidence must be between 0.5 and 1.0")
        category = CATEGORY_BY_KEY[key]
        loaded[cache_key] = {
            "key": category["key"],
            "label": category["label"],
            "confidence": float(confidence),
            "reason": f"fallback:cache:{raw.get('reason', 'reviewed')}",
        }
    _FALLBACK_CACHE = loaded


def _fallback_category(source_id: str, title: str) -> CategoryResult | None:
    return _FALLBACK_CACHE.get(category_cache_key(source_id, title))


def _contains_word(text: str, needle: str) -> bool:
    return _contains_comparison_word(comparison_text(text), comparison_text(needle))


def _contains_comparison_word(text: str, normalized_needle: str) -> bool:
    return normalized_needle in _WORD_PATTERN.findall(text)


def _matches(text: str, keyword: str | Keyword, *, is_title: bool) -> bool:
    if isinstance(keyword, str):
        return _contains_word(text, keyword)
    if keyword.title_only and not is_title:
        return False
    if keyword.compound_word:
        return any(match.group(0) != keyword.normalized_value for match in keyword.pattern.finditer(text))
    return keyword.pattern.search(text) is not None


def _matched_keywords(
    text: str,
    keywords: Iterable[str | Keyword],
    *,
    is_title: bool,
) -> list[str | Keyword]:
    return [keyword for keyword in keywords if _matches(text, keyword, is_title=is_title)]


def _has_enough_evidence(matches: Iterable[str | Keyword]) -> bool:
    """Reject a lone signal explicitly marked too ambiguous to classify by itself."""
    return any(isinstance(keyword, str) or not keyword.weak for keyword in matches)


def _category_keys_for_hint(hint_text: str) -> set[str]:
    """Return canonical intents represented by a source category string."""
    keys = set()
    for rule in RULES:
        matches = _matched_keywords(hint_text, rule.keywords, is_title=False)
        if matches and _has_enough_evidence(matches):
            keys.add(rule.key)
    return keys


def _forced_title_format(title_text: str, title_comparison: str) -> str:
    """Prefer explicit event-format nouns over incidental descriptive words."""
    if re.search(r"\b\w*filmfestival\w*\b", title_text):
        return "cinema"
    if re.search(r"\b(?:sport|\w*tennis\w*|\w*sport(?:tag|fest|turnier|woche))\b", title_text):
        return "sports"
    if re.search(r"\b(?:fahrrad|rad)tour\w*\b", title_comparison):
        return "sports"
    if re.search(r"\blearning[ -]?session\b", title_text):
        return "workshop"
    if (
        re.search(r"\b\w*museum\w*\b", title_text)
        and re.search(r"\b(?:geöffnet|geoeffnet|öffnung|oeffnung|open)\w*\b", title_text)
    ):
        return "exhibition"
    if re.search(r"\b(?!ein(?:fuehrung|führung)\b)\w*(?:führung(?:en)?|fuehrung(?:en)?)\b", title_text):
        return "outdoor"
    if _contains_comparison_word(title_comparison, "bildungsurlaub"):
        return "workshop"
    return ""


_GUIDED_TOUR_TITLE_PATTERN = re.compile(
    r"\b(?!ein(?:fuehrung|führung)\b)\w*(?:führung(?:en)?|fuehrung(?:en)?)\b"
)
_INDOOR_MUSEUM_CONTEXT_PATTERN = re.compile(
    r"\b(?:\w*museum\w*|bundeskunsthalle|ausstellungshaus)\b"
)
_OUTDOOR_GUIDED_TOUR_PATTERN = re.compile(
    r"\b(?:stadt(?:rundgang|führung|fuehrung)|für entdecker|fuer entdecker|"
    r"botanisch\w*|garten\w*|park\w*|rund um|außenbereich|aussenbereich|"
    r"skulpturenpark|freilicht\w*)\b"
)


def _is_indoor_museum_guided_tour(
    title_text: str,
    venue_text: str,
    source_text: str,
) -> bool:
    """Recognize only tours anchored to a known indoor museum context."""
    if not _GUIDED_TOUR_TITLE_PATTERN.search(title_text):
        return False
    if _OUTDOOR_GUIDED_TOUR_PATTERN.search(title_text):
        return False
    return bool(
        _INDOOR_MUSEUM_CONTEXT_PATTERN.search(venue_text)
        or source_text in {
            "bundeskunsthalle",
            "deutsches museum bonn",
            "lvr-landesmuseum bonn",
            "museum koenig",
        }
    )


def _contains_any(text: str, patterns: tuple[str, ...]) -> bool:
    return any(re.search(pattern, text) for pattern in patterns)


def _match_count(text: str, patterns: tuple[str, ...]) -> int:
    return sum(1 for pattern in patterns if re.search(pattern, text))


def _contextual_event_format(
    title_text: str,
    description_text: str,
) -> tuple[str, str, float] | None:
    """Infer formats from corroborating, reusable content signals.

    These rules deliberately describe event shapes rather than named events,
    performers, venues, or sources. Ambiguous single words are insufficient:
    each branch either requires multiple signals or a well-defined public
    programme marker.
    """

    content = f"{title_text} {description_text}"
    child_program = _contains_any(
        content,
        (
            r"\bbibliotheks(?:sommer|ferien)\b",
            r"\b(?:kinder|jugend|ferien)(?:programm|aktion|spaß|spass)\b",
        ),
    )
    interactive_play = _contains_any(
        content,
        (
            r"\b(?:vr|virtual reality)\b",
            r"\bcontroller\w*\b",
            r"\b(?:brett|karten|rollen|video|gesellschafts)spiel(?:e|en|abend|treff)?\b",
            r"\bsocial[ -]?deduction[ -]?spiel\w*\b",
            r"\bgaming\b",
        ),
    )
    if child_program and interactive_play:
        return ("kids", "format:interactive-child-programme", 0.95)

    practical_process = _match_count(
        content,
        (
            r"\bexperimentier\w*\b",
            r"\bschritt für schritt\b",
            r"\b(?:selbst|gemeinsam) (?:gestalten|schreiben|bauen|erarbeiten)\b",
            r"\b(?:\w*auffrisch|aufzufrisch|frisch\w*.{0,20}\bauf)\w*\b",
            r"\b(?:\w*(?:fähigkeiten|kenntnisse)\w*.{0,30}\bstärk\w*|stärk\w*.{0,30}\w*(?:fähigkeiten|kenntnisse))\b",
            r"\bunsicherheiten (?:abbauen|abzubauen)\b",
            r"\b(?:schreiben|erzählen|gestalten).{0,80}(?:entsteh|erarbeit)\w*\b",
        ),
    )
    creative_process = _match_count(
        description_text,
        (
            r"\bschreib\w*\b",
            r"\berzähl\w*\b",
            r"\bgestalt\w*\b",
            r"\bentsteh\w*\b",
        ),
    )
    if practical_process >= 2 or creative_process >= 3:
        return ("workshop", "format:guided-practical-learning", 0.9)

    social_format = _contains_any(
        content,
        (
            r"\bmedit\w*\b",
            r"\bselbsthilfe(?:gruppe)?\b",
            r"\btrauer(?:gruppe|treff|café|cafe|nde)?\b",
            r"\b(?:häkeln|stricken|nähen|handarbeit)\b",
            r"\bmitmachformat\w*\b",
            r"\bfreizeitprogramm\b",
            r"\bmiteinander\b",
            r"\blernt sich kennen\b",
        ),
    )
    participation = _contains_any(
        content,
        (
            r"\bgemeinsam\b",
            r"\bteilnehm\w*\b",
            r"\boffen für\b",
            r"\bgleichgesinnt\w*\b",
            r"\bunterstützen sich\b",
            r"\bmitmachen\b",
            r"\bmitgestalten\b",
            r"\bkomm mit uns\b",
            r"\b\w*programm\b",
            r"\blernt sich kennen\b",
        ),
    )
    playable_format = interactive_play and _contains_any(
        content,
        (r"\bbring\w*\b", r"\bspiel\w*\b", r"\bcontroller\w*\b"),
    )
    if (social_format and participation) or playable_format:
        return ("activities", "format:participatory-social-activity", 0.9)

    outdoor_context = _match_count(
        content,
        (
            r"\b(?:wiese|weide|heide|wald|tongrube|weiher)\w*\b",
            r"\b(?:kräuter|kraeuter|flora|fauna|tier\w*)\b",
            r"\b(?:eifel|naturgebiet|landschaft)\b",
            r"\bkomm mit uns\b",
        ),
    )
    if outdoor_context >= 2:
        return ("outdoor", "format:guided-nature-experience", 0.85)

    exhibition_context = _match_count(
        content,
        (
            r"\bkünstler\w*\b",
            r"\bintervention\b",
            r"\b(?:skulptur|installation|kunstobjekt)\w*\b",
            r"\bprojekt\b",
        ),
    )
    if exhibition_context >= 2:
        return ("exhibition", "format:artistic-installation", 0.9)

    concert_context = (
        _contains_any(content, (r"\btribute\b",))
        and _contains_any(content, (r"\blive\b", r"\bband\b", r"\bmusik\b"))
    ) or (
        _match_count(
            content,
            (
                r"\b(?:metal|gothic|postpunk|punkrock|dark wave|cold wave|ebm)\b",
                r"\b(?:klänge|klaenge|klange|stücke|stuecke)\b",
                r"\b(?:live club|live-band|live band)\b",
                r"\bopen[ -]?air\b",
            ),
        )
        >= 2
    )
    if concert_context:
        return ("concert", "format:live-music-performance", 0.9)

    stage_context = _contains_any(content, (r"\bpräsentiert\w*\b", r"\bpraesentiert\w*\b"))
    stage_context = stage_context and _contains_any(content, (r"\bhommage\b", r"\bprogramm\b"))
    if stage_context:
        return ("stage", "format:presented-stage-programme", 0.8)

    information_context = _contains_any(
        description_text,
        (
            r"\binformiert und berät\b",
            r"\binformiert und beraet\b",
            r"^wie kann ich\b",
            r"\bwie (?:kann|können) (?:ich|wir|man)\b",
        ),
    )
    if information_context:
        return ("talk", "format:public-information-session", 0.8)

    civic_celebration = _contains_any(content, (r"\bverleihung\b", r"\bpreisübergabe\b"))
    civic_celebration = civic_celebration and _contains_any(
        description_text,
        (r"\bim rahmen (?:der|des)\b", r"\b(?:fest|festival|partie)\w*\b"),
    )
    organisation_anniversary = bool(
        re.search(r"\b\d{1,3}\s+jahre\b", title_text)
        and re.search(r"\b(?:e\.?\s*v\.?|verein|initiative|hilfe)\b", title_text)
    )
    if civic_celebration or organisation_anniversary:
        return ("festival", "format:public-celebration", 0.85)

    return None


def _heuristic_confidence(
    title_matches: list[str],
    description_matches: list[str],
    hint_matches: list[str],
) -> float:
    """Estimate confidence from independent evidence, not an arbitrary score divisor."""
    if title_matches:
        confidence = 0.72 + min(max(len(title_matches) - 1, 0), 2) * 0.08
    elif description_matches:
        # Match the publication quality gate: a supported classification is
        # never emitted with a confidence that the same pipeline calls low.
        confidence = HEURISTIC_CONFIDENCE_THRESHOLD
    elif hint_matches:
        # A single focused source tag survives the broad-bag guard above and is
        # useful evidence, but remains weaker than visitor-facing title copy.
        confidence = 0.6
    else:
        return 0.0
    if title_matches and description_matches:
        confidence += 0.12
    if (title_matches or description_matches) and hint_matches:
        confidence += 0.05
    return round(min(confidence, 0.95), 2)


def categorize_event(
    source_category: str,
    title: str,
    description: str = "",
    *,
    venue: str = "",
    source: str = "",
    source_id: str = "",
    default_category_key: str = "",
    category_locked: bool = False,
) -> CategoryResult:
    """Return the canonical category for an event.

    Titles are the strongest signal, descriptions are moderate, and source
    category bags are intentionally weak because many municipal sources attach a
    generic all-purpose bag to every record.
    """

    if default_category_key and default_category_key not in CATEGORY_BY_KEY:
        raise ValueError(f"unknown default category: {default_category_key}")
    if category_locked:
        if not default_category_key:
            raise ValueError("category_locked requires default_category_key")
        category = CATEGORY_BY_KEY[default_category_key]
        return {
            "key": category["key"],
            "label": category["label"],
            "confidence": 1.0,
            "reason": f"source:locked-default:{default_category_key}",
        }

    title_text = normalize_text(title)
    hint_text = normalize_text(source_category)
    description_text = normalize_text(description)
    venue_text = normalize_text(venue)
    source_text = normalize_text(source)
    title_comparison = _comparison_from_normalized(title_text)
    hint_comparison = _comparison_from_normalized(hint_text)
    description_comparison = _comparison_from_normalized(description_text)

    if (
        re.search(r"\bklassik\w*\b", title_text)
        and re.search(r"\b(?:benefiz)?konzert\w*\b|\bkammermusik\w*\b|\bmusiker\w*\b", description_text)
    ):
        category = CATEGORY_BY_KEY["concert"]
        return {
            "key": category["key"],
            "label": category["label"],
            "confidence": 1.0,
            "reason": "forced:classical-concert-format",
        }

    if (
        re.search(r"\berinner\w*\b", title_text)
        and re.search(r"\b(?:geschichtswerkstatt|stadtgeschichte|geschichte)\w*\b", description_text)
    ):
        category = CATEGORY_BY_KEY["talk"]
        return {
            "key": category["key"],
            "label": category["label"],
            "confidence": 1.0,
            "reason": "forced:historical-education-event",
        }

    if (
        re.search(r"\b(?:versklavung\w*|sklaverei\w*)\b", title_text)
        and re.search(r"\b(?:vortr\w*|geschichte\w*|erinner\w*)\b", description_text)
    ):
        category = CATEGORY_BY_KEY["talk"]
        return {
            "key": category["key"],
            "label": category["label"],
            "confidence": 1.0,
            "reason": "forced:historical-education-event",
        }

    # Explicit sport and guided-listening formats in the title outrank broader
    # programme context such as "Ferienspaß" or "künstlerische Intervention".
    explicit_title_format = _forced_title_format(title_text, title_comparison)
    if explicit_title_format == "sports" or _contains_comparison_word(title_comparison, "soundwalk"):
        key = explicit_title_format or "outdoor"
        category = CATEGORY_BY_KEY[key]
        return {
            "key": category["key"],
            "label": category["label"],
            "confidence": 1.0,
            "reason": f"forced:{key}-title-format",
        }

    if (
        "cinema-special" not in hint_text
        and any(_matches(title_comparison, bit, is_title=True) for bit in LOW_VALUE_TITLE_CONTEXT)
        and not any(_matches(title_comparison, bit, is_title=True) for bit in DESTINATION_TITLE_CONTEXT)
    ):
        # Municipal sources often attach broad all-purpose category bags like
        # "Kultur Konzert" to routine meetups/courses. For those low-value title
        # shapes, only classify from the actual title/description.
        hint_text = ""
        hint_comparison = ""

    hint_category_keys = _category_keys_for_hint(hint_comparison)

    # Broad municipal bags such as "Kultur Markt Ausstellung Konzert Führung"
    # describe the entire calendar, not an individual event. Two focused tags
    # can still express a legitimate hybrid format and retain their normal weak
    # tie-breaking role.
    if len(hint_category_keys) > 2:
        hint_text = ""
        hint_comparison = ""
        hint_category_keys = set()

    # A focused exhibition tag corroborated by an explicit description is more
    # reliable than an incidental conceptual word such as "Natur" in the title.
    if (
        hint_category_keys == {"exhibition"}
        and _contains_comparison_word(description_comparison, "ausstellung")
    ):
        category = CATEGORY_BY_KEY["exhibition"]
        return {
            "key": category["key"],
            "label": category["label"],
            "confidence": 1.0,
            "reason": "forced:exhibition-source-content-consensus",
        }

    if (
        hint_category_keys == {"exhibition"}
        and _GUIDED_TOUR_TITLE_PATTERN.search(title_text)
    ):
        category = CATEGORY_BY_KEY["exhibition"]
        return {
            "key": category["key"],
            "label": category["label"],
            "confidence": 1.0,
            "reason": "forced:museum-guided-tour",
        }

    if _is_indoor_museum_guided_tour(title_text, venue_text, source_text):
        category = CATEGORY_BY_KEY["exhibition"]
        return {
            "key": category["key"],
            "label": category["label"],
            "confidence": 1.0,
            "reason": "forced:indoor-museum-guided-tour",
        }

    title_format = explicit_title_format
    if title_format:
        category = CATEGORY_BY_KEY[title_format]
        return {
            "key": category["key"],
            "label": category["label"],
            "confidence": 1.0,
            "reason": f"forced:{title_format}-title-format",
        }

    # Aggregator-style artist-at-venue titles can name a venue such as "Alte VHS".
    # The concert source category is more reliable than that venue token.
    if "concert" in hint_text and " @ " in title_text:
        category = CATEGORY_BY_KEY["concert"]
        return {
            "key": category["key"],
            "label": category["label"],
            "confidence": 1.0,
            "reason": "forced:concert-artist-at-venue",
        }

    # Explicit market formats in the title remain markets even when their copy
    # naturally repeats broad family words several times.
    if any(bit in title_comparison for bit in STRONG_MARKET_TITLE_CONTEXT):
        category = CATEGORY_BY_KEY["market"]
        return {
            "key": category["key"],
            "label": category["label"],
            "confidence": 1.0,
            "reason": "forced:market-title",
        }

    for forced_key, needles in FORCED_CATEGORY_RULES:
        if any(
            _matches(text, needle, is_title=is_title)
            for text, is_title in ((title_comparison, True), (hint_comparison, False))
            for needle in needles
        ):
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
    best_title_matches: list[str] = []
    best_description_matches: list[str] = []
    best_hint_matches: list[str] = []
    for rule in RULES:
        title_keywords = _matched_keywords(title_comparison, rule.keywords, is_title=True)
        description_keywords = _matched_keywords(description_comparison, rule.keywords, is_title=False)
        hint_keywords = _matched_keywords(hint_comparison, rule.keywords, is_title=False)
        if not _has_enough_evidence(title_keywords + description_keywords + hint_keywords):
            continue
        title_matches = [
            keyword if isinstance(keyword, str) else keyword.value
            for keyword in title_keywords
        ]
        description_matches = [
            keyword if isinstance(keyword, str) else keyword.value
            for keyword in description_keywords
        ]
        hint_matches = [
            keyword if isinstance(keyword, str) else keyword.value
            for keyword in hint_keywords
        ]
        score = 3 * len(title_matches)
        # A description corroborates an intent, but repeated synonyms must not
        # snowball past an explicit title format.
        score += 2 if description_matches else 0
        # Source categories remain weak fallbacks. Broad bags were discarded
        # above; focused tags may break an otherwise unsupported tie but cannot
        # overpower title or description evidence.
        score += 1 if hint_matches else 0
        if score == 0:
            continue
        if score > best_score or (score == best_score and rule.priority > best_priority):
            best_key = rule.key
            best_score = score
            best_priority = rule.priority
            best_title_matches = title_matches
            best_description_matches = description_matches
            best_hint_matches = hint_matches
            bits = []
            if title_matches:
                bits.append("title=" + ",".join(title_matches[:3]))
            if description_matches:
                bits.append("description=" + ",".join(description_matches[:3]))
            if hint_matches:
                bits.append("source_category=" + ",".join(hint_matches[:3]))
            best_reason = f"{rule.key}:" + ";".join(bits)

    if best_key == "other":
        contextual_format = _contextual_event_format(title_text, description_text)
        if contextual_format:
            key, reason, confidence = contextual_format
            category = CATEGORY_BY_KEY[key]
            return {
                "key": category["key"],
                "label": category["label"],
                "confidence": confidence,
                "reason": reason,
            }

    if best_key == "other" and default_category_key:
        category = CATEGORY_BY_KEY[default_category_key]
        return {
            "key": category["key"],
            "label": category["label"],
            "confidence": 1.0,
            "reason": f"source:default:{default_category_key}",
        }

    fallback = _fallback_category(source_id or source, title_text)
    if best_key == "other" and fallback:
        return dict(fallback)

    confidence = _heuristic_confidence(
        best_title_matches,
        best_description_matches,
        best_hint_matches,
    )
    category = CATEGORY_BY_KEY[best_key]
    return {
        "key": category["key"],
        "label": category["label"],
        "confidence": confidence,
        "reason": best_reason,
    }
