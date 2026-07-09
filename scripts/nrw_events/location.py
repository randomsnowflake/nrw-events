"""Location normalization, resolution, and distance calculations."""

from __future__ import annotations

import math
import re
from html import unescape
from typing import Optional

from . import config


BONN_LAT, BONN_LON = config.BONN_LAT, config.BONN_LON
MAX_RADIUS_KM = config.MAX_RADIUS_KM


def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distance in km between two latitude/longitude pairs."""
    radius = 6371
    dlat, dlon = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    )
    return radius * 2 * math.asin(math.sqrt(a))


def coords_for_city(city: str) -> tuple:
    """Compatibility fallback for legacy callers that deliberately center unknown cities on Bonn."""
    return config.VENUE_COORDS.get((city or "").lower(), (BONN_LAT, BONN_LON))


def resolve_location(city: str, coords: Optional[tuple] = None) -> tuple[Optional[tuple], str, str]:
    """Resolve an event location without silently treating unknown places as Bonn."""
    if coords is not None:
        try:
            lat, lon = float(coords[0]), float(coords[1])
        except (IndexError, TypeError, ValueError):
            return None, "unresolved", "invalid_explicit_coordinates"
        if -90 <= lat <= 90 and -180 <= lon <= 180:
            return (lat, lon), "exact", "source_coordinates"
        return None, "unresolved", "invalid_explicit_coordinates"
    normalized = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", unescape(city or ""))).strip().lower()
    if normalized in config.VENUE_COORDS:
        return config.VENUE_COORDS[normalized], "known_city", "configured_city"
    return None, "unresolved", "unknown_city"


def guess_city_from_text(text: str) -> Optional[str]:
    """Find a configured city in free text, preferring specific names to Bonn."""
    text_lower = re.sub(r"bundesstadt\s+bonn", " ", (text or "").lower())
    cities = sorted(config.VENUE_COORDS, key=lambda city: (city == "bonn", -len(city)))
    for city in cities:
        if re.search(rf"(?<![a-zäöüß]){re.escape(city)}(?![a-zäöüß])", text_lower):
            return city
    return None
