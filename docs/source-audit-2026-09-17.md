# Importer source audit: 2026-09-17

Base: `7e887f7` (`main`, including merged #373, #374 and #375).
Read-only first-party HTTP probes and offline response replay; no live import,
consumer pin update, merge, deployment or publication.

## Brüser Berg: verified calendar migration

- `https://brueser-berg-puls.base44.app/`: HTTP 404.
- The official association homepage links to
  `https://brueser-berg.de/veranstaltungen-2025.html` (HTTP 200). Despite the
  historical slug, that page now explicitly links to
  `https://brueser-berg-2026.base44.app` (HTTP 200).
- The new bootstrap declares the same application ID,
  `6a71c68354b14b3b2e8741d7`. The adapter's existing public entity endpoint on
  that verified host returns HTTP 200 with 90 rows:
  `https://brueser-berg-2026.base44.app/api/apps/6a71c68354b14b3b2e8741d7/entities/Event?sort=date&limit=500`.

Change only the host constant (also used for missing-link fallback). Preserve
source ID, locality gate, entity discovery, parsing, optional detail enrichment,
retention and failure reporting. No scraping of authenticated/private data.
The captured bootstrap and full 90-row public response are retained as offline
fixtures. The response was recaptured for review on September 17 at 06:30 UTC.
All keys, record order, null/empty values and event content are preserved;
only nonempty `created_by_id` account identifiers are replaced with a 24-character
zero string. JSON whitespace is normalized. No fields or records are omitted.
The raw response SHA-256 is
`2e351d8843206c6dc02b37a08f96a64b20fd1d12680c3840f4492ef9eb23507b`;
the sanitized fixture SHA-256 is
`b9193b7f4bdc328c49bd2ed490c8669702c7b244e38ba667ba93e1fc85fe8344`.
The regression suite replays both the audit window (two events) and a window
through December 31 (all eight local occurrences), asserting the 90/8/82 split,
canonical validation, deduplication and source identity.

Offline replay of all 90 saved rows, without detail networking: 8 local / 82
nonlocal; 8 raw / 8 canonical / 8 after global deduplication. These are adapter
counts, not a publication forecast. The audit window September 17–October 14
contains the first two; the other six remain outside that window:

| Date | Local occurrence | Existing category |
| --- | --- | --- |
| October 11 | Hofflohmarkt Bonn-Brüser Berg | market |
| October 13 | Energieberatung der Bonner Energie Agentur | workshop |
| October 17 | Workshop: Identität & Antirassismus – Offene Gespräche | workshop |
| November 5 | Besuch der Schulen und Kitas durch Sankt Martin | workshop |
| November 6 | Martinszug | kids |
| November 8 | Kreatives Atelier – Weihnachtsschmuck für Senioren | workshop |
| November 22 | Schmücken des Weihnachtsbaums und Aufstellen der Krippe | workshop |
| December 5 | Brüser Berg Advent Samstag | market |

## Bonn.de: singular conference label must retain existing exclusion

The live HTML calendar's format selector identifies `Tagung/Kongress` as category
60430. The JSON feed returns HTTP 200 but has no records with that spelling, so
JSON-only inspection misses the drift. The HTML calendar filtered by category
60430 and September 17–October 14 returns one results page. Four actual event
cards carry that label; their exact inner markup is retained in the fixture.

The existing `Tagungen/Kongresse` label is explicitly blocked. Add the singular
label to that same set, **not** to the format map, allowlist, free-activity
allowlist or neutral metadata set. This is a deliberate policy-preserving
exclusion, not merely warning suppression: mixed-topic conference cards can
otherwise bypass the existing exclusion through their allowed second topic.

Per-occurrence replay through the actual listing parser, canonical validation
and global deduplication, optional detail fetching disabled:

| Date | Occurrence | Before | After |
| --- | --- | --- | --- |
| September 25 | BarCamp Nachhaltige Zukunft Bonn 2026 | 1 raw / 1 canonical / 1 dedup, workshop | excluded |
| October 1 | Fachtagung Klima-Engagement | excluded | excluded |
| October 2 | Kulturveranstaltungen als politische Orte? | 1 raw / 1 canonical / 1 dedup, talk | excluded |
| October 8 | Fachtagung für Engagierte der Seniorenarbeit Bonn & NRW | excluded | excluded |

Reconciliation: before 2 accepted + 2 excluded = 4; after 0 accepted + 4 excluded
= 4. Both formerly accepted rows had unknown price. No admission inference is
added or changed. Other adapters' independently sourced occurrences are not
removed. Unknown labels remain actionable. Tests cover both label spellings,
allowed/free co-tags, the captured cards and source-outage controls.

## Rhein Antik: unresolved upstream maintenance

Both `https://rhein-antik.de/termine/` and `https://rhein-antik.de/` return HTTP 503
with the same 2,557-byte maintenance document. The previously identified public
WordPress route `https://rhein-antik.de/wp-json/wp/v2/pages?slug=termine` returns
HTTP 200 with an empty array. Search still indexes the original schedule and
third-party market listings, but neither is a verified replacement calendar
published by Rhein Antik. Leave this adapter and its outage evidence unchanged.
The reported five retries / 69 consecutive failures are monitoring history,
not independently reproduced historical observations in this audit.

## Verification

- RED: four new tests ran against unchanged production code; six assertions
  failed (retired host, missing singular block, leaked cards and warnings).
- GREEN: 38 targeted tests passed, including existing source and taxonomy tests.
- Full offline gate: 1,659 tests passed (102.485 seconds).
- Ruff: all checks passed. Mypy: no issues in 67 source files.
- Generated inventories regenerated without drift; `git diff --check` passed.
- GitHub Actions API reports `enabled: false`; local gates are the evidence,
  not absent CI checks.

Review follow-up: replaced the reduced JSON fixture with the complete sanitized
capture and added the full locality replay. All 15 focused tests and all 1,660
offline tests passed (27.004 seconds for the full suite). Ruff, mypy (67 source
files), generated-inventory checks and `git diff --check` passed. The official
association link and disabled GitHub Actions setting were rechecked.
