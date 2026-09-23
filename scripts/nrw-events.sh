#!/bin/bash
# NRW Event Discovery — Weekend Planner
# Usage: nrw-events.sh [days_ahead|verb] [flags]
#   days_ahead: number of days to look ahead (default: 3 for a Fri–Sun weekend)
#
# Examples:
#   bash nrw-events.sh        # Next 3 days (weekend)
#   bash nrw-events.sh 7      # Full week ahead
#   bash nrw-events.sh 1      # Just today
#
# Event discovery uses deterministic public sources.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
export PYTHONPATH="$SCRIPT_DIR${PYTHONPATH:+:$PYTHONPATH}"

# The Python runner loads .env files itself while preserving real environment
# variables. Keep the shell wrapper thin so process settings cannot be
# accidentally overwritten by blank values in a local .env file.
python3 "$SCRIPT_DIR/nrw-events.py" "$@"
