"""Owning implementation of dedup rules; core is a compatibility facade."""

from __future__ import annotations

from .ai_contracts import TARGET_SOURCE_IDS

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


# Sources whose copy is never published: their generated fallback sentence must
# not replace a primary duplicate's description.
_RESTRICTED_FALLBACK_SOURCE_IDS = frozenset({
    "beuel-net", "bonn-de-events", "bonn-de-sports",
}) | TARGET_SOURCE_IDS


_REVIEWED_OCCURRENCE_SOURCE_TITLE_ALIASES = {
    # Festival identity reviewed against b-future.org on 2026-10-02. Keep
    # the detailed public calendar slot over the sparse annual press overview.
    ("bonn-district-festivals", "bfuturejournalismusfestival"):
        "bonn-bfuture-festival",
    ("bonn-de-events", "bfuturefestival"):
        "bonn-bfuture-festival",
    ("marktcom", "familienferienflohmarktbonn"):
        "bonn-rigalsche-wiese-flohmarkt",
    # 11.11. Marktplatz opening, reviewed 2026-10-13: the organiser titles it
    # with the session motto, Bonn.de as "Sessionseröffnung Bonn".
    ("karneval-in-bonn", "eroeffnungderkarnevalssessiontraditionemhaetzefastelovendheundjetzt"):
        "bonn-karneval-sessionseroeffnung-marktplatz",
    ("bonn-de-events", "sessionseroeffnungbonn"):
        "bonn-karneval-sessionseroeffnung-marktplatz",
    # The city's annual press overview, reviewed 2026-10-15 (live duplicate).
    ("bonn-district-festivals", "karnevalseroeffnung"):
        "bonn-karneval-sessionseroeffnung-marktplatz",
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
        "urslipfisterundjoroloffbandpeggymarchfrauhuggenbergerundichmusikshow",
    ): "pantheon-ursli-pfister-peggy-march",
    ("rathausmusik", "musikaufderrathaustreppetheroots"):
        "rathausmusik-2026-09-10-primary",
    ("beuel-net", "musikaufderrathaustreppethecottiesbeatrb"):
        "rathausmusik-2026-09-10-primary",
    # Reviewed 2026-10-20: Theater Bonn plays 25.10. at 18:00; Bonn.de's
    # mirror of the production says 19:30.
    ("theater-bonn", "dieleidendesjungenwerther"): "theater-bonn-werther",
    ("bonn-de-events", "dieleidendesjungenwerther"): "theater-bonn-werther",
    # Reviewed 2026-10-20: the ADFC page runs the Quartiersmanagement's
    # workshop the Repair Cafés list. Its header says Ellesdorfer Straße 50,
    # its own copy "Garage an der Ellesdorfer Str. 40".
    ("repair-cafes-bonn", "fahrradselbsthilfewerkstattlannesdorf"):
        "lannesdorf-fahrrad-selbsthilfe-werkstatt",
    ("adfc-bonn", "fahrradselbsthilfewerkstattlannesdorfmehlem"):
        "lannesdorf-fahrrad-selbsthilfe-werkstatt",
    # Reviewed 2026-10-20: Bonn.de's "Fotowalk" is the Institut français and
    # LVR-LandesMuseum walk from Colmantstraße.
    ("institut-francais-bonn", "fotowalkfranzoesischesbonn"):
        "institut-francais-fotowalk",
    ("bonn-de-events", "fotowalk"):
        "institut-francais-fotowalk",
    # Reviewed 2026-10-24: Beuel.net lists the Gewerbegemeinschaft's
    # last-Sunday market (beuelhats.de) that Brückenforum runs on the square.
    ("brueckenforum-bonn", "flohundtroedelmarktbeuelerrathausplatz"):
        "beuel-rathausplatz-flohmarkt",
    ("beuel-net", "flohmarktambeuelerrathaus"):
        "beuel-rathausplatz-flohmarkt",
}


# Alias families whose publishers are reviewed to disagree on the start clock
# or house number of one occurrence, mapped to the source whose facts were
# confirmed. They fold across those conflicts when two sources list the family
# on the same day, and the confirmed source's record wins.
_REVIEWED_SOURCE_CONFLICT_FAMILIES = {
    "theater-bonn-werther": "theater-bonn",
    "lannesdorf-fahrrad-selbsthilfe-werkstatt": "repair-cafes-bonn",
}


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
        "rathaus beuel",
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
    """Rank direct/local publishers above aggregators."""
    normalized = " ".join((source or "").casefold().split())
    # Annual district permissions are discovery leads, not current event
    # confirmations. Every independently imported duplicate takes precedence.
    if "bonn district festivals" in normalized:
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
