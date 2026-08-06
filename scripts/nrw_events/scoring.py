"""Ranking functions independent from source-specific parsing."""

import re

from . import config


def distance_score(km: float, radius_km: float | None = None) -> float:
    """Score 0.1–1.0 by distance from Bonn."""
    if km <= 0:
        return 1.0
    radius = radius_km or config.MAX_RADIUS_KM
    return max(0.1, 1.0 - (km / radius) * 0.9)


_NEGATIVE_KEYWORDS = frozenset({
    "kinder", "kids", "grundschüler", "grundschueler", "familie", "family", "vorlesen",
    "basteln", "jugendliche", "babys", "spielgruppe", "krabbelgruppe", "eltern-kind",
})
_ADULT_OUTDOOR_SIGNALS = frozenset({
    "wein", "wine", "winzer", "weingut", "afterwalk", "genuss", "lounge", "beats",
    "festival", "markt", "flohmarkt", "street food", "kulinar", "stadtteilfest",
    "straßenfest", "strassenfest", "dorffest", "kirmes", "viertel", "meile",
})
_FAMILY_SIDE_OFFER_TERMS = frozenset({
    "kids", "kinder", "family", "familie", "vorlesen", "basteln",
})

# How CATEGORY_WEIGHT keys match against event text. Default is word-prefix so
# German compounds still score ("wein" matches "Weinfest") without the raw
# substring misfires ("sport" in "Transport", "wein" in "Schweinfurt"). The
# overrides mirror category_taxonomy's word / word_suffix / compound_word modes.
_WORD_ONLY = frozenset({"art"})  # prefix would hit "Artenschutz", "Artist"
_SUFFIX_ONLY = frozenset({"tour"})  # prefix would hit "Tourismusbüro"; keep "Radtour"
_COMPOUND = frozenset({
    # keys that appear on either side of German compounds
    # ("Jazzkonzert" / "Konzertabend", "Filmfestival", "Weihnachtsmarkt")
    "concert", "konzert", "music", "musik", "festival", "markt", "flohmarkt",
    "museum", "ausstellung", "theater", "wanderung", "führung", "lesung",
    "vortrag", "party", "club",
})


def _keyword_pattern(keyword: str) -> re.Pattern[str]:
    escaped = re.escape(keyword.casefold())
    if keyword in _WORD_ONLY:
        body = escaped
    elif keyword in _SUFFIX_ONLY:
        body = rf"\w*{escaped}"
    elif keyword in _COMPOUND:
        body = rf"\w*{escaped}\w*"
    else:
        body = rf"{escaped}\w*"
    return re.compile(rf"\b{body}\b")


_CATEGORY_MATCHERS = tuple(
    (keyword, weight, _keyword_pattern(keyword))
    for keyword, weight in config.CATEGORY_WEIGHT.items()
)


def category_score(text: str) -> float:
    """Combine the strongest configured boost and demotion for event text."""
    normalized = text.casefold()
    kids_only = (
        any(word in normalized for word in _NEGATIVE_KEYWORDS)
        and not any(word in normalized for word in _ADULT_OUTDOOR_SIGNALS)
    )
    matched = [
        (keyword, weight)
        for keyword, weight, pattern in _CATEGORY_MATCHERS
        if pattern.search(normalized)
    ]
    if not kids_only:
        matched = [
            (keyword, weight)
            for keyword, weight in matched
            if keyword not in _FAMILY_SIDE_OFFER_TERMS
        ]

    demotion = min((weight for _keyword, weight in matched if weight < 1), default=1.0)
    boost = max((weight for _keyword, weight in matched if weight >= 1), default=1.0)
    score = demotion * boost
    if kids_only:
        score = min(score, 0.25)
    return score
