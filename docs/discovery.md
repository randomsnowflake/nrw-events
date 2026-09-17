# NRW Events

> This file is an optional agent-skill manifest (for assistants that load `SKILL.md`
> skills). The tool is a plain CLI — see [README.md](../README.md) to run it directly.
> `{baseDir}` is the skill root (this repo's root).

```bash
bash {baseDir}/scripts/nrw-events.sh [days_ahead]   # default: 3 (weekend)
```

Every event is discovered **live** at run time — there are no hardcoded event
names or dates anywhere in the code. The script fans out across official APIs,
JSON-LD pages, iCal feeds, municipal/regional calendars, venue calendars,
nightlife sources, and web-search fallbacks. Current sources include Köln Open
Data, Bonn.de JSON + sports + annual "Veranstaltungsjahr" listings, Harmonie
Bonn, Rheinauen-Flohmarkt, Bundeskunsthalle, Königswinter,
VVS Siebengebirge, Siegburg, Troisdorf, Naturregion Sieg, Hennef, Meckenheim,
Wachtberg, Much, IONAS4/SiteKit/standard regional calendars, regional HTML and
tourism calendars, Kinderflohmarkt.com, Grote & Hiller, Hofflohmärkte Köln,
HofFloh Bonn, Lampert Märkte, Okken Märkte, Geide Märkte, Cölln Konzept,
Rhein Antik,
Cölln Antik&Design,
kommunale MEC-Marktkalender (Hennef, Sankt Augustin),
marktcom (Marktverzeichnis, nur Second-Hand-Formate),
requested venue calendars,
Theater Bonn, Junges Theater Bonn, Kleines Theater Bad Godesberg, Theater
Marabu, Theater im Ballsaal, TiK Theater im Keller,
Tanzschule Max7, AfterJobParty Bonn,
RheinEvents, Salsa in Bonn,
Food & Genuss primary sources (Craftquelle, BFF Bonner Schifffahrt, vomFASS,
Biertasting Bonn, Ludwig's, Redüttchen, Street Food Bonn incl. the organiser's
Siegburg landing page, Street Food Festival "Das Original", Choco Dealer),
BV Holzlar (Bonn-Ost neighbourhood associations: Holzlar, Kohlkaul,
Roleber-Gielgen, Mühlenverein), Rhein in Flammen Bonn,
Literaturhaus Bonn, Parkbuchhandlung Bad Godesberg,
Bonn.jetzt, Radio Bonn/Rhein-Sieg weekly tips, Ruhr-Guide, and Exa Search.
Grok Search is permanently retired. Bonn sport-club scrape candidates discovered for
Tag des Bonner Sports / local sport coverage: SSB Bonn root + Sport im Park,
Bonn.de sports + annual Veranstaltungjahr pages, TGV Bonn, 1. BC Beuel, SSF
Bonn, Bonn Rugby UC, OFC Bonn, Post-Sportverein Bonn Clubway feed, Bonner
Bogenschützenclub, and BSV Bonn/Rhein-Sieg event pages. Bonn.jetzt is
especially useful for Bonn's local digital/community events and weekend oddities
that bigger feeds miss. Scores by distance (Bonn=1.0, Königswinter≈0.9,
Ahrweiler≈0.74, Köln=0.7, Düsseldorf=0.4) × category preference
(electronic/techno=1.8x, wine/winery/wine-walk=1.4–1.55x, hiking/guided
walks/Drachenfels/Siebengebirge=1.3–1.45x, architecture=1.6x, concerts=1.5x,
exhibitions=1.4x, kids-only=0.25x). Output: markdown report grouped by category +
JSON event list plus metadata defaulting to the user's XDG state directory
(`~/.local/state/nrw-events` when `XDG_STATE_HOME` is unset).

For the restricted sources Bonn.de Events/Sports, marktcom and Radio
Bonn/Rhein-Sieg, source prose is never published. By default,
`OPENAI_API_KEY` and `NRW_EVENTS_AI_ENRICHMENT=1` run two separate
`gpt-5.6-luna` Responses API calls that extract facts and then write
`ai_summary`; without the selected provider's key the summary stays empty.
For model comparisons, `NRW_EVENTS_AI_PROVIDER=openrouter` uses
`OPENROUTER_API_KEY`, strict structured Chat Completions, zero-data-retention
routing, and a provider-specific cache namespace. Its default model is
`deepseek/deepseek-v4-flash-0731` with reasoning disabled.
AI runs only after canonical validation, publication filtering and global
deduplication. `NRW_EVENTS_REVIEWED_AI_SUMMARIES_PATH` may point to a strict
version-1 export of website `content_reviewed` rules; exact final-ID matches are
applied first and never enter the AI cache/provider batch. A malformed configured
manifest fails the refresh, while an absent variable leaves default behavior unchanged.
The persistent cache is controlled by `NRW_EVENTS_AI_CACHE_DB`,
`NRW_EVENTS_CACHE_DIR`, `NRW_EVENTS_AI_MAX_ATTEMPTS`,
`NRW_EVENTS_AI_NEGATIVE_CACHE_HOURS`, `NRW_EVENTS_AI_TIMEOUT_SECONDS`,
`NRW_EVENTS_AI_BATCH_TIMEOUT_SECONDS`,
`NRW_EVENTS_AI_WORKERS`, `NRW_EVENTS_AI_MAX_EVENTS`,
`NRW_EVENTS_AI_MAX_NEW_CACHE_ROWS_PER_DAY`, and
`NRW_EVENTS_AI_MODEL`. The facts and summary stages have independent cache
compatibility versions: summary-only prompt or quality changes reuse successful
facts, while extraction prompt/schema/sanitizer changes intentionally invalidate
both stages. The daily new-row limit defaults to 150 as a cost fuse; set it to
`0` only for a deliberate, monitored full reprocess.

### Registered sources

See the generated [sources inventory](sources.md).

## Architecture and source registration

The full module inventory is generated from the package. Responsibilities and
dependency direction are documented in `docs/ARCHITECTURE.md`.

See the generated [modules inventory](modules.md).

To add a standard iCal or JSON-LD source, add one typed `SourceSpec` plus a
fixture/contract test. Add a dedicated `fetch()` module only for proprietary
HTML or aggregate parsing, then register its callable in `scripts/nrw_events/sources/registry.json`. No event
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

- `EXA_API_KEY` — credentials for the optional Exa search fallback.
- `NRW_EVENTS_MAX_PER_SECTION=N` — cap events shown per category (0/unset = all).
- `NRW_EVENTS_REPORT_MAX_CHARS=N` — optionally cap the complete Markdown report (0/unset = full output).
- `NRW_EVENTS_DAYS_AHEAD=3` — default time window when the CLI has no day argument (1–90).
- `NRW_EVENTS_SCORE_FLOOR=0.4` — minimum score to keep. Lower = more/noisier.
- `NRW_EVENTS_RADIUS_KM=75` — maximum distance from Bonn (`--umkreis 15km`).
- `NRW_EVENTS_CATEGORIES=market,festival` — canonical category filter (`--kategorie`).
- `NRW_EVENTS_FREE_ONLY=1` — keep only explicitly free events (`--kostenlos`).
- `NRW_EVENTS_JSON_STDOUT=1` — emit only JSON to stdout and do not publish snapshots (`--json`).
- `NRW_EVENTS_HIGHLIGHTS_JSON_OUT` / `NRW_EVENTS_SERIES_LEDGER_JSON` — override durable highlight and series paths.
- `NRW_EVENTS_PREVIOUS_META_JSON` — previous published metadata used to retain unexpired events from a degraded source.
- `NRW_EVENTS_DESCRIPTION_MAX_CHARS=700` — maximum normalized description length.
- `NRW_EVENTS_CATEGORY_FALLBACK_CACHE=/path/cache.json` — optional reviewed category cache; the importer itself never invokes an LLM.
- `NRW_EVENTS_EXA_QUERIES=10` — how many `search_queries()` to send to Exa (~5 results each).
- `NRW_EVENTS_USER_AGENT` — override the default browser-like user agent.
- `NRW_EVENTS_HTTP_RETRY_ATTEMPTS=5` — transient HTTP/network retry limit.
- `NRW_EVENTS_HTTP_REQUEST_BUDGET_SECONDS=45.0` — total request, retry, and backoff budget.
- `NRW_EVENTS_HTTP_RETRY_BASE_SECONDS=1.0` — exponential backoff base with jitter.
- `NRW_EVENTS_HTTP_RETRY_MAX_DELAY_SECONDS=60.0` — cap retry waits. `NRW_EVENTS_HTTP_MAX_RESPONSE_BYTES=10000000` keeps the current Bonn export complete while bounding unexpectedly large responses; set `0` only to opt into unlimited reads.
- `NRW_EVENTS_SOURCE_BASELINE_MIN_COUNT=10` — annotate a source that drops from a recent meaningful count to zero.
- `NRW_EVENTS_MINIMUM_SNAPSHOT_RATIO=0.5` — preserve the last-known-good snapshot when a run falls below half the previous event count.
- `NRW_EVENTS_MAX_FAILED_SOURCE_RATIO=0.5` — fail the run when more than half of active sources fail.
- `NRW_EVENTS_SOURCE_WORKERS=4` / `NRW_EVENTS_SOURCE_TIMEOUT_SECONDS=600` — source parallelism and network-phase budget.
- `NRW_EVENTS_COMPONENT_WORKERS=3` — one shared pool for independent component calendars, capped at four workers. Use `0` or `1` for serial execution.
- `NRW_EVENTS_SOURCE_PROCESSING_GRACE_SECONDS=180` — extra worker time to process an already fetched large source result.
- `NRW_EVENTS_BONN_DE_DELAY_SECONDS=2.0` — minimum delay between `bonn.de` requests.
- `NRW_EVENTS_BONN_CALENDAR_MAX_PAGES=30` — safety cap for paginated Bonn.de calendars.
- `BRIGHT_DATA_API_KEY` / `BRIGHT_DATA_ZONE` — Bright Data Web Unlocker credentials; vomFASS refreshes only on Mondays and always uses this proxy. Hofflohmärkte Köln and allowlisted IONAS4 regional calendars are direct-first and use it only after selected transient failures or exhausted direct-request timeouts.
- `NRW_EVENTS_CACHE_DIR=~/.cache/nrw-events` — persistent cache root for bounded detail-page enrichment.
- `XDG_CACHE_HOME=~/.cache` — cache base when `NRW_EVENTS_CACHE_DIR` is unset.
- `NRW_EVENTS_DETAIL_CACHE_TTL_HOURS=24` — default TTL for successful generic detail-page fetches; `0` disables memory and disk caching.
- `NRW_EVENTS_DETAIL_ENRICHMENT=1` — shared primary-detail enrichment; set to `0` to disable.
- `NRW_EVENTS_DETAIL_BATCH_TIMEOUT_SECONDS` — whole-source budget for optional detail-page enrichment: 45 seconds by default, 240 for Köln Open Data's large teaser feed. An explicit value overrides both defaults; the outer source deadline still applies.
- `NRW_EVENTS_BONN_DETAIL_DESCRIPTION_MAX_CHARS=500` — target length for meaningful Bonn.de detail summaries after logistics boilerplate is removed.
- `NRW_EVENTS_JSON_OUT` / `NRW_EVENTS_META_JSON_OUT` — override output paths.
- `NRW_EVENTS_LOG_LEVEL=INFO` — log level for the importer.
- `NRW_EVENTS_LOG_FILE` / `NRW_EVENTS_JSON_LOG_FILE` — optional durable text or JSON-lines logs.
- `NRW_EVENTS_PERFORMANCE=1` — export before invocation for aggregate stderr metrics. This flag is read before the env file. See `docs/performance.md` for offline replay and strict snapshot comparison.
- `NRW_EVENTS_TAXONOMY_CACHE=1` — bounded pure keyword cache. Set to `0` to disable it. The cache does not store classification decisions or reviewed fallbacks.
- `NRW_EVENTS_NORMALIZATION_CACHE=1` — bounded text-comparison cache with the separator in its key. Set to `0` to disable it independently.
- `NRW_EVENTS_ICAL_PRUNE=1` — evaluate the unchanged quality policy before full iCal construction. Set to `0` for the legacy path. Historical announcements and cancellation handling remain intact.
- `NRW_EVENTS_ENV_FILE` — optional explicit `.env` path for wrappers and callers.

API keys and tuning values are read from the environment, an explicit
`NRW_EVENTS_ENV_FILE`, or the repository `.env`; the current working directory
is never searched. The canonical setting list is [.env.example](../.env.example).

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
  formats, create a `fetch()` module and register its callable in `scripts/nrw_events/sources/registry.json`.
  Add any new town to `config.VENUE_COORDS`.

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
