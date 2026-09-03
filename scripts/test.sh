#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
export PYTHONPATH="$REPO_DIR/scripts${PYTHONPATH:+:$PYTHONPATH}"

python_bin="python3"
if [[ -x "$REPO_DIR/.venv/bin/python" ]]; then
  python_bin="$REPO_DIR/.venv/bin/python"
fi

if [[ "${NRW_EVENTS_COVERAGE:-0}" == "1" ]]; then
  test_runner=("$python_bin" -m coverage run --source="$REPO_DIR/scripts/nrw_events" -m unittest)
else
  test_runner=("$python_bin" -m unittest)
fi

if (( $# )); then
  PYTHONWARNINGS=error::ResourceWarning "${test_runner[@]}" -v "$@"
else
  PYTHONWARNINGS=error::ResourceWarning "${test_runner[@]}" discover -s tests -t . -v
fi

if [[ "${NRW_EVENTS_COVERAGE:-0}" == "1" ]]; then
  "$python_bin" -m coverage report --fail-under=80
fi
