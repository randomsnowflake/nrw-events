"""Summary generation, validation and retry accounting with injected application."""
from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import datetime
from typing import Any

from . import ai_cache as _impl_ai_cache
from . import ai_contracts as _impl_ai_contracts
from . import ai_policy as _impl_ai_policy
from . import ai_summary_repair
from . import ai_transport as _impl_ai_transport
from .ai_writer_input import WriterInput
from .models import RawEvent


def write_summary(
    connection: sqlite3.Connection, row: sqlite3.Row, *,
    facts: dict[str, Any], payload: dict[str, Any], writer_input: WriterInput,
    api: _impl_ai_contracts.StructuredClient, configured: _impl_ai_transport.AISettings,
    current_time: datetime, configured_timeout_seconds: float | None,
    locked_category: str | None, jev_active: bool, previous_failure: str,
    source_material: str, apply: Callable[[dict[str, Any]], RawEvent],
) -> RawEvent | None:
    writer_facts = writer_input.facts
    stage2_payload = writer_input.payload
    decision_category = locked_category or ((facts.get("_jev") or {}).get("category") if jev_active else None)
    # Metadata is already rendered deterministically by _clean_summary_result.
    # Jev owns classification when enabled; uncertainty keeps the existing
    # deterministic category instead of asking the writer to guess again.
    writer_fields = {"ai_summary"}
    if not jev_active and not decision_category:
        writer_fields.add("category_key")
    summary_schema = {
        **_impl_ai_contracts._SUMMARY_SCHEMA,
        "properties": {key: value for key, value in _impl_ai_contracts._SUMMARY_SCHEMA["properties"].items() if key in writer_fields},
        "required": sorted(writer_fields),
    }
    summary_prompt = _impl_ai_contracts._SUMMARY_PROMPT.replace(
        "Setze die übrigen Felder nur, wenn die Fakten sie eindeutig tragen; andernfalls null.",
        "Gib ausschließlich die im Ausgabeschema geforderten Felder zurück.")
    if "category_key" not in writer_fields:
        stage2_payload["field_policy"].pop("category_taxonomy", None)
        summary_prompt = summary_prompt.replace(
            "Ordne nach der Hauptaktivität ein: Wanderungen und Führungen sind\noutdoor; nightlife ist für Partys und Clubs, nicht für eine Zielgruppe wie Singles; Live-Musik ist concert.", "")
    quality_feedback = previous_failure if previous_failure.startswith("summary ") else ""
    while row["stage2_attempts"] < configured.max_attempts:
        usage = _impl_ai_contracts.Usage()
        try:
            request_payload: dict[str, Any] = dict(stage2_payload)
            if quality_feedback:
                retry_detail = {
                    "summary mentions a date outside the selected event": " Nenne keine Eröffnungs-, Abschluss- oder sonstigen Fremdtermine. Beschreibe nur den Inhalt dieses Termins.",
                    "summary contains a clock time absent from the facts": " Lass Uhrzeiten im Beschreibungstext weg; sie werden separat angezeigt.",
                    "summary contains sponsor or cooperation copy": " Lass Förderer, Sponsoren und Kooperationen vollständig weg.",
                    "summary repeats a long source phrase": " Formuliere kürzer und eigenständig; übernimm keine langen Formulierungen aus den Faktenlisten.",
                    "summary contradicts the source location": " Verwende ausschließlich den Veranstaltungsort aus facts; lass andere Ortsangaben weg.",
                    "summary invents registration information": " Lass Anmelde- und Reservierungsangaben im Beschreibungstext weg.",
                    "summary contains promotional language": " Schreibe kurze sachliche Sätze ohne Einladung, Empfehlung oder Wertung.",
                }.get(quality_feedback, "")
                if quality_feedback == "summary invents a target group":
                    retry_detail = (
                        " Das Feld target_group ist leer. Formuliere die Altersangabe neutral, "
                        "zum Beispiel als 'Teilnahme ab 8 Jahren', und nicht als Zielgruppe."
                    )
                request_payload["retry_instruction"] = (
                    "Der vorige Text wurde von der lokalen Qualitätsprüfung abgelehnt: "
                    f"{quality_feedback}.{retry_detail} Schreibe vollständig neu und vermeide diesen Fehler."
                )
            result, usage = api.structured(
                stage="summary", system=summary_prompt, payload=request_payload,
                schema=summary_schema, attempt=row["stage2_attempts"] + 1,
            )
            result = {key: value for key, value in result.items() if key in writer_fields}
            result["category_key"] = decision_category or result.get("category_key")
            result = _impl_ai_policy._clean_summary_result(
                result,
                admission_conflict=bool(stage2_payload["field_policy"]["admission_conflict"]),
                facts=facts,
            )
            quality_facts = {
                **writer_facts,
                "_publication_start": payload["start_date"],
                "_publication_end": payload["end_date"] or payload["start_date"],
            }
            quality_error = _impl_ai_policy._summary_quality(result.get("ai_summary"), source_material, quality_facts)
            if quality_error and jev_active and row["stage2_attempts"] + 1 < configured.max_attempts:
                repaired = ai_summary_repair.repair(
                    connection, summary=result.get("ai_summary") or "", error=quality_error, facts=quality_facts,
                    model=configured.jev_model, api_key=configured.jev_api_key,
                    timeout_seconds=min(15.0, configured.timeout_seconds),
                )
                used = repaired["usage"]
                usage = _impl_ai_contracts.Usage(
                    input_tokens=usage.input_tokens + used.get("input_tokens", 0),
                    cached_input_tokens=usage.cached_input_tokens,
                    output_tokens=usage.output_tokens + used.get("output_tokens", 0),
                    cost_usd=usage.cost_usd + used.get("cost", 0),
                )
                if repaired["summary"] and not _impl_ai_policy._summary_quality(repaired["summary"], source_material, quality_facts):
                    result["ai_summary"] = repaired["summary"]
                    result["_jev_repair"] = repaired["metadata"]
                    quality_error = ""
            if quality_error:
                quality_feedback = quality_error
                raise _impl_ai_contracts.AIEnrichmentError(quality_error)
            row = _impl_ai_cache._record_success(connection, row, stage=2, payload=result, usage=usage, now=current_time)
            return apply(result)
        except Exception as exc:
            if isinstance(exc, _impl_ai_contracts.AIEnrichmentError) and isinstance(exc.usage, _impl_ai_contracts.Usage):
                usage = exc.usage
            safe_error = (
                exc
                if isinstance(exc, _impl_ai_contracts.AIEnrichmentError)
                else _impl_ai_contracts.AIEnrichmentError(type(exc).__name__, transient=True)
            )
            terminal = row["stage2_attempts"] + 1 >= configured.max_attempts
            if (
                terminal
                and configured_timeout_seconds is not None
                and configured.timeout_seconds < configured_timeout_seconds
                and (
                    isinstance(exc, TimeoutError)
                    or "TimeoutError" in str(safe_error)
                    or "wall-clock deadline" in str(safe_error)
                )
            ):
                terminal = False
            row = _impl_ai_cache._record_failure(
                connection, row, stage=2, error=safe_error, usage=usage,
                settings=configured, now=current_time, terminal=terminal,
            )
            if row["stage2_attempts"] < configured.max_attempts:
                _impl_ai_transport._sleep_before_ai_retry(safe_error, row["stage2_attempts"] - 1, configured)
    return None
