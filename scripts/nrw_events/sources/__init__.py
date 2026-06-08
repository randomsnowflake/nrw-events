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
)

SOURCES = {
    # Structured APIs / feeds (highest trust)
    "Köln API": koeln.fetch,
    "Bonn HTML": bonn.fetch_html,
    "Bonn.de RSS": bonn.fetch_rss,
    "Harmonie Bonn": harmonie.fetch,
    "Meetup": meetup.fetch,
    "Songkick": songkick.fetch,
    # Live structured scrapers (JSON-LD / iCal / structured HTML)
    "Rheinauen-Flohmarkt": flohmarkt.fetch,
    "Bonn district festivals": bonn.fetch_press_festivals,
    "Bundeskunsthalle": bundeskunsthalle.fetch,
    "Königswinter": koenigswinter.fetch,
    "VVS Siebengebirge": siebengebirge.fetch_vvs,
    "Bonn.jetzt": bonnjetzt.fetch,
    # Web-search fallbacks (lowest trust)
    "Exa Search": search.fetch_exa,
    "Grok Search": search.fetch_grok,
}
