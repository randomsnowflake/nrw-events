"""Owning implementation of dedup rules; core is a compatibility facade."""

from __future__ import annotations

_AGGREGATOR_SOURCE_MARKERS = (
    "bonn.jetzt", "eventbrite", "meetup", "radio bonn", "ruhr-guide",
    "kinderflohmarkt.com",
)


_MARKET_DIRECTORY_SOURCE_MARKERS = (
    "marktcom", "krencky24", "meine-flohmarkt-termine",
    "meine-kunsthandwerker-termine", "flohmarkt-termine", "flohmap",
)


_CIVIC_AGGREGATOR_SOURCE_MARKERS = (
    "bonn.de events", "bonn.de sports", "bonn district festivals",
)


_CIVIC_AGGREGATOR_SOURCE_EXACT = frozenset({"ahrtal"})


_RESTRICTED_FALLBACK_SOURCE_IDS = frozenset({
    "beuel-net", "bonn-de-events", "bonn-de-sports",
})


_REVIEWED_OCCURRENCE_SOURCE_TITLE_ALIASES = {
    ("marktcom", "familienferienflohmarktbonn"):
        "bonn-rigalsche-wiese-flohmarkt",
    ("beuel-net", "festderbeuelervereinepromenadenfest"):
        "beuel-2026-beuelfest-promenadenfest",
    ("beuel-net", "promenadenfestundbeuelfest"):
        "beuel-2026-beuelfest-promenadenfest",
    ("brueckenforum-bonn", "beuelfestundpromenadenfest"):
        "beuel-2026-beuelfest-promenadenfest",
    ("bonn-de-events", "repaircafeholzunddrechselarbeiten"):
        "repair-cafe-mva-woodworking",
    ("repair-cafes-bonn", "holzarbeitenunddrechselnimrepaircafemvabonn"):
        "repair-cafe-mva-woodworking",
    ("bonn-de-events", "repaircaferadschraubenundanderebasteleien"):
        "repair-cafe-mva-general",
    ("repair-cafes-bonn", "repaircafemvabonnfahrradgeraetenaehen"):
        "repair-cafe-mva-general",
    ("bonn-de-events", "akkordeonkonzertvonbonnakko"):
        "hardtberg-bonnakko-concert",
    (
        "hardtberg-kultur",
        "gastkonzertensemblebonnakkomagischeklaengeauftastenundknoepfen",
    ): "hardtberg-bonnakko-concert",
    ("bonn-de-events", "lukasrietzschelsanditz"):
        "haus-der-geschichte-sanditz",
    ("haus-der-geschichte", "buchvorstellungsanditzlukasrietzschel"):
        "haus-der-geschichte-sanditz",
    (
        "pantheon-bonn",
        "diegeschwisterpfisterpraesentierenurslipfisterpeggymarchfrauhuggenbergerundich",
    ): "pantheon-ursli-pfister-peggy-march",
    (
        "bonn-de-events",
        "urslipfisterjoroloffbandpeggymarchfrauhuggenbergerundichmusikshow",
    ): "pantheon-ursli-pfister-peggy-march",
    ("rathausmusik", "musikaufderrathaustreppetheroots"):
        "rathausmusik-2026-09-10-primary",
    ("beuel-net", "musikaufderrathaustreppethecottiesbeatrb"):
        "rathausmusik-2026-09-10-primary",
}


_SEARCH_SOURCE_MARKERS = ("exa search", "grok search")


_REUSED_OVERVIEW_LINK_THRESHOLD = 5


_CITYWIDE_VENUE_ALIAS_FAMILIES = {
    "street-food-festival": (
        frozenset({
            "theaterplatz",
            "bad godesberg",
            "bad godesberg innenstadt",
            "bad godesberger innenstadt",
            "innenstadt bad godesberg",
        }),
    ),
}


_REVIEWED_VENUE_ALIAS_FAMILIES = (
    frozenset({
        "moehneplatz bonn beuel", "moehneplatz", "rathaustreppe",
        "beueler rathaustreppe", "beueler rathaus", "beueler rathausplatz",
    }),
    frozenset({
        "sieglarer marktplatz", "marktplatz sieglar", "troisdorf sieglar",
    }),
)


_VENUE_LOCATION_FIELDS = (
    "venue_id",
    "venue_address",
    "venue_district",
    "venue_type",
    "venue_latitude",
    "venue_longitude",
    "distance_km",
    "location_confidence",
    "location_source",
)


def source_authority(source: str) -> int:
    """Rank direct/local publishers above aggregators and search discovery."""
    normalized = " ".join((source or "").casefold().split())
    if any(marker in normalized for marker in _SEARCH_SOURCE_MARKERS):
        return 0
    if any(marker in normalized for marker in _AGGREGATOR_SOURCE_MARKERS):
        return 1
    if any(marker in normalized for marker in _MARKET_DIRECTORY_SOURCE_MARKERS):
        return 1
    if (
        normalized in _CIVIC_AGGREGATOR_SOURCE_EXACT
        or any(marker in normalized for marker in _CIVIC_AGGREGATOR_SOURCE_MARKERS)
    ):
        return 2
    return 3
