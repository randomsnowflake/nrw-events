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
    word_suffix: bool = False


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


def word(value: str) -> Keyword:
    return Keyword(value=value, word=True)


def suffix_word(value: str) -> Keyword:
    return Keyword(value=value, word_suffix=True)


def title_only(value: str) -> Keyword:
    return Keyword(value=value, title_only=True)


FORCED_CATEGORY_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    # Source adapters use this internal marker only after proving that an item
    # belongs to a curated cinema format, so incidental words in a synopsis
    # (such as "Sportlehrerin") cannot move the event into another category.
    ("cinema", ("cinema-special",)),
    # Photo-club meetings are recurring community activities, not club nights.
    ("activities", ("fotoclub", "foto club")),
    # KUNST!RASEN is a Bonn concert venue; the "kunst" substring is not an
    # exhibition signal in aggregator-style "artist @ venue" titles.
    ("concert", ("kunst!rasen", "kunstrasen")),
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
    # Caricature shows are exhibitions even when the body text discusses
    # politics or digital collections.
    ("exhibition", ("karikatur",)),
    # A screening remains a cinema event when an accompanying discussion is
    # also advertised.
    ("cinema", ("openair-kino", "open-air kino", "open air kino")),
)

LOW_VALUE_TITLE_CONTEXT = (
    "treff", "frühstück", "fruehstueck", "senioren", "cafe", "café",
    "sprachkurs", "deutschkurs", "english club", "sprechstunde", "beratung",
    "rat", "sitzung", "ausschuss", "verwaltungsrat", "netzwerktreffen",
    "kaffeenachmittag", "mittagstisch",
)

DESTINATION_TITLE_CONTEXT = (
    "festival", "flohmarkt", "konzert", "theater", "kino", "ausstellung", "vernissage",
    "führung", "fuehrung", "tour", "soundwalk", "tag der offenen tür", "tag der offenen tuer",
    "repair café", "repair cafe", "reparatur-café", "reparatur-cafe", "biker-treffen",
)

STRONG_MARKET_TITLE_CONTEXT = (
    "flohmarkt", "trödelmarkt", "troedelmarkt", "antikmarkt", "basar",
    "feierabendmarkt", "jahrmarkt", "krammarkt", "viehmarkt",
    "designmarkt", "weihnachtsmarkt", "adventsmarkt", "nikolausmarkt",
    "dreikönigsmarkt", "dreikoenigsmarkt", "herbstmarkt", "frühlingsmarkt",
    "fruehlingsmarkt", "martinimarkt", "töpfermarkt", "toepfermarkt",
    "kunsthandwerkermarkt",
    "schallplattenbörse", "schallplattenboerse",
    "fashion, family & kids markt",
)


