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
    koeln, bonn, harmonie, meetup, bonnjetzt, songkick,
    koenigswinter, siebengebirge, flohmarkt, bundeskunsthalle, search,
    meckenheim, much, naturregion_sieg,
    ruhrguide, troisdorf, regional_feeds, regional_html, regional_ionas4,
    regional_sitekit, regional_tourism, regional_venues, requested_venues,
    rausgegangen, bonn_venues, radiobonn,
)
from ..source_specs import AdapterType, SourceSpec, adapter_for

SOURCE_SPECS = (
    SourceSpec("siegburg", "Siegburg", ("https://siegburg.de/kalender/kombinierter-kalender/event.ics?weekends=false&tagMode=ANY",), AdapterType.ICAL, "Siegburg"),
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
    "Harmonie Bonn": harmonie.fetch,
    "Meetup": meetup.fetch,
    "Songkick": songkick.fetch,
    # Live structured scrapers (JSON-LD / iCal / structured HTML)
    "Rheinauen-Flohmarkt": flohmarkt.fetch,
    "Bonn district festivals": bonn.fetch_press_festivals,
    "Bundeskunsthalle": bundeskunsthalle.fetch,
    "Königswinter": koenigswinter.fetch,
    "VVS Siebengebirge": siebengebirge.fetch_vvs,
    "Naturregion Sieg": naturregion_sieg.fetch,  # tourism HTML tiles (Sieg valley)
    "Meckenheim": meckenheim.fetch,    # Voreifel HTML calendar
    "Much": much.fetch,                # Bergisches Land HTML calendar
    "ionas4 regional": regional_ionas4.fetch,
    "SiteKit regional": regional_sitekit.fetch,
    "Standard regional feeds": regional_feeds.fetch,
    "Regional HTML calendars": regional_html.fetch,
    "Deskline regional": regional_tourism.fetch,
    "Regional venues": regional_venues.fetch,
    "Requested venue calendars": requested_venues.fetch,
    "Bonn venue calendars": bonn_venues.fetch,
    "Rausgegangen Party": rausgegangen.fetch,
    "Bonn.jetzt": bonnjetzt.fetch,
    "Radio Bonn/Rhein-Sieg": radiobonn.fetch,
    "Ruhr-Guide": ruhrguide.fetch,
    # Web-search fallbacks (lowest trust)
    "Exa Search": search.fetch_exa,
    "Grok Search": search.fetch_grok,
}

SOURCES = {**STANDARD_SOURCES, **CUSTOM_SOURCES}
SOURCE_IDS = {spec.display_name: spec.id for spec in SOURCE_SPECS} | {
    name: name.lower().replace(" ", "-") for name in CUSTOM_SOURCES
}
