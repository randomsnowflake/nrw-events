"""
Source registry.

``SOURCES`` maps a display name to a ``fetch() -> list[dict]`` callable. To add a
source: write a module in this package exposing a fetch function, then register
it here. To remove one: delete its line (and ideally its module).

Each fetcher must be self-contained, swallow its own errors (return [] on
failure), and return events already built via ``common.make_event`` or shaped
like one. Ordering is irrelevant — the runner fans them out in parallel.
"""

from . import (
    koeln, bonn, harmonie, haus_der_geschichte, meetup, bonnjetzt,
    koenigswinter, siebengebirge, flohmarkt, bundeskunsthalle, search,
    meckenheim, much, naturregion_sieg, siegburg,
    ruhrguide, regional_feeds, regional_html, regional_ionas4,
    regional_sitekit, regional_tourism, regional_venues, requested_venues,
    bonn_venues, bonn_food, radiobonn, bonn_districts, cinema_specials, uni_bonn,
    kinderflohmarkt, grote_hiller, hofflohmaerkte, coelln_konzept, lampert,
    hoffloh_bonn, okken, geide, bonner_weihnachtsmarkt, katharinenhof,
    kleines_theater, theater_bonn, junges_theater_bonn, theater_marabu,
    theater_im_ballsaal, tik_bonn,
    max7, afterjobparty, rheinevents, salsainbonn,
)
from ..source_specs import AdapterType, SourceSpec, adapter_for

SOURCE_SPECS = (
    SourceSpec("troisdorf", "Troisdorf", ("https://www.troisdorf.de/de/kalender/startseite/event.ics?weekends=false&tagMode=ANY",), AdapterType.ICAL, "Troisdorf", "troisdorf lokal kultur markt", 0.95),
    SourceSpec("wachtberg", "Wachtberg", ("https://www.wachtberg.de/kalender/veranstaltungen/event.ics?weekends=false&tagMode=ALL",), AdapterType.ICAL, "Wachtberg"),
    SourceSpec("hennef", "Hennef", ("https://www.hennef.de/veranstaltungen/",), AdapterType.JSON_LD, "Hennef", "lokal veranstaltung markt kultur outdoor", 0.95, 20),
)

STANDARD_SOURCES = {spec.display_name: adapter_for(spec) for spec in SOURCE_SPECS}

CUSTOM_SOURCES = {
    # High-trust official feeds/calendars
    "Köln Open Data": koeln.fetch,
    "Bonn.de Events": bonn.fetch_events,        # official calendar HTML listing — primary
    "Bonn.de Sports": bonn.fetch_sports,        # sport/active teaser page, not covered by main calendar filters
    "Universität Bonn": uni_bonn.fetch,          # official iCal plus cached first-party detail pages
    "Harmonie Bonn": harmonie.fetch,
    "Haus der Geschichte": haus_der_geschichte.fetch,
    "Kleines Theater Bad Godesberg": kleines_theater.fetch,
    "Theater Bonn": theater_bonn.fetch,
    "Junges Theater Bonn": junges_theater_bonn.fetch,
    "Theater Marabu": theater_marabu.fetch,
    "Theater im Ballsaal": theater_im_ballsaal.fetch,
    "TiK Theater im Keller": tik_bonn.fetch,
    "Meetup": meetup.fetch,
    # Live structured scrapers (JSON-LD / iCal / structured HTML)
    "Rheinauen-Flohmarkt": flohmarkt.fetch,
    "Kinderflohmarkt.com": kinderflohmarkt.fetch,
    "Grote & Hiller": grote_hiller.fetch,
    "Hofflohmärkte Köln": hofflohmaerkte.fetch,
    "Cölln Konzept": coelln_konzept.fetch,
    "Lampert Märkte": lampert.fetch,
    "HofFloh Bonn": hoffloh_bonn.fetch,
    "Okken Märkte": okken.fetch,
    "Geide Märkte": geide.fetch,
    "Bonner Weihnachtsmarkt": bonner_weihnachtsmarkt.fetch,
    "Katharinenhof Flohmarkt": katharinenhof.fetch,
    "Tanzschule Max7": max7.fetch,
    "AfterJobParty Bonn": afterjobparty.fetch,
    "RheinEvents": rheinevents.fetch,
    "Salsa in Bonn": salsainbonn.fetch,
    "Bonn district festivals": bonn.fetch_press_festivals,
    "Bundeskunsthalle": bundeskunsthalle.fetch,
    "Königswinter": koenigswinter.fetch,
    "VVS Siebengebirge": siebengebirge.fetch_vvs,
    "Naturregion Sieg": naturregion_sieg.fetch,  # tourism HTML tiles (Sieg valley)
    "Meckenheim": meckenheim.fetch,    # Voreifel HTML calendar
    "Much": much.fetch,                # Bergisches Land HTML calendar
    "Siegburg": siegburg.fetch,        # iCal plus official event-detail descriptions
    "ionas4 regional": regional_ionas4.fetch,
    "SiteKit regional": regional_sitekit.fetch,
    "Standard regional feeds": regional_feeds.fetch,
    "Regional HTML calendars": regional_html.fetch,
    "Deskline regional": regional_tourism.fetch,
    "Regional venues": regional_venues.fetch,
    "Requested venue calendars": requested_venues.fetch,
    "Bonn venue calendars": bonn_venues.fetch,
    "Curated cinema specials": cinema_specials.fetch,
    "Craftquelle Bonn": bonn_food.fetch_craftquelle,
    "BFF Bonner Schifffahrt": bonn_food.fetch_bff,
    "vomFASS Bonn": bonn_food.fetch_vomfass,
    "Biertasting Bonn": bonn_food.fetch_biertasting,
    "Ludwig's Bonn": bonn_food.fetch_ludwigs,
    "Redüttchen": bonn_food.fetch_reduettchen,
    "Street Food Bonn": bonn_food.fetch_street_food,
    "Bonn.jetzt": bonnjetzt.fetch,
    "Radio Bonn/Rhein-Sieg": radiobonn.fetch,
    "Bürgerverein Vilich-Müldorf": bonn_districts.fetch_vilich_mueldorf,
    "Beuel.net": bonn_districts.fetch_beuel,
    "Bad Godesberg Stadtmarketing": bonn_districts.fetch_bad_godesberg,
    "Hardtberg Kultur": bonn_districts.fetch_hardtberg,
    "BSV Roleber": bonn_districts.fetch_roleber,
    "Ruhr-Guide": ruhrguide.fetch,
    # Web-search fallbacks (lowest trust)
    "Exa Search": search.fetch_exa,
    "Grok Search": search.fetch_grok,
}

_SHADOWED_SOURCES = STANDARD_SOURCES.keys() & CUSTOM_SOURCES.keys()
assert not _SHADOWED_SOURCES, f"shadowed sources: {sorted(_SHADOWED_SOURCES)}"

SOURCES = {**STANDARD_SOURCES, **CUSTOM_SOURCES}
SOURCE_IDS = {spec.display_name: spec.id for spec in SOURCE_SPECS} | {
    name: name.lower().replace(" ", "-") for name in CUSTOM_SOURCES
}
SOURCE_IDS["Universität Bonn"] = "uni-bonn"