# The priority only breaks equal scores. More specific commercial/category intent
# should beat broad family/culture words in ties: e.g. "Kinderbücher-Flohmarkt"
# is a flea market first, not a generic family event.
RULES: tuple[Rule, ...] = (
    Rule("market", 14, ("flohmarkt", "kindersachen flohmarkt", "trödel", "troedel", "wochenmarkt", "freitagsmarkt", "frischemarkt", "feierabendmarkt", "jahrmarkt", "krammarkt", "viehmarkt", "stoffmarkt", "büchermarkt", "buechermarkt", "kunstmarkt", "designmarkt", "spezialmarkt", "antikmarkt", "kreativmarkt", "lebenskunstmarkt", "weihnachtsmarkt", "adventsmarkt", "nikolausmarkt", "dreikönigsmarkt", "dreikoenigsmarkt", "herbstmarkt", "frühlingsmarkt", "fruehlingsmarkt", "martinimarkt", "töpfermarkt", "toepfermarkt", "kunsthandwerkermarkt", "schallplattenbörse", "schallplattenboerse", "kindersachenbasar", "kinderbasar", "fashion, family & kids markt", word("market"), Keyword("markt", title_only=True, word=True), Keyword("basar", title_only=True, word_suffix=True), Keyword("antik", title_only=True, word=True))),
    Rule("food", 13, ("streetfood-festival", "streetfood", "street food", "foodtruck", "kulinar", "genuss", "schlemmer", "grillen", "dîner", "diner en blanc", "wine", "winzer", "weinprobe", "weinfest", "weinmoment", "weinlounge", "biergarten", "tasting", word("wein"), word("bier"))),
    Rule(
        "kids",
        12,
        (
            "kinder", "kids", "familie", "family", "jugend",
            "mitmach", "märchen", "maerchen", "puppentheater", "puppenspiel", "kinderbühne", "kinderbuehne", "kasper", "vorlesen", "lese-abenteuer",
            "sommerleseclub", "lesesommer", "vorlesesommer", "vorlesehund",
            "feriencamp", "ferienaktion", "ferienprogramm",
            "ferienspaß", "ferienspass", "kuscheltierübernachtung",
            "bambini", "krabbel", "lego", "zauberwürfel", "zauberwuerfel",
            "storytime", "martinszug", word("dino"),
        ),
    ),
    Rule("workshop", 11, ("workshop", "werkstatt", "digitale werkstatt", "kurs", "seminar", "training", "summer school", "zeichnen lernen", "gag-schreiben", "repair", "reparatur-café", "reparatur-cafe", "sprechstunde", "weiterbildung", "bildungsurlaub", "vhs", "bastel", "schmücken", "schmuecken", "keramik", "malen", "kreativ", "kunstprojekt", "brotbacken", "backkurs", "hilfestellung", "onleihe", "e-medien", "emedien", "libby", "makerspace", "3d-druck", "lasercutter", "quilting", "quilten")),
    Rule("talk", 10, ("lesung", "lesekreis", "lesezirkel", "buchtreff", "buchvorstellung", "vorlesung", "vortrag", "lecture", "diskussion", "tagung", "kongress", "konferenz", "conference", "symposium", "podium", "patiententag", "bürgerinformation", "buergerinformation", "literatur", word("speaker"), word("speakers"), word("liest"), word("bildung"), "informationsveranstaltung", "präventionsabend", "praeventionsabend", "philosophisch", "künstliche intelligenz", "kuenstliche intelligenz", word("ki"), "chatgpt", "canva", "digital", "hackerspace", "digi:snack", "cloud tech", "azure", "gespräch", "gespraech", "politik", word("forum"), word("talk"), Keyword("info", title_only=True, word=True), title_only("meetup"), title_only("community meeting"))),
    Rule("sports", 9, (word("sport"), "sportveranstaltung", "sportwoche", "sportwochenende", "tennis", "volleyball", "lauf", "joggen", "running", "rennen", "marathon", "handball", "final4", "yoga", "fitness", "tanzen", "tanzkurs", "radtour", "radlertreff", "fahrrad", "rennrad", "stadtradeln", "radeln", "pedelec", "paddeln", "klettern", "schwimmen", "boule", "schach")),
    Rule("cinema", 8, ("kino", "film", "movie", "cinema", "open-air kino", "open air kino", "filmabend", "screening")),
    Rule("concert", 7, ("konzert", "concert", "livemusik", "live-musik", "live musik", "livekonzert", "live-konzert", "live-band", "live band", "release show", "musik", "music", "jazz", "samba", "forro", "forró", "orchester", "sinfonie", "symphon", "klavier", "recital", "dirigent", "flöte", "floete", "singen", word("chor"), word("band"), word("swing"))),
    Rule("nightlife", 6, (word("techno"), word("electronic"), word("elektro"), word("party"), "clubnacht", "clubabend", "club party", word("dj"), word("nightlife"), word("rave"), word("disco"), word("beats"), word("lounge"), word("barhopping"), word("speeddating"), word("singles"), Keyword("bar", title_only=True, word=True))),
    Rule("stage", 5, ("theater", "bühne", "buehne", "kabarett", "comedy", "comedian", "kleinkunst", "stand-up", "standup", "satire", "impro", "improtheater", "improvisationstheater", "variete", "varieté", "revue", "poetry slam", "poetryslam", "lachen", "zirkus", "cirque", word("tanz"), word("dance"), "musical", "show", word("performance"), word("oper"), word("stage"), word("slam"))),
    Rule("exhibition", 4, ("ausstellung", "exhibition", "museum", "galerie", "gallery", "kunst", "karikatur", "vernissage", "atelier", "installation")),
    Rule(
        "activities",
        3,
        (
            Keyword("aktivität", title_only=True, word_suffix=True),
            Keyword("treff", title_only=True, word_suffix=True),
            Keyword("treffen", title_only=True, word_suffix=True),
            Keyword("spiele", title_only=True, word=True),
            title_only("spieletreff"),
            title_only("spieleabend"),
            title_only("spielnachmittag"),
            title_only("spielenachmittag"),
            title_only("spiele-nachmittag"),
            title_only("brettspiel"),
            title_only("board game"),
            title_only("board games"),
            title_only("skat-treff"),
            Keyword("bingo", title_only=True, word=True),
            Keyword("gaming", title_only=True, word=True),
            Keyword("quiz", title_only=True, word=True),
            title_only("kaffeeklatsch"),
            title_only("gemeindecafé"),
            title_only("gemeindecafe"),
            title_only("trauercafé"),
            title_only("trauer-café"),
            title_only("friedhofscafé"),
            title_only("friedhofscafe"),
            title_only("gartencafe"),
            title_only("gartencafé"),
            title_only("mittagstisch"),
            title_only("mitbringbrunch"),
            title_only("selbsthilfegruppe"),
            title_only("gemeinsam statt einsam"),
            title_only("gemeinsam freizeit"),
            title_only("cleanup"),
            title_only("clean-up"),
        ),
    ),
    Rule("outdoor", 2, ("outdoor", "draußen", "draussen", "garden party", "führung", "fuehrung", "tour", "blick hinter die kulissen", "wander", "spaziergang", "rundgang", "rundfahrt", "herbstfahrt", "natur", suffix_word("garten"), "exkursion", "ausflug", "hohes venn", suffix_word("park"), "streuobst", "wildkräuter", "wildkraeuter", "straßenbäume", "strassenbaeume", "stolpersteine", "freiluga", "festungstage")),
    Rule("festival", 1, (suffix_word("fest"), "festival", "kirmes", "kerb", "meile", "karneval", "weihnachtsfeier", "public viewing", "convention", "sommernacht", "tag der offenen tür", "tag der offenen tuer", "tag des offenen denkmals", "stadtteilfest", "straßenfest", "strassenfest", "dorffest")),
)

