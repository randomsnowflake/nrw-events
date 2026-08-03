#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
STATE_DIR="${1:-${XDG_STATE_HOME:-$HOME/.local/state}/nrw-events-canary}"
CURRENT_STATE="$STATE_DIR/current"
PREVIOUS_META="$STATE_DIR/previous-meta.json"
REPORT="$STATE_DIR/canary-report.md"

mkdir -p "$CURRENT_STATE"
set +e
XDG_STATE_HOME="$CURRENT_STATE" \
NRW_EVENTS_PREVIOUS_META_JSON="$PREVIOUS_META" \
NRW_EVENTS_SOURCE_BASELINE_MIN_COUNT=10 \
NRW_EVENTS_HTTP_RETRY_ATTEMPTS=2 \
NRW_EVENTS_SOURCE_TIMEOUT_SECONDS=120 \
python3 "$REPO_DIR/scripts/nrw-events.py" --days 28
importer_exit=$?

python3 "$REPO_DIR/scripts/check_canary.py" \
  "$CURRENT_STATE/nrw-events/nrw-events-latest-meta.json" \
  --report "$REPORT" \
  --importer-exit "$importer_exit"
canary_exit=$?
set -e

if [[ "$importer_exit" == "0" && "$canary_exit" == "0" ]]; then
  cp "$CURRENT_STATE/nrw-events/nrw-events-latest-meta.json" "$PREVIOUS_META"
fi

printf 'Canary report: %s\n' "$REPORT"
exit "$canary_exit"
