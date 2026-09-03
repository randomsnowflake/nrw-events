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

<!-- BEGIN GENERATED SOURCES -->
| Region | Quelle | ID | Adapter |
|---|---|---|---|
| bonn-region | ADFC Bonn/Rhein-Sieg | `adfc-bonn` | `python` |
| bonn-region | AfterJobParty Bonn | `afterjobparty-bonn` | `python` |
| bonn-region | Bad Godesberg Stadtmarketing | `bad-godesberg-stadtmarketing` | `python` |
| bonn-region | Beethovenfest Bonn | `beethovenfest-bonn` | `python` |
| bonn-region | Beuel.net | `beuel-net` | `python` |
| bonn-region | BFF Bonner Schifffahrt | `bff-bonner-schifffahrt` | `python` |
| bonn-region | Biertasting Bonn | `biertasting-bonn` | `python` |
| bonn-region | Bonn district festivals | `bonn-district-festivals` | `python` |
| bonn-region | Bonn venue calendars | `bonn-venue-calendars` | `python` |
| bonn-region | Bonn.de Events | `bonn-de-events` | `python` |
| bonn-region | Bonn.de Sports | `bonn-de-sports` | `python` |
| bonn-region | Bonn.jetzt | `bonn-jetzt` | `python` |
| bonn-region | Bonner Weihnachtsmarkt | `bonner-weihnachtsmarkt` | `python` |
| bonn-region | BonnLive | `bonnlive` | `python` |
| bonn-region | BSV Roleber | `bsv-roleber` | `python` |
| bonn-region | Bundeskunsthalle | `bundeskunsthalle` | `python` |
| bonn-region | BV Holzlar | `bv-holzlar` | `python` |
| bonn-region | b° future festival | `b-future-festival` | `python` |
| bonn-region | Bürgerverein Rossel-Wilberhofen | `rossel-wilberhofen-dorfflohmarkt` | `python` |
| bonn-region | Bürgerverein Vilich-Müldorf | `b-rgerverein-vilich-m-ldorf` | `python` |
| bonn-region | Choco Dealer | `choco-dealer` | `python` |
| bonn-region | Craftquelle Bonn | `craftquelle-bonn` | `python` |
| bonn-region | Curated cinema specials | `curated-cinema-specials` | `python` |
| bonn-region | Cölln Antik&Design | `c-lln-antik-design` | `python` |
| bonn-region | Cölln Konzept | `c-lln-konzept` | `python` |
| bonn-region | Deskline regional | `deskline-regional` | `python` |
| bonn-region | Deutsches Museum Bonn | `deutsches-museum-bonn` | `python` |
| bonn-region | Exa Search | `exa-search` | `python` |
| bonn-region | FedCon Events | `fedcon-events` | `python` |
| bonn-region | Geide Märkte | `geide-m-rkte` | `python` |
| bonn-region | Grok Search | `grok-search` | `python` |
| bonn-region | Grote & Hiller | `grote-hiller` | `python` |
| bonn-region | Hardtberg Kultur | `hardtberg-kultur` | `python` |
| bonn-region | Harmonie Bonn | `harmonie-bonn` | `python` |
| bonn-region | Haus der Geschichte | `haus-der-geschichte` | `python` |
| bonn-region | Haus der Geschichte Begleitungen | `haus-der-geschichte-begleitungen` | `python` |
| bonn-region | Heimatmuseum Beuel | `hgv-beuel` | `python` |
| bonn-region | Hennef | `hennef` | `json_ld` |
| bonn-region | HofFloh Bonn | `hoffloh-bonn` | `python` |
| bonn-region | Hofflohmärkte Köln | `hofflohm-rkte-k-ln` | `python` |
| bonn-region | In guten Kreisen | `in-guten-kreisen` | `python` |
| bonn-region | ionas4 regional | `ionas4-regional` | `python` |
| bonn-region | Junges Theater Bonn | `junges-theater-bonn` | `python` |
| bonn-region | Katharinenhof Flohmarkt | `katharinenhof-flohmarkt` | `python` |
| bonn-region | Kihapp – Veranstalterdaten | `kihapp` | `python` |
| bonn-region | Kinderflohmarkt.com | `kinderflohmarkt-com` | `python` |
| bonn-region | Kirmes in Bonn | `bonnkirmes` | `python` |
| bonn-region | Kleines Theater Bad Godesberg | `kleines-theater-bad-godesberg` | `python` |
| bonn-region | Krewelshof Kindersachen-Flohmarkt | `krewelshof-kindersachen-flohmarkt` | `python` |
| bonn-region | KUNST!RASEN Bonn | `kunstrasen-bonn` | `python` |
| bonn-region | Kunstmuseum Bonn | `kunstmuseum-bonn` | `python` |
| bonn-region | Köln Open Data | `k-ln-open-data` | `python` |
| bonn-region | Königswinter | `k-nigswinter` | `python` |
| bonn-region | Lampert Märkte | `lampert-m-rkte` | `python` |
| bonn-region | Literaturhaus Bonn | `literaturhaus-bonn` | `python` |
| bonn-region | Ludwig's Bonn | `ludwig-s-bonn` | `python` |
| bonn-region | LuPe Events | `lupe-events` | `python` |
| bonn-region | marktcom | `marktcom` | `python` |
| bonn-region | Meckenheim | `meckenheim` | `python` |
| bonn-region | Melan Märkte | `melan-m-rkte` | `python` |
| bonn-region | Much | `much` | `python` |
| bonn-region | Municipal MEC markets | `municipal-mec-markets` | `python` |
| bonn-region | Museum Koenig Bonn | `museum-koenig-bonn` | `python` |
| bonn-region | Naturregion Sieg | `naturregion-sieg` | `python` |
| bonn-region | Okken Märkte | `okken-m-rkte` | `python` |
| bonn-region | Parkbuchhandlung | `parkbuchhandlung` | `python` |
| bonn-region | Radio Bonn/Rhein-Sieg | `radio-bonn-rhein-sieg` | `python` |
| bonn-region | Redüttchen | `red-ttchen` | `python` |
| bonn-region | Regional HTML calendars | `regional-html-calendars` | `python` |
| bonn-region | Regional venues | `regional-venues` | `python` |
| bonn-region | Requested venue calendars | `requested-venue-calendars` | `python` |
| bonn-region | Rhein Antik | `rhein-antik` | `python` |
| bonn-region | Rheinauen-Flohmarkt | `rheinauen-flohmarkt` | `python` |
| bonn-region | Rheinbach Flohmarkt | `rheinbach-flohmarkt` | `python` |
| bonn-region | RheinEvents | `rheinevents` | `python` |
| bonn-region | Rieder Märkte | `rieder-solingen-rewe` | `python` |
| bonn-region | RiF Events | `rif-events` | `python` |
| bonn-region | Ruhr-Guide | `ruhr-guide` | `python` |
| bonn-region | Salsa in Bonn | `salsa-in-bonn` | `python` |
| bonn-region | Schmitt Veranstaltungen | `schmitt-veranstaltungen` | `python` |
| bonn-region | Siegburg | `siegburg` | `python` |
| bonn-region | SiteKit regional | `sitekit-regional` | `python` |
| bonn-region | Standard regional feeds | `standard-regional-feeds` | `python` |
| bonn-region | Street Food Bonn | `street-food-bonn` | `python` |
| bonn-region | Street Food Festival Original | `street-food-festival-original` | `python` |
| bonn-region | Swisttal | `swisttal` | `html` |
| bonn-region | Tanzschule Max7 | `tanzschule-max7` | `python` |
| bonn-region | Theater Bonn | `theater-bonn` | `python` |
| bonn-region | Theater im Ballsaal | `theater-im-ballsaal` | `python` |
| bonn-region | Theater Marabu | `theater-marabu` | `python` |
| bonn-region | TiK Theater im Keller | `tik-theater-im-keller` | `python` |
| bonn-region | Tourismus NRW Pützchens Markt | `tourismus-nrw-puetzchens-markt` | `python` |
| bonn-region | Troisdorf | `troisdorf` | `ical` |
| bonn-region | Universität Bonn | `uni-bonn` | `python` |
| bonn-region | Veranstaltungen Brüser Berg | `veranstaltungen-brueser-berg` | `python` |
| bonn-region | vomFASS Bonn | `vomfass-bonn` | `python` |
| bonn-region | VVS Siebengebirge | `vvs-siebengebirge` | `python` |
| bonn-region | Wachtberg | `wachtberg` | `ical` |
| bonn-region | Waldbröl | `waldbroel` | `ical` |
<!-- END GENERATED SOURCES -->

