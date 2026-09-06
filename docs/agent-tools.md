# Offline agent tools

Start with `python3 scripts/agent_tools.py context` for topics. Add `source ID` to
read the exact registry entry, parser paths, fixture manifest entries and test
references. Tests discovered by text references are candidates, not a complete
coverage map; inline source fixtures remain in those test files. Unknown IDs fail.
`docs/task-map.json` supplies domain-level pointers; the offline suite validates
its paths. Generated full inventories live separately in `sources.md`/`modules.md`.

```sh
python3 scripts/agent_tools.py context source bonn-de-events
python3 scripts/agent_tools.py inspect ID_OR_URL --snapshot /path/feed.json
python3 scripts/agent_tools.py inspect ID --snapshot /path/final.json --stage /path/raw.json --full
bash scripts/test.sh --agent tests.test_report
bash scripts/test.sh --agent
```

Inspection matches exact event IDs, previous IDs and source URLs. Public detail
URLs may be used when they contain the event ID. Website evergreen slugs require
the consumer's `inspect:event` command. Output defaults to selected provenance and
calendar fields, at most 10 matches per stage; `--limit` accepts 1–100. `--full`
includes complete matching records. It never dumps every event, fetches sources,
loads credentials or writes snapshots. No matches exit 1; invalid input exits 2.
Only supplied stages can be inspected; missing provenance is not invented.

`--agent` wraps the canonical shell test gate and keeps all its tests, warnings,
coverage rules and exit codes. Full logs and `summary.json` are retained in a
unique `.cache/agent-tests/` directory. Summaries include count, duration, exit
code and log size; failure output is limited to the last 60 lines. Read the full
log when needed. Plain mode remains available. The runner uses the repo virtual
environment, works from any current directory and prints a heartbeat every 30s.

Measure comparable tasks using session input tokens/tool calls, elapsed time and
test summaries. Log bytes are output-volume evidence, not a token or cost metric.
Do not replace the full publication gate with a successful focused run.
