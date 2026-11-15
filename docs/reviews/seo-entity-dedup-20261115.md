# Weekly report action 3: occurrence review

Reviewed against importer base `6198c78` on 2026-11-15. The smallest safe
importer change is regression coverage: current matching already gives the
correct result. No matching threshold, adapter, validation or publication
policy needs changing for these pairs.

## Offline evidence

The four exact records in `tests/data/seo-entity-occurrences.json` were copied
without field edits from:

`/home/clawdbot/.local/share/veranstaltungen-bonn/morning-runner/.cache/releases/20261115T043328-ead5732f/content/original/events-archive.json`

None of the four IDs occurs in that release's `content/original/website-events.json`.
The source URLs below are recorded provenance, not newly fetched evidence.

## Dernau: separate occurrences, no redirect between these IDs

Both records come from `ahrtal-tourismus` / Ahrtal, at
“Ortskern von Dernau und in den Weinbergen”, Bachstraße, 53507 Dernau.

| Public ID | Recorded occurrence |
| --- | --- |
| `martinsmarkt-der-regionalen-genuesse-941a096473` | 2026-11-06, 18:00; Friday torch walk |
| `martinsmarkt-der-regionalen-genuesse-2b12e175ca` | 2026-11-07, 11:00–23:00; Saturday market |

The Friday record's time note explicitly distinguishes Friday's 18:00 walk
from the Saturday/Sunday market at 11:00. Its source URL is
`https://www.ahrtal.com/de/events/martinsmarkt-der-regionalen-genuesse-2026-11-06/eventtermin.html`.
Saturday carries the overview URL `https://www.ahrtal.com/de/events` and a
series/run association, which is not proof of the same occurrence. Neither
record lists the other as a previous or merged ID. Keep both public IDs.

## Bad Godesberg: one occurrence, existing importer winner

Both records describe 2026-11-08, 11:00–17:00, Bonn-Bad Godesberg. The Bonn.de
row names “Bad Godesberger Innenstadt” but its address is Theaterplatz,
53177 Bonn; Stadtmarketing names Theaterplatz directly.

- Winner: `deutsch-hollaendischer-stoff-und-tuchmarkt-9c953f1492`,
  `bad-godesberg-stadtmarketing` / Bad Godesberg Stadtmarketing.
  Source: `https://bad-godesberg.info/veranstaltungen_st/stoff-und-tuchmarkt`.
- Retired copy: `deutsch-hollaendischer-stoffmarkt-d829ed1c4a`,
  `bonn-de-events` / Bonn.de Events.
  Source: `https://www.bonn.de/veranstaltungskalender/veranstaltungen/hauptkalender/extern/Deutsch-Hollaendische-Stoffmarkt-.php`.

The archived winner already contains the retired ID in both `previous_event_ids`
and `merged_event_ids`, plus both source links. The old copy was last seen on
November 5; the winner was last seen November 8. This is residual archived
duplication, not a current importer matching failure. The parent should use
this winner for the website archive/redirect work.

## Code path and regression scope

`publication_selection.py` reconciles published IDs before `report.deduplicate`.
`dedup_index.py` admits the Stoffmarkt candidates; `duplicate_identity.py`
matches the title variant and existing reviewed Theaterplatz/Innenstadt venue
alias while checking the occurrence clock/date. `deduplication.py` prefers
the primary publisher, and `dedup_merge.py` preserves links and merge lineage
through canonical validation. The website owns durable archive storage.

Tests remove pre-existing links/lineage before canonicalization so the stored
merge is not needed to establish matching. All 24 input orders produce three
occurrences; published-ID reconciliation preserves the exact historical IDs.
Nearby different dates, start times, cities, venues and house numbers stay
separate. No new fuzzy rule or production import is needed. Historical archive
cleanup and redirects remain outside this repository and this commit.
