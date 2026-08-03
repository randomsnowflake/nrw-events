"""Ranking functions independent from source-specific parsing."""

from . import config


def distance_score(km: float, radius_km: float | None = None) -> float:
    """Score 0.1–1.0 by distance from Bonn."""
    if km <= 0:
        return 1.0
    radius = radius_km or config.MAX_RADIUS_KM
    return max(0.1, 1.0 - (km / radius) * 0.9)


def category_score(text: str) -> float:
    """Combine the strongest configured boost and demotion for event text."""
    normalized = text.casefold()
    negative_keywords = {
        "kinder", "kids", "grundschüler", "grundschueler", "familie", "family", "vorlesen",
        "basteln", "jugendliche", "babys", "spielgruppe", "krabbelgruppe", "eltern-kind",
    }
    adult_outdoor_signals = {
        "wein", "wine", "winzer", "weingut", "afterwalk", "genuss", "lounge", "beats",
        "festival", "markt", "flohmarkt", "street food", "kulinar", "stadtteilfest",
        "straßenfest", "strassenfest", "dorffest", "kirmes", "viertel", "meile",
    }
    kids_only = (
        any(word in normalized for word in negative_keywords)
        and not any(word in normalized for word in adult_outdoor_signals)
    )
    family_side_offer_terms = {
        "kids", "kinder", "family", "familie", "vorlesen", "basteln",
    }
    matched = [
        (keyword, weight)
        for keyword, weight in config.CATEGORY_WEIGHT.items()
        if keyword.casefold() in normalized
    ]
    if not kids_only:
        matched = [
            (keyword, weight)
            for keyword, weight in matched
            if keyword not in family_side_offer_terms
        ]

    demotion = min((weight for _keyword, weight in matched if weight < 1), default=1.0)
    boost = max((weight for _keyword, weight in matched if weight >= 1), default=1.0)
    score = demotion * boost
    if kids_only:
        score = min(score, 0.25)
    return score
