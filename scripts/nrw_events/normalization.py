"""Shared text normalization for stable comparison keys."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from html import unescape
from typing import Mapping

_GERMAN_TRANSLITERATION = str.maketrans({
    "ä": "ae",
    "ö": "oe",
    "ü": "ue",
    "ß": "ss",
})


def comparison_text(value: str, *, separator: str = " ") -> str:
    """Casefold and transliterate text into a punctuation-insensitive key."""
    folded = (value or "").casefold().translate(_GERMAN_TRANSLITERATION)
    ascii_text = unicodedata.normalize("NFKD", folded).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", separator, ascii_text).strip(separator)


@dataclass(frozen=True, slots=True)
class VenueRecord:
    """One auditable place identity shared by every source adapter."""

    id: str
    display_name: str
    aliases: tuple[str, ...] = ()
    city: str = ""
    district: str = ""
    venue_type: str = ""
    address: str = ""
    latitude: float | None = None
    longitude: float | None = None


@dataclass(frozen=True, slots=True)
class VenueResolution:
    """Canonical venue fields ready for the public event contract."""

    venue: str
    venue_id: str = ""
    venue_address: str = ""
    venue_district: str = ""
    venue_type: str = ""
    venue_latitude: float | None = None
    venue_longitude: float | None = None


def _venue(
    venue_id: str,
    display_name: str,
    *aliases: str,
    city: str = "",
    district: str = "",
    venue_type: str = "",
    address: str = "",
    coordinates: tuple[float, float] | None = None,
) -> VenueRecord:
    latitude, longitude = coordinates or (None, None)
    return VenueRecord(
        venue_id,
        display_name,
        aliases,
        city,
        district,
        venue_type,
        address,
        latitude,
        longitude,
    )


# The first 17 records come from the two Bonn open-data GeoJSON layers already
# used by the importer (OD=4490 and OD=4489). The remaining records cover the
# most frequent stable places in the current feed. Missing facts stay empty;
# the registry never guesses coordinates or addresses from a broad category.
VENUE_REGISTRY: tuple[VenueRecord, ...] = (
    _venue("oper-bonn", "Oper Bonn", city="Bonn", district="Bonn", venue_type="theater", address="Am Boeselagerhof 1, 53111 Bonn", coordinates=(50.7366531557, 7.10680608)),
    _venue("schauspielhaus-bad-godesberg", "Schauspielhaus Bad Godesberg", "Schauspielhaus", city="Bonn", district="Bonn-Bad Godesberg", venue_type="theater", address="Am Michaelshof 9, 53177 Bonn", coordinates=(50.6832698865, 7.1538043057)),
    _venue("werkstattbuehne-bonn", "Werkstattbühne", city="Bonn", district="Bonn", venue_type="theater", address="Rheingasse 1, 53111 Bonn", coordinates=(50.7363281468, 7.1066077703)),
    _venue("contra-kreis-theater", "Contra-Kreis-Theater", city="Bonn", district="Bonn", venue_type="theater", address="Am Hof 3-5, 53113 Bonn", coordinates=(50.7333174226, 7.1011347839)),
    _venue("kleines-theater-bad-godesberg", "Kleines Theater Bad Godesberg", "Kleines Theater", city="Bonn", district="Bonn-Bad Godesberg", venue_type="theater", address="Koblenzer Straße 78, 53177 Bonn", coordinates=(50.6808563311, 7.1554770039)),
    _venue("junges-theater-bonn", "Junges Theater Bonn", city="Bonn", district="Bonn-Beuel", venue_type="theater", address="Hermannstraße 50, 53225 Bonn", coordinates=(50.7364969604, 7.116846546)),
    _venue("theater-im-ballsaal", "Theater im Ballsaal", city="Bonn", district="Bonn", venue_type="theater", address="Frongasse 9, 53121 Bonn", coordinates=(50.7274839852, 7.0746502028)),
    _venue("theater-im-keller-bonn", "Theater im Keller (tik)", "Theater im Keller", "tik Bonn", city="Bonn", district="Bonn-Hardtberg", venue_type="theater", address="Rochusstraße 30, 53123 Bonn", coordinates=(50.7203595265, 7.0587439738)),
    _venue("kulturzentrum-brotfabrik", "Kulturzentrum Brotfabrik", "Brotfabrik Bonn", "Brotfabrik Bühne Bonn", city="Bonn", district="Bonn-Beuel", venue_type="cultural_center", address="Kreuzstraße 16, 53225 Bonn", coordinates=(50.7409106202, 7.1236954826)),
    _venue("euro-theater-central", "Euro Theater Central", city="Bonn", district="Bonn", venue_type="theater", address="Budapester Straße 19, 53111 Bonn", coordinates=(50.7360134126, 7.0944407837)),
    _venue("theater-marabu", "Theater Marabu", city="Bonn", district="Bonn-Beuel", venue_type="theater", address="Kreuzstraße 16, 53225 Bonn", coordinates=(50.7410341682, 7.1240597235)),
    _venue("pantheon-theater", "Pantheon Theater", "Pantheon", city="Bonn", district="Bonn-Beuel", venue_type="theater", address="Siegburger Straße 42, 53229 Bonn", coordinates=(50.7397197207, 7.1326091857)),
    _venue("haus-der-springmaus", "Haus der Springmaus", city="Bonn", district="Bonn", venue_type="theater", address="Frongasse 8-10, 53121 Bonn", coordinates=(50.7273918984, 7.0744090056)),
    _venue("kulturzentrum-tapetenfabrik", "Kulturzentrum Tapetenfabrik", city="Bonn", district="Bonn-Beuel", venue_type="cultural_center", coordinates=(50.7422640595, 7.1271582042)),
    _venue("gop-variete-bonn", "GOP Varieté", "GOP Varieté Bonn", city="Bonn", district="Bonn", venue_type="theater", address="Karl-Carstens-Straße 1, 53113 Bonn", coordinates=(50.7189927791, 7.1220603618)),
    _venue("malentes-theaterpalast", "Malentes Theaterpalast", city="Bonn", district="Bonn-Beuel", venue_type="theater", address="Holzlarer Weg 42, 53229 Bonn", coordinates=(50.743436371, 7.1597730589)),
    _venue("rheinbuehne-kabarett", "Rheinbühne Kabarett", "Rheinbühne", city="Bonn", district="Bonn", venue_type="theater", address="Oxfordstraße 20-22, 53111 Bonn", coordinates=(50.7376361538, 7.0987225323)),
    _venue("haus-der-geschichte-bonn", "Haus der Geschichte", city="Bonn", venue_type="museum"),
    _venue("arp-museum-bahnhof-rolandseck", "Arp Museum Bahnhof Rolandseck", city="Remagen", venue_type="museum"),
    _venue("stadtmuseum-siegburg", "Stadtmuseum im Kulturhaus", city="Siegburg", venue_type="museum"),
    _venue("kunstmuseum-bonn", "Kunstmuseum Bonn", city="Bonn", venue_type="museum", address="Helmut-Kohl-Allee 2, Bonn"),
    _venue("bundeskunsthalle", "Bundeskunsthalle", city="Bonn", venue_type="museum"),
    _venue("repair-cafe-mva-bonn", "Repair Café MVA Bonn", city="Bonn", venue_type="workshop"),
    _venue("museum-koenig-bonn", "Museum Koenig Bonn", city="Bonn", venue_type="museum"),
    _venue("lvr-landesmuseum-bonn", "LVR-LandesMuseum Bonn", "LVR-LandesMuseum", city="Bonn", venue_type="museum"),
    _venue("interim-zentralbibliothek-koeln", "Interim Zentralbibliothek", city="Köln", venue_type="library"),
    _venue("brueckenforum-bonn", "Brückenforum Bonn", "Brückenforum", city="Bonn", district="Bonn-Beuel", venue_type="event_venue"),
    _venue("arkadenhof-universitaet-bonn", "Arkadenhof Universität Bonn", city="Bonn", venue_type="university"),
    _venue("kult41", "KULT41", city="Bonn", venue_type="cultural_center"),
    _venue("selbstwerk-bonn", "Selbstwerk Bonn", city="Bonn", venue_type="workshop"),
    _venue("rex-lichtspieltheater", "Rex-Lichtspieltheater", city="Bonn", venue_type="cinema"),
    _venue("arithmeum-bonn", "Arithmeum", "Arithmeum - rechnen einst und heute", city="Bonn", venue_type="museum", address="Lennestraße 2, 53113 Bonn"),
    _venue("die-werke-bonn", "Die WERKE Bonn", city="Bonn", venue_type="workshop"),
    _venue("botanische-gaerten-bonn", "Botanische Gärten Bonn", city="Bonn", venue_type="garden"),
    _venue("rheinaue-bonn", "Rheinaue", "Freizeitpark Rheinaue", city="Bonn", venue_type="park", coordinates=(50.7106, 7.1283)),
    _venue("haus-der-jugend-bonn", "Haus der Jugend", city="Bonn", venue_type="cultural_center", address="Reuterstraße 100, 53129 Bonn"),
    _venue("kulturzentrum-hardtberg", "Kulturzentrum Hardtberg", "Hardtberger Kulturzentrum", "Kulturzentrum Hardtberg e.v.", city="Bonn", district="Bonn-Hardtberg", venue_type="cultural_center"),
    _venue("museum-august-macke-haus", "Museum August Macke Haus", city="Bonn", venue_type="museum"),
    _venue("rhein-sieg-forum", "RHEIN SIEG FORUM", city="Siegburg", venue_type="event_venue"),
    _venue("stadthalle-remagen", "Stadthalle Remagen", city="Remagen", venue_type="event_venue"),
    _venue("internationaler-club-bonn", "Internationaler Club", "International Club", city="Bonn", venue_type="university", address="Poppelsdorfer Allee 53, Bonn"),
    _venue("annaplatz-bad-honnef", "Annaplatz", "Anna-Platz", "Anna-Platz Rommersdorf", city="Bad Honnef", venue_type="public_space", address="Rommersdorfer Straße 90a, 53604 Bad Honnef"),
)


_VENUE_BY_ID = {venue.id: venue for venue in VENUE_REGISTRY}
_VENUE_BY_ALIAS: dict[str, VenueRecord] = {}
for _record in VENUE_REGISTRY:
    for _alias in (_record.display_name, *_record.aliases):
        _key = comparison_text(_alias)
        if _key in _VENUE_BY_ALIAS and _VENUE_BY_ALIAS[_key] != _record:
            raise ValueError(f"duplicate venue alias: {_alias}")
        _VENUE_BY_ALIAS[_key] = _record


_TAG = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"\s+")
_POSTCODE = re.compile(r"\b\d{5}\b")
_COUNTRY_OR_REGION = re.compile(
    r"^(?:d|de|deutschland|germany|nrw|nordrhein-westfalen|rlp|rheinland-pfalz)\.?$",
    re.IGNORECASE,
)
_ROOM_DETAIL = re.compile(
    r"\b(?:raum(?:nummer)?|seminarraum|zimmer|etage|stock(?:werk)?|ebene)\b",
    re.IGNORECASE,
)
_STREET = re.compile(
    r"(?:\b[\wäöüÄÖÜß-]*(?:straße|strasse|weg|allee|gasse|ufer|platz|markt|chaussee|ring)\b|\b[\wäöüÄÖÜß-]*str\.)",
    re.IGNORECASE,
)


def _clean_venue_text(value: str) -> str:
    return _SPACE.sub(" ", unescape(_TAG.sub(" ", value or ""))).strip(" ,;·-–—")


def _same_place(left: str, right: str) -> bool:
    left_key = comparison_text(left)
    right_key = comparison_text(right)
    return bool(left_key and right_key and left_key == right_key)


def _compatible_city(left: str, right: str) -> bool:
    left_key = comparison_text(left)
    right_key = comparison_text(right)
    return bool(left_key and right_key and (
        left_key == right_key
        or left_key.startswith(right_key + " ")
        or right_key.startswith(left_key + " ")
    ))


def _record_for(value: str, city: str, explicit_id: str = "") -> VenueRecord | None:
    if explicit_id and explicit_id in _VENUE_BY_ID:
        return _VENUE_BY_ID[explicit_id]
    candidates = [value]
    first_segment = value.split(",", 1)[0].strip()
    if first_segment and first_segment != value:
        candidates.append(first_segment)
    for candidate in candidates:
        record = _VENUE_BY_ALIAS.get(comparison_text(candidate))
        if not record:
            continue
        if (not city or not record.city or _compatible_city(record.city, city)
                or _same_place(candidate, city)):
            return record
    return None


def _looks_like_address(segment: str, *, first: bool = False) -> bool:
    if _POSTCODE.search(segment):
        return True
    if not first and re.search(r"\d", segment):
        return True
    if not first:
        return bool(_STREET.search(segment))
    return any(
        re.match(r"\s*\d", segment[match.end():])
        for match in _STREET.finditer(segment)
    )


def _split_inline_address(segment: str) -> tuple[str, str]:
    """Split ``Venue Name Street 1`` while leaving pure addresses unnamed."""
    candidates = [match.start() for match in _STREET.finditer(segment)
                  if re.search(r"\d", segment[match.start():])]
    postcode = _POSTCODE.search(segment)
    if postcode:
        candidates.append(postcode.start())
    candidates = [position for position in candidates if position > 0]
    if not candidates:
        return segment, ""
    position = max(candidates)
    name = segment[:position].strip(" ,;·-–—(")
    address = segment[position:].strip(" ,;·-–—)")
    return (name, address) if name and address else (segment, "")


def _split_venue(value: str, city: str) -> tuple[str, str]:
    segments = [part.strip(" ,;·-–—") for part in value.split(",")]
    segments = [part for part in segments if part]
    if not segments:
        return "", ""

    inline_name, inline_address = _split_inline_address(segments[0])
    if inline_address:
        segments = [inline_name, inline_address, *segments[1:]]

    first_address = len(segments)
    for index, segment in enumerate(segments):
        if _looks_like_address(segment, first=index == 0):
            first_address = index
            break
    if first_address == 0:
        name_segments: list[str] = []
        address_segments = segments
    else:
        name_segments = segments[:first_address]
        address_segments = segments[first_address:]

    if name_segments:
        unique_name_segments = [name_segments[0]]
        seen_name_segments = {comparison_text(name_segments[0])}
        for segment in name_segments[1:]:
            key = comparison_text(segment)
            if (_ROOM_DETAIL.search(segment) or _same_place(segment, city)
                    or not key or key in seen_name_segments):
                continue
            seen_name_segments.add(key)
            unique_name_segments.append(segment)
        name_segments = unique_name_segments
    address_parts: list[str] = []
    seen_address_parts: set[str] = set()
    for segment in address_segments:
        if _COUNTRY_OR_REGION.match(segment) or _ROOM_DETAIL.search(segment):
            continue
        key = comparison_text(segment)
        if not key or key in seen_address_parts:
            continue
        seen_address_parts.add(key)
        address_parts.append(segment)

    # Source strings commonly delimit postcode and municipality separately
    # (``Street 1, 53111, Bonn``). Keep the public address conventional and,
    # when a source only provides a bare postcode, retain its explicit event
    # municipality instead of silently dropping that useful context.
    if len(address_parts) >= 2 and re.fullmatch(r"\d{5}", address_parts[-2]):
        address_parts[-2:] = [f"{address_parts[-2]} {address_parts[-1]}"]
    elif address_parts and re.search(r"(?:^|, )\d{5}$", address_parts[-1]) and city:
        address_city = "Bonn" if comparison_text(city).startswith("bonn ") else city
        postcode = address_parts[-1]
        if len(address_parts) >= 2 and _compatible_city(address_parts[-2], address_city):
            address_parts[-2:] = [f"{postcode} {address_city}"]
        else:
            address_parts[-1] = f"{postcode} {address_city}"

    return ", ".join(name_segments), ", ".join(address_parts)


def resolve_venue(
    value: str,
    city: str = "",
    *,
    explicit_id: str = "",
) -> VenueResolution:
    """Resolve source venue text without inventing facts for unknown places."""
    cleaned = _clean_venue_text(value)
    record = _record_for(cleaned, city, explicit_id)
    parsed_name, parsed_address = _split_venue(cleaned, city)
    if record:
        return VenueResolution(
            record.display_name,
            record.id,
            record.address or parsed_address,
            record.district,
            record.venue_type,
            record.latitude,
            record.longitude,
        )
    if _same_place(parsed_name, city):
        parsed_name = ""
    return VenueResolution(parsed_name, venue_address=parsed_address)


def canonical_venue_id(event: Mapping[str, object]) -> str:
    """Return a stable identity for high-confidence recurring venue aliases.

    Sources describe the same place as a district, plaza collection, street
    address, or colloquial venue name. Keep this deliberately small and
    auditable: explicit source-provided identities win, followed by aliases
    backed by verified recurring event records.
    """
    explicit = str(event.get("venue_id") or "").strip()
    if explicit:
        return explicit

    title = comparison_text(str(event.get("title") or ""), separator="")
    venue = comparison_text(str(event.get("venue") or ""), separator="")
    venue_address = comparison_text(
        str(event.get("venue_address") or ""), separator=""
    )
    city = comparison_text(str(event.get("city") or ""), separator="")
    description = comparison_text(
        str(event.get("description") or ""),
        separator="",
    )
    text = f"{title}{venue}{venue_address}{city}{description}"

    # The Rigal'sche Wiese is inside Bad Godesberg, but it is not the generic
    # Innenstadt market area and therefore must be resolved first.
    if (
        (
            city in {"bonn", "bonnbadgodesberg", "badgodesberg"}
            or "badgodesberg" in text
        )
        and (
            "rigal" in text
            or "friedrichebertstrasse32" in text
        )
    ):
        return "rigalsche-wiese-bad-godesberg"

    if (
        "badgodesberg" in text
        and ("antik" in title or "troedelmarkt" in title)
        and any(
            marker in text
            for marker in (
                "badgodesbergerinnenstadt",
                "theaterplatz",
                "amfronhof",
                "michaelshof",
                "fussgaengerzone",
            )
        )
    ):
        return "bad-godesberg-innenstadt"

    if "friedensplatz" in venue and city == "bonn":
        return "friedensplatz-bonn"

    if (
        city == "troisdorf"
        and "hitmarkt" in text
        and any(
            marker in text
            for marker in ("rottersee", "spicherstrasse101", "hitmarkt")
        )
    ):
        return "hit-markt-rotter-see"

    if (
        city == "linzamrhein"
        and "antik" in title
        and "markt" in title
    ):
        return "innenstadt-linz"

    return ""
