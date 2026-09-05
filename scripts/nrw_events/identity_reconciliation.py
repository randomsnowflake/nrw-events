"""Owning implementation of identity reconciliation; core is a compatibility facade."""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import replace

from . import (
    performance,
    report,
)
from . import retention_policy as _impl_retention_policy
from .identity import event_id
from .models import CanonicalEvent, normalize_source_id
from .normalization import comparison_text


def _cross_run_match_score(current: CanonicalEvent | dict, prior: dict) -> int:
    """Score corroborating fields for a same-title/date cross-run match."""
    def start_time(value: object, start_at: object) -> str:
        if match := re.match(r"\s*(\d{1,2}):(\d{2})", str(value or "")):
            return f"{int(match.group(1)):02d}:{match.group(2)}"
        iso_value = str(start_at or "")
        return iso_value[11:16] if len(iso_value) >= 16 else ""

    score = 0
    def source_links(event: CanonicalEvent | dict) -> set[str]:
        links = {
            report._normalized_link_key(str(value))
            for value in (event.get("source_links") or [])
            if str(value or "").strip()
        }
        if event.get("link_kind") == "detail" and event.get("link"):
            links.add(report._normalized_link_key(str(event.get("link"))))
        return links

    if source_links(current) & source_links(prior):
        score += 8
    if current.get("source_id") and current.get("source_id") == _impl_retention_policy._event_source_id(prior):
        score += 4
    current_time = start_time(current.get("time"), current.get("start_at"))
    prior_time = start_time(prior.get("time"), prior.get("start_at"))
    if current_time and prior_time and current_time != prior_time:
        return -1
    if current_time and prior_time and current_time == prior_time:
        score += 3
    if current.get("venue_id") and current.get("venue_id") == str(prior.get("venue_id") or ""):
        score += 3
    elif (
        current.get("venue")
        and prior.get("venue")
        and comparison_text(str(current.get("venue"))) == comparison_text(str(prior["venue"]))
    ):
        score += 2
    if (
        current.get("city")
        and prior.get("city")
        and comparison_text(str(current.get("city"))) == comparison_text(str(prior["city"]))
    ):
        score += 2
    return score


def _uniquely_disambiguates_occurrence(
    current: CanonicalEvent | dict,
    prior: dict,
    current_group: Sequence[CanonicalEvent | dict],
    prior_group: Sequence[dict],
) -> bool:
    """Require a pair-specific signal when title/date groups are ambiguous."""
    def start_time(event: CanonicalEvent | dict) -> str:
        if match := re.match(r"\s*(\d{1,2}):(\d{2})", str(event.get("time") or "")):
            return f"{int(match.group(1)):02d}:{match.group(2)}"
        start_at = str(event.get("start_at") or "")
        return start_at[11:16] if len(start_at) >= 16 else ""

    def normalized_link(event: CanonicalEvent | dict) -> str:
        return str(event.get("link") or "").rstrip("/")

    def normalized_source_links(event: CanonicalEvent | dict) -> set[str]:
        links = {
            report._normalized_link_key(str(value))
            for value in (event.get("source_links") or [])
            if str(value or "").strip()
        }
        if event.get("link_kind") == "detail" and event.get("link"):
            links.add(report._normalized_link_key(str(event.get("link"))))
        return links

    def normalized_venue(event: CanonicalEvent | dict) -> str:
        return str(event.get("venue_id") or "") or comparison_text(str(event.get("venue") or ""))

    for getter in (start_time, normalized_link, normalized_venue):
        value = getter(current)
        if not value or value != getter(prior):
            continue
        if (
            sum(getter(event) == value for event in current_group) == 1
            and sum(getter(event) == value for event in prior_group) == 1
        ):
            return True
    shared_source_links = normalized_source_links(current) & normalized_source_links(prior)
    return bool(shared_source_links and any(sum(link in normalized_source_links(event) for event in prior_group) == 1 for link in shared_source_links))


def _uniquely_matches_renamed_occurrence(
    current: CanonicalEvent | dict, prior: dict,
) -> bool:
    """Match a conservative upstream title expansion to its published record."""
    current_title = comparison_text(str(current.get("title") or ""))
    prior_title = comparison_text(str(prior.get("title") or ""))
    current_display_title = " ".join(str(current.get("title") or "").casefold().split())
    prior_display_title = " ".join(str(prior.get("title") or "").casefold().split())
    explicit_short_expansion = bool(
        len(prior_display_title.split()) == 1
        and re.match(
            rf"^{re.escape(prior_display_title)}\s*[-–—:]\s+\S",
            current_display_title,
        )
    )
    if (
        not explicit_short_expansion
        and (
            min(len(current_title.replace(" ", "")), len(prior_title.replace(" ", ""))) < 12
            or (current_title not in prior_title and prior_title not in current_title)
        )
    ):
        return False
    current_venue = comparison_text(str(current.get("venue") or ""))
    prior_venue = comparison_text(str(prior.get("venue") or ""))
    return bool(
        min(len(current_venue.replace(" ", "")), len(prior_venue.replace(" ", ""))) >= 8
        and (current_venue in prior_venue or prior_venue in current_venue)
        and normalize_source_id(current.get("source_id") or current.get("source"))
        == _impl_retention_policy._event_source_id(prior)
    )


