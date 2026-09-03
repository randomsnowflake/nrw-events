"""Optional, exact-match reviewed summaries applied before billable AI."""

from __future__ import annotations

import json
import os
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

from .health import diagnostic_warning
from .identity import event_id
from .models import CanonicalEvent, normalize_source_id

ENV_NAME = "NRW_EVENTS_REVIEWED_AI_SUMMARIES_PATH"
_ROOT_KEYS = {"version", "rules"}
_RULE_KEYS = {"id", "match", "evidence", "set"}
_MATCH_KEYS = {"source_id", "title", "event_ids", "start_dates", "links", "times"}
_EVIDENCE_KEYS = {"verdict"}


def _matches(event: CanonicalEvent, match: dict[str, Any]) -> bool:
    required = {
        "source_id": event.source_id,
        "title": event.title,
    }
    if any(str(match.get(key) or "") != value for key, value in required.items()):
        return False
    lists = {
        "event_ids": event_id(event),
        "start_dates": event.start_date,
        "links": event.link,
        "times": event.time,
    }
    for key, value in lists.items():
        allowed = match.get(key)
        if key in {"event_ids", "start_dates"} and not isinstance(allowed, list):
            return False
        if allowed and (
            not isinstance(allowed, list)
            or not value
            or value not in {str(item) for item in allowed}
        ):
            return False
    return True


def apply_reviewed_summaries(
    events: Sequence[CanonicalEvent], path_value: str | None = None,
) -> tuple[list[CanonicalEvent], list[dict[str, Any]]]:
    """Apply unambiguous content-reviewed summaries; fail on malformed input."""
    raw_path = path_value if path_value is not None else os.environ.get(ENV_NAME, "")
    if not raw_path.strip():
        return list(events), []
    path = Path(raw_path).expanduser()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid reviewed AI summary JSON: {exc}") from exc
    if (
        not isinstance(payload, dict)
        or type(payload.get("version")) is not int
        or payload["version"] != 1
    ):
        raise ValueError("reviewed AI summary manifest version must be 1")
    if unknown := set(payload) - _ROOT_KEYS:
        raise ValueError(
            f"reviewed AI summary manifest has unknown keys: {sorted(unknown)}"
        )
    rules = payload.get("rules")
    if not isinstance(rules, list):
        raise ValueError("reviewed AI summary manifest must contain a rules list")

    reviewed: list[tuple[str, dict[str, Any], str]] = []
    rule_ids: set[str] = set()
    for rule in rules:
        if not isinstance(rule, dict):
            raise ValueError("every reviewed AI summary rule must be an object")
        if unknown := set(rule) - _RULE_KEYS:
            raise ValueError(
                f"reviewed AI summary rule has unknown keys: {sorted(unknown)}"
            )
        rule_id = rule.get("id")
        if not isinstance(rule_id, str) or not rule_id or rule_id in rule_ids:
            raise ValueError("reviewed AI summary rule IDs must be unique non-empty strings")
        rule_ids.add(rule_id)
        evidence = rule.get("evidence")
        updates = rule.get("set")
        match = rule.get("match")
        if isinstance(match, dict) and (unknown := set(match) - _MATCH_KEYS):
            raise ValueError(
                f"reviewed AI summary rule {rule_id!r} match has unknown keys: "
                f"{sorted(unknown)}"
            )
        if isinstance(evidence, dict) and (
            unknown := set(evidence) - _EVIDENCE_KEYS
        ):
            raise ValueError(
                f"reviewed AI summary rule {rule_id!r} evidence has unknown keys: "
                f"{sorted(unknown)}"
            )
        summary = updates.get("ai_summary") if isinstance(updates, dict) else None
        valid_lists = all(
            isinstance(match.get(key), list)
            and bool(match[key])
            and all(isinstance(value, str) and value for value in match[key])
            for key in ("event_ids", "start_dates")
        ) if isinstance(match, dict) else False
        optional_lists = all(
            key not in match
            or (
                isinstance(match[key], list)
                and all(isinstance(value, str) and value for value in match[key])
            )
            for key in ("links", "times")
        ) if isinstance(match, dict) else False
        if not (
            isinstance(evidence, dict)
            and evidence.get("verdict") == "content_reviewed"
            and isinstance(updates, dict)
            and set(updates) == {"ai_summary"}
            and isinstance(match, dict)
            and isinstance(match.get("source_id"), str)
            and normalize_source_id(match["source_id"]) == match["source_id"]
            and isinstance(match.get("title"), str)
            and bool(match["title"])
            and valid_lists
            and optional_lists
            and isinstance(summary, str)
            and summary.strip()
            and len(summary.strip()) <= 4000
        ):
            raise ValueError(
                f"reviewed AI summary rule {rule_id!r} is malformed"
            )
        reviewed.append((rule_id, match, summary.strip()))

    output: list[CanonicalEvent] = []
    warnings: list[dict[str, Any]] = []
    for event in events:
        matches = [
            (rule_id, summary)
            for rule_id, match, summary in reviewed
            if _matches(event, match)
        ]
        if len(matches) == 1:
            output.append(replace(
                event,
                ai_summary=matches[0][1],
            ))
        else:
            output.append(event)
            if len(matches) > 1:
                warnings.append(diagnostic_warning(
                    event.source,
                    "ReviewedSummaryAmbiguityWarning",
                    f"multiple reviewed summaries matched event {event_id(event)}; none applied",
                    source_id=event.source_id,
                ))
    return output, warnings