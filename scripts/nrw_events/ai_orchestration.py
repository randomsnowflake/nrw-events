"""Owning implementation of ai orchestration; core is a compatibility facade."""

from __future__ import annotations

import json
import math
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any, cast

from . import ai_cache as _impl_ai_cache
from . import ai_contracts as _impl_ai_contracts
from . import ai_decisions, common
from . import ai_policy as _impl_ai_policy
from . import ai_settings as _impl_ai_settings
from . import ai_transport as _impl_ai_transport
from .ai_fact_stage import extract_facts
from .ai_summary_stage import write_summary
from .ai_writer_input import prepare_writer_input
from .identity import event_id
from .models import RawEvent, normalize_source_id


def enrich_event(
    event: RawEvent,
    *,
    settings: _impl_ai_transport.AISettings | None = None,
    client: _impl_ai_contracts.StructuredClient | None = None,
    now: datetime | None = None,
    configured_timeout_seconds: float | None = None,
    outcome: dict[str, bool] | None = None,
) -> RawEvent:
    """Enrich one target event, using a forever cache keyed by content/version."""
    if not _impl_ai_policy.is_target_event(event):
        return event
    source_material = _impl_ai_policy._source_material(event)
    original = dict(event)
    _impl_ai_policy.strip_restricted_copy(event)
    # AI may legitimately fill blank time/venue/city fields. URLs are already
    # a public contract, so capture the pre-AI occurrence identity and carry it
    # through the canonical pipeline instead of hashing generated metadata.
    event["preserved_event_id"] = event_id(original)
    configured = settings or _impl_ai_settings.settings_from_env()
    if not configured.enabled or not configured.api_key or not source_material:
        return _impl_ai_cache._reuse_cached_success(event, configured)
    current_time = now or _impl_ai_cache._utc_now()
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)
    payload = _impl_ai_policy._input_payload(original, source_material)
    digest = _impl_ai_policy._input_hash(payload)
    key = event_id(original)
    source_id = normalize_source_id(original.get("source_id") or original.get("source"))
    api = client or (
        _impl_ai_transport.OpenRouterClient(configured)
        if configured.provider == "openrouter"
        else _impl_ai_transport.ResponsesClient(configured)
    )

    cache_key = "\0".join((key, digest, _impl_ai_cache.cache_pipeline_version(configured)))
    with _impl_ai_cache._locked_database(configured.cache_db, cache_key=cache_key) as connection:
        row = _impl_ai_cache._ensure_row(
            connection, event_key=key, digest=digest, source_id=source_id,
            settings=configured, now=current_time,
        )
        row = _impl_ai_cache._reuse_compatible_facts(
            connection, row, settings=configured, now=current_time,
        )
        previous_failure = str(row["last_error"] or "")
        row = _impl_ai_cache._refresh_failed_facts(connection, row, payload, current_time)
        row = _impl_ai_cache._reset_expired_failure_window(connection, row, current_time)
        negative_until = _impl_ai_cache._parse_timestamp(row["negative_until"])
        if negative_until and negative_until > current_time:
            if outcome is not None and row["last_error"] and not row["stage2_json"]:
                outcome["failed"] = True
            return _impl_ai_cache._reuse_cached_success(event, configured)
        facts: dict[str, Any] | None = None
        if row["stage1_json"]:
            try:
                cached_facts = json.loads(row["stage1_json"])
                facts = cached_facts if isinstance(cached_facts, dict) else None
            except json.JSONDecodeError:
                facts = None
        if row["stage2_json"]:
            try:
                cached_result = json.loads(row["stage2_json"])
            except json.JSONDecodeError:
                return event
            if not isinstance(cached_result, dict):
                return event
            if (
                facts is not None
                and _impl_ai_policy._calendar_occurrence_overrides_non_event(facts, original, source_id)
                and not cached_result.get("ai_summary")
            ):
                connection.execute(
                    """UPDATE ai_event_enrichment
                       SET stage2_json = '', stage2_attempts = 0,
                           negative_until = '', last_error = '', updated_at = ?
                       WHERE event_key = ? AND input_hash = ? AND pipeline_version = ?""",
                    (
                        _impl_ai_cache._timestamp(current_time), row["event_key"], row["input_hash"],
                        row["pipeline_version"],
                    ),
                )
                connection.commit()
                row = connection.execute(
                    """SELECT * FROM ai_event_enrichment
                       WHERE event_key = ? AND input_hash = ? AND pipeline_version = ?""",
                    (row["event_key"], row["input_hash"], row["pipeline_version"]),
                ).fetchone()
            else:
                try:
                    return _impl_ai_policy._apply_result(event, cached_result)
                except (TypeError, ValueError):
                    return event
        routing = None
        jev_active = configured.jev_enabled and bool(configured.jev_api_key)
        locked_category = (
            str(original["category_key"])
            if original.get("category_key") not in {None, "", "other"}
            and _impl_ai_policy._confidence(original.get("category_confidence")) >= 0.75
            else None
        )
        if jev_active:
            routing = ai_decisions.route(
                connection, payload, model=configured.jev_model, api_key=configured.jev_api_key,
                timeout_seconds=min(15.0, configured.timeout_seconds),
                structured_only=" ".join(source_material.split()) == " ".join(
                    _impl_ai_policy._source_material({**original, "description": "", "description_html": ""}).split()
                ),
                locked_category=locked_category, facts_known=facts is not None,
            )
            if routing:
                decision_usage = routing["usage"]
                # Account for Jev even when the decision falls back to extraction.
                connection.execute(
                    """UPDATE ai_event_enrichment SET input_tokens = input_tokens + ?,
                       output_tokens = output_tokens + ?, cost_usd = cost_usd + ?
                       WHERE event_key = ? AND input_hash = ? AND pipeline_version = ?""",
                    (decision_usage.get("input_tokens", 0), decision_usage.get("output_tokens", 0),
                     decision_usage.get("cost", 0), row["event_key"], row["input_hash"], row["pipeline_version"]),
                )
                connection.commit()
                if routing["facts"] is not None:
                    facts = _impl_ai_policy._sanitize_extracted_facts(routing["facts"], payload)
                    facts["_jev"] = routing["metadata"]
                    row = _impl_ai_cache._record_success(connection, row, stage=1, payload=facts,
                                                        usage=_impl_ai_contracts.Usage(), now=current_time)
                elif facts is not None:
                    facts["_jev"] = routing["metadata"]
        fact_stage = extract_facts(
            connection, row, facts=facts, payload=payload, api=api, configured=configured,
            current_time=current_time, routing=routing,
            configured_timeout_seconds=configured_timeout_seconds,
        )
        facts, row = fact_stage.facts, fact_stage.row
        if facts is None:
            if outcome is not None:
                outcome["failed"] = True
            return _impl_ai_cache._reuse_cached_success(event, configured)
        if _impl_ai_policy._calendar_occurrence_overrides_non_event(facts, original, source_id):
            facts = {
                **facts,
                "is_concrete_event": True,
                "event_evidence": (
                    "Der kanonische Kalenderdatensatz enthält einen konkreten Termin."
                ),
                "start_date": facts.get("start_date") or payload["start_date"] or None,
                "end_date": facts.get("end_date") or payload["end_date"] or None,
                "time": facts.get("time") or payload["time"] or None,
            }

        writer_input = prepare_writer_input(original, facts, payload)
        if facts.get("is_concrete_event") is False:
            non_event: dict[str, Any] = {key: None for key in _impl_ai_contracts._SUMMARY_SCHEMA["required"] if key != "ai_summary"}
            non_event["ai_summary"] = ""
            _impl_ai_cache._record_success(connection, row, stage=2, payload=non_event, usage=_impl_ai_contracts.Usage(), now=current_time)
            return event
        enriched = write_summary(
            connection, row, facts=facts, payload=payload, writer_input=writer_input,
            api=api, configured=configured, current_time=current_time,
            configured_timeout_seconds=configured_timeout_seconds,
            locked_category=locked_category, jev_active=jev_active,
            previous_failure=previous_failure, source_material=source_material,
            apply=lambda result: _impl_ai_policy._apply_result(event, result),
        )
        if enriched is not None:
            return enriched
        if outcome is not None:
            outcome["failed"] = True
        return _impl_ai_cache._reuse_cached_success(event, configured)


