"""Location normalization, resolution, and distance calculations."""

from __future__ import annotations

import math
import re
from html import unescape
from typing import Optional

from . import config
from .normalization import comparison_text


BONN_LAT, BONN_LON = config.BONN_LAT, config.BONN_LON
MAX_RADIUS_KM = config.MAX_RADIUS_KM
_AMBIGUOUS_CITY_NAMES = frozenset({"grafschaft", "linz", "much", "wissen"})


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
        city_pattern = rf"(?<![a-zäöüß]){re.escape(city)}(?![a-zäöüß])"
        if city not in _AMBIGUOUS_CITY_NAMES and re.search(city_pattern, text_lower):
            return city
        if city in _AMBIGUOUS_CITY_NAMES and (
            text_lower.strip(" ,;-") == city
            or re.search(rf"\b\d{{5}}\s+{re.escape(city)}(?![a-zäöüß])", text_lower)
            or re.search(rf",\s*{re.escape(city)}(?![a-zäöüß])", text_lower)
            or re.search(rf"\bin\s+{re.escape(city)}(?![a-zäöüß])", text_lower)
        ):
            return city
    return None


def district_from_postcode(text: str) -> str:
    """Return the Bonn city district encoded in a postcode found in ``text``."""
    for match in re.findall(r"\b(53\d{3})\b", text or ""):
        district = config.BONN_POSTCODE_DISTRICTS.get(match)
        if district:
            return district
    return ""


def refine_bonn_location(city: str, text: str) -> str:
    """Resolve a bare "Bonn" to its district using a postcode, then a name.

    Most sources only ever say "Bonn". A postal address in the venue is the
    strongest available signal and is checked first; the configured district
    names in :func:`refine_city_from_text` handle the rest.
    """
    if (city or "").strip().casefold() != "bonn":
        return city
    return district_from_postcode(text) or refine_city_from_text(city, text)


def refine_city_from_text(city: str, text: str) -> str:
    """Refine a coarse Bonn location to a configured district found in text.

    This is deliberately driven by the configured geography rather than a list
    of event titles.  The longest district name wins, so e.g. Vilich-Müldorf is
    not reduced to Vilich when both tokens occur.
    """
    coarse = comparison_text(city)
    district_keys = [key for key in config.VENUE_COORDS if key.startswith("bonn-")]
    district_words = {
        key: comparison_text(key.removeprefix("bonn-"))
        for key in district_keys
    }
    if coarse != "bonn" and not coarse.startswith("bonn ") and coarse not in district_words.values():
        return city

    haystack = f" {comparison_text(text)} "
    for key, district in sorted(district_words.items(), key=lambda item: -len(item[1])):
        if f" {district} " in haystack:
            suffix = key.removeprefix("bonn-")
            return "Bonn-" + suffix.title()
    return city