@performance.measured("identity.reconcile")
def _reconcile_published_ids(
    events: Sequence[CanonicalEvent | dict], previous: dict,
) -> list[CanonicalEvent | dict]:
    """Carry a published URL across safe metadata and source-winner changes.

    Identity fields can improve between healthy refreshes, so retained-source
    handling alone is insufficient. Candidate pairs must share title and start
    date, then at least one strong or two independent occurrence signals. A
    greedy one-to-one assignment avoids giving one historical URL to multiple
    same-day performances.
    """
    prior_groups: dict[tuple[str, str], list[dict]] = {}
    prior_date_source_groups: dict[tuple[str, str], list[dict]] = {}
    for prior in previous.get("events") or []:
        if not isinstance(prior, dict) or not str(prior.get("event_id") or "").strip():
            continue
        key = (
            comparison_text(str(prior.get("title") or "")),
            str(prior.get("start_date") or prior.get("date") or ""),
        )
        prior_groups.setdefault(key, []).append(prior)
        prior_date_source_groups.setdefault((key[1], _impl_retention_policy._event_source_id(prior)), []).append(prior)

    reconciled = list(events)
    current_groups: dict[tuple[str, str], list[CanonicalEvent | dict]] = {}
    for current in reconciled:
        key = (
            comparison_text(str(current.get("title") or "")),
            str(current.get("start_date") or current.get("date") or ""),
        )
        current_groups.setdefault(key, []).append(current)
    candidate_pairs: list[tuple[int, int, dict]] = []
    fallback_pairs: list[tuple[int, int, dict]] = []
    for index, current in enumerate(reconciled):
        key = (
            comparison_text(str(current.get("title") or "")),
            str(current.get("start_date") or current.get("date") or ""),
        )
        current_group = current_groups[key]
        prior_group = prior_groups.get(key, [])
        for prior in prior_group:
            score = _cross_run_match_score(current, prior)
            unambiguous_group = len(current_group) == len(prior_group) == 1
            if score >= 4 and (
                unambiguous_group
                or _uniquely_disambiguates_occurrence(current, prior, current_group, prior_group)
            ):
                candidate_pairs.append((score, index, prior))
        if prior_group:
            continue
        date_source_key = (
            key[1],
            normalize_source_id(current.get("source_id") or current.get("source")),
        )
        for prior in prior_date_source_groups.get(date_source_key, []):
            score = _cross_run_match_score(current, prior)
            if score >= 4 and _uniquely_matches_renamed_occurrence(current, prior):
                fallback_pairs.append((score, index, prior))

    fallback_current_counts = Counter(index for _score, index, _prior in fallback_pairs)
    fallback_prior_counts = Counter(id(prior) for _score, _index, prior in fallback_pairs)
    candidate_pairs.extend(
        pair for pair in fallback_pairs
        if fallback_current_counts[pair[1]] == 1 and fallback_prior_counts[id(pair[2])] == 1
    )

    used_current: set[int] = set()
    used_prior_ids: set[str] = set()
    candidates_by_current: dict[int, list[tuple[int, dict]]] = {}
    for score, index, prior in candidate_pairs:
        candidates_by_current.setdefault(index, []).append((score, prior))
    for index, candidates in sorted(candidates_by_current.items()):
        current = reconciled[index]
        natural_id = event_id(current)
        candidates.sort(
            key=lambda item: (item[0], str(item[1].get("event_id")) == natural_id),
            reverse=True,
        )
        _score, prior = candidates[0]
        prior_id = str(prior["event_id"])
        if index in used_current or prior_id in used_prior_ids:
            continue
        inherited_ids = list(dict.fromkeys([
            *(current.get("previous_event_ids") or []),
            *(
                str(candidate.get("event_id") or "").strip()
                for _candidate_score, candidate in candidates
                if str(candidate.get("event_id") or "").strip() != prior_id
            ),
            *(prior.get("previous_event_ids") or []),
        ]))[:20]
        updates = {
            "preserved_event_id": prior_id,
            "previous_event_ids": inherited_ids,
        }
        reconciled[index] = (
            {**current, **updates}
            if isinstance(current, dict)
            else replace(current, preserved_event_id=prior_id, previous_event_ids=inherited_ids)
        )
        used_current.add(index)
        used_prior_ids.add(prior_id)
    return reconciled
