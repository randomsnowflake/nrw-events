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
    siegburg, hennef, meckenheim, wachtberg, much, naturregion_sieg,
    ruhrguide, troisdorf, regional_feeds, regional_html, regional_ionas4,
    regional_sitekit, regional_tourism, regional_venues, requested_venues,
    eventbrite, rausgegangen, bonn_venues,
)

SOURCES = {
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
    "Siegburg": siegburg.fetch,        # Kreisstadt iCal (Rhein-Sieg)
    "Troisdorf": troisdorf.fetch,      # town iCal (Rhein-Sieg)
    "Naturregion Sieg": naturregion_sieg.fetch,  # tourism HTML tiles (Sieg valley)
    "Hennef": hennef.fetch,            # town JSON-LD (Rhein-Sieg)
    "Meckenheim": meckenheim.fetch,    # Voreifel HTML calendar
    "Wachtberg": wachtberg.fetch,      # Voreifel iCal
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
    "Eventbrite Party": eventbrite.fetch,
    "Bonn.jetzt": bonnjetzt.fetch,
    "Ruhr-Guide": ruhrguide.fetch,
    # Web-search fallbacks (lowest trust)
    "Exa Search": search.fetch_exa,
    "Grok Search": search.fetch_grok,
}
