"""Owning implementation of retention policy; core is a compatibility facade."""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

from . import (
    ai_enrichment,
    common,
    config,
    detail_enrichment,
    performance,
    report,
)
from .health import (
    SourceResult,
    SourceStatus,
    diagnostic_warning,
)
from .identity import content_hash, event_id
from .models import MAX_DISCOVERY_PROVENANCE_SOURCES, CanonicalEvent, normalize_source_id
from .normalization import comparison_text
from .runtime import RunContext
from .validation import EventValidationError, validate_event

_DISCOVERY_ONLY_SOURCE_IDS = frozenset({"radio-bonn-rhein-sieg"})


_BONN_FALLBACK_SOURCE_IDS = frozenset({"bonn-de-events", "bonn-de-sports"})


_RADIO_RUNNER_SOURCE = "Radio Bonn/Rhein-Sieg"


_LEGACY_DATED_RANGE_TITLE = re.compile(
    r"^\s*\d{1,2}\.\d{1,2}\.20\d{2}\s*[-–]\s*"
    r"\d{1,2}\.\d{1,2}\.20\d{2}\s+(.+?)"
    r"(?:\s*[-–]\s*täglich\s+ab\s+Mittagszeit)?\s*$",
    re.IGNORECASE,
)


def _previous_snapshot(path: str, warnings: list[dict[str, str]] | None = None) -> dict:
    metadata_path = Path(path).expanduser()
    try:
        with metadata_path.open(encoding="utf-8") as handle:
            payload = json.load(handle)
        if not isinstance(payload, dict):
            return {}
        if "events" not in payload and payload.get("events_path"):
            events_path = Path(str(payload["events_path"])).expanduser()
            if not events_path.is_absolute():
                events_path = metadata_path.parent / events_path
            try:
                events = json.loads(events_path.read_text(encoding="utf-8"))
                if isinstance(events, list):
                    payload["events"] = events
            except (OSError, ValueError, TypeError):
                pass
        return payload
    except (OSError, ValueError, AttributeError) as exc:
        if metadata_path.exists() and warnings is not None:
            warnings.append(
                diagnostic_warning(
                    "previous-snapshot",
                    type(exc).__name__,
                    f"previous snapshot could not be read: {type(exc).__name__}",
                    source_id="previous-snapshot",
                )
            )
        return {}


def _event_source_id(event: CanonicalEvent | dict) -> str:
    explicit = normalize_source_id(event.get("source_id"))
    if explicit:
        return explicit
    source = normalize_source_id(event.get("source"))
    # Migration path for snapshots written before grouped adapters emitted
    # child IDs. These adapters carry the stable municipality in ``city``.
    if source == "ionas4-regional" and event.get("city"):
        return normalize_source_id(f"ionas4-{event['city']}")
    if source == "sitekit-regional" and event.get("city"):
        city = str(event["city"]).casefold()
        city = city.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")
        return normalize_source_id(f"sitekit-{city}")
    return source


def _retained_event_counts_by_source(
    events: Sequence[CanonicalEvent | dict],
    published_event_ids: set[str],
) -> dict[str, int]:
    """Count retained rows without assuming snapshots still hold model objects."""
    counts: dict[str, int] = {}
    for event in events:
        if event_id(event) not in published_event_ids:
            continue
        source_id = _event_source_id(event)
        counts[source_id] = counts.get(source_id, 0) + 1
    return counts


def _is_discovery_only_event(event: dict) -> bool:
    """Reject tagged discovery records and legacy rows from discovery-only sources."""
    return (
        event.get("source_role") == "discovery"
        or _event_source_id(event) in _DISCOVERY_ONLY_SOURCE_IDS
    )


