"""Resolve audited Radio Bonn leads to qualifying publication sources.

The Radio adapter is discovery-only. This module is the deliberately narrow
bridge from its sanitized in-memory master-data leads to a checked-in audit
manifest. It prefers first-party ownership, allows explicitly audited local
fallbacks, and never accepts Radio prose as publication copy.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field, replace
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Callable, Iterable, Mapping, Sequence
from urllib.parse import urlsplit, urlunsplit
from zoneinfo import ZoneInfo

from . import common
from .models import (
    MAX_DISCOVERY_PROVENANCE_SOURCES,
    CanonicalEvent,
    normalize_source_id,
)
from .normalization import comparison_text
from .validation import EventValidationError, validate_event


RADIO_SOURCE_ID = "radio-bonn-rhein-sieg"
MANIFEST_PATH = Path(__file__).with_name("sources") / "radio_primary_sources.json"
_ALLOWED_EVIDENCE = frozenset({"confirmed", "probable", "unresolved"})
_ALLOWED_CORRECTIONS = frozenset({
    "title", "start_date", "end_date", "time", "time_note", "city", "venue", "status",
})
_ALLOWED_RESOLUTION_MODES = frozenset({"", "match_existing_primary"})
_ALLOWED_ENTRY_FIELDS = frozenset({
    "title", "start_date", "primary_url", "primary_source", "primary_source_id",
    "evidence_status", "verified_at", "fallback_publication", "corrections",
    "resolution_mode", "withhold_reason",
})
_SAFE_LEAD_FIELDS = frozenset({
    "title", "date", "time", "time_note", "start_date", "end_date", "all_day",
    "ongoing", "timezone", "status", "venue", "city", "category", "category_key",
    "category_label", "score",
})
_TIME_TOKEN_RE = re.compile(r"(?<!\d)([01]?\d|2[0-3]):([0-5]\d)(?!\d)")
_BROAD_CALENDAR_HOSTS = frozenset({
    "bonn.de", "www.bonn.de", "troisdorf.de", "www.troisdorf.de",
})
_SINGLE_MATCH_OUTPUT_CORRECTIONS = frozenset({"time", "time_note", "city", "venue"})
_DERIVED_LOCATION_FIELDS = frozenset({
    "venue_id", "venue_address", "venue_district", "venue_type", "venue_latitude",
    "venue_longitude", "distance_km", "location_confidence", "location_source",
})


@dataclass(frozen=True, slots=True)
class RadioPrimaryEntry:
    title: str
    start_date: str
    primary_url: str
    primary_source: str
    primary_source_id: str
    evidence_status: str
    verified_at: str
    fallback_publication: bool
    corrections: Mapping[str, str] = field(default_factory=dict)
    resolution_mode: str = ""
    withhold_reason: str = ""

    @property
    def key(self) -> tuple[str, str]:
        return self.title, self.start_date


@dataclass(frozen=True, slots=True)
class RadioResolutionOutcome:
    events: tuple[CanonicalEvent, ...]
    research_leads: tuple[dict[str, object], ...]
    dispositions: Mapping[tuple[str, str], str]
    cancellations: tuple[dict[str, object], ...] = ()

    @property
    def research_lead_reasons(self) -> dict[str, int]:
        return dict(Counter(
            str(lead.get("reason") or "needs_primary_source")
            for lead in self.research_leads
        ))


def _required_text(raw: Mapping[str, object], field_name: str) -> str:
    value = raw.get(field_name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"radio primary manifest: {field_name} must be non-empty text")
    return value.strip()


def _optional_text(raw: Mapping[str, object], field_name: str) -> str:
    value = raw.get(field_name, "")
    if not isinstance(value, str):
        raise ValueError(f"radio primary manifest: {field_name} must be text")
    return value.strip()


def _iso_date(value: str, field_name: str) -> str:
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"radio primary manifest: invalid {field_name}: {value}") from exc
    return value


def _valid_web_url(value: str) -> bool:
    if not value:
        return False
    parsed = urlsplit(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _parse_entry(raw: object) -> RadioPrimaryEntry:
    if not isinstance(raw, dict):
        raise ValueError("radio primary manifest: every entry must be an object")
    unknown_fields = set(raw) - _ALLOWED_ENTRY_FIELDS
    if unknown_fields:
        raise ValueError(
            f"radio primary manifest: unsupported entry fields: {sorted(unknown_fields)}"
        )
    title = _required_text(raw, "title")
    start_date = _iso_date(_required_text(raw, "start_date"), "start_date")
    evidence_status = _required_text(raw, "evidence_status")
    if evidence_status not in _ALLOWED_EVIDENCE:
        raise ValueError(f"radio primary manifest: invalid evidence_status for {title}")
    fallback = raw.get("fallback_publication")
    if not isinstance(fallback, bool):
        raise ValueError(f"radio primary manifest: fallback_publication must be boolean for {title}")
    primary_url = _optional_text(raw, "primary_url")
    if primary_url and not _valid_web_url(primary_url):
        raise ValueError(f"radio primary manifest: invalid primary_url for {title}")
    primary_source = _optional_text(raw, "primary_source")
    primary_source_id = _optional_text(raw, "primary_source_id")
    if primary_source_id and normalize_source_id(primary_source_id) != primary_source_id:
        raise ValueError(f"radio primary manifest: invalid primary_source_id for {title}")
    if fallback and (
        evidence_status == "unresolved"
        or not primary_url
        or not primary_source
        or not primary_source_id
    ):
        raise ValueError(f"radio primary manifest: unsafe fallback for {title}")
    corrections = raw.get("corrections", {})
    if not isinstance(corrections, dict):
        raise ValueError(f"radio primary manifest: corrections must be an object for {title}")
    unknown = set(corrections) - _ALLOWED_CORRECTIONS
    if unknown:
        raise ValueError(
            f"radio primary manifest: unsupported corrections for {title}: {sorted(unknown)}"
        )
    normalized_corrections: dict[str, str] = {}
    for field_name, value in corrections.items():
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"radio primary manifest: invalid {field_name} correction for {title}")
        normalized_corrections[field_name] = value.strip()
        if field_name in {"start_date", "end_date"}:
            _iso_date(value.strip(), field_name)
        if field_name == "status" and value.strip() not in {
            "scheduled", "cancelled", "postponed", "unknown",
        }:
            raise ValueError(f"radio primary manifest: invalid status correction for {title}")
    corrected_start = normalized_corrections.get("start_date", start_date)
    corrected_end = normalized_corrections.get("end_date", corrected_start)
    if date.fromisoformat(corrected_end) < date.fromisoformat(corrected_start):
        raise ValueError(f"radio primary manifest: end_date precedes start_date for {title}")
    resolution_mode = _optional_text(raw, "resolution_mode")
    if resolution_mode not in _ALLOWED_RESOLUTION_MODES:
        raise ValueError(f"radio primary manifest: invalid resolution_mode for {title}")
    withhold_reason = _optional_text(raw, "withhold_reason")
    if not fallback and not resolution_mode and not withhold_reason:
        raise ValueError(f"radio primary manifest: non-fallback entry lacks disposition for {title}")
    if fallback and (resolution_mode or withhold_reason):
        raise ValueError(f"radio primary manifest: fallback has conflicting disposition for {title}")
    if resolution_mode and (not primary_url or not primary_source_id):
        raise ValueError(f"radio primary manifest: match entry lacks primary identity for {title}")
    return RadioPrimaryEntry(
        title=title,
        start_date=start_date,
        primary_url=primary_url,
        primary_source=primary_source,
        primary_source_id=primary_source_id,
        evidence_status=evidence_status,
        verified_at=_iso_date(_required_text(raw, "verified_at"), "verified_at"),
        fallback_publication=fallback,
        corrections=normalized_corrections,
        resolution_mode=resolution_mode,
        withhold_reason=withhold_reason,
    )


def load_manifest(path: str | Path = MANIFEST_PATH) -> tuple[RadioPrimaryEntry, ...]:
    """Load and strictly validate the finite Radio primary-source audit."""
    manifest_path = Path(path)
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError("radio primary manifest: schema_version must be 1")
    if set(payload) != {"schema_version", "entries"}:
        raise ValueError("radio primary manifest: unsupported top-level fields")
    raw_entries = payload.get("entries")
    if not isinstance(raw_entries, list):
        raise ValueError("radio primary manifest: entries must be a list")
    entries = tuple(_parse_entry(raw) for raw in raw_entries)
    keys = [entry.key for entry in entries]
    if len(keys) != len(set(keys)):
        duplicates = sorted(key for key, count in Counter(keys).items() if count > 1)
        raise ValueError(f"radio primary manifest: duplicate exact keys: {duplicates}")
    return entries


def entry_for_key(
    key: tuple[str, str],
    manifest: Sequence[RadioPrimaryEntry] | None = None,
) -> RadioPrimaryEntry:
    entries = manifest if manifest is not None else load_manifest()
    try:
        return next(entry for entry in entries if entry.key == key)
    except StopIteration as exc:
        raise KeyError(key) from exc


def expected_resolution_class(entry: RadioPrimaryEntry) -> str:
    if entry.fallback_publication:
        return "promote"
    if entry.resolution_mode == "match_existing_primary":
        return "match"
    return "withhold"


def _lead_key(lead: Mapping[str, object]) -> tuple[str, str]:
    return (
        str(lead.get("title") or "").strip(),
        str(lead.get("start_date") or lead.get("date") or "").strip(),
    )


def _url_key(value: str) -> str:
    parsed = urlsplit(value.strip())
    path = parsed.path.rstrip("/") or "/"
    return urlunsplit((parsed.scheme.casefold(), parsed.netloc.casefold(), path, parsed.query, ""))


def _url_host(value: str) -> str:
    return urlsplit(value.strip()).netloc.casefold()


def _title_tokens(value: str) -> set[str]:
    return {
        token for token in comparison_text(value).split()
        if len(token) >= 3 and token not in {"der", "die", "das", "und", "beim", "2026"}
    }


def _title_matches(expected: str, actual: str) -> bool:
    expected_key = comparison_text(expected)
    actual_key = comparison_text(actual)
    if expected_key == actual_key:
        return True
    if min(len(expected_key), len(actual_key)) >= 12 and (
        expected_key in actual_key or actual_key in expected_key
    ):
        return True
    expected_tokens = _title_tokens(expected)
    actual_tokens = _title_tokens(actual)
    overlap = expected_tokens & actual_tokens
    return bool(overlap) and (
        len(overlap) >= 2
        and len(overlap) / min(len(expected_tokens), len(actual_tokens)) >= 0.6
    )


def _series_matches(entry: RadioPrimaryEntry, event: CanonicalEvent) -> bool:
    corrected_title = entry.corrections.get("title", entry.title)
    if "stadtgartenkonzerte" in comparison_text(entry.title):
        return "stadtgartenkonzerte" in comparison_text(event.title)
    return _title_matches(corrected_title, event.title)


def _date_matches(entry: RadioPrimaryEntry, event: CanonicalEvent) -> bool:
    target_start = entry.corrections.get("start_date", entry.start_date)
    if entry.resolution_mode != "match_existing_primary":
        return event.start_date == target_start
    target_end = entry.corrections.get("end_date", target_start)
    return target_start <= event.start_date <= target_end


def _candidate_matches(
    entry: RadioPrimaryEntry,
    event: CanonicalEvent,
    same_url_count: int,
) -> bool:
    if event.source_role != "primary":
        return False
    if not _date_matches(entry, event):
        return False
    exact_url = bool(entry.primary_url and event.link) and (
        _url_key(event.link) == _url_key(entry.primary_url)
    )
    exact_source = event.source_id == entry.primary_source_id
    same_host = bool(entry.primary_url and event.link) and (
        _url_host(event.link) == _url_host(entry.primary_url)
    )
    if not (exact_url or exact_source or same_host):
        return False
    if entry.resolution_mode == "match_existing_primary":
        if exact_url:
            return True
        if same_host and _url_host(entry.primary_url) not in _BROAD_CALENDAR_HOSTS:
            return True
        return (exact_source or same_host) and _series_matches(entry, event)
    corrected_title = entry.corrections.get("title", entry.title)
    if _title_matches(corrected_title, event.title):
        return True
    # A date-specific first-party detail URL is stronger than a secondary title
    # and safely handles audited title replacements such as Art&Eat.
    return exact_url and same_url_count == 1


def _annotate(event: CanonicalEvent, discovered_via: Iterable[str]) -> CanonicalEvent:
    incoming: list[str] = []
    for source_id in discovered_via:
        normalized = normalize_source_id(source_id)
        if normalized and normalized not in incoming:
            incoming.append(normalized)
    incoming = incoming[:MAX_DISCOVERY_PROVENANCE_SOURCES]
    existing = [source_id for source_id in event.discovered_via if source_id not in incoming]
    existing_limit = MAX_DISCOVERY_PROVENANCE_SOURCES - len(incoming)
    provenance = [*existing[:existing_limit], *incoming]
    return replace(event, discovered_via=provenance)


def _rebuild_temporal_fields(raw: dict[str, object]) -> None:
    """Recompute timestamps after audited date/time corrections."""
    start_date = date.fromisoformat(str(raw["start_date"]))
    end_date = date.fromisoformat(str(raw["end_date"]))
    tokens = _TIME_TOKEN_RE.findall(str(raw.get("time") or ""))
    timezone_name = str(raw.get("timezone") or "Europe/Berlin")
    try:
        timezone = ZoneInfo(timezone_name)
    except (KeyError, ValueError):
        timezone_name = "Europe/Berlin"
        timezone = ZoneInfo(timezone_name)
    raw["timezone"] = timezone_name
    raw["start_at"] = ""
    raw["end_at"] = ""
    if not tokens:
        raw["all_day"] = True
        return
    start_clock = time(int(tokens[0][0]), int(tokens[0][1]))
    start = datetime.combine(start_date, start_clock, timezone)
    raw["start_at"] = start.isoformat(timespec="seconds")
    raw["all_day"] = False
    if len(tokens) < 2:
        return
    end_clock = time(int(tokens[1][0]), int(tokens[1][1]))
    end = datetime.combine(end_date, end_clock, timezone)
    if end <= start:
        end += timedelta(days=1)
    raw["end_at"] = end.isoformat(timespec="seconds")


def _apply_single_match_corrections(
    event: CanonicalEvent, entry: RadioPrimaryEntry,
) -> CanonicalEvent:
    """Apply audited visitor fields when one official occurrence matched.

    Title/date corrections on match-only entries describe the Radio umbrella
    and its matching bounds; they must not overwrite split official records.
    """
    corrections = {
        field_name: value
        for field_name, value in entry.corrections.items()
        if field_name in _SINGLE_MATCH_OUTPUT_CORRECTIONS
    }
    if not corrections:
        return event
    raw = event.to_dict()
    raw.update(corrections)
    if "time" in corrections:
        _rebuild_temporal_fields(raw)
    if {"city", "venue"} & corrections.keys():
        for field_name in _DERIVED_LOCATION_FIELDS:
            raw.pop(field_name, None)
    return validate_event(raw)


def _promote_fallback(
    lead: Mapping[str, object],
    entry: RadioPrimaryEntry,
) -> CanonicalEvent:
    raw = {key: lead[key] for key in _SAFE_LEAD_FIELDS if key in lead}
    raw.update(entry.corrections)
    raw["title"] = str(raw.get("title") or entry.title)
    raw["start_date"] = str(raw.get("start_date") or entry.start_date)
    raw["date"] = raw["start_date"]
    raw["end_date"] = str(raw.get("end_date") or raw["start_date"])
    _rebuild_temporal_fields(raw)
    raw.update({
        "source": entry.primary_source,
        "source_id": entry.primary_source_id,
        "source_role": "primary",
        "discovered_via": [RADIO_SOURCE_ID],
        "link": entry.primary_url,
        "link_kind": "",
        "description_source": "generated",
        "ai_summary": "",
    })
    raw["description"] = common.factual_event_description(
        raw["title"],
        date_value=raw["start_date"],
        end_date_value=raw["end_date"],
        time_text=str(raw.get("time") or ""),
        venue=str(raw.get("venue") or ""),
        city=str(raw.get("city") or ""),
    )
    raw["description_html"] = ""
    return validate_event(raw)


def resolve_radio_leads(
    leads: Sequence[Mapping[str, object]],
    primary_events: Sequence[CanonicalEvent],
    *,
    manifest: Sequence[RadioPrimaryEntry] | None = None,
    publication_filter: Callable[[CanonicalEvent], str] | None = None,
) -> RadioResolutionOutcome:
    """Match, promote, or retain sanitized Radio discovery leads.

    Existing first-party records are considered before any fallback is built.
    Match-only series can annotate multiple official act records, while ordinary
    audited leads resolve to at most one unambiguous official occurrence.
    """
    entries = tuple(manifest) if manifest is not None else load_manifest()
    by_key = {entry.key: entry for entry in entries}
    events = list(primary_events)
    research_leads: list[dict[str, object]] = []
    dispositions: dict[tuple[str, str], str] = {}
    cancellations: list[dict[str, object]] = []

    for lead in leads:
        key = _lead_key(lead)
        entry = by_key.get(key)
        if entry is None:
            unresolved = dict(lead)
            unresolved["reason"] = "needs_primary_source"
            research_leads.append(unresolved)
            continue
        if expected_resolution_class(entry) == "withhold":
            withheld = dict(lead)
            withheld["reason"] = entry.withhold_reason or "needs_primary_source"
            research_leads.append(withheld)
            dispositions[key] = "withheld"
            continue

        target_start = entry.corrections.get("start_date", entry.start_date)
        same_url_count = sum(
            1 for event in events
            if event.start_date == target_start
            and entry.primary_url and event.link
            and _url_key(event.link) == _url_key(entry.primary_url)
        )
        matched_indexes = [
            index for index, event in enumerate(events)
            if _candidate_matches(entry, event, same_url_count)
        ]
        if matched_indexes:
            if entry.resolution_mode != "match_existing_primary" and len(matched_indexes) > 1:
                matched_indexes = []
            else:
                provenance = lead.get("discovered_via")
                discovered_via = (
                    value for value in provenance if isinstance(value, str)
                ) if isinstance(provenance, list) else (RADIO_SOURCE_ID,)
                discovered_via = tuple(discovered_via) or (RADIO_SOURCE_ID,)
                for index in matched_indexes:
                    if len(matched_indexes) == 1:
                        events[index] = _apply_single_match_corrections(events[index], entry)
                    events[index] = _annotate(events[index], discovered_via)
                audited_status = entry.corrections.get("status", "")
                if audited_status in {"cancelled", "postponed"}:
                    for index in matched_indexes:
                        tombstone = events[index].to_dict()
                        tombstone.update({
                            "status": audited_status,
                            "source": entry.primary_source,
                            "source_id": entry.primary_source_id,
                            "source_role": "primary",
                            "link": entry.primary_url,
                            "discovered_via": list(events[index].discovered_via),
                        })
                        cancellations.append(tombstone)
                    dispositions[key] = "matched_existing_primary_with_cancellation"
                    continue
                dispositions[key] = "matched_existing_primary"
                continue

        if entry.fallback_publication:
            try:
                promoted = _promote_fallback(lead, entry)
            except EventValidationError as exc:
                unresolved = dict(lead)
                unresolved["reason"] = f"primary_fallback_invalid:{exc}"
                research_leads.append(unresolved)
                dispositions[key] = "fallback_invalid"
                continue
            rejection_reason = publication_filter(promoted) if publication_filter else ""
            if rejection_reason:
                unresolved = dict(lead)
                filter_name = rejection_reason.removeprefix("filter:")
                unresolved["reason"] = f"primary_fallback_filtered:{filter_name}"
                research_leads.append(unresolved)
                dispositions[key] = "fallback_filtered"
                continue
            events.append(promoted)
            if promoted.status in {"cancelled", "postponed"}:
                cancellation = promoted.to_dict()
                cancellations.append(cancellation)
            dispositions[key] = "promoted_fallback"
            continue

        unresolved = dict(lead)
        unresolved["reason"] = "needs_existing_primary_match"
        research_leads.append(unresolved)
        dispositions[key] = "awaiting_existing_primary"

    return RadioResolutionOutcome(
        events=tuple(events),
        research_leads=tuple(research_leads),
        dispositions=dispositions,
        cancellations=tuple(cancellations),
    )
