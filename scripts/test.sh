#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
export PYTHONPATH="$REPO_DIR/scripts${PYTHONPATH:+:$PYTHONPATH}"

if (( $# )); then
  PYTHONWARNINGS=error::ResourceWarning python3 -m unittest -v "$@"
else
  PYTHONWARNINGS=error::ResourceWarning python3 -m unittest discover -s tests -t . -v
fi
