#!/usr/bin/env python3
"""Explicit, synthetic live smoke test; does not import or change events."""
import argparse

from nrw_events.config import load_env_file
from nrw_events.decisions import DecisionError, OpenRouterDecisionClient

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--live", action="store_true", required=True)
parser.parse_args()
load_env_file()
try:
    result = OpenRouterDecisionClient().evaluate(
        state={"title": "Jazzkonzert", "description": "Ein Trio spielt Jazz im Konzertsaal."},
        questions={"music": {"type": "noul", "instructions": "Ist dies eine Musikveranstaltung?"}},
    )
    print(f"Jev OK: model={result['model']}, music={result['answers']['music']['noul']}")
except DecisionError as error:
    raise SystemExit(str(error)) from None
