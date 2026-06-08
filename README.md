# NRW Events

A single-file, zero-dependency event aggregator for **Bonn and the surrounding NRW
region** (75 km radius — Köln, Siegburg, Königswinter, Düsseldorf, Aachen, and the
nearby Ahrtal / Siebengebirge / Andernach areas).

It pulls from a dozen-plus public sources, deduplicates them, scores each event by
**distance + category preference**, and prints a clean Markdown report grouped by
category. It also writes a JSON dump for programmatic use.

No framework, no package install — just Python 3 standard library.

## Why

City event calendars are fragmented: open-data APIs, RSS feeds, iCal feeds, JSON-LD
pages, and a long tail of small local stuff (Stadtteilfeste, Dorffeste, wine walks,
guided hikes) that never shows up in the big aggregators. This script fans out across
all of them in parallel and merges the results into one ranked list.

## Quick start

```bash
# Next 3 days (a Fri–Sun weekend)
bash scripts/nrw-events.sh

# Full week ahead
bash scripts/nrw-events.sh 7

# Just today
bash scripts/nrw-events.sh 1
```

Output goes to stdout as Markdown; a JSON copy is written to `/tmp/nrw-events-latest.json`.

You can also run the Python directly:

```bash
python3 scripts/nrw-events.py 5
```

## Requirements

- **Python 3.9+** (standard library only — `urllib`, `xml.etree`, `concurrent.futures`).
- No `pip install` step. No third-party packages.

## API keys (optional)

The script runs fully without keys — the deterministic scrapers do the heavy lifting.
Two optional keys unlock extra web-search fallbacks:

| Key           | Service              | Enables                                            |
|---------------|----------------------|----------------------------------------------------|
| `EXA_API_KEY` | [Exa](https://exa.ai) | Neural web-search fallback for obscure local pages |
| `XAI_API_KEY` | [xAI Grok](https://x.ai) | Optional agentic search sweep (off by default)  |

Provide them as real environment variables, or copy `.env.example` to `.env` and fill
them in:

```bash
cp .env.example .env
$EDITOR .env
```

Lookup order: real env vars → `NRW_EVENTS_ENV_FILE` → repo-root `.env` → CWD `.env`.
Real environment variables always win. **`.env` is gitignored.**

## Configuration (environment variables)

Defaults favour **quantity over quality** — the report shows everything so you can
filter it yourself.

| Variable                      | Default | Effect                                                              |
|-------------------------------|---------|---------------------------------------------------------------------|
| `NRW_EVENTS_MAX_PER_SECTION`  | `0`     | Cap events shown per category (`0` = show all). Use for terse output.|
| `NRW_EVENTS_SCORE_FLOOR`      | `0.4`   | Minimum score to keep. Lower = more results/more noise.             |
| `NRW_EVENTS_EXA_QUERIES`      | `10`    | How many search queries to send to Exa (each ~5 results).           |
| `NRW_EVENTS_ENABLE_GROK`      | unset   | Set to `1` to enable the slow/costly Grok agentic sweep.            |
| `NRW_EVENTS_ENV_FILE`         | unset   | Explicit path to a `.env` file.                                     |

Example — a tight, high-signal weekend list:

```bash
NRW_EVENTS_SCORE_FLOOR=0.7 NRW_EVENTS_MAX_PER_SECTION=5 bash scripts/nrw-events.sh
```

## How scoring works

Each event gets `distance_score × category_weight × source_trust`:

- **Distance** — Bonn city centre = 1.0, decaying linearly to ~0.3 at the 75 km edge.
- **Category** — opinionated weights in `CATEGORY_WEIGHT` (e.g. electronic/techno `1.8×`,
  architecture `1.6×`, wine walks `1.55×`, concerts/exhibitions `1.5×`/`1.4×`;
  kids-only events are demoted). **Edit these to match your own taste.**
- **Trust** — per-source multiplier (structured APIs > scraped HTML > web search).

Events with a family side-offer (e.g. a wine walk that also has a kids' quiz) are *not*
demoted; only genuinely kids-only events are.

## Sources

Deterministic, structured sources are preferred over scraping, and scraping over search.

- **Open-data API** — Köln Open Data events
- **RSS / HTML** — Bonn.de event calendar
- **iCal (RFC 5545)** — generic fetcher (used for Harmonie Bonn + curated Meetup groups)
- **JSON-LD (schema.org)** — VVS Siebengebirge, Andernach tourism
- **Bespoke scrapers** — Königswinter calendar, Tourismus Siebengebirge, Ahrtal /
  Ahrwein wine events, Bonn.jetzt, Songkick (concerts), Bundeskunsthalle
- **Curated recurring** — Rheinaue flea market, official Bonn district-festival list
- **Web search fallback** — Exa (default), Grok (opt-in)

### Adding a source

Most German venues run WordPress + "The Events Calendar" (Tribe), which exposes a clean
`.ics` feed at `?post_type=tribe_events&ical=1`. Prefer iCal/JSON-LD over HTML scraping.

1. Probe it first:
   ```bash
   curl -sL '<url>' | grep -c 'BEGIN:VEVENT'        # iCal
   curl -sL '<url>' | grep -c 'application/ld+json' # JSON-LD
   ```
2. Add a one-line fetcher using the generic helpers `fetch_ical(...)` or
   `events_from_jsonld(...)` (see `fetch_harmonie_bonn()` for the pattern).
3. Register it in the `fetchers = {...}` dict in `main()`.
4. Add any new town to `VENUE_COORDS` so distance scoring works.

For curated Meetup groups, edit the `MEETUP_GROUPS` list — each group's public iCal feed
lives at `https://www.meetup.com/<slug>/events/ical/` (no auth).

## Output

- **Markdown** to stdout — grouped into Nightlife, Concerts, Exhibitions, Talks/Community,
  Walks/Markets/Outdoor, and Other; each event shows when, venue, distance, a star rating,
  description, and link.
- **JSON** to `/tmp/nrw-events-latest.json` — the full deduplicated, scored list.

## Customisation

This is intentionally a single hackable script. The most common edits:

- **`CATEGORY_WEIGHT`** — retune the ranking to your interests.
- **`BONN_LAT` / `BONN_LON` / `MAX_RADIUS_KM`** — recenter on a different city/radius.
- **`VENUE_COORDS`** — add towns for accurate distance scoring.
- **`fetchers` dict** — add or remove sources.

## License

MIT — see [LICENSE](LICENSE).

## Disclaimer

This tool scrapes and aggregates publicly available event data from third-party sites.
Always verify dates, times, and ticketing on the official event page before showing up.
Respect each source's terms of use and rate limits.
