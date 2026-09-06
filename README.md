# NRW Events

Standard-library Python importer for Bonn and surrounding NRW events. It publishes
validated event snapshots with source health, provenance, stable occurrence IDs,
series and highlights. The website consumer is `veranstaltungen-bonn.de`.

## Develop

```sh
bash scripts/test.sh --agent                        # full offline suite, concise output
bash scripts/test.sh --agent tests.test_report      # focused regression tests
python3 scripts/agent_tools.py context source bonn-de-events
python3 scripts/agent_tools.py inspect EVENT_ID --snapshot /path/to/snapshot.json
```

The runner uses `.venv/bin/python` when available. `NRW_EVENTS_COVERAGE=1` also
checks coverage. GitHub Actions remain disabled. Focused tests are for editing;
run the full offline suite before publication (consumer release preparation owns
that final run). See [agent instructions](AGENTS.md) and [agent tools](docs/agent-tools.md).

## References (load only what the task needs)

- [Architecture and ownership](docs/ARCHITECTURE.md)
- [CLI usage, configuration, source policy and output contracts](docs/reference.md)
- [Event discovery workflow](docs/discovery.md)
- [Generated source inventory](docs/sources.md)
- [Generated module inventory](docs/modules.md)
- [Offline performance replay](docs/performance.md)

Source inventory is generated from `scripts/nrw_events/sources/registry.json`.
Run `python3 scripts/generate_docs.py` after changing the registry or modules;
`--check` runs in the offline tests. Runtime requires Python 3.10–3.14; optional
development dependencies are installed with `pip install -e '.[dev]'`.

Licensed under [MIT](LICENSE). Source links remain authoritative; imported details
can change and should not be fabricated when unavailable.