def _retention_labels(
    results: dict[str, SourceResult],
    previous: dict,
    unpublished_fallback_source_ids: frozenset[str] = frozenset(),
) -> set[str]:
    """Return stable logical source IDs whose fresh data cannot be trusted."""
    previous_results = previous.get("source_results") or {}
    previous_event_ids = {
        _event_source_id(event)
        for event in previous.get("events") or []
        if isinstance(event, dict) and event.get("source")
    }
    previous_retained = [
        item for item in previous.get("retained_sources") or []
        if isinstance(item, dict)
    ]
    previous_retained_ids = {
        normalize_source_id(item.get("source_id") or item.get("source"))
        for item in previous_retained
        if item.get("source_id") or item.get("source")
    }
    labels: set[str] = set()
    for runner_source, result in results.items():
        prior = previous_results.get(runner_source) or {}
        prior_labels = {
            normalize_source_id(label)
            for label in (
                prior.get("event_source_ids")
                or prior.get("event_sources")
                or []
            )
            if str(label).strip()
        }
        prior_labels.update(
            normalize_source_id(item.get("source_id") or item.get("source"))
            for item in previous_retained
            if item.get("runner_source") == runner_source
            and (item.get("source_id") or item.get("source"))
        )
        runner_source_id = normalize_source_id(runner_source)
        if not prior_labels and runner_source_id in previous_event_ids:
            # Bootstrap snapshots predate per-runner source metadata. Most
            # standalone adapters use the runner name as their event source.
            prior_labels.add(runner_source_id)
        fresh_labels = set(result.event_source_ids)
        unavailable = (
            result.status in {SourceStatus.FAILED, SourceStatus.PARSER_EMPTY}
            or (result.status == SourceStatus.DEGRADED and not fresh_labels)
            or result.status == SourceStatus.SCHEDULED_SKIP
            or "zero_after_recent_nonempty" in result.anomalies
        )
        if unavailable:
            labels.update(prior_labels)

        for warning in result.warnings:
            warning_source = normalize_source_id(
                warning.get("source_id") or warning.get("source")
            )
            # Grouped adapters report the concrete municipality/venue that
            # failed. Retain that logical child only when it produced no fresh
            # records; a zero-event retained child remains tracked across
            # consecutive failures.
            if (
                warning_source
                and warning_source in (previous_event_ids | previous_retained_ids)
            ):
                labels.add(warning_source)
            elif (
                warning_source.startswith("meetup-")
                and "meetup" in previous_event_ids
            ):
                # A pre-source-ID snapshot cannot map Meetup records back to
                # individual groups. Preserve the legacy group conservatively
                # for this one migration run; new snapshots use child IDs.
                labels.add("meetup")

    # An audited Radio fallback is promoted from the manifest and owns no runner
    # source, so nothing above can ever name it. Its whole publication depends on
    # a lead the editors rotate in and out weekly: the run after the paragraph
    # disappears, the event would leave the feed while it is still running.
    # Only a source that actually published last night is worth naming here, and
    # a real adapter that meanwhile owns that ID stays authoritative.
    fresh_source_ids = {
        source_id for result in results.values() for source_id in result.event_source_ids
    }
    labels.update(
        (unpublished_fallback_source_ids & previous_event_ids) - fresh_source_ids
    )
    return labels


