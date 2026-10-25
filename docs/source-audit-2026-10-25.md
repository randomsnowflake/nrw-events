# Importer source audit: 2026-10-25

Base: `ed0816b` (`main`). Trigger: weekly report of 25 October 2026
(technical evidence observed 04:50 CET; newest saved import is run
`12a0812f…`, generated 2026-10-24 04:41 CEST, importer `ed0816b`, 28-day
window 24.10.–20.11.). Read-only first-party HTTP probes on 25 October
09:00–10:15 CET and offline replay; no live import, pin update, merge or
deployment. Builds on #425 (Pantheon, Rathausmusik, LuPe; same morning).

## Requested venue calendars: Pantheon and Haus der Springmaus

Both `source_collapse: zero_after_recent_nonempty` findings come from the same
run. They are child anomalies (`retention_policy._attach_child_baselines`):
a child with at least 10 accepted records in the previous run
(`NRW_EVENTS_SOURCE_BASELINE_MIN_COUNT`) and 0 now. The anomaly does not say
why; the run's endpoint telemetry does:

| Source | Endpoint 24.10. | Last success | Retained in 24.10. feed |
| --- | --- | --- | --- |
| `pantheon-bonn` | `https://www.pantheon.de/programm/`: 2 attempts, `URLError: timed out` | 2026-10-23 04:42 | 18 |
| `haus-der-springmaus` | `https://www.springmaus-theater.de/events.html?s=all`: 2 attempts, `URLError: timed out` | 2026-10-23 04:42 | 22 (+2 `bonn-de-events`, +1 submission) |

On 20 October both parsed normally (Pantheon 140 candidates, Springmaus 152).
The timeouts started the outage clock (`first_failure_at 2026-10-24T02:41Z`,
1 failure). Retention kept the events; after 7 days of continued failure
(`retention_policy.py`, `timedelta(days=7)`) they would leave the feed.

### Haus der Springmaus: transient timeout, no defect

Live on 25.10.: HTTP 200, 431 KB. The unchanged parser returns 147 records
(28.10.2026–02.12.2027), 26 of them between 25.10. and 22.11., e.g.
28.10. "Alice Köfer – Alles zu meiner Zeit". A commented-out card contains a
Latin-1 byte (`Mär.`); `http._decode_body` already handles that. No code
change. Expected: the next import succeeds and resets the ledger.

### Pantheon: upstream programme list is empty

