#!/usr/bin/env python3
"""
NRW Event Discovery — Weekend Planner (entrypoint).

Thin launcher. All logic lives in the ``nrw_events`` package next to this file:
  nrw_events/config.py    — geography, category weights, venue coords, source lists
  nrw_events/common.py    — HTTP, HTML/JSON-LD/iCal parsing, dates, scoring, make_event
  nrw_events/report.py    — dedup + Markdown rendering
  nrw_events/sources/     — one module per source (each a fetch() -> list[dict])
  nrw_events/runner.py    — orchestration (fan-out, filter, dedup, output)

Usage:
  python3 scripts/nrw-events.py [days_ahead]   # default 3 (a Fri–Sun weekend)
"""

from nrw_events.runner import main

if __name__ == "__main__":
    raise SystemExit(main())