def _retain_previous_events(
    results: dict[str, SourceResult], previous: dict, context: RunContext,
    unpublished_fallback_source_ids: frozenset[str] = frozenset(),
) -> tuple[list[CanonicalEvent], dict[str, object]]:
    labels = _retention_labels(results, previous, unpublished_fallback_source_ids)
    empty_summary: dict[str, object] = {
        "fresh_event_count": 0,
        "retained_event_count": 0,
        "expired_retained_event_count": 0,
        "retained_sources": [],
    }
    if not labels:
        return [], empty_summary

    previous_retention = {
        normalize_source_id(item.get("source_id") or item.get("source")): item
        for item in previous.get("retained_sources") or []
        if isinstance(item, dict) and item.get("source")
    }
    source_names: dict[str, str] = {
        label: str(item.get("source") or label)
        for label, item in previous_retention.items()
        if label in labels
    }
    runner_sources: dict[str, str] = {
        label: str(item.get("runner_source"))
        for label, item in previous_retention.items()
        if label in labels and item.get("runner_source")
    }
    previous_results = previous.get("source_results") or {}
    for runner_source, result in results.items():
        prior = previous_results.get(runner_source) or {}
        prior_ids = {
            normalize_source_id(value)
            for value in (prior.get("event_source_ids") or prior.get("event_sources") or [])
            if str(value).strip()
        }
        runner_source_id = normalize_source_id(runner_source)
        if not prior_ids and runner_source_id in labels:
            prior_ids.add(runner_source_id)
        for source_id in prior_ids & labels:
            runner_sources.setdefault(source_id, runner_source)
        for warning in result.warnings:
            source_id = normalize_source_id(warning.get("source_id") or warning.get("source"))
            if source_id in labels and warning.get("source"):
                source_names[source_id] = str(warning["source"])
                runner_sources[source_id] = runner_source
    # A promoted fallback has no runner source, yet the Radio adapter is what
    # feeds it. Booking it there keeps the summary readable and lets the next run
    # recover the label from the snapshot when Radio itself is unavailable.
    for label in unpublished_fallback_source_ids & labels:
        runner_sources.setdefault(label, _RADIO_RUNNER_SOURCE)
    retained: list[CanonicalEvent] = []
    expired_counts = dict.fromkeys(labels, 0)
    candidate_counts = dict.fromkeys(labels, 0)
    window_start = context.window.start.strftime("%Y-%m-%d")
    window_end = context.window.end.strftime("%Y-%m-%d")
    for raw_event in previous.get("events") or []:
        if not isinstance(raw_event, dict):
            continue
        if _is_discovery_only_event(raw_event):
            continue
        label = _event_source_id(raw_event)
        if label not in labels:
            continue
        source_names.setdefault(label, str(raw_event.get("source") or label))
        raw_end = str(raw_event.get("end_date") or raw_event.get("date") or "")
        if "ongoing until " in raw_end:
            raw_end = raw_end.rsplit("ongoing until ", 1)[-1]
        elif "–" in raw_end:
            raw_end = raw_end.rsplit("–", 1)[-1]
        parsed_end = common.parse_date(raw_end)
        if parsed_end and parsed_end.strftime("%Y-%m-%d") < window_start:
            expired_counts[label] += 1
            continue
        try:
            retained_raw = {**raw_event, "source_id": label}
            event = validate_event(retained_raw)
        except EventValidationError:
            continue
        published_event_id = str(raw_event.get("event_id") or "").strip()
        if published_event_id:
            event = replace(event, preserved_event_id=published_event_id)
        if event.end_date < window_start:
            expired_counts[label] += 1
            continue
        if event.start_date > window_end:
            continue
        retained.append(event)
        candidate_counts[label] += 1

    prior_generated_at = str(previous.get("generated_at") or "")
    retained_sources = []
    for label in sorted(labels):
        prior = previous_retention.get(label) or {}
        runner_source = runner_sources.get(label) or prior.get("runner_source") or ""
        scheduled_skip = (
            runner_source in results
            and results[runner_source].status == SourceStatus.SCHEDULED_SKIP
        )
        retained_sources.append({
            "source": source_names.get(label, label),
            "source_id": label,
            "runner_source": runner_source,
            "retained_event_count": candidate_counts[label],
            "expired_event_count": expired_counts[label],
            "last_success_at": prior.get("last_success_at") or prior_generated_at,
            "consecutive_failures": (
                int(prior.get("consecutive_failures") or 0)
                if scheduled_skip
                else int(prior.get("consecutive_failures") or 0) + 1
            ),
        })
    return retained, {
        **empty_summary,
        "retained_event_count": len(retained),
        "expired_retained_event_count": sum(expired_counts.values()),
        "retained_sources": retained_sources,
    }


def _attach_baselines(results: dict[str, SourceResult], previous: dict, minimum_count: int) -> None:
    """Expose count changes without treating seasonal empty calendars as failures."""
    for name, result in results.items():
        if result.status == SourceStatus.SCHEDULED_SKIP:
            continue
        if any(
            details.get("status") == 200
            and int(details.get("bytes") or 0) > 2000
            and details.get("candidate_count") == 0
            for details in result.endpoints.values()
        ):
            result.anomalies.append("zero_candidates_from_nonempty_body")
        prior = previous.get(name, {})
        prior_status = prior.get("status")
        prior_count = (
            prior.get("last_nonempty_raw_event_count")
            if prior_status in {SourceStatus.SCHEDULED_SKIP.value, SourceStatus.DISABLED.value}
            else prior.get("raw_event_count")
        )
        if not isinstance(prior_count, int):
            continue
        result.last_nonempty_raw_event_count = (
            result.raw_event_count
            if result.raw_event_count > 0
            else int(prior.get("last_nonempty_raw_event_count") or prior_count or 0)
        )
        result.baseline = {"previous_raw_event_count": prior_count}
        if prior_count >= minimum_count and result.raw_event_count == 0:
            result.anomalies.append("zero_after_recent_nonempty")
        elif prior_count >= minimum_count and result.raw_event_count * 2 < prior_count:
            result.anomalies.append("large_drop_after_recent_nonempty")


