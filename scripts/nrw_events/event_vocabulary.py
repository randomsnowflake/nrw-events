"""Shared editorial vocabulary used across filtering and classification."""

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class TermPolicy:
    """Declare each independent use of a vocabulary term explicitly."""

    term: str
    classify_as_market: bool = False
    drop_as_routine_market: bool = False


_DATA_DIR = Path(__file__).parent


def _load_market_terms() -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Project both data-owned policies into one auditable vocabulary."""
    categories = json.loads((_DATA_DIR / "categories.json").read_text(encoding="utf-8"))
    market_rule = next(rule for rule in categories["rules"] if rule["key"] == "market")
    classified = tuple(keyword["value"] for keyword in market_rule["keywords"])
    junk_rules = json.loads((_DATA_DIR / "junk_rules_data.json").read_text(encoding="utf-8"))
    return classified, tuple(junk_rules["routine_market"])


_CLASSIFIED_MARKET_TERMS, _ROUTINE_MARKET_TERMS = _load_market_terms()

MARKET_TERM_POLICIES: tuple[TermPolicy, ...] = tuple(
    TermPolicy(
        term,
        classify_as_market=term in _CLASSIFIED_MARKET_TERMS,
        drop_as_routine_market=term in _ROUTINE_MARKET_TERMS,
    )
    for term in dict.fromkeys((*_CLASSIFIED_MARKET_TERMS, *_ROUTINE_MARKET_TERMS))
)

MARKET_CLASSIFICATION_TERMS = tuple(
    policy.term for policy in MARKET_TERM_POLICIES if policy.classify_as_market
)
ROUTINE_MARKET_DROP_TERMS = frozenset(
    policy.term for policy in MARKET_TERM_POLICIES if policy.drop_as_routine_market
)
