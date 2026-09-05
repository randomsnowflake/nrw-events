"""Owning implementation of deduplication; core is a compatibility facade."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import replace
from typing import Any

from . import common
from . import dedup_index as _impl_dedup_index
from . import dedup_merge as _impl_dedup_merge
from . import dedup_rules as _impl_dedup_rules
from . import duplicate_identity as _impl_duplicate_identity
from .models import CanonicalEvent
from .normalization import comparison_text


def _series_place_key(event: Any) -> str:
    venue_id = str(event.get("venue_id") or "").strip()
    if venue_id:
        return f"venue:{venue_id}"
    venue = comparison_text(str(event.get("venue") or ""))
    city = comparison_text(str(event.get("city") or ""))
    return f"label:{city}\n{venue}" if venue else ""


def suppress_redundant_series_umbrellas(
    events: list[CanonicalEvent],
) -> list[CanonicalEvent]:
    """Drop a covered lower-authority umbrella when first-party programme rows exist.

    This is deliberately narrower than duplicate matching: one generic calendar
    row can represent several concrete programme items, so it cannot be merged
    into an arbitrary child.  Only a single-day umbrella whose exact title is a
    higher-authority event's explicit ``series_title`` is removed.  Uncovered
    dates, different venues, peers, and multi-day fallbacks remain publishable.
    """
    programme_rows = defaultdict(list)
    for event in events:
        series_title = comparison_text(str(event.get("series_title") or ""))
        title = comparison_text(str(event.get("title") or ""))
        place = _series_place_key(event)
        authority = _impl_dedup_rules.source_authority(str(event.get("source") or ""))
        if (
            series_title
            and title != series_title
            and place
            and event.get("status") == "scheduled"
            and authority == 3
        ):
            start_date = str(event.get("start_date") or "")
            end_date = str(event.get("end_date") or start_date)
            if start_date:
                programme_rows[(series_title, place)].append((
                    start_date,
                    end_date,
                    authority,
                ))

    result = []
    for event in events:
        title = comparison_text(str(event.get("title") or ""))
        start_date = str(event.get("start_date") or "")
        end_date = str(event.get("end_date") or start_date)
        place = _series_place_key(event)
        authority = _impl_dedup_rules.source_authority(str(event.get("source") or ""))
        covered = bool(
            title
            and place
            and start_date
            and start_date == end_date
            and any(
                programme_start <= start_date <= programme_end
                and programme_authority > authority
                for programme_start, programme_end, programme_authority
                in programme_rows.get((title, place), ())
            )
        )
        if not covered:
            result.append(event)
    return result


def deduplicate(
    events: list[CanonicalEvent],
    *,
    cancellations: list[dict] | None = None,
) -> list[CanonicalEvent]:
    """Collapse duplicates and apply authoritative cancellation tombstones."""
    link_identity_counts = _impl_dedup_index._link_identity_counts(events)

    def merge_preferred(current: CanonicalEvent, candidate: CanonicalEvent) -> CanonicalEvent:
        reviewed_owner_pair = (
            _impl_duplicate_identity._reviewed_occurrence_alias_matches(current, candidate)
            and {current.get("source"), candidate.get("source")}
            == {"Bonn district festivals", "Bonn.de Events"}
        )
        if reviewed_owner_pair:
            winner, duplicate = (
                (current, candidate)
                if current.get("source") == "Bonn.de Events"
                else (candidate, current)
            )
        else:
            current_rank = (
                _impl_dedup_rules.source_authority(current.get("source", "")),
                current["score"],
                _impl_duplicate_identity._duration_days(current),
            )
            candidate_rank = (
                _impl_dedup_rules.source_authority(candidate.get("source", "")),
                candidate["score"],
                _impl_duplicate_identity._duration_days(candidate),
            )
            current_tiebreaker = (
                str(current.get("source_id") or ""),
                str(current.get("link") or ""),
            )
            candidate_tiebreaker = (
                str(candidate.get("source_id") or ""),
                str(candidate.get("link") or ""),
            )
            candidate_wins = candidate_rank > current_rank or (
                candidate_rank == current_rank and candidate_tiebreaker < current_tiebreaker
            )
            winner, duplicate = (
                (candidate, current)
                if candidate_wins else (current, candidate)
            )
        protect_authoritative_schedule = (
            _impl_duplicate_identity._venue_qualified_aggregator_title_matches(winner, duplicate)
            and _impl_dedup_rules.source_authority(winner.get("source", ""))
            > _impl_dedup_rules.source_authority(duplicate.get("source", ""))
        )
        return _impl_dedup_merge._merge_duplicate_metadata(
            winner,
            duplicate,
            link_identity_counts=link_identity_counts,
            adopt_schedule=not protect_authoritative_schedule,
        )

    authoritative_cancellations = [
        event
        for event in (cancellations or [])
        if event.get("status") in {"cancelled", "postponed"}
        and _impl_dedup_rules.source_authority(event.get("source", "")) >= 2
    ]
    result: list = []
    blocking_frequencies = Counter(
        key
        for event in events
        if event.get("status") not in {"cancelled", "postponed"}
        for key in _impl_dedup_index._dedup_blocking_keys(event)
    )
    candidate_index: dict[tuple[str, ...], set[int]] = {}
    for ev in events:
        if ev.get("status") in {"cancelled", "postponed"}:
            continue
        match_index = next(
            (
                index for index in _impl_dedup_index._blocking_candidates(ev, candidate_index, blocking_frequencies)
                if _impl_duplicate_identity.events_are_duplicates(result[index], ev)
            ),
            None,
        )
        if match_index is None:
            result.append(ev)
            _impl_dedup_index._index_blocking_keys(ev, len(result) - 1, candidate_index)
            continue

        current = result[match_index]
        result[match_index] = merge_preferred(current, ev)
        _impl_dedup_index._index_blocking_keys(result[match_index], match_index, candidate_index)

    # Replace the scheduled record with its authoritative schedule change. By
    # keeping the scheduled record's identity fields, the public event ID stays
    # stable when an occurrence changes from scheduled to cancelled.
    for cancellation in authoritative_cancellations:
        match_index = next(
            (
                index for index, scheduled in enumerate(result)
                if _impl_dedup_rules.source_authority(cancellation.get("source", ""))
                >= _impl_dedup_rules.source_authority(scheduled.get("source", ""))
                and _impl_duplicate_identity.events_are_duplicates(cancellation, scheduled)
            ),
            None,
        )
        updates = {
            "status": cancellation.get("status", "cancelled"),
            "cancellation_source": cancellation.get("source", ""),
            "replacement_start_date": cancellation.get("replacement_start_date", ""),
            "score": 0.0,
        }
        if match_index is None:
            # A tombstone only means something for an occurrence a visitor could
            # still be looking at. ``cancelled_events`` is filled inside
            # ``make_event``, before the report window is applied, so a source
            # that keeps a months-old "verschoben" entry in its calendar would
            # otherwise publish that past date as a standalone event — listed
            # everywhere, with no detail page, because the site builds pages for
            # current events only.
            if not common.event_in_window(cancellation):
                continue
            if isinstance(cancellation, CanonicalEvent):
                result.append(replace(cancellation, **updates))
            else:
                result.append({**cancellation, **updates})
        elif isinstance(result[match_index], CanonicalEvent):
            result[match_index] = replace(result[match_index], **updates)
        else:
            result[match_index] = {**result[match_index], **updates}

    # Metadata enrichment can make a winner comparable to an earlier result
    # that neither of its inputs matched on its own. Collapse those transitive
    # pairs until the exported set is closed under ``events_are_duplicates``.
    transitive_index: dict[tuple[str, ...], set[int]] = {}
    if result:
        _impl_dedup_index._index_blocking_keys(result[0], 0, transitive_index)
    right_index = 1
    while right_index < len(result):
        left_index = next(
            (
                index for index in _impl_dedup_index._blocking_candidates(
                    result[right_index], transitive_index, blocking_frequencies,
                )
                if _impl_duplicate_identity.events_are_duplicates(result[index], result[right_index])
            ),
            None,
        )
        if left_index is None:
            _impl_dedup_index._index_blocking_keys(result[right_index], right_index, transitive_index)
            right_index += 1
            continue
        result[left_index] = merge_preferred(result[left_index], result[right_index])
        del result[right_index]
        # Enrichment may make the merged winner comparable to an earlier row.
        right_index = max(1, left_index)
        transitive_index = {}
        for index in range(right_index):
            _impl_dedup_index._index_blocking_keys(result[index], index, transitive_index)

    # A recurring series is not a duplicate: each date is a separately usable
    # occurrence. Cross-source authority is therefore resolved only inside the
    # same overlapping date interval by the loop above.
    classified: list[Any] = []
    for event in result:
        link = event.get("link", "")
        link_kind = ""
        if link:
            identity_count = link_identity_counts.get(_impl_duplicate_identity._normalized_link_key(link), 0)
            link_kind = (
                "overview"
                if identity_count >= _impl_dedup_rules._REUSED_OVERVIEW_LINK_THRESHOLD
                else "detail"
            )
        if isinstance(event, CanonicalEvent):
            classified.append(replace(event, link_kind=link_kind))
        else:
            classified.append({**event, "link_kind": link_kind})
    return classified