## Architecture (one file per source)

The full module inventory is generated from the package. Responsibilities and
dependency direction are documented in `docs/ARCHITECTURE.md`.

<!-- BEGIN GENERATED MODULES -->
```text
scripts/nrw_events/
  __init__.py
  ai_enrichment.py
  category_taxonomy.py
  common.py
  config.py
  core.py
  dates.py
  detail_enrichment.py
  early_publication.py
  event_builder.py
  event_types.py
  event_vocabulary.py
  health.py
  highlights.py
  http.py
  ical.py
  identity.py
  jsonld.py
  junk_rules.py
  location.py
  market_source_fallbacks.py
  models.py
  normalization.py
  observability.py
  quality.py
  radio_primary_resolution.py
  report.py
  reviewed_corrections.py
  reviewed_summaries.py
  richtext.py
  runner.py
  runtime.py
  scoring.py
  series.py
  source_specs.py
  source_types.py
  text.py
  title_normalization.py
  validation.py
  sources/
    registry.json
    __init__.py
    adfc_bonn.py
    afterjobparty.py
    b_future_festival.py
    beethovenfest_bonn.py
    bonn.py
    bonn_districts.py
    bonn_food.py
    bonn_literature.py
    bonn_venues.py
    bonner_weihnachtsmarkt.py
    bonnjetzt.py
    bonnkirmes.py
    bonnlive.py
    bundeskunsthalle.py
    cinema_specials.py
    coelln_antik_design.py
    coelln_konzept.py
    deutsches_museum_bonn.py
    fedcon_events.py
    fixed_markets.py
    flohmarkt.py
    geide.py
    grote_hiller.py
    harmonie.py
    haus_der_geschichte.py
    hgv_beuel.py
    hoffloh_bonn.py
    hofflohmaerkte.py
    in_guten_kreisen.py
    junges_theater_bonn.py
    katharinenhof.py
    kihapp.py
    kinderflohmarkt.py
    kleines_theater.py
    koeln.py
    koenigswinter.py
    krewelshof.py
    kunstmuseum_bonn.py
    kunstrasen_bonn.py
    lampert.py
    lupe_events.py
    marktcom.py
    max7.py
    mec_municipal.py
    meckenheim.py
    melan.py
    much.py
    museum_koenig.py
    naturregion_sieg.py
    okken.py
    radiobonn.py
    regional_common.py
    regional_feeds.py
    regional_html.py
    regional_ionas4.py
    regional_sitekit.py
    regional_tourism.py
    regional_venues.py
    requested_venues.py
    rhein_antik.py
    rheinbach_flohmarkt.py
    rheinevents.py
    rieder_markets.py
    rif_events.py
    rossel_wilberhofen.py
    ruhrguide.py
    salsainbonn.py
    schmitt_markets.py
    search.py
    siebengebirge.py
    siegburg.py
    theater_bonn.py
    theater_im_ballsaal.py
    theater_marabu.py
    tik_bonn.py
    tourismus_nrw_featured.py
    uni_bonn.py
```
<!-- END GENERATED MODULES -->

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

