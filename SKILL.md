---
name: nrw-events
description: "Use when finding current events or activities in Bonn and nearby NRW. Not for other-region trips or single-venue research."
tags:
  - bonn
  - nrw
  - events
  - veranstaltungen
  - weekend
  - concerts
  - exhibitions
  - markets
  - open-source
  - python
metadata:
  hermes:
    tags: [bonn, nrw, events, veranstaltungen, weekend, concerts, exhibitions, markets, open-source, python]
---

# NRW Events discovery

Use the importer to discover current events in Bonn and the surrounding region.
Read [the discovery workflow](docs/discovery.md) before running an event search;
it contains the commands, coverage, source policy and presentation requirements.
For a requested shortlist, respect the user's requested scope and count.

Load [CLI/configuration reference](docs/reference.md) only for configuration or
output-contract questions. The complete [source inventory](docs/sources.md) and
[module inventory](docs/modules.md) are separate generated references.

For code changes start with [AGENTS.md](AGENTS.md), not the discovery workflow.
Use `python3 scripts/agent_tools.py context source SOURCE_ID` for focused ownership,
tests and fixtures, and `bash scripts/test.sh --agent` for concise offline tests.
