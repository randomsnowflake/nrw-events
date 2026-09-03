# Importer performance

## Status

Issue #356 adds measurement tools before changes to parsing or concurrency.
The current fixture proves repeatability, not production speed.
The complete multi-source baseline, composite fixture, and live canaries are still required.
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

- Add per-source stage attribution, separate parser/network spans, and explicit filter and early-dedup measurements.
- Add a large generated recurrence fixture and a real composite-adapter replay with detail-cache hits and misses.
- Add an operator-supplied cache input without access to the live writable cache.
- Measure the complete multi-source path and establish its critical path before concurrency changes.
- Repeat the overhead measurement after the final instrumentation changes.
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
