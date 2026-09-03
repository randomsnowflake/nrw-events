# Importer performance

## Status

The local implementation covers measurement, bounded caches, safe iCal pruning, and bounded component concurrency.
The fixtures prove repeatability and controlled local improvements, not production speed.
Live canaries and the website release gate remain required before deployment.
GitHub Actions remain outside this workflow.

### Initial measurement, 2026-09-03

A local replay used 1,091 records from the saved public Wachtberg iCal response.
Five runs with telemetry took 3,664.83 ms median (minimum 3,654.34 ms, p95 3,673.92 ms).
Five runs without telemetry took 3,657.50 ms median (minimum 3,645.80 ms, p95 3,677.67 ms).
The measured overhead was 0.20%.
All repetitions and both modes produced identical snapshots under the explicit comparison policy.

The first measured run spent 2,090.27 ms in taxonomy and 3,274.80 ms in event construction.
These durations overlap because event construction calls taxonomy.
The run published 75 events and recorded 899 out-of-window parser candidates.
This replay did not include universal detail enrichment or other sources.
It identifies an iCal CPU bottleneck, not the complete production critical path.

The initial implementation passed 1,493 local tests with 88% coverage, Ruff, mypy, and both documentation checks.
The suite emitted two socket ResourceWarnings and one SQLite ResourceWarning.
The unchanged parent commit `0d4184282d69ff7e7d363d0c38a4462a6febe712` emitted the same warning classes during its 1,477-test run.
These warnings remain a separate test-cleanup concern.

### Remaining work for #356

- Verify the measured multi-source critical path against the production run.
- Verify the website snapshot contract before release.

The current measurements and full test logs are in `/tmp/nrw-performance-evidence.rnDzxw` on the development machine.
The detached `base` worktree in that directory preserves the parent version for further comparisons.
No production deployment or refresh forms part of this initial result.

## Offline replay

Run the synthetic fixture from the importer directory:

```sh
PYTHONPATH=scripts .venv/bin/python -m nrw_events.benchmark \
  tests/data/performance/manifest.json --repetitions 5 \
  --output /tmp/nrw-events-benchmark.json
```

Each repetition uses a fresh Python process and a separate temporary state directory.
The process does not load `.env` or inherit API credentials.
AI enrichment is disabled.
The transport serves recorded response bodies instead of live HTTP requests.
Missing responses cause the benchmark to fail, even if an adapter catches the transport error.

The manifest fixes the date and publication window.
Response file paths are relative to the manifest.
The `sources` array accepts registered source names or declarative iCal sources.
The example manifest shows the iCal fields.
Optional `previous_snapshot` and `series_ledger` fields select existing input files.
The benchmark reads these files but does not publish over them.

The optional `detail_cache_dir` field copies an existing cache into the isolated state directory.
The original cache remains read-only.
The `detail_cache_seed` array prepares selected direct-transport entries from recorded response files.
Each entry specifies `namespace`, `url`, `file`, and optional `age_seconds`.
The default age is 60 seconds.

Keep operator-supplied responses outside the repository.
Do not commit credentials, production caches, or private source text.

## Results

The JSON report includes each run, its snapshot, aggregate counts, and stage durations.
It reports minimum, median, and nearest-rank p95 for wall time and process CPU time.
It returns a nonzero exit code for semantic differences or a failed import.
Degraded sources remain visible in each snapshot.

Stage durations include nested work.
Concurrent stages overlap.
Their sum is not the elapsed import time.
Thread CPU time excludes network waits, lock waits, and GIL waits.
The outer process CPU measurement includes all importer threads.
The elapsed measurement excludes Python startup and response-file loading.

The current replay includes parsing, canonicalization, retention, deduplication, series enrichment, snapshot construction, and JSON serialization.
It does not exercise the atomic production generation switch.
It does not establish live-network performance or source stability.

## Measurement overhead

Repeat the same command with `--without-telemetry`.
Compare at least five runs in each mode on the same quiet machine.
Use identical input files, Python versions, and worker counts.
Do not infer production gains from the small example fixture.

## Snapshot equivalence

Compare two metadata snapshots:

```sh
PYTHONPATH=scripts .venv/bin/python -m nrw_events.snapshot_compare \
  /tmp/baseline-meta.json /tmp/candidate-meta.json
```

The comparator ignores only explicit operational paths in `VOLATILE_PATHS`.
These paths include the top-level run ID, generation timestamp, local output path, and known duration fields.
Event IDs, public links, event order, nested series run IDs, warnings, source health, and retention remain strict.
Unknown timing fields also remain strict.

## Production diagnostics

Set `NRW_EVENTS_PERFORMANCE=1` for an authorized importer invocation.
The importer emits one `import_performance` JSON record on stderr.
The record contains aggregate metrics, not event text or URLs.
The public snapshot schema remains unchanged.
Remove this variable to disable the diagnostics.

Do not start another production refresh only to collect metrics while an authorized refresh is active.