Live on 25.10. (re-verified after #425):

- `/programm/`, `?date=2026-10`, `?date=2026-11` and the homepage section
  "Die nächsten Vorstellungen": HTTP 200, about 18.7 KB instead of 824 KB, an
  empty `<div id="events"><ul>` with only a `cache:eventlist/…` comment and an
  empty month navigation; `/programm/` is headed "Januar".
- The first-party ticket endpoint still reports retained shows as orderable:
  `https://www.pantheon.de/json/event.json?t[]=568671&t[]=604004&t[]=610115`
  returns `isTicketOrder: 1` for all three IDs.

The shows still exist, but the publisher's HTML listing is broken or being
rebuilt. No list exists to parse, so there is no parser or URL defect and no
first-party listing to switch to (`/sitemap.xml`: 404). Do not fabricate dates
from retained records or ticket IDs. If the listing stays empty, the
18 retained events expire with the 7-day grace (from the 31 October run).
Other coverage in the 24.10. feed: 4 `bonn-de-events` records at
`pantheon-theater`.

## Permanent source outages (task 06)

Ledger figures from the 24.10. import; `consecutive_failures` counts import
runs, not days. Order: confirmed feed loss first, then noise.

| Prio | Source | Official URL | Cause (25.10.) | Real future events affected | Other coverage | Smallest repair |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | `parkbuchhandlung` | https://www.parkbuchhandlung.de/veranstaltungen/ | **Parser defect.** The theme now writes `class="mkdf-event-title" >`; the pattern required `">`. 0 candidates from a 258 KB page since 17.10. | 07.11. Lese-Highlights (only retained, drops after the 25.10. run), 10.11. Julia von Lucadou, 18.11. Cohn-Bendit & Fischer, 05.12. Weihnachtszauber, 17.12. Edgar Selge | 10.11. via `theater-bonn`; the others are missing | Fixed in #428 (regex + live fixture + positive/negative tests) |
| 2 | `rhein-antik` | https://rhein-antik.de/termine/ | Upstream maintenance page: HTTP 503, 2,557 bytes "Maintenance", also on `/`. `wp-json/wp/v2/pages?slug=termine` returns `[]`. Last success 17.08.; failing since 09.09. (135 runs). | Not verifiable: no reachable first-party schedule | No Rhein Antik market in the feed; other antique markets come from other organizers | None in code. Keep the adapter and ledger (see audit 2026-09-10). 2027 dates go to the separate season task. |
| 3 | `json-ld` | none; pseudo source | `jsonld.py:76` logs `JSONDecodeError` as source "JSON-LD" without `source_id`. Origin: VHS Bonn course pages (e.g. https://www.vhs-bonn.de/kurs/N1832) whose schema.org `Course` block lacks a comma after `"startDate"` (line 11, col 5). 19 warnings in the 24.10. run; since 05.10. (26 runs). | None: VHS events come from list + iCal (16 accepted); `Course` is not an Event type and is never extracted | n/a | Report JSON-LD decode errors on detail pages as a diagnostic (`…Warning`) or with the page's `source_id`/`optional_detail`, so they neither degrade VHS nor open a "json-ld" ledger. Keep a test that a JSON-LD-only list adapter still turns parser-empty. |
| 4 | `bonn-de-events` | https://www.bonn.de/citykey/events-json.php, https://www.bonn.de/bonn-erleben/ausgehen-und-erleben/veranstaltungskalender.php | `degraded` without data loss; there is no count threshold, see below. 24.10.: 2× `CategoryTaxonomyWarning` (unknown: "! Stadtbezirk", "Beethovenjahr 2027", "Martinszug", "Förderschule", "Theater Bonn. Tanz.") + timeouts of `bonn-venue-geojson` (`https://stadtplan.bonn.de/geojson?OD=4490`/`4489`; on 25.10. HTTP 200 with 2 bytes after about 36 s). On 20.10. only the taxonomy warnings: still degraded. | None: 1,490 accepted (20.10.: 1,511); GeoJSON only adds venue coordinates | n/a | Classify the five labels in `bonn.py` (`_KNOWN_SOURCE_CATEGORIES` or the map/block sets): an editorial decision per label, one PR. GeoJSON: upstream; no longer timeout. |
| 5 | `in-guten-kreisen` | https://in-guten-kreisen.de/winedb/ical.php (linked from https://in-guten-kreisen.de/termine/) | Real empty programme: valid `VCALENDAR` (645 bytes) without `VEVENT`; the first-party list `winedb/display.php` "Weinproben" is empty too. `fetch_ical` treats that as parser-empty → `degraded`, ledger since 17.10. (12 runs). Not "completely empty": the official Friday series "Wein ins Wochenende" still yields 4 accepted records. | None known | n/a | `fetch_ical(..., empty_calendar_is_valid=True)` in `in_guten_kreisen.py`. The existing `ical.py` guard still flags truncated feeds or feeds with a `BEGIN:VEVENT` marker. |
| 6 | `rathausmusik` | http://www.rathausmusik.com/ | Season over: the reviewed `rathausmusik_schedule` correction (July–September) expired on 2026-09-30 (#425), so the parser returns `[]` → parser-empty. 24.10. additionally a one-off HTTP 503; 25.10. HTTP 200, 25,844 bytes, as on 20.10. Failing since 10.10. (36 runs). | None: Thursday concerts July–September only | n/a | Optional: treat an empty result as expected while no `rathausmusik_schedule` entry is active (`empty_is_healthy` callable). The 2027 season needs a new reviewed correction anyway. Do not extend the expired correction. |

### Why `bonn-de-events` is "degraded" (no threshold)

Per-source status comes from `SourceResult.finish` (`health.py:263`): `failed`
on an adapter exception; `parser_empty` when a measured endpoint is empty and
no records exist; otherwise **any** entry in `warnings` or any endpoint with
`error_type` → `degraded` (`health.py:280`). This includes editorial
diagnostics such as `CategoryTaxonomyWarning`. A structural rejection, that is
any reason outside `quality:`/`filter:`, also degrades a source
(`source_execution.py:306`). `ops/import_health.py:59` turns every degraded
row into a `source_degraded` incident. There is no per-source count
threshold. Count rules exist only elsewhere:

- `zero_after_recent_nonempty` / `large_drop_after_recent_nonempty`: the
  previous count is at least 10 and the new count is 0, or less than half
  (`retention_policy.py:403–449`).
- The whole run fails below 50 % of the previous event count or when more than
  50 % of the attempted sources fail (`source_execution._run_status`).

The distinction matters. A diagnostic (`…Warning`) degrades the status, but
`health.outage_warning` does not count it as an outage. So it starts no
retention and no outage clock
(`tests/test_source_outage.py::test_taxonomy_diagnostic_clears_old_false_outage_but_real_failures_still_age`
asserts exactly that). "degraded" with 1,490 records therefore means: data
complete, at least one warning open.

## Not done

No live import, pin bump, release or deployment. #428 and the
Springmaus/Pantheon state take effect only after a pin bump, a release and the
next real import. The repairs in table rows 3–6 are separate single tasks.
