# Source audit: 2026-09-10

Base inspected: `8075081ae1d53820034f61c3f219d07634e19e64`.
No production changes, source replacement, retry increase, merge or deployment.

## Bonner Kinemathek

The live programme returned HTTP 200 and **32 recognizable cards**, but the
existing special-format gate rejected all 32. This was not a broken HTML wrapper:
`Pink Movie Club: Grandma` and `Fahrradkino: Sommer auf Asphalt` use publisher-owned
series names without the generic special-format keywords. Extend only this
source's title-prefix gate, not the shared cinema classifier or ordinary-screening
policy. The other 30 live cards remain excluded.

First-party listing: <https://www.bonnerkinemathek.de/programm/>.
Both detail pages also returned HTTP 200:

- <https://www.bonnerkinemathek.de/programm/pink-movie-club-grandma/> —
  2026-09-11, 20:30, Kino in der Brotfabrik.
- <https://www.bonnerkinemathek.de/programm/fahrradkino-sommer-auf-asphalt-2/> —
  2026-09-17, 19:30, Kino in der Brotfabrik.

`tests/fixtures/bonner_kinemathek_programme_20260910.html` contains verbatim
programme cards for those specials and the ordinary-screening control `The Invite`.
The regression failed with `[]` on the base and now retains only the two specials.

## Bonn.de taxonomy

<https://www.bonn.de/citykey/events-json.php> returned 5,001 records, with one
`Käpt´n Book` record: `Lesefest Käpt´n Book: Abschlussfest 2026`, October 11,
10:00–18:00, Deutsches Museum Bonn. Its topic is `Aktion/Workshop`; `Käpt´n Book`
is an umbrella facet, not an occurrence format or evidence of free admission.
Recognize the exact source spelling as neutral, preserving its topic and existing
eligibility rules. Do not map every children's-festival occurrence to `talk` or
`festival`, and do not make the facet alone importable.

The unmodified JSON record is captured in
`tests/fixtures/bonn_kaeptn_book_20260910.json`. Its actual date is **outside** the
production window. The fixture test widens its own window, without changing the
record's date. Additional listing tests cover workshop/reading classification,
unknown admission, explicit paid/free detail evidence, and facet-only rejection.

## Rhein Antik: unresolved upstream maintenance

The daily report dates continuous failure to August 17; this audit independently
confirms today's state, not every intervening day's history. GET probes returned
HTTP 503 and the same 2,557-byte `Maintenance` response, without `Retry-After`, at:

- `https://rhein-antik.de/termine/`
- `https://rhein-antik.de/`
- `https://www.rhein-antik.de/termine/`
- `http://rhein-antik.de/termine/`
- `https://rhein-antik.de/veranstaltungen/`
- `https://rhein-antik.de/antikmarkt-bonn/`
- `https://rhein-antik.de/bonn/`
- `https://rhein-antik.de/wp-sitemap.xml`

The WordPress API root is reachable (HTTP 200), but both
`/wp-json/wp/v2/pages?slug=termine` and
`/wp-json/wp/v2/pages?per_page=100&_fields=id,slug,status,link,modified,title,content`
return empty arrays. Search still indexes the original schedule, but a cached
search snippet is not a live, reusable first-party fallback. No evidence-backed
replacement was found. Leave the adapter, retries and retention behavior unchanged;
recheck the original schedule after upstream recovery.

## Verification and publication scope

The public `https://www.veranstaltungen-bonn.de/event-data.json` reports generation
`2026-09-10T05:07:16+02:00`, exact window **2026-09-10 through 2026-10-07**,
and 2,684 records. Neither target film is in that public artifact.

A local `run_import` execution used that exact window, the affected Kinemathek
child through its existing fetch/health wrapper, `bonn.fetch_events`, and
`rhein_antik.fetch`. It used isolated cache/output settings, no previous production
snapshot, no AI enrichment, and did not publish files to production:

| Layer/source | Result |
| --- | --- |
| Kinemathek raw / canonical accepted | 2 / 2, healthy, no warnings |
| Bonn.de raw / canonical accepted | 4,543 / 1,178, healthy, no warnings |
| Rhein Antik | 0 / 0, degraded HTTP 503 |
| Selected-source pre-dedup publication candidates | 1,103 |
| Selected-source post-dedup publication result | 1,061 |

Both films survive canonical validation, scoring and global deduplication in that
selected-source run, with correct dates/times, `cinema`, detail links, scraped
synopses and unknown (not inferred-free) admission. These counts are **not** an
all-source production forecast; the website was not rebuilt or deployed.

Tests: new failures reproduced before implementation; 72 focused tests and the
full 1,639-test offline suite passed. Generated inventories remain unchanged.
GitHub Actions are disabled for this repository by policy and verified API state;
there are no CI jobs to wait for, and this change does not enable them.

After importer review/merge, the website needs a separate update of both its
submodule gitlink and immutable `nrw-events.ref`, followed by its authorized
release workflow. An importer PR alone cannot change production.