- `EXA_API_KEY` / `XAI_API_KEY` — credentials for the optional Exa and Grok search fallbacks.
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
- `NRW_EVENTS_ENABLE_GROK=1` — enable the slow/costly agentic Grok sweep (off by default).
- `NRW_EVENTS_USER_AGENT` — override the default browser-like user agent.
- `NRW_EVENTS_HTTP_RETRY_ATTEMPTS=5` — transient HTTP/network retry limit.
- `NRW_EVENTS_HTTP_REQUEST_BUDGET_SECONDS=45.0` — total request, retry, and backoff budget.
- `NRW_EVENTS_HTTP_RETRY_BASE_SECONDS=1.0` — exponential backoff base with jitter.
- `NRW_EVENTS_HTTP_RETRY_MAX_DELAY_SECONDS=60.0` — cap retry waits. `NRW_EVENTS_HTTP_MAX_RESPONSE_BYTES=10000000` keeps the current Bonn export complete while bounding unexpectedly large responses; set `0` only to opt into unlimited reads.
- `NRW_EVENTS_SOURCE_BASELINE_MIN_COUNT=10` — annotate a source that drops from a recent meaningful count to zero.
- `NRW_EVENTS_MINIMUM_SNAPSHOT_RATIO=0.5` — preserve the last-known-good snapshot when a run falls below half the previous event count.
- `NRW_EVENTS_MAX_FAILED_SOURCE_RATIO=0.5` — fail the run when more than half of active sources fail.
- `NRW_EVENTS_SOURCE_WORKERS=4` / `NRW_EVENTS_SOURCE_TIMEOUT_SECONDS=600` — source parallelism and network-phase budget.
- `NRW_EVENTS_SOURCE_PROCESSING_GRACE_SECONDS=180` — extra worker time to process an already fetched large source result.
- `NRW_EVENTS_BONN_DE_DELAY_SECONDS=2.0` — minimum delay between `bonn.de` requests.
- `NRW_EVENTS_BONN_CALENDAR_MAX_PAGES=30` — safety cap for paginated Bonn.de calendars.
- `BRIGHT_DATA_API_KEY` / `BRIGHT_DATA_ZONE` — Bright Data Web Unlocker credentials; vomFASS refreshes only on Mondays and always uses this proxy. Hofflohmärkte Köln and allowlisted IONAS4 regional calendars are direct-first and use it only after selected transient failures or exhausted direct-request timeouts.
- `NRW_EVENTS_CACHE_DIR=~/.cache/nrw-events` — persistent cache root for bounded detail-page enrichment.
- `XDG_CACHE_HOME=~/.cache` — cache base when `NRW_EVENTS_CACHE_DIR` is unset.
- `NRW_EVENTS_DETAIL_CACHE_TTL_HOURS=24` — default TTL for successful generic detail-page fetches; `0` disables memory and disk caching.
- `NRW_EVENTS_DETAIL_ENRICHMENT=1` — shared primary-detail enrichment; set to `0` to disable.
- `NRW_EVENTS_DETAIL_BATCH_TIMEOUT_SECONDS=45` — whole-source budget for optional detail-page enrichment.
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
