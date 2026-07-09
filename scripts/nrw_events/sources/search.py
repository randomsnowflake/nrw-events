"""
Web-search fallbacks for obscure local/province events the scrapers miss.

fetch_exa()  — Exa neural search (default; needs EXA_API_KEY)
fetch_grok() — xAI Grok agentic web search (opt-in: NRW_EVENTS_ENABLE_GROK=1 + XAI_API_KEY)

Both feed results through common.search_result_event(), which requires an
in-window date signal and a real topical signal before keeping anything.
The query templates below interpolate the current month/year — they contain
neighbourhood/town *keywords*, never event names or fixed dates.
"""

import os

from .. import common


def search_queries() -> list:
    """Shared local/province/outdoor fallback queries (month/year interpolated)."""
    month = common.TODAY.strftime("%B %Y")
    year = common.TODAY.strftime("%Y")
    return [
        f"Veranstaltungen Bonn Wochenende {month} Stadtteilfest Dorffest Markt Konzert Ausstellung",
        f"Bonn Poppelsdorf Endenich Beuel Bad Godesberg Kessenich Dottendorf Fest Meile Markt {month}",
        f"Bonn Nordstadt Südstadt Altstadt Tannenbusch Auerberg Röttgen Stadtteil Veranstaltung {month}",
        f"Bonn Konzert Club Party Live-Musik Indie Electronic Kulturzentrum {month}",
        f"Bonn Brotfabrik Pantheon Harmonie Bla Theater Lesung Comedy Kabarett Programm {month}",
        f"Bonn Museum Ausstellung Vernissage Kunstmuseum Bundeskunsthalle LVR Haus der Geschichte {month}",
        f"Bonn Flohmarkt Trödelmarkt Wochenmarkt Bauernmarkt Kunsthandwerkermarkt {month}",
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
            )
            for result in data.get("results", []):
                ev = common.search_result_event(
                    result.get("title") or "", result.get("url") or "",
                    f"{result.get('publishedDate') or ''} {result.get('text') or result.get('summary') or ''}",
                    source, 0.58)
                if ev:
                    events.append(ev)
        except Exception as e:
            common.log_source_error(f"{source} ({query[:30]}...)", e)
    return events


def fetch_grok() -> list:
    source = "Grok Search"
    api_key = os.environ.get("XAI_API_KEY", "")
    if not api_key:
        common.log_source_disabled(source, "disabled: XAI_API_KEY is not configured")
        return []
    if os.environ.get("NRW_EVENTS_ENABLE_GROK", "").lower() not in {"1", "true", "yes"}:
        common.log_source_disabled(source, "disabled: set NRW_EVENTS_ENABLE_GROK=1 to enable Grok search")
        return []
    events = []
    system_prompt = (
        "Find concrete dated events near Bonn, Germany within ~75km. "
        "Prioritize small local/outdoor/province events: Königswinter, Siebengebirge, Ahrtal, Andernach, "
        "hikes, wine walks, markets, festivals, guided tours. Return only a JSON array of objects with "
        "title, date, city, venue, description, url. Exclude static tourism pages without a specific date."
    )
    for query in search_queries()[:2]:
        try:
            data = common.post_json(
                "https://api.x.ai/v1/responses",
                {"model": "grok-4-1-fast",
                 "input": [{"role": "developer", "content": system_prompt},
                           {"role": "user", "content": query}],
                 "tools": [{"type": "web_search"}]},
                timeout=35, headers={"Authorization": f"Bearer {api_key}"},
            )
            text_parts = []
            for item in data.get("output", []):
                if item.get("type") == "message" and item.get("role") == "assistant":
                    for part in item.get("content", []):
                        if part.get("type") in {"output_text", "text"} and part.get("text"):
                            text_parts.append(part["text"])
            for c in common.extract_json_array("\n".join(text_parts)):
                if not isinstance(c, dict):
                    continue
                title = c.get("title") or c.get("name") or ""
                link = c.get("url") or c.get("link") or ""
                desc = " ".join(str(c.get(k) or "") for k in ["date", "venue", "description"])
                ev = common.search_result_event(title, link, desc, source, 0.7)
                if ev:
                    ev["date"] = str(c.get("date") or "")[:40]
                    ev["venue"] = str(c.get("venue") or "")[:120]
                    ev["city"] = str(c.get("city") or ev["city"])[:80].title()
                    events.append(ev)
        except Exception as e:
            common.log_source_error(f"{source} ({query[:30]}...)", e)
    return events