def _source_result_for_identity(
    source_id: str, source: str, results: dict[str, SourceResult],
) -> SourceResult | None:
    """Resolve an exact source owner before considering aggregate membership."""
    for result in results.values():
        if source_id == result.source_id or (source and source == result.source):
            return result
    for result in results.values():
        if source_id in result.event_source_ids or source in result.event_sources:
            return result
    return results.get(source)


def _source_result_for_event(
    event: CanonicalEvent, results: dict[str, SourceResult],
) -> SourceResult | None:
    """Resolve a canonical child source back to its runner result."""
    return _source_result_for_identity(event.source_id, event.source, results)


def _operational_source_result_for_event(
    event: CanonicalEvent, results: dict[str, SourceResult],
) -> SourceResult | None:
    """Prefer an active aggregate producer over a scheduled exact owner."""
    result = _source_result_for_event(event, results)
    if result is None or result.status != SourceStatus.SCHEDULED_SKIP:
        return result
    for candidate in results.values():
        if (
            candidate.status != SourceStatus.SCHEDULED_SKIP
            and (
                event.source_id in candidate.event_source_ids
                or event.source in candidate.event_sources
            )
        ):
            return candidate
    return result


@performance.measured("publication.filter")
def _publication_filter_reason(
    event: CanonicalEvent, settings: config.RuntimeConfig,
) -> str:
    """Return the final publication rejection reason without mutating health data."""
    if not common.event_in_window(event):
        return "filter:window"
    if event.distance_km is not None and event.distance_km > settings.radius_km:
        return "filter:radius"
    if event.score < settings.score_floor and event.status == "scheduled":
        return "filter:score_floor"
    return ""


def _attach_cross_run_fields(
    events: Sequence[CanonicalEvent | dict], previous: dict, generated_at: str,
) -> list[CanonicalEvent]:
    """Carry first-seen timestamps and expose a content-change fingerprint."""
    previous_by_id = {
        str(record.get("event_id") or event_id(record)): record
        for record in previous.get("events") or []
        if isinstance(record, dict)
    }
    enriched: list[CanonicalEvent] = []
    for event in events:
        canonical_event: CanonicalEvent
        if isinstance(event, dict):
            canonical_event = CanonicalEvent(**{
                name: event[name]
                for name in CanonicalEvent.__dataclass_fields__
                if name in event
            })
        else:
            canonical_event = event
        identifier = event_id(canonical_event)
        prior = previous_by_id.get(identifier, {})
        first_seen = str(
            (prior.get("first_seen_at")
            or prior.get("generated_at")
            or previous.get("generated_at"))
            if prior
            else generated_at
        )
        current_event = canonical_event
        cancelled_at = current_event.cancelled_at
        if current_event.status == "cancelled":
            cancelled_at = str(prior.get("cancelled_at") or cancelled_at or generated_at)
        candidate = replace(
            current_event,
            first_seen_at=first_seen,
            cancelled_at=cancelled_at,
            cancellation_source=(
                current_event.cancellation_source
                or (
                    current_event.source
                    if current_event.status in {"cancelled", "postponed"}
                    else ""
                )
            ),
        )
        enriched.append(replace(candidate, content_hash=content_hash(candidate)))
    return enriched


