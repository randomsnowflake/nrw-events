"""Ranking functions independent from source-specific parsing."""

from . import config


def distance_score(km: float) -> float:
    """Score 0.1–1.0 by distance from Bonn."""
    if km <= 0:
        return 1.0
    return max(0.1, 1.0 - (km / config.MAX_RADIUS_KM) * 0.9)


def category_score(text: str) -> float:
    """Preference score from event text, with a guard for kids-only listings."""
    text_lower = text.lower()
    negative_keywords = {
        "kinder", "kids", "grundschüler", "grundschueler", "familie", "family", "vorlesen",
        "basteln", "jugendliche", "babys", "spielgruppe", "krabbelgruppe", "eltern-kind",
    }
    adult_outdoor_signals = {
        "wein", "wine", "winzer", "weingut", "afterwalk", "genuss", "lounge", "beats",
        "festival", "markt", "flohmarkt", "street food", "kulinar", "stadtteilfest",
        "straßenfest", "strassenfest", "dorffest", "kirmes", "viertel", "meile",
    }
    if any(word in text_lower for word in negative_keywords) and not any(word in text_lower for word in adult_outdoor_signals):
        return 0.25
    return max([0.8] + [weight for keyword, weight in config.CATEGORY_WEIGHT.items() if keyword in text_lower])