_NON_WORD = r"[^\wäöüÄÖÜß]"


def normalize_text(value: str) -> str:
    value = (value or "").lower()
    value = value.replace("&amp;", "&")
    return re.sub(r"\s+", " ", value).strip()


def _contains_word(text: str, needle: str) -> bool:
    escaped = re.escape(normalize_text(needle))
    return re.search(rf"(^|{_NON_WORD}){escaped}($|{_NON_WORD})", text) is not None


def _contains_word_suffix(text: str, needle: str) -> bool:
    escaped = re.escape(normalize_text(needle))
    return re.search(rf"(^|{_NON_WORD})[\wäöüÄÖÜß]*{escaped}($|{_NON_WORD})", text) is not None


def _matches(text: str, keyword: str | Keyword, *, is_title: bool) -> bool:
    if isinstance(keyword, str):
        return keyword in text
    if keyword.title_only and not is_title:
        return False
    if keyword.word:
        return _contains_word(text, keyword.value)
    if keyword.word_suffix:
        return _contains_word_suffix(text, keyword.value)
    return keyword.value in text


def _matched_values(text: str, keywords: Iterable[str | Keyword], *, is_title: bool) -> list[str]:
    values: list[str] = []
    for keyword in keywords:
        if _matches(text, keyword, is_title=is_title):
            values.append(keyword if isinstance(keyword, str) else keyword.value)
    return values