### Extended measurement and composite replay

Metrics now include per-source stages and counts, a separate iCal parser stage, filtering, validation, ranking, and each deduplication pass.
The replay compares public metadata, highlights, and the durable series ledger.
No ledger fields are excluded from this comparison.

The extended measurement took 2,620.45 ms median with telemetry and 2,605.92 ms without telemetry on Wachtberg.
Each mode used five runs.
The measured overhead was 0.56%, with identical public data and durable artifacts.

Generate the composite fixture in an empty directory:

```sh
.venv/bin/python scripts/generate_composite_performance_fixture.py /tmp/nrw-composite-fixture --per-calendar 10
```

This fixture uses the real SiteKit and IONAS4 adapters against synthetic recorded responses.
It covers eleven municipal calendars, shared detail enrichment, 55 cache hits, and 165 cache misses.
The first two repeated runs produced 110 events, healthy source states, no warnings, and identical artifacts.
The fixture does not contact these municipalities.

## Taxonomy keyword cache

The keyword cache stores immutable matches for identical text, keyword policy, and title scope.
It holds at most 8,192 entries.
Texts longer than 8,192 characters bypass the cache.
Each caller receives a separate result list.
Final classifications, source defaults, and reviewed fallback decisions remain uncached.

Set `NRW_EVENTS_TAXONOMY_CACHE=0` to disable this optimization.
For an offline comparison, set `"taxonomy_cache": false` in the replay manifest.
The benchmark reports cache hits, misses, current size, maximum size, and process peak RSS.

The Wachtberg replay took 2,649.86 ms median with the cache (minimum 2,647.54 ms, p95 2,689.56 ms).
The same implementation with the cache disabled took 3,666.66 ms median (minimum 3,651.83 ms, p95 3,682.17 ms).
Each mode used five fresh processes.
The runtime reduction was 27.73%, with zero semantic differences.
The first cached run reused 51,758 of 63,742 keyword-group lookups.
This result applies to the recorded iCal pipeline, not the complete production import.

The 10,000-record synthetic fixture took 17,597.98 ms median without the cache and 9,428.31 ms with it.
Each mode used five fresh processes and produced identical snapshots.
Peak RSS was 117.17 MiB without the cache and 118.98 MiB with it.
The fixture has unusually repetitive text, so its 46.42% reduction is not a forecast for production.
The cache held 2,870 entries after the run.
A separate 10,000-key concurrency test filled the cache and verified the 8,192-entry limit.

Generate a larger synthetic fixture in a new directory:

```sh
.venv/bin/python scripts/generate_performance_fixture.py /tmp/nrw-scale-fixture --records 10000
```

Run both generated manifests through the benchmark.
The fixture contains two independent calendars, historical and current dates, recurring events, RDATE, EXDATE, and cancellations.
Its `.example.test` URLs identify synthetic data, not public events.

## Comparison-key cache

The comparison-key cache stores immutable normalized strings.
The key contains the original text and the separator.
It holds at most 16,384 entries and bypasses text longer than 4,096 characters.
Null-input compatibility and Unicode handling remain unchanged.

Set `NRW_EVENTS_NORMALIZATION_CACHE=0` to disable this cache independently.
For an offline comparison, set `"normalization_cache": false` in the manifest.
To disable both optimizations, set both cache flags to zero.

With both caches active, the Wachtberg replay took 2,627.37 ms median across five runs.
The result was 28.34% faster than the uncached 3,666.66 ms baseline, with identical snapshots.
Most of this improvement came from the keyword cache.
The comparison-key cache targets repeated identity and deduplication work in larger mixed-source imports.
The synthetic 10,000-record fixture took 8,709.72 ms median with both caches (minimum 8,640.64 ms, p95 8,764.94 ms).
This was 50.51% faster than the uncached fixture and 7.62% faster than the keyword cache alone.
All snapshots remained identical.
Peak RSS was 118.36 MiB.
The first run reused 1,037,509 of 1,038,428 comparison keys and retained 919 entries.

## iCal pruning constraint

Out-of-window records are not disposable.
The runner passes them to the series ledger, including historical records and cancellation announcements.
A date-only filter would change series identities, announcement counts, or ledger state.
Any fast path must preserve these effects and the original quality decisions.
The equivalence gate includes the durable series ledger, not only public metadata.

### Safe quality pruning

The iCal parser evaluates the existing quality policy before full event construction.
It skips only scheduled records that the same policy already rejects.
Cancellation and postponement records keep the complete construction path.
All surviving records retain their original fields, including historical series information.
This replaces the proposed date-only optimization in #358 because that approach would change the ledger.

The parser reuses immutable quality decisions within one calendar invocation.
The complete quality input forms the cache key.
The cache holds at most 2,048 entries and clears when full.
No decisions survive the parser invocation.
If the quality policy reads another field, extend the preparation and its equivalence tests together.

Set `NRW_EVENTS_ICAL_PRUNE=0` to restore the complete construction path.
For an offline comparison, set `"ical_prune": false` in the manifest.

