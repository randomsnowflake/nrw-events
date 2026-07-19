---
name: nrw-events
description: "Discover events, concerts, exhibitions, nightlife, outdoor activities, markets and festivals in Bonn and surrounding NRW cities (75km radius: Köln, Siegburg, Troisdorf, Königswinter, Düsseldorf, Aachen, etc). Use when: 'what's happening this weekend', 'events in Bonn', 'things to do', 'concerts near me', 'exhibitions Köln', 'weekend plans', 'what should we do', 'any cool events', 'nightlife Bonn', 'activities around here'. Also use when the user asks about activities, events, or things to do in or near Bonn. NOT for: trip planning to other regions, or deep-dives on a single venue."
tags:
  - bonn
  - nrw
  - events
  - veranstaltungen
  - weekend
  - concerts
  - exhibitions
  - markets
  - open-source
  - python
metadata:
  hermes:
    tags: [bonn, nrw, events, veranstaltungen, weekend, concerts, exhibitions, markets, open-source, python]
---

# NRW Events

> This file is an optional agent-skill manifest (for assistants that load `SKILL.md`
> skills). The tool is a plain CLI — see [README.md](README.md) to run it directly.
> `{baseDir}` is the skill root (this repo's root).

```bash
bash {baseDir}/scripts/nrw-events.sh [days_ahead]   # default: 3 (weekend)
```

Every event is discovered **live** at run time — there are no hardcoded event
names or dates anywhere in the code. The script fans out across official APIs,
JSON-LD pages, iCal feeds, municipal/regional calendars, venue calendars,
nightlife sources, and web-search fallbacks. Current sources include Köln Open
Data, Bonn.de JSON + sports + annual "Veranstaltungsjahr" listings, Harmonie
Bonn, Meetup, Rheinauen-Flohmarkt, Bundeskunsthalle, Königswinter,
VVS Siebengebirge, Siegburg, Troisdorf, Naturregion Sieg, Hennef, Meckenheim,
Wachtberg, Much, IONAS4/SiteKit/standard regional calendars, regional HTML and
tourism calendars, Kinderflohmarkt.com, Grote & Hiller, Hofflohmärkte Köln,
Cölln Konzept and requested venue calendars,
Bonn.jetzt, Radio Bonn/Rhein-Sieg weekly tips, Ruhr-Guide, Exa Search, and
optional Grok Search. Bonn sport-club scrape candidates discovered for
Tag des Bonner Sports / local sport coverage: SSB Bonn root + Sport im Park,
Bonn.de sports + annual Veranstaltungjahr pages, TGV Bonn, 1. BC Beuel, SSF
Bonn, Bonn Rugby UC, OFC Bonn, Post-Sportverein Bonn Clubway feed, Bonner
Bogenschützenclub, and BSV Bonn/Rhein-Sieg event pages. Bonn.jetzt is
especially useful for Bonn's local digital/community events and weekend oddities
that bigger feeds miss. Scores by distance (Bonn=1.0, Königswinter≈0.9,
Ahrweiler≈0.74, Köln=0.7, Düsseldorf=0.4) × category preference
(electronic/techno=1.8x, wine/winery/wine-walk=1.4–1.55x, hiking/guided
walks/Drachenfels/Siebengebirge=1.3–1.45x, architecture=1.6x, concerts=1.5x,
exhibitions=1.4x, kids-only=0.2x). Output: markdown report grouped by category +
JSON event list plus metadata defaulting to the user's XDG state directory
(`~/.local/state/nrw-events` when `XDG_STATE_HOME` is unset).

## Architecture (one file per source)

The script is a small package, not a monolith. To understand or change a source,
open just its file:

```
scripts/nrw_events/
  config.py    — geography, category weights, venue coords, Meetup group list
  models.py    — typed event contract shared by sources and the pipeline
  location.py / scoring.py — reusable geographic resolution and ranking
  source_types.py — source and parser interfaces
  common.py    — HTTP, parsing, detail-cache, and quality facade
  report.py    — dedup + Markdown rendering
  runner.py    — orchestration (fan-out, filter, dedup, output)
  sources/     — one module per source, each a fetch() -> list[dict]
    __init__.py  — SOURCES registry (display name -> fetch function)
```

To add a standard iCal or JSON-LD source, add one typed `SourceSpec` plus a
fixture/contract test. Add a dedicated `fetch()` module only for proprietary
HTML or aggregate parsing, then register it in `CUSTOM_SOURCES`. No event
data ever lives in these files — only source URLs and parsing logic.

## After Running

Present the **FULL event list** — every event in every category, exactly as the
script outputs them. Do NOT trim to highlights or a "best picks" shortlist. The
script shows all events by default (no per-section cap).

After the full list, you MAY add a short opinionated "top picks" line at the end —
but it never replaces the complete list. Consider: weather (outdoor events), and
the user's stated interests (architecture/museums/electronic music/food tend to
rank highest by default).

