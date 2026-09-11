"""
Web-search fallbacks for obscure local/province events the scrapers miss.

fetch_exa()  — Exa neural search (default; needs EXA_API_KEY)
fetch_grok() — retired provider, retained only for explicit disabled health reports

Exa feeds results through common.search_result_event(), which requires an
in-window date signal and a real topical signal before keeping anything.
The query templates below interpolate the current month/year — they contain
neighbourhood/town *keywords*, never event names or fixed dates.
"""

import os

from .. import common
from ..health import SourceFetchResult


def search_queries() -> list:
    """Shared local/province/outdoor fallback queries (month/year interpolated)."""
    month = common.runtime_window().start.strftime("%B %Y")
    year = common.runtime_window().start.strftime("%Y")
    return [
        f"Veranstaltungen Bonn Wochenende {month} Stadtteilfest Dorffest Markt Konzert Ausstellung",
        f"Bonn Poppelsdorf Endenich Beuel Bad Godesberg Kessenich Dottendorf Fest Meile Markt {month}",
        f"Bonn Nordstadt Südstadt Altstadt Tannenbusch Auerberg Röttgen Stadtteil Veranstaltung {month}",
        f"Bonn Konzert Club Party Live-Musik Indie Electronic Kulturzentrum {month}",
        f"Bonn Brotfabrik Pantheon Harmonie Bla Theater Lesung Comedy Kabarett Programm {month}",
        f"Bonn Museum Ausstellung Vernissage Kunstmuseum Bundeskunsthalle LVR Haus der Geschichte {month}",
        f"Bonn Flohmarkt Trödelmarkt Antikmarkt Hofflohmarkt Nachtflohmarkt Kunsthandwerkermarkt {month}",
        f"Königswinter Siebengebirge Drachenfels Wanderung Führung Markt Wochenende {month}",
        f"site:vv-siebengebirge.de/veranstaltung Siebengebirge Wanderung Natur Führung {year}",
        f"Ahrtal Ahrweiler Dernau Mayschoss Weinwanderung Weinprobe Weinfest {month}",
        f"site:ahrtal.com/de/events Ahrtal Event Wein Wanderung Führung {year}",
        f"Andernach Bad Honnef Linz Unkel Remagen Open-Air Schlossgarten Markt Wochenende {month}",
        f"Rhein-Sieg-Kreis Siegburg Troisdorf Sankt Augustin Hennef Veranstaltung Fest {month}",
        f"Bonn Umgebung Natur Wanderung Führung Siebengebirge Kottenforst Wochenende {month}",
    ]


def fetch_exa() -> list:
    source = "Exa Search"
    api_key = os.environ.get("EXA_API_KEY", "")
    if not api_key:
        common.log_source_disabled(source, "disabled: EXA_API_KEY is not configured")
        return []
    events = []
    exa_n = int(os.environ.get("NRW_EVENTS_EXA_QUERIES", "10"))
    for query in search_queries()[:exa_n]:
        try:
            data = common.post_json(
                "https://api.exa.ai/search",
                {"query": query, "numResults": 5, "type": "auto",
                 "contents": {"text": {"maxCharacters": 500}}},
                timeout=25, headers={"x-api-key": api_key},
                retry_safe=True,
            )
            for result in data.get("results", []):
                text = result.get("text") or result.get("summary") or ""
                ev = common.search_result_event(
                    result.get("title") or "", result.get("url") or "",
                    text,
                    source, 0.58)
                if ev:
                    events.append(ev)
        except Exception as e:  # noqa: PERF203 - search queries must fail independently
            common.log_source_error(f"{source} ({query[:30]}...)", e)
    return events


def fetch_grok() -> SourceFetchResult:
    """Retired event provider; legacy credentials/opt-in cannot re-enable it."""
    return SourceFetchResult.disabled("Grok event search permanently retired")