Five Wachtberg runs took 2,123.73 ms median (minimum 2,105.60 ms, p95 2,130.26 ms).
The preceding cached implementation took 2,620.45 ms median.
The parser stage decreased from 2,460.54 ms to 1,964.50 ms, a 20.16% reduction.
Full event construction decreased from 1,091 calls to 642 calls, a 41.16% reduction.
The parser reused 538 quality decisions and computed 553 decisions.
Public metadata, raw surviving records, parser counts, highlights, and the durable ledger remained identical in the equivalence checks.

The 10,000-record fixture remained equivalent, with no material runtime regression.
Its medians were 8,620.25 ms without pruning and 8,585.64 ms with pruning.
The small difference is not a speed claim because other local checks overlapped part of the comparison.
These local results do not establish full-source production performance.

## Bounded component concurrency

One run-scoped pool serves SiteKit, IONAS4, regional HTML calendars, requested venues, and Bonn venues.
The pool also serves their universal detail phase.
Other logical sources retain their existing behavior.
Set `NRW_EVENTS_COMPONENT_WORKERS=3` for the conservative default.
The maximum is four workers.
Values `0` and `1` restore serial component execution.
The replay manifest uses `"component_workers": 1` for the serial comparison.

Each host group keeps its original sequence.
The existing request slots and Bonn throttle remain active across all source and component threads.
Workers copy the runtime context and original deadline, cancellation signal, and processing grace period.
Each worker owns its diagnostics and parser counters.
The parent merges events, warnings, endpoints, and counters in registry order.
Nested component calls run serially to prevent pool deadlocks.
Shared detail phases retain one absolute deadline and the global repeated-link policy.
The importer persists caches only after component completion.
If a timed-out component remains active, the mid-run cache flush is deferred instead.

Ten scheduler tests cover overlap, the shared worker limit, host ordering, cancellation, deadlines, context restoration, diagnostics, and cache flush order.
The full local suite passed 1,528 tests with 89% coverage.
Ruff, mypy, and both documentation checks passed.
The existing socket and SQLite cleanup warnings remain separate from these checks.

### Controlled network-wait comparison

The replay accepts `network_latency_ms` from zero to 1,000 milliseconds.
It applies this simulated delay inside each HTTP request slot.
The report labels the delay explicitly.
This simulation does not contact public sources or establish production performance.

Five runs of the eleven-calendar fixture used 20 ms per recorded request.
Serial execution took 3,032.05 ms median (minimum 2,986.72 ms, p95 3,058.27 ms).
Three component workers took 2,012.54 ms median (minimum 1,982.73 ms, p95 2,015.90 ms).
The total reduction was 33.62%.
SiteKit decreased from 2,956 ms to 1,938 ms, a 34.44% reduction.
IONAS4 decreased from 2,532 ms to 1,344 ms, a 46.92% reduction.
All public metadata and durable artifacts remained identical.

### Mixed-source comparison

The mixed fixture combines the saved Wachtberg feed with the eleven-calendar fixture and the same simulated network delay.
Each mode used five fresh processes with isolated state.

| Mode | Minimum | Median | p95 | Peak RSS |
| --- | ---: | ---: | ---: | ---: |
| Caches and pruning disabled, serial components | 4,004.08 ms | 4,038.31 ms | 4,177.41 ms | 77.33 MiB |
| Caches and pruning enabled, serial components | 3,617.55 ms | 3,656.86 ms | 3,690.29 ms | 79.67 MiB |
| Caches and pruning enabled, three component workers | 2,652.01 ms | 2,734.60 ms | 2,780.02 ms | 80.25 MiB |

All three modes produced identical public metadata, highlights, and ledger state.
Component concurrency added a 25.22% reduction after the cache and pruning improvements.
The combined reduction was 32.29%, below the 35% roadmap target.
This fixture covers three logical sources, not all 99 production sources.
The remaining measured CPU bottleneck is Wachtberg taxonomy work.
The process-pool decision remains open until lower-risk work and production measurements establish its benefit.

### Literal prerequisite for policy regex patterns

A follow-up profile found 349,915 keyword checks in the Wachtberg source path.
The policy loader escapes each keyword before it constructs the regex.
Every match therefore requires that exact normalized literal in the input text.
The matcher now rejects absent literals before it evaluates the regex.
Custom regex patterns retain the original path unless they explicitly declare a literal prerequisite.
The existing taxonomy switch disables both the cache and this prerequisite check.

Boundary tests cover every match mode, Unicode, punctuation, compound exclusions, and custom case-insensitive patterns.
The full local suite passed 1,531 tests with 89% coverage.
The same mixed-source replay took 2,590.77 ms median across five runs (minimum 2,509.57 ms, p95 2,764.28 ms).
This is a 35.85% reduction from its 4,038.31 ms baseline.
Public metadata, highlights, and the durable ledger remained identical.
The production improvement still requires live measurement.
