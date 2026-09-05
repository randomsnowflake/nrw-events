"""Owning implementation of dedup merge; core is a compatibility facade."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from . import common
from . import dedup_rules as _impl_dedup_rules
from . import duplicate_identity as _impl_duplicate_identity
from .identity import event_id
from .models import MAX_DISCOVERY_PROVENANCE_SOURCES, CanonicalEvent
from .validation import EventValidationError


def _adopted_description(source: Mapping[str, Any]) -> dict:
    """Take a duplicate's copy as a unit.

    The markup renders one particular description. Adopting the text without it
    left the winner's own markup behind, so the page showed a generated
    "findet am … statt" line above the real write-up it was supposed to render.
    """
    return {
        "description": source["description"],
        "description_source": source.get("description_source", "scraped"),
        "description_html": source.get("description_html", ""),
    }


def _merged_exhibitor_information(winner: Mapping[str, Any], duplicate: Mapping[str, Any]) -> dict | None:
    """Preserve richer seller facts without letting them touch visitor fields."""
    primary = winner.get("exhibitor") or {}
    secondary = duplicate.get("exhibitor") or {}
    if not primary and not secondary:
        return None

    merged = dict(primary)
    for section in ("fee", "registration"):
        preferred = dict(primary.get(section) or {})
        fallback = secondary.get(section) or {}
        for key, value in fallback.items():
            if preferred.get(key) in {None, ""} and value not in {None, ""}:
                preferred[key] = value
        merged[section] = preferred
    for key in ("setupTime", "accessHours"):
        if merged.get(key) in {None, ""} and secondary.get(key) not in {None, ""}:
            merged[key] = secondary[key]
    return merged


def _merge_duplicate_metadata(
    winner: Any,
    duplicate: Any,
    *,
    link_identity_counts: dict[str, int] | None = None,
    adopt_schedule: bool = True,
) -> Any:
    """Keep the authoritative record and enrich it field by field."""
    updates: dict[str, Any] = {}
    link_identity_counts = link_identity_counts or {}
    def unique_detail_link(event: Mapping[str, Any]) -> list[str]:
        link = str(event.get("link") or "")
        if not link:
            return []
        is_detail = event.get("link_kind") == "detail" or (
            not event.get("link_kind")
            and link_identity_counts.get(_impl_duplicate_identity._normalized_link_key(link), 0)
            < _impl_dedup_rules._REUSED_OVERVIEW_LINK_THRESHOLD
        )
        return [link] if is_detail else []

    source_links = list(dict.fromkeys([
        *(winner.get("source_links") or []),
        *unique_detail_link(winner),
        *(duplicate.get("source_links") or []),
        *unique_detail_link(duplicate),
    ]))[:20]
    if source_links:
        updates["source_links"] = source_links
    exhibitor = _merged_exhibitor_information(winner, duplicate)
    if exhibitor and exhibitor != (winner.get("exhibitor") or {}):
        updates["exhibitor"] = exhibitor
    duplicate_alias = (
        event_id(duplicate)
        if winner.get("source") != duplicate.get("source")
        and event_id(duplicate) != event_id(winner)
        else ""
    )
    previous_event_ids = list(dict.fromkeys([
        *(winner.get("previous_event_ids") or []),
        *(duplicate.get("previous_event_ids") or []),
        duplicate_alias,
    ]))[:20]
    previous_event_ids = [identifier for identifier in previous_event_ids if identifier]
    if previous_event_ids:
        updates["previous_event_ids"] = previous_event_ids
    discovered_via = list(winner.get("discovered_via") or [])
    for source_id in duplicate.get("discovered_via") or []:
        if len(discovered_via) >= MAX_DISCOVERY_PROVENANCE_SOURCES:
            break
        if source_id not in discovered_via:
            discovered_via.append(source_id)
    if discovered_via != list(winner.get("discovered_via") or []):
        updates["discovered_via"] = discovered_via
    winner_start = winner.get("start_at")
    winner_end = winner.get("end_at")
    duplicate_start = duplicate.get("start_at")
    duplicate_end = duplicate.get("end_at")
    if (
        winner_start
        and winner_end == winner_start
        and duplicate_start == winner_start
        and duplicate_end
        and duplicate_end > winner_end
    ):
        updates["end_at"] = duplicate_end
        if duplicate.get("time"):
            updates["time"] = duplicate["time"]
    separate_admission_charge = (
        _impl_duplicate_identity._has_separate_admission_charge(winner)
        or _impl_duplicate_identity._has_separate_admission_charge(duplicate)
    )
    seller_fee_evidence = common.has_seller_fee(
        " ".join((
            winner.get("description", ""), winner.get("price", ""),
            duplicate.get("description", ""), duplicate.get("price", ""),
        )),
    )
    if separate_admission_charge:
        updates["price"] = ""
        updates["admission_basis"] = ""
        updates["admission"] = {
            "isFree": None,
            "amount": None,
            "currency": "EUR",
            "basis": "",
            "note": "",
            "donationSuggested": False,
        }
    for field in (
        "price", "availability", "venue", "organizer", "time", "time_note", "start_at", "end_at",
    ):
        if not adopt_schedule and field in {"time", "time_note", "start_at", "end_at"}:
            continue
        if field == "price" and separate_admission_charge:
            continue
        winner_value_is_missing = not winner.get(field)
        winner_venue_is_implausible = field == "venue" and not _impl_duplicate_identity._venue_comparison_text(winner)
        duplicate_value_is_usable = bool(duplicate.get(field)) and (
            field != "venue" or bool(_impl_duplicate_identity._venue_comparison_text(duplicate))
        )
        if (
            field == "price"
            and seller_fee_evidence
            and duplicate.get("admission_basis") in {"implicit", "inferred"}
        ):
            duplicate_value_is_usable = False
        if (winner_value_is_missing or winner_venue_is_implausible) and duplicate_value_is_usable:
            if field == "venue" and not winner.get("identity_venue_locked"):
                updates["identity_venue"] = winner.get("venue", "")
                updates["identity_venue_locked"] = True
            if field in {"time", "start_at"} and not winner.get("identity_time_locked"):
                updates["identity_time"] = winner.get("time") or winner.get("start_at") or ""
                updates["identity_time_locked"] = True
            updates[field] = duplicate[field]
            if field == "price":
                updates["admission_basis"] = duplicate.get("admission_basis", "")
                updates["admission"] = duplicate.get("admission")
            elif winner_venue_is_implausible:
                for location_field in _impl_dedup_rules._VENUE_LOCATION_FIELDS:
                    updates[location_field] = duplicate.get(location_field)

    if (
        winner.get("price")
        and duplicate.get("price")
        and not _impl_duplicate_identity._price_has_currency(winner.get("price"))
        and _impl_duplicate_identity._price_has_currency(duplicate.get("price"))
        and _impl_duplicate_identity._paid_admission_identity(winner) == _impl_duplicate_identity._paid_admission_identity(duplicate)
        and _impl_duplicate_identity._paid_admission_identity(winner) is not None
    ):
        updates["price"] = duplicate["price"]
        updates["admission_basis"] = duplicate.get("admission_basis", "")
        updates["admission"] = duplicate.get("admission")

    if (
        winner.get("price")
        and not winner.get("admission_basis")
        and duplicate.get("price") == winner.get("price")
        and duplicate.get("admission_basis")
    ):
        updates["admission_basis"] = duplicate["admission_basis"]
        updates["admission"] = duplicate.get("admission")

    winner_link = winner.get("link", "")
    duplicate_link = duplicate.get("link", "")
    winner_link_is_reused = (
        winner_link
        and link_identity_counts.get(_impl_duplicate_identity._normalized_link_key(winner_link), 0)
        >= _impl_dedup_rules._REUSED_OVERVIEW_LINK_THRESHOLD
    )
    duplicate_link_is_not_reused = (
        duplicate_link
        and link_identity_counts.get(_impl_duplicate_identity._normalized_link_key(duplicate_link), 0)
        < _impl_dedup_rules._REUSED_OVERVIEW_LINK_THRESHOLD
    )
    if (not winner_link and duplicate_link) or (
        _impl_duplicate_identity._is_radio_aggregation_link(winner_link)
        and duplicate_link
        and not _impl_duplicate_identity._is_radio_aggregation_link(duplicate_link)
    ) or (
        winner_link_is_reused
        and duplicate_link_is_not_reused
        and _impl_duplicate_identity._link_route_depth(duplicate_link) > _impl_duplicate_identity._link_route_depth(winner_link)
    ):
        updates["link"] = duplicate_link

    duplicate_has_charge = _impl_duplicate_identity._has_separate_admission_charge(duplicate)
    winner_has_charge = _impl_duplicate_identity._has_separate_admission_charge(winner)
    duplicate_is_restricted_fallback = (
        duplicate.get("source_id") in _impl_dedup_rules._RESTRICTED_FALLBACK_SOURCE_IDS
        and winner.get("source_id") not in _impl_dedup_rules._RESTRICTED_FALLBACK_SOURCE_IDS
    )
    if not duplicate_is_restricted_fallback and ((duplicate_has_charge and not winner_has_charge) or (
        len(duplicate.get("description", "").strip())
        > len(winner.get("description", "").strip())
        and not (winner_has_charge and not duplicate_has_charge)
    )):
        updates.update(_adopted_description(duplicate))

    # AI copy is generated independently of the source record that wins
    # canonical identity. A higher-authority duplicate must not discard a
    # validated summary that another copy of the same occurrence already has.
    # Keep this fill-only: never overwrite the winner's own accepted summary.
    winner_has_extracted_content = (
        winner.get("description_source") == "scraped"
        and bool(
            str(winner.get("description") or "").strip()
            or str(winner.get("description_html") or "").strip()
        )
    )
    if (
        not winner.get("ai_summary")
        and duplicate.get("ai_summary")
        and not (duplicate_is_restricted_fallback and winner_has_extracted_content)
    ):
        updates["ai_summary"] = duplicate["ai_summary"]

    # Classification is derived data, but a broad aggregator label must not
    # override a usable classification from the canonical publisher. Peers may
    # still improve one another, and any source may fill an uncategorized record.
    winner_category = winner.get("category_key")
    category_authority_is_sufficient = (
        winner_category in {None, "", "other"}
        or _impl_dedup_rules.source_authority(duplicate.get("source", ""))
        >= _impl_dedup_rules.source_authority(winner.get("source", ""))
    )
    if (duplicate.get("category_key")
            and category_authority_is_sufficient
            and duplicate.get("category_confidence", 0) > winner.get("category_confidence", 0)):
        for field in ("category", "category_key", "category_label", "category_confidence", "category_reason"):
            if duplicate.get(field):
                updates[field] = duplicate[field]

    if isinstance(winner, CanonicalEvent):
        # Field-wise enrichment can combine two individually valid records into
        # an invalid clock/admission shape. Re-enter the canonical boundary so
        # the published snapshot keeps the same invariants as source records.
        from .validation import canonicalize_event

        try:
            return canonicalize_event(replace(winner, **updates).to_dict())
        except EventValidationError:
            return winner
    return {**winner, **updates}
