# Importer agent instructions

- Inspect Git status/diff and preserve unrelated changes. This is a separate repository from the website consumer. Never update its gitlink/ref until the importer commit exists.
- Start with `python3 scripts/agent_tools.py context <topic>`; no topic lists choices. `context source SOURCE_ID` reads the registry and discovers test references. Search the returned owning files before broader searches.
- Read `docs/ARCHITECTURE.md` for ownership; load `docs/reference.md` sections only when needed. Generated inventories live in `docs/sources.md` and `docs/modules.md`; do not read them for a single-source task.
- `core.py`/`common.py` are compatibility facades. Change owning modules; sources return raw records, domain policy stays source-independent, and publication belongs to orchestration.
- Prefer declarative registry entries for standard iCal/JSON-LD/HTML sources. Add a proprietary parser only when necessary. Preserve source-backed identity/provenance; never invent dates, prices, venues or descriptions.
- Use exact offline fixtures and focused tests while editing: `bash scripts/test.sh --agent tests.test_report`. Run `bash scripts/test.sh --agent` before publication; website release `prepare`/`resume` owns this final suite when deploying both repos. The full gate includes Ruff, Mypy, docs and coverage; install `requirements-dev.lock` into `.venv` first.
- Agent mode prints counts/duration and a full log path; failures retain full diagnostics. Logs and summaries live in ignored `.cache/agent-tests`. Ordinary `bash scripts/test.sh` remains supported.
- Regenerate inventories with `python3 scripts/generate_docs.py` after registry/module changes. Tests check drift and task-map paths. Use offline replay in `docs/performance.md`; avoid all-source live fetches while editing adapters.
- `python3 scripts/agent_tools.py inspect ID_OR_SOURCE_URL --snapshot PATH` extracts records and available provenance. Add `--stage PATH` for recorded intermediate snapshots; unavailable stages are not reconstructed or fetched.
- GitHub Actions remain disabled. Push the importer before its consumer. For consumer releases follow the website's `docs/release-workflow.md`; never repeat a production import after a prepared package activates.
