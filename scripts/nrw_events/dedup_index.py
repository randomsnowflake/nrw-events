"""Owning implementation of dedup index; core is a compatibility facade."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping
from typing import Any

from . import duplicate_identity as _impl_duplicate_identity
from .normalization import comparison_text


def _link_identity_counts(events: list) -> dict[str, int]:
    """Count distinct event identities per URL, not dates or syndicated records."""
    identities_by_link = defaultdict(set)
    for event in events:
        link = event.get("link", "")
        if not link:
            continue
        identity = (
            _impl_duplicate_identity.normalize_title(event.get("title", "")),
            _impl_duplicate_identity.normalize_title(event.get("venue", "")),
        )
        identities_by_link[_impl_duplicate_identity._normalized_link_key(link)].add(identity)
    return {link: len(identities) for link, identities in identities_by_link.items()}


def _dedup_blocking_keys(event: Mapping[str, Any]) -> set[tuple[str, ...]]:
    """Return conservative cheap keys for plausible duplicate candidates."""
    title = _impl_duplicate_identity.normalize_title(event.get("title", ""))
    city = _impl_duplicate_identity._normalized_city(event.get("city", ""))
    prefix = title[:12]
    suffix = title[-12:]
    venue_id = str(event.get("venue_id") or "")
    category = str(event.get("category_key") or "")
    start_at = str(event.get("start_at") or "")
    words = tuple(
        word for word in comparison_text(event.get("title", "")).split()
        if len(word) >= 3 and word not in {
            "das", "der", "die", "ein", "eine", "einer", "einem", "einen",
            "und", "oder", "von", "vom", "zur", "zum",
        }
    )
    second = words[1] if len(words) > 1 else ""
    last = words[-1] if words else ""
    word_shape = (words[0], second, last) if words else ()
    keys: set[tuple[str, ...]] = set()
    for day in _impl_duplicate_identity._occurrence_date_keys(event):
        reviewed_family = _impl_duplicate_identity._reviewed_occurrence_alias_family(event)
        if reviewed_family:
            keys.add(("reviewed-occurrence", day, reviewed_family))
        if prefix:
            keys.add(("title-prefix", day, city, prefix))
            keys.add(("title-prefix-any-city", day, prefix))
        if suffix:
            keys.add(("title-suffix", day, city, suffix))
            keys.add(("title-suffix-any-city", day, suffix))
        if word_shape:
            keys.add(("title-shape", day, city, *word_shape))
            keys.add(("title-shape-any-city", day, *word_shape))
        if second and last:
            keys.add(("title-tail", day, city, second, last))
            keys.add(("title-tail-any-city", day, second, last))
        if len(words) > 1:
            keys.add(("title-last-pair", day, city, words[-2], last))
            keys.add(("title-last-pair-any-city", day, words[-2], last))
        for word in words:
            keys.add(("title-word", day, city, word))
            keys.add(("title-word-any-city", day, word))
        market_family = _impl_duplicate_identity._market_title_family(event.get("title", ""))
        if market_family:
            keys.add(("market-family", day, city, market_family))
        if venue_id and category:
            keys.add(("registered-venue", day, venue_id, category))
        if start_at:
            keys.update(("timed-word", start_at, word) for word in words)
    return keys


def _blocking_candidates(
    event: Mapping[str, Any],
    index: dict[tuple[str, ...], set[int]],
    frequencies: Counter[tuple[str, ...]],
) -> list[int]:
    keys = _dedup_blocking_keys(event)
    selective = {key for key in keys if frequencies.get(key, 0) <= 32}
    if not selective and keys:
        minimum = min(frequencies.get(key, 0) for key in keys)
        selective = {key for key in keys if frequencies.get(key, 0) == minimum}
    return sorted({candidate for key in selective for candidate in index.get(key, ())})


def _index_blocking_keys(
    event: Mapping[str, Any],
    index_value: int,
    index: dict[tuple[str, ...], set[int]],
) -> None:
    for key in _dedup_blocking_keys(event):
        index.setdefault(key, set()).add(index_value)
