---
name: nrw-events
description: "Discover events, concerts, exhibitions, nightlife, outdoor activities, markets and festivals in Bonn and surrounding NRW cities (75km radius: Köln, Siegburg, Troisdorf, Königswinter, Düsseldorf, Aachen, etc). Use when: 'what's happening this weekend', 'events in Bonn', 'things to do', 'concerts near me', 'exhibitions Köln', 'weekend plans', 'what should we do', 'any cool events', 'nightlife Bonn', 'activities around here'. Also use when the user asks about activities, events, or things to do in or near Bonn. NOT for: trip planning to other regions, or deep-dives on a single venue."
---

# NRW Events

> This file is an optional agent-skill manifest (for assistants that load `SKILL.md`
> skills). The tool is a plain CLI — see [README.md](README.md) to run it directly.
> `{baseDir}` is the skill root (this repo's root).

```bash
bash {baseDir}/scripts/nrw-events.sh [days_ahead]   # default: 3 (weekend)
```

Fetches from Köln Open Data API, Bonn.de HTML/RSS, a built-in recurring Rheinauen-Flohmarkt source, a built-in Bonn local/district recurring source, Ahrtal/Ahrwein calendars plus an AhrWeinWalk highlight source, Bonn.de RSS calendar feed, Harmonie Bonn (Tribe iCal), curated Bonn-area Meetup groups (per-group iCal), Königswinter official calendar, VVS Siebengebirge guided hikes, Tourismus Siebengebirge current dates, Andernach official/tourism pages, Bonn.jetzt, Songkick, Bundeskunsthalle, Exa Search, and optional Grok Search. Bonn.jetzt is especially useful for Bonn's local digital/community events and weekend oddities that bigger feeds miss. The built-in Rheinauen-Flohmarkt source exists because this is a recurring Bonn staple and should not depend on flaky search results. The Ahrtal/Ahrwein sources exist because Ahrweiler/Bad Neuenahr-Ahrweiler/Dernau/Mayschoss are close enough to Bonn to be strong weekend targets even though they are outside NRW; wine walks, vineyard hikes, food/wine festivals, and family side-offers such as the AhrWeinWalk kids quiz should surface. Königswinter/Siebengebirge/Andernach sources exist to catch walks, guided tours, small-town markets, Rhine/open-air events, and province outings missed by the big city calendars. Scores by distance (Bonn=1.0, Königswinter≈0.9, Ahrweiler≈0.74, Köln=0.7, Andernach≈0.5, Düsseldorf=0.4) × category preference (electronic/techno=1.8x, wine/winery/wine-walk=1.4–1.55x, hiking/guided walks/Drachenfels/Siebengebirge=1.3–1.45x, architecture=1.6x, concerts=1.5x, exhibitions=1.4x, kids-only=0.2x). Output: markdown report grouped by category + JSON at `/tmp/nrw-events-latest.json`.

## After Running

Present the **FULL event list** — every event in every category, exactly as the script outputs them. Do NOT trim to highlights or a "best picks" shortlist. The script shows all events by default (no per-section cap).

After the full list, you MAY add a short opinionated "top picks" line at the end — but it never replaces the complete list. Consider: weather (outdoor events), and the user's stated interests (e.g. architecture/museums/electronic music/food tend to rank highest by default).

To trim output for terse contexts, set `NRW_EVENTS_MAX_PER_SECTION=N`.

## Small local / province events

- Prioritize **small local stuff**, not just official concert/exhibition calendars: Stadtteilfeste, Dorffeste, Kirmes, Genussmeilen, Weinmeilen, food/market events, local history walks, garden/nature days, Siebengebirge/Kottenforst/Rhein-side walks, and village/province events around Bonn.
- Keep the script's **Bonn local recurring** source current. It intentionally encodes official annual district-event lists and stable local patterns because these events are often hidden in press pages, association pages, or community posts rather than clean event APIs.
- Poppelsdorf/Endenich/Beuel/Bad Godesberg/Ippendorf/Dransdorf are first-class discovery areas. Events on the Poppelsdorfer Meile/Clemens-August-Straße should be considered highly relevant, even if they are mostly gastro/local/neighbourhood events.
- Search fallback should include neighbourhood and province terms: `Stadtteilfest`, `Dorffest`, `Kirmes`, `Genussmeile`, `Weinmeile`, `Rundgang`, `Führung`, `Natur`, `Kottenforst`, `Siebengebirge`, `Königswinter`, `Drachenfels`, `Petersberg`, `Heisterbach`, `Bad Honnef`, `Andernach`, `Namedy`, `Linz`, `Unkel`, `Remagen`, `Ahrtal`, `Dernau`, `Mayschoss`, `Clemens-August-Straße`, `Poppelsdorf`, `Endenich`, `Beuel`, `Bad Godesberg`.
- Do not blindly include static shop/route pages from search. Prefer pages with date/time/event wording, or explicit event terms like Weinmeile/Stadtteilfest/Dorffest/Kirmes.

## Regional outdoor / small-place sources

- Keep first-class scrapers for:
  - `koenigswinter.de/de/veranstaltungen.html`: official Königswinter calendar, good for Siebengebirgsmuseum, Kulturtage, markets, guided tours.
  - `vv-siebengebirge.de/veranstaltungen/`: VVS guided hikes, forest/nature days, Margarethenhöhe/Lohrberg/outdoor events; parse JSON-LD Events.
  - `siebengebirge.com/...veranstaltungen-aktuell`: Tourismus Siebengebirge / museum current dates, especially Drachenfels, culinary city tours, Königswinter history walks.
  - `ahrtal.com/de/events` and `ahrwein.de/veranstaltungen/alle-wein-events-im-ahrtal`: Ahrtal wine walks, tastings, guided hikes, valley festivals.
  - `andernach.de/aktuelles/veranstaltungskalender/` and `andernach-begeistert.de/sehen-erleben/veranstaltungen/`: Andernach small-city/open-air/festival highlights such as Schlossgarten, Filmnächte, Michelsmarkt, Kulturnacht.
- Add explicit coordinates before relying on city-level defaults. Important places: Königswinter, Bad Honnef, Drachenfels, Petersberg, Heisterbach, Margarethenhöhe, Ahrweiler, Dernau, Mayschoss, Altenahr, Andernach, Namedy, Linz, Unkel, Rolandseck.
- Exa Search is the default broad-web fallback because it is less rate-limit-prone and better at obscure local event pages. Keep query count tight and require in-window date evidence for search-derived results.
- Grok Search is available but disabled by default because agentic browsing is high-quality but slow/costly for the hot path. Enable explicitly with `NRW_EVENTS_ENABLE_GROK=1` when doing a deep/manual sweep.

## Ahrtal / Ahrweiler inclusion

- Include nearby **Ahrtal / Ahrweiler / Bad Neuenahr-Ahrweiler** events despite the project name being NRW. From Bonn they are often as practical as Köln and much more relevant for wine, walking, outdoor, and scenic day trips.
- Keep a first-class **Ahrtal/AhrWeinWalk** source in the script. `AhrWeinWalk`-style events can look like local news articles, not classic event listings, so search-only discovery is too flaky.
- Do **not** demote an otherwise adult/outdoor/wine event just because the description mentions `Kinder`, `Familie`, or a kids quiz. Demote kids-only events, but not wine walks, vineyard hikes, markets, outdoor festivals, or food/wine events with a family side-offer.
- Useful discovery queries when doing manual fallback:
  - `site:ahrtal.de Ahrweiler Veranstaltung Wein Wanderung <dates>`
  - `Ahrtal Ahrweiler Wochenende Weinwanderung Festival <month year>`
  - `AhrWeinWalk Himmelfahrt Ahrweiler Anreise Kinderquiz`

## Tuning (env vars)

Defaults favour **quantity over quality** (filter the full list yourself):

- `NRW_EVENTS_MAX_PER_SECTION=N` — cap events shown per category (0/unset = all). Use for terse contexts.
- `NRW_EVENTS_SCORE_FLOOR=0.4` — minimum score to keep. Lower = more events/more sludge; raise to tighten.
- `NRW_EVENTS_EXA_QUERIES=10` — how many of the `search_queries()` to send to Exa (each ~5 results). Raise to widen, at higher API cost.
- `NRW_EVENTS_ENABLE_GROK=1` — enable the slow/costly agentic Grok sweep (off by default).

API keys are read from the environment or a `.env` file. See [.env.example](.env.example).

## Adding new sources (esp. iCal / Tribe Events)

Most Bonn/NRW venues run WordPress + "The Events Calendar" (Tribe), which exposes a clean `.ics` feed at `?post_type=tribe_events&ical=1`. **iCal is far more reliable than scraping HTML** — prefer it.

- Generic helper: `fetch_ical(url, source, default_city, category, trust)` parses any RFC 5545 feed (handles line folding, TZID, escaping) and runs every event through `make_event()` (date-window + radius + scoring). One-liner to add a venue — see `fetch_harmonie_bonn()`.
- For schema.org JSON-LD pages, use the existing `events_from_jsonld(html, source, default_city, category, trust, default_link)`.
- Before wiring a source in, probe it: `curl -sL '<url>' | grep -c 'BEGIN:VEVENT'` (iCal) or `grep -c 'application/ld+json'` (JSON-LD). Only wire sources that return real structured data.
- Add the fetcher to the `fetchers = {...}` registry in `main()`. Add any new town to `VENUE_COORDS` so distance scoring works.

### Meetup groups (active)

Curated Bonn-area groups live in the `MEETUP_GROUPS` list in the script and are fetched via each group's public iCal feed (`https://www.meetup.com/<slug>/events/ical/`, no auth). To add/remove: edit the list `(slug, default_city, category-hint, trust)`. **Re-probe periodically** — a `404` means the slug is wrong; a `200` with zero `BEGIN:VEVENT` means the group is inactive (drop it). Probe with: `curl -s 'https://www.meetup.com/<slug>/events/ical/' | grep -c BEGIN:VEVENT`.

## Suggested future sources (verified candidates)

Probed and worth adding when time allows:

- **More Tribe iCal venues** — re-probe periodically; Harmonie works, others may migrate to Tribe. Test pattern: `<domain>/?post_type=tribe_events&ical=1`.
- **Bonn.de RSS by Stadtteil** — the RSS endpoint accepts search params; could fire one feed per neighborhood (Poppelsdorf/Beuel/Godesberg/Endenich) for denser district coverage.
- **Pantheon / Brotfabrik / Bla / Kunstmuseum / Uni Bonn** — no usable iCal/JSON-LD found (HTML-only, fragile). Would need bespoke scrapers; currently covered indirectly via Bonn.de RSS + Exa.

## Dead Sources (skip)

Rausgegangen.de (blocks headless; `/api/v3` path 404s), Bandsintown API (auth deny), Ticketmaster (no key), ga.de RSS (404), opendata.bonn.de CKAN `package_search` (404 — portal does not expose CKAN API at that path).
