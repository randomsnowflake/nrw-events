#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
export PYTHONPATH="$REPO_DIR/scripts${PYTHONPATH:+:$PYTHONPATH}"
# Unit tests own their network fixtures. Disable the persistent production GET
# cache unless an individual cache test explicitly enables it.
export NRW_EVENTS_HTTP_CACHE_TTL_HOURS=0

if (( $# )); then
  PYTHONWARNINGS=error::ResourceWarning python3 -m unittest -v "$@"
else
  PYTHONWARNINGS=error::ResourceWarning python3 -m unittest discover -s tests -t . -v
fi
