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
    ruhrguide, troisdorf, regional,
)

SOURCES = {
    # Structured APIs / feeds (highest trust)
    "Köln API": koeln.fetch,
    "Bonn.de Events": bonn.fetch_events_json,   # full official calendar (JSON) — primary
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
    "ionas4 regional": regional.fetch_ionas4,
    "SiteKit regional": regional.fetch_sitekit,
    "Standard regional feeds": regional.fetch_standard_feeds,
    "Regional HTML calendars": regional.fetch_html_sources,
    "Deskline regional": regional.fetch_deskline_and_venues,
    "Regional venues": regional.fetch_more_venues,
    "Bonn.jetzt": bonnjetzt.fetch,
    "Ruhr-Guide": ruhrguide.fetch,
    # Web-search fallbacks (lowest trust)
    "Exa Search": search.fetch_exa,
    "Grok Search": search.fetch_grok,
}