The JSON output keeps legacy `date` / `time` display fields and also provides
canonical `start_date`, `end_date`, `start_at`, `end_at`, `all_day`, `timezone`,
`status`, and location-confidence fields for machine consumers.

To trim output for terse contexts, set `NRW_EVENTS_MAX_PER_SECTION=N`.

## Small local / province events

- Prioritize **small local stuff**, not just official concert/exhibition calendars:
  Stadtteilfeste, Dorffeste, Kirmes, Genussmeilen, Weinmeilen, food/market events,
  local history walks, garden/nature days, Siebengebirge/Kottenforst/Rhein-side
  walks, and village/province events around Bonn.
- The **Bonn district festivals** source parses the city's annual
  "Veranstaltungsjahr" press release live (`sources/bonn.py → fetch_press_festivals`).
  This is where the small Stadtteilfeste / Kirmes / neighbourhood markets come from —
  events that are published in press pages rather than clean event APIs. The URL is
  built dynamically from the current year, so it keeps working with no code change.
- Poppelsdorf/Endenich/Beuel/Bad Godesberg/Ippendorf/Dransdorf are first-class
  discovery areas. Events on the Poppelsdorfer Meile/Clemens-August-Straße should be
  considered highly relevant, even if they are mostly gastro/local/neighbourhood.
- The Exa search fallback already includes neighbourhood and province terms
  (`Stadtteilfest`, `Dorffest`, `Kirmes`, `Genussmeile`, `Weinmeile`, `Rundgang`,
  `Führung`, `Natur`, `Kottenforst`, `Siebengebirge`, `Königswinter`, `Drachenfels`,
  `Ahrtal`, `Dernau`, `Mayschoss`, `Poppelsdorf`, `Endenich`, `Beuel`,
  `Bad Godesberg`, …). Edit `sources/search.py → search_queries()` to tune.

## Ahrtal / Ahrweiler inclusion

- Nearby **Ahrtal / Ahrweiler / Bad Neuenahr-Ahrweiler** wine walks, vineyard
  hikes, and valley festivals are still in scope — from Bonn they are often as
  practical as Köln and much more relevant for wine/outdoor/scenic weekends.
- They are surfaced via the **Exa search fallback** (which includes
  `site:ahrtal.com` and Ahr wine/walk queries) and ranked highly by the wine/outdoor
  category weights. There is no dedicated Ahrtal scraper, because `ahrtal.com` and
  `ahrwein.de` expose no structured (JSON-LD/iCal) event data — a bespoke HTML
  scraper there was unreliable and was removed.
- Do **not** demote an otherwise adult/outdoor/wine event just because the
  description mentions `Kinder`, `Familie`, or a kids quiz. Demote kids-only events,
  but not wine walks, vineyard hikes, markets, outdoor festivals, or food/wine
  events with a family side-offer. (`common.category_score` already handles this.)

## Tuning (env vars)

Defaults favour **quantity over quality** (filter the full list yourself):

