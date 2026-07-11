#!/usr/bin/env bash
set -euo pipefail

PYTHONWARNINGS=error::ResourceWarning python3 -m unittest discover -s tests -t . -v
