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
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DAYS="${1:-3}"

# Optionally load a .env file. Real environment variables take precedence.
# Lookup order: NRW_EVENTS_ENV_FILE, repo-root .env, current-directory .env.
for envfile in "${NRW_EVENTS_ENV_FILE:-}" "$REPO_ROOT/.env" "$PWD/.env"; do
    if [ -n "$envfile" ] && [ -f "$envfile" ]; then
        set -a
        # shellcheck disable=SC1090
        source "$envfile"
        set +a
        break
    fi
done

python3 "$SCRIPT_DIR/nrw-events.py" "$DAYS"
