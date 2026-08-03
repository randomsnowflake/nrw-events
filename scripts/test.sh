#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
export PYTHONPATH="$REPO_DIR/scripts${PYTHONPATH:+:$PYTHONPATH}"

if [[ "${NRW_EVENTS_COVERAGE:-0}" == "1" ]]; then
  test_runner=(python3 -m coverage run --source="$REPO_DIR/scripts/nrw_events" -m unittest)
else
  test_runner=(python3 -m unittest)
fi

if (( $# )); then
  PYTHONWARNINGS=error::ResourceWarning "${test_runner[@]}" -v "$@"
else
  PYTHONWARNINGS=error::ResourceWarning "${test_runner[@]}" discover -s tests -t . -v
fi

if [[ "${NRW_EVENTS_COVERAGE:-0}" == "1" ]]; then
  python3 -m coverage report --fail-under=80
fi