@performance.measured("dedup.retained_against_fresh")
def _retained_events_without_fresh_duplicate(
    fresh_events: list[CanonicalEvent],
    retained_events: list[CanonicalEvent],
) -> list[CanonicalEvent]:
    """Keep retained occurrences absent from the indexed fresh event set."""
    fresh_ids = {event_id(event) for event in fresh_events}
    blocking_frequencies = Counter(
        key
        for event in fresh_events
        for key in report._dedup_blocking_keys(event)
    )
    candidate_index: dict[tuple[str, ...], set[int]] = {}
    for index, event in enumerate(fresh_events):
        report._index_blocking_keys(event, index, candidate_index)

    def is_legacy_dated_title_twin(fresh: CanonicalEvent, retained: CanonicalEvent) -> bool:
        match = _LEGACY_DATED_RANGE_TITLE.match(str(retained.get("title") or ""))
        return bool(
            match
            and _event_source_id(fresh) == _event_source_id(retained)
            and str(fresh.get("start_date") or "")
            == str(retained.get("start_date") or "")
            and comparison_text(str(fresh.get("city") or ""))
            == comparison_text(str(retained.get("city") or ""))
            and comparison_text(str(fresh.get("title") or ""))
            == comparison_text(match.group(1))
        )

    def is_same_source_refresh(fresh: CanonicalEvent, retained: CanonicalEvent) -> bool:
        """Match a retained occurrence even when its source corrected the venue."""
        return bool(
            _event_source_id(fresh) == _event_source_id(retained)
            and comparison_text(str(fresh.get("title") or ""))
            == comparison_text(str(retained.get("title") or ""))
            and str(fresh.get("start_date") or "")
            == str(retained.get("start_date") or "")
            and comparison_text(str(fresh.get("city") or ""))
            == comparison_text(str(retained.get("city") or ""))
            and (
                not fresh.get("start_at")
                or not retained.get("start_at")
                or report._same_explicit_start(
                    str(fresh.get("start_at")), str(retained.get("start_at"))
                )
            )
        )

    return [
        candidate
        for candidate in retained_events
        if event_id(candidate) not in fresh_ids
        and not any(
            report.events_are_duplicates(fresh_events[index], candidate)
            or is_legacy_dated_title_twin(fresh_events[index], candidate)
            or is_same_source_refresh(fresh_events[index], candidate)
            for index in report._blocking_candidates(
                candidate, candidate_index, blocking_frequencies
            )
        )
    ]


def _enrich_promoted_fallbacks(
    events: Sequence[CanonicalEvent],
    promoted_fallback_event_ids: frozenset[str],
) -> list[CanonicalEvent]:
    """Read the audited primary page of every promoted Radio fallback.

    A promoted fallback carries master data only: the lead's safe fields plus a
    sentence built from them. Its link is the audited first-party URL, which is
    the one page holding the venue, the hours and the programme.

    The per-source detail pass runs while a source is imported, long before the
    manifest resolves that URL, so these records are the only ones that never
    get a second pass. Without it the site publishes its most visited Kirmes
    pages with no venue and no description at all.

    Identity is pinned before the fetch. Filling a blank venue would otherwise
    move a URL that is already public; this mirrors how AI enrichment preserves
    the pre-enrichment occurrence id.
    """
    resolved: list[CanonicalEvent] = list(events)
    indexes = [
        index for index, event in enumerate(resolved)
        if event_id(event) in promoted_fallback_event_ids
    ]
    if not indexes:
        return resolved

    drafts: list[dict] = []
    for index in indexes:
        draft = resolved[index].to_dict()
        draft["preserved_event_id"] = event_id(resolved[index])
        drafts.append(draft)

    # A manifest entry may expand into several occurrences with one audited
    # primary URL. The generic detail pass deliberately rejects duplicate URLs
    # in one batch as likely overview pages. These URLs are explicitly audited,
    # so pass each occurrence separately; the shared cache fetches the document
    # once while title-aware extraction still runs for every occurrence.
    enriched_drafts = [
        detail_enrichment.enrich_events(
            [draft], cache_namespace="radio-primary-fallback-v1",
        )[0]
        for draft in drafts
    ]
    for index, draft in zip(indexes, enriched_drafts, strict=False):
        try:
            resolved[index] = validate_event(draft)
        except EventValidationError as exc:  # noqa: PERF203 - each optional detail result fails soft
            # Enrichment is optional. A page that pushes the record past a
            # quality rule leaves the audited fallback exactly as it was.
            common.log_source_error(
                f"{draft.get('source') or 'radio fallback'} primary detail", exc,
            )
    return resolved


def _prefer_retained_primary_over_radio_fallback(
    fresh_events: list,
    retained_events: list,
    promoted_fallback_event_ids: frozenset[str],
) -> tuple[list, list]:
    """Keep richer retained first-party data when Radio only supplied a fallback."""
    fresh: list = list(fresh_events)
    remaining_retained: list = list(retained_events)
    for fresh_index, fallback in enumerate(fresh):
        if event_id(fallback) not in promoted_fallback_event_ids:
            continue
        duplicate_indexes = [
            index for index, retained in enumerate(remaining_retained)
            if report.events_are_duplicates(fallback, retained)
        ]
        if len(duplicate_indexes) != 1:
            continue
        retained_index = duplicate_indexes[0]
        retained = remaining_retained[retained_index]
        if retained.get("description_source") != "scraped":
            continue
        remaining_retained.pop(retained_index)
        incoming = list(dict.fromkeys(fallback.get("discovered_via", [])))
        existing = [
            source_id for source_id in retained.get("discovered_via", [])
            if source_id not in incoming
        ]
        existing_limit = max(MAX_DISCOVERY_PROVENANCE_SOURCES - len(incoming), 0)
        provenance = [*existing[:existing_limit], *incoming[:MAX_DISCOVERY_PROVENANCE_SOURCES]]
        fresh[fresh_index] = (
            replace(retained, discovered_via=provenance)
            if isinstance(retained, CanonicalEvent)
            else {**retained, "discovered_via": provenance}
        )
    return fresh, remaining_retained