def _category_keys_for_hint(hint_text: str) -> set[str]:
    """Return canonical intents represented by a source category string."""
    return {
        rule.key
        for rule in RULES
        if _matched_values(hint_text, rule.keywords, is_title=False)
    }


def _forced_title_format(title_text: str) -> str:
    """Prefer explicit event-format nouns over incidental descriptive words."""
    if re.search(r"\b(?:sport|\w*tennis\w*)\b", title_text):
        return "sports"
    if re.search(r"\b\w*(?:führung(?:en)?|fuehrung(?:en)?)\b", title_text):
        return "outdoor"
    if _contains_word(title_text, "bildungsurlaub"):
        return "workshop"
    return ""


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


def categorize_event(
    source_category: str,
    title: str,
    description: str = "",
) -> CategoryResult:
    """Return the canonical category for an event.

    Titles are the strongest signal, descriptions are moderate, and source
    category bags are intentionally weak because many municipal sources attach a
    generic all-purpose bag to every record.
    """

    title_text = normalize_text(title)
    hint_text = normalize_text(source_category)
    description_text = normalize_text(description)

    # Explicit sport and guided-listening formats in the title outrank broader
    # programme context such as "Ferienspaß" or "künstlerische Intervention".
    explicit_title_format = _forced_title_format(title_text)
    if explicit_title_format == "sports" or _contains_word(title_text, "soundwalk"):
        key = explicit_title_format or "outdoor"
        category = CATEGORY_BY_KEY[key]
        return {
            "key": category["key"],
            "label": category["label"],
            "confidence": 1.0,
            "reason": f"forced:{key}-title-format",
        }

    if ("cinema-special" not in hint_text
            and any(bit in title_text for bit in LOW_VALUE_TITLE_CONTEXT)
            and not any(bit in title_text for bit in DESTINATION_TITLE_CONTEXT)):
        # Municipal sources often attach broad all-purpose category bags like
        # "Kultur Konzert" to routine meetups/courses. For those low-value title
        # shapes, only classify from the actual title/description.
        hint_text = ""

    hint_category_keys = _category_keys_for_hint(hint_text)

    # Broad municipal bags such as "Kultur Markt Ausstellung Konzert Führung"
    # describe the entire calendar, not an individual event. Two focused tags
    # can still express a legitimate hybrid format and retain their normal weak
    # tie-breaking role.
    if len(hint_category_keys) > 2:
        hint_text = ""
        hint_category_keys = set()

    # A focused exhibition tag corroborated by an explicit description is more
    # reliable than an incidental conceptual word such as "Natur" in the title.
    if (
        hint_category_keys == {"exhibition"}
        and _contains_word(description_text, "ausstellung")
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
        and re.search(r"\b\w*(?:führung(?:en)?|fuehrung(?:en)?)\b", title_text)
    ):
        category = CATEGORY_BY_KEY["exhibition"]
        return {
            "key": category["key"],
            "label": category["label"],
            "confidence": 1.0,
            "reason": "forced:museum-guided-tour",
        }

    title_format = _forced_title_format(title_text)
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
    if any(bit in title_text for bit in STRONG_MARKET_TITLE_CONTEXT):
        category = CATEGORY_BY_KEY["market"]
        return {
            "key": category["key"],
            "label": category["label"],
            "confidence": 1.0,
            "reason": "forced:market-title",
        }

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

    category = CATEGORY_BY_KEY[best_key]
    return {
        "key": category["key"],
        "label": category["label"],
        "confidence": round(min(1.0, best_score / 6), 2),
        "reason": best_reason,
    }