def enrich_events(
    events: list[Any],
    *,
    settings: _impl_ai_transport.AISettings | None = None,
    stats: dict[str, int] | None = None,
    stats_by_source: dict[str, dict[str, int]] | None = None,
) -> list[Any]:
    """Enrich only the configured target sources, with an optional pilot cap.

    Restricted sources publish no source prose, so an event this pass skips
    keeps only its master-data fallback. Report every skip through ``stats`` so
    a truncated batch surfaces as a source warning instead of silent thin pages.
    """
    configured = settings or _impl_ai_settings.settings_from_env()
    deadline = time.monotonic() + configured.batch_timeout_seconds
    maximum_calls_per_event = max(2 * configured.max_attempts, 1)
    # Routing shares 15 seconds; each nonfinal writer attempt may use one
    # bounded repair decision. Do not charge these short decisions a full
    # generative timeout and prematurely starve the remaining event batch.
    decision_reserve = 15.0 * configured.max_attempts if configured.jev_enabled and configured.jev_api_key else 0.0
    capped = 0
    capped_without_summary = 0
    expired = 0
    expired_without_summary = 0
    cache_budget_skipped = 0
    cache_budget_skipped_without_summary = 0
    enriched = list(events)
    candidates: list[tuple[int, RawEvent]] = []
    source_stats: dict[str, dict[str, int]] = {}

    def bump(source_id: str, key: str, value: int = 1) -> None:
        source = source_stats.setdefault(source_id, {})
        source[key] = source.get(key, 0) + value

    for index, value in enumerate(events):
        if not isinstance(value, dict) or not _impl_ai_policy.is_target_event(value):
            continue
        target = cast(RawEvent, value)
        source_id = normalize_source_id(target.get("source_id") or target.get("source"))
        source_stats.setdefault(source_id, {})
        try:
            in_window = common.event_in_window(target)
        except (AttributeError, TypeError):
            in_window = True
        if not in_window:
            enriched[index] = _impl_ai_policy.strip_restricted_copy(target)
            continue
        # A locally reviewed or otherwise already accepted summary is final.
        # Do not touch the cache or create a billable model request for it.
        if str(target.get("ai_summary") or "").strip():
            enriched[index] = _impl_ai_policy.strip_restricted_copy(cast(RawEvent, dict(target)))
            continue
        candidates.append((index, target))

    def ranking_value(value: Any) -> float:
        try:
            parsed = float(value or 0)
        except (OverflowError, TypeError, ValueError):
            return 0
        return parsed if math.isfinite(parsed) else 0

    cached_fallbacks = {
        index: _impl_ai_cache._reuse_cached_success(cast(RawEvent, dict(target)), configured)
        for index, target in candidates
    }

    def candidate_priority(item: tuple[int, RawEvent]) -> tuple[bool, int, float, str]:
        index, target = item
        today = common.runtime_window().start.date()
        try:
            start = datetime.fromisoformat(str(target.get("start_date") or target.get("date") or "")[:10]).date()
            end = datetime.fromisoformat(str(target.get("end_date") or target.get("start_date") or target.get("date") or "")[:10]).date()
            distance = 0 if start <= today <= end else max((start - today).days, 0)
        except ValueError:
            distance = 9999
        demand_score = ranking_value(target.get("priority_bonus")) + ranking_value(target.get("score"))
        stable_key = f"{target.get('source_id', '')}\n{target.get('title', '')}\n{target.get('start_date', '')}"
        has_cached_summary = bool(
            str(cached_fallbacks[index].get("ai_summary") or "").strip()
        )
        return has_cached_summary, distance, -demand_score, stable_key

    candidates.sort(key=candidate_priority)
    pending: list[tuple[int, RawEvent]] = []
    for position, (index, target) in enumerate(candidates):
        if configured.max_events and position >= configured.max_events:
            capped += 1
            cached = cached_fallbacks[index]
            enriched[index] = cached
            without_summary = int(not str(cached.get("ai_summary", "")).strip())
            capped_without_summary += without_summary
            source_id = normalize_source_id(target.get("source_id") or target.get("source"))
            bump(source_id, "ai_cap_skipped_event_count")
            bump(source_id, "ai_cap_skipped_without_summary_event_count", without_summary)
            continue
        pending.append((index, target))

    def enrich_one(item: tuple[int, RawEvent]) -> tuple[int, RawEvent, str, bool, bool, bool, bool, bool]:
        index, target = item
        source_id = normalize_source_id(target.get("source_id") or target.get("source"))
        remaining = deadline - time.monotonic()
        generative_budget = remaining - decision_reserve
        if generative_budget <= 0 or generative_budget / maximum_calls_per_event < 20:
            cached = _impl_ai_cache._reuse_cached_success(target, configured)
            return index, cached, source_id, True, not str(cached.get("ai_summary", "")).strip(), False, False, False
        # One event may need facts and summary retries. Divide the remaining
        # source budget across that worst case so every concurrently running
        # event still finishes within the shared wall-clock batch deadline.
        request_timeout = min(
            configured.timeout_seconds,
            generative_budget / maximum_calls_per_event,
        )
        try:
            outcome: dict[str, bool] = {}
            result = enrich_event(
                target,
                settings=replace(configured, timeout_seconds=request_timeout),
                configured_timeout_seconds=configured.timeout_seconds,
                outcome=outcome,
            )
            return index, result, source_id, False, False, False, False, outcome.get("failed", False)
        except _impl_ai_contracts.AICacheMissBudgetExceeded:
            cached = _impl_ai_cache._reuse_cached_success(target, configured)
            return (
                index, cached, source_id, False, False, True,
                not str(cached.get("ai_summary", "")).strip(),
                False,
            )

    if pending:
        with ThreadPoolExecutor(
            max_workers=min(configured.workers, len(pending)),
            thread_name_prefix="nrw-ai",
        ) as executor:
            # The worker returns its own skip flag rather than incrementing a
            # shared counter, so the tally stays correct without a lock.
            for (
                index,
                result,
                source_id,
                skipped,
                without_summary,
                budget_skipped,
                budget_without_summary,
                failed,
            ) in executor.map(enrich_one, pending):
                enriched[index] = result
                expired += int(skipped)
                expired_without_summary += int(without_summary)
                cache_budget_skipped += int(budget_skipped)
                cache_budget_skipped_without_summary += int(budget_without_summary)
                bump(source_id, "ai_deadline_skipped_event_count", int(skipped))
                bump(
                    source_id,
                    "ai_deadline_skipped_without_summary_event_count",
                    int(without_summary),
                )
                bump(source_id, "ai_cache_budget_skipped_event_count", int(budget_skipped))
                bump(
                    source_id,
                    "ai_cache_budget_skipped_without_summary_event_count",
                    int(budget_without_summary),
                )
                bump(source_id, "ai_failed_event_count", int(failed))
    if stats is not None:
        stats["ai_deadline_skipped_event_count"] = expired
        stats["ai_cap_skipped_event_count"] = capped
        stats["ai_deadline_skipped_without_summary_event_count"] = expired_without_summary
        stats["ai_cap_skipped_without_summary_event_count"] = capped_without_summary
        stats["ai_cache_budget_skipped_event_count"] = cache_budget_skipped
        stats[
            "ai_cache_budget_skipped_without_summary_event_count"
        ] = cache_budget_skipped_without_summary
    if stats_by_source is not None:
        stats_by_source.update(source_stats)
    return enriched