def _prefer_retained_primary_over_bonn_fallback(
    fresh_events: list[CanonicalEvent],
    retained_events: list[CanonicalEvent],
) -> tuple[
    list[CanonicalEvent],
    list[CanonicalEvent],
    list[CanonicalEvent],
]:
    """Keep an unrefreshed primary record ahead of a fresh Bonn fallback.

    Targeted refreshes deduplicate selected sources separately from retained
    sources. The final cross-snapshot filter used to let every fresh record win
    wholesale, which made a Bonn.de calendar copy replace a richer first-party
    record even though the normal same-run deduplicator ranks Bonn lower.

    Only one-to-one, strongly identified pairs are promoted. Broad venue/date
    duplicate matches remain on the existing path because a museum can publish
    several different events in one category on the same day.
    """
    fresh = list(fresh_events)
    retained = list(retained_events)
    blocking_frequencies = Counter(
        key for event in retained for key in report._dedup_blocking_keys(event)
    )
    candidate_index: dict[tuple[str, ...], set[int]] = {}
    for index, event in enumerate(retained):
        report._index_blocking_keys(event, index, candidate_index)

    matches_by_fresh: dict[int, list[int]] = {}
    fresh_indexes_by_retained: dict[int, list[int]] = {}
    for fresh_index, fallback in enumerate(fresh):
        if _event_source_id(fallback) not in _BONN_FALLBACK_SOURCE_IDS:
            continue
        matches = []
        for retained_index in report._blocking_candidates(
            fallback, candidate_index, blocking_frequencies
        ):
            primary = retained[retained_index]
            same_strong_identity = (
                event_id(fallback) == event_id(primary)
                or report._titles_match(fallback, primary)
                or bool(
                    fallback.get("series_id")
                    and fallback.get("series_id") == primary.get("series_id")
                )
            )
            if (
                report.source_authority(primary.get("source", ""))
                > report.source_authority(fallback.get("source", ""))
                and same_strong_identity
                and report.events_are_duplicates(fallback, primary)
            ):
                matches.append(retained_index)
                fresh_indexes_by_retained.setdefault(retained_index, []).append(
                    fresh_index
                )
        if matches:
            matches_by_fresh[fresh_index] = matches

    consumed_retained: set[int] = set()
    promoted_retained: list[CanonicalEvent] = []
    for fresh_index, retained_indexes in matches_by_fresh.items():
        if len(retained_indexes) != 1:
            continue
        retained_index = retained_indexes[0]
        if len(fresh_indexes_by_retained[retained_index]) != 1:
            continue
        promoted = report._merge_duplicate_metadata(
            retained[retained_index],
            fresh[fresh_index],
        )
        fresh[fresh_index] = promoted
        promoted_retained.append(promoted)
        consumed_retained.add(retained_index)

    return (
        fresh,
        [
            event for index, event in enumerate(retained)
            if index not in consumed_retained
        ],
        promoted_retained,
    )


def _enforce_restricted_publication_boundary(events: list,
    warnings: list[dict[str, str]] | None = None,
) -> list:
    """Reapply the no-source-copy contract after cross-source deduplication."""
    protected = []
    for event in events:
        if not ai_enrichment.is_target_event(event):
            protected.append(event)
            continue
        raw = event.to_dict() if isinstance(event, CanonicalEvent) else dict(event)
        ai_enrichment.strip_restricted_copy(raw)
        try:
            protected.append(validate_event(raw))
        except EventValidationError as exc:
            if warnings is not None:
                warnings.append(
                    diagnostic_warning(
                        raw.get("source", "publication-boundary"),
                        "PublicationBoundaryWarning",
                        f"record dropped after restricted-copy sanitization: {exc}",
                        source_id=raw.get("source_id", ""),
                    ))
    return protected