- `NRW_EVENTS_MAX_PER_SECTION=N` — cap events shown per category (0/unset = all).
- `NRW_EVENTS_DAYS_AHEAD=3` — default time window when the CLI has no day argument (1–90).
- `NRW_EVENTS_SCORE_FLOOR=0.4` — minimum score to keep. Lower = more/noisier.
- `NRW_EVENTS_EXA_QUERIES=10` — how many `search_queries()` to send to Exa (~5 results each).
- `NRW_EVENTS_ENABLE_GROK=1` — enable the slow/costly agentic Grok sweep (off by default).
- `NRW_EVENTS_USER_AGENT` — override the default browser-like user agent.
- `NRW_EVENTS_HTTP_RETRY_ATTEMPTS=5` — transient HTTP/network retry limit.
- `NRW_EVENTS_HTTP_RETRY_BASE_SECONDS=1.0` — exponential backoff base with jitter.
- `NRW_EVENTS_HTTP_RETRY_MAX_DELAY_SECONDS=60.0` / `NRW_EVENTS_HTTP_MAX_RESPONSE_BYTES=5000000` — cap retry waits and response sizes.
- `NRW_EVENTS_SOURCE_BASELINE_MIN_COUNT=10` — annotate a source that drops from a recent meaningful count to zero.
- `NRW_EVENTS_BONN_DE_DELAY_SECONDS=2.0` — minimum delay between `bonn.de` requests.
- `NRW_EVENTS_CACHE_DIR=~/.cache/nrw-events` — persistent cache root for bounded detail-page enrichment.
- `NRW_EVENTS_DETAIL_CACHE_TTL_HOURS=24` — default TTL for successful generic detail-page fetches; `0` disables memory and disk caching.
- `NRW_EVENTS_BONN_DETAIL_DESCRIPTION_MAX_CHARS=500` — target length for meaningful Bonn.de detail summaries after logistics boilerplate is removed.
- `NRW_EVENTS_JSON_OUT` / `NRW_EVENTS_META_JSON_OUT` — override output paths.
- `NRW_EVENTS_LOG_LEVEL=INFO` — log level for the importer.
- `NRW_EVENTS_LOG_FILE` / `NRW_EVENTS_JSON_LOG_FILE` — optional durable text or JSON-lines logs.
- `NRW_EVENTS_ENV_FILE` — optional explicit `.env` path for wrappers and callers.

API keys and tuning values are read from the environment, an explicit
`NRW_EVENTS_ENV_FILE`, or the repository `.env`; the current working directory
is never searched. The canonical setting list is [.env.example](.env.example).

Detail-page caches are deliberately bounded and versioned. Listing pages, APIs,
and feeds remain live on every run; only enrichment requests are cached. Radio
Bonn/Rhein-Sieg is an editorial discovery source, so deduplication should retain
a direct non-Radio event URL when the same event also appears from a primary
source.

## Adding new sources (esp. iCal / Tribe Events)

Most Bonn/NRW venues run WordPress + "The Events Calendar" (Tribe), which exposes a
clean `.ics` feed at `?post_type=tribe_events&ical=1`. **iCal is far more reliable
than scraping HTML** — prefer it.

- Generic helpers in `common.py`: `fetch_ical(url, source, default_city, category,
  trust)` parses any RFC 5545 feed; `events_from_jsonld(html, source, default_city,
  category, trust, default_link)` parses schema.org JSON-LD Events (handles
  `location` given as an object or an array). Both run every event through
  `make_event()` (date-window + radius + scoring).
- Before wiring a source in, probe it: `curl -sL '<url>' | grep -c 'BEGIN:VEVENT'`
  (iCal) or `grep -c 'application/ld+json'` (JSON-LD). Only wire sources that return
  real structured data.
- Add standard iCal/JSON-LD sources as a `SourceSpec` in `sources/__init__.py`
  plus a contract case in `tests/sources/parser_cases.py`. For proprietary
  formats, create a `fetch()` module and register it in `CUSTOM_SOURCES`.
  Add any new town to `config.VENUE_COORDS`.

### Meetup groups (active)

Curated Bonn-area groups live in `config.MEETUP_GROUPS` and are fetched via each
group's public iCal feed (`https://www.meetup.com/<slug>/events/ical/`, no auth).
To add/remove: edit the list `(slug, default_city, category-hint, trust)`.
**Re-probe periodically** — a `404` means the slug is wrong; a `200` with zero
`BEGIN:VEVENT` means the group is inactive (drop it). Probe with:
`curl -s 'https://www.meetup.com/<slug>/events/ical/' | grep -c BEGIN:VEVENT`.

## Notes on seasonality

Some live sources are legitimately empty in certain windows — that is correct
behaviour, not a bug:

- **Harmonie Bonn** takes a summer break; its concerts reappear in autumn.
- **Rheinauen-Flohmarkt** runs a seasonal stretch (roughly April–October); it only
  shows when its season overlaps the requested window.

## Dead Sources (skip — do not re-add without structured data)

- **Andernach** (andernach.de / andernach-begeistert.de) — pages expose only
  WebSite/WebPage JSON-LD, no Event data. Removed.
- **Tourismus Siebengebirge** (siebengebirge.com) — only ever served a stale
  past-season list, nothing forward-looking. Removed.
- **Ahrtal / Ahrwein** (ahrtal.com, ahrwein.de) — no JSON-LD/iCal; HTML scrape was
  unreliable. Ahr valley now comes via Exa search. Removed.
- Songkick and Rausgegangen.de (removed; Rausgegangen blocks headless),
  Bandsintown (auth deny), Ticketmaster (no key),
  ga.de RSS (404), opendata.bonn.de CKAN (404).
