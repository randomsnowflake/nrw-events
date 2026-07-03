#!/bin/bash
# NRW Event Discovery — Weekend Planner
# Usage: nrw-events.sh [days_ahead]
#   days_ahead: number of days to look ahead (default: 3 for a Fri–Sun weekend)
#
# Examples:
#   bash nrw-events.sh        # Next 3 days (weekend)
#   bash nrw-events.sh 7      # Full week ahead
#   bash nrw-events.sh 1      # Just today
#
# Optional API keys (set as real env vars or in a .env file at the repo root):
#   EXA_API_KEY  — enables the Exa neural web-search fallback
#   XAI_API_KEY  — enables the optional Grok agentic search
#                  (also requires NRW_EVENTS_ENABLE_GROK=1)
# The script runs without any keys; the deterministic scrapers do the heavy lifting.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DAYS="${1:-3}"

# The Python runner loads .env files itself while preserving real environment
# variables. Keep the shell wrapper thin so `EXA_API_KEY=... bash ...` cannot be
# accidentally overwritten by a blank key in a local .env file.
python3 "$SCRIPT_DIR/nrw-events.py" "$DAYS"
