"""Owning implementation of ranking; core is a compatibility facade."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from . import performance


@performance.measured("ranking.features")
def ranking_features(ev: Mapping[str, Any]) -> dict[str, float]:
    """Return named editorial ranking features without presentation side effects."""
    text = (ev.get("title", "") + " " + ev.get("category", "") + " " + ev.get("description", "")).lower()
    features = {}
    if "flohmarkt" in text:
        features["flea_market"] = 0.5
    if any(k in text for k in ["ahrweinwalk", "weinwanderung", "ahrtal", "ahrweiler"]):
        features["ahr_wine"] = 0.55
    if any(k in text for k in ["stadtteilfest", "straßenfest", "strassenfest", "dorffest",
                               "poppelsdorf", "weinmeile", "genussmeile"]):
        features["local_festival"] = 0.45
    if "antikmarkt" in text:
        features["antique_market"] = 0.3
    if ev.get("city") == "Bonn":
        features["bonn_local"] = 0.1
    return features


def _priority_bonus(ev: Mapping[str, Any]) -> float:
    stored = ev.get("priority_bonus")
    if (
        isinstance(ev.get("ranking_features"), dict)
        and isinstance(stored, int | float)
        and not isinstance(stored, bool)
    ):
        return float(stored)
    return sum(ranking_features(ev).values())


PREFERRED_ORDER = [
    ("Nightlife & Electronic", "🌙"),
    ("Concerts & Live Music", "🎵"),
    ("Exhibitions & Museums", "🏛️"),
    ("Talks, Community & Culture", "🧠"),
    ("Walks, Markets & Outdoor", "🚶"),
    ("Other", "📌"),
]
