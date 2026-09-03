"""Stable public identifiers for a single event occurrence.

The website turns ``event_id`` into a permanent URL, so the value must survive
everything that legitimately changes about an event between two imports:

* feed order (an id derived from a list index is not an id),
* which source won deduplication for this occurrence,
* description, price, admission, category or link enrichment.

It must at the same time stay *different* for anything that is a different
occurrence — most importantly the individual dates and start times of a series.
The identity tuple below is therefore deliberately small: what a visitor would
name to say which event they mean.

``source_id`` identifies a *source*, not an event, and never takes part.

The website reimplements this contract in TypeScript for events that never pass
through this importer (user submissions). Both implementations are pinned to the
same golden vectors in ``tests/data/event_id_vectors.json``; keep the two in
step whenever this module changes.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping, Sequence
from hashlib import sha256
from typing import Any

from . import performance
from .normalization import comparison_text

#: Length of the readable title segment inside an id. Long enough to stay
#: recognisable when shared, short enough to keep URLs manageable.
TITLE_SLUG_LIMIT = 56

#: Plain truncated SHA-256 rather than a parameterized digest, so the website's
#: TypeScript implementation of this same contract can reproduce it exactly.
DIGEST_LENGTH = 10

#: Field separator inside the hashed payload. A newline cannot occur in any of
#: the normalized parts, so no two different tuples can serialize alike.
_FIELD_SEPARATOR = "\n"


def _venue_key(event: Mapping[str, Any]) -> str:
    """Return the strongest available venue identity for this occurrence."""
    published_name = str(event.get("identity_venue") or "").strip()
    if published_name or event.get("identity_venue_locked"):
        return comparison_text(published_name)
    registry_id = str(event.get("venue_id") or "").strip()
    return registry_id or comparison_text(str(event.get("venue") or ""))


#: Leading clock time of a ``time`` field. Only the start may define identity:
#: ``time`` frequently carries a range, because ``rc.time_text`` emits
#: ``08:00–14:00`` as soon as a listing names two clock times. An end time that
#: appears, disappears or is reformatted upstream must not move a published URL.
_START_TIME_RE = re.compile(r"\s*(\d{1,2}):(\d{2})")


def _time_key(event: Mapping[str, Any]) -> str:
    """Return the start-time identity, distinguishing the dates of a series."""
    published_time = str(event.get("identity_time") or "").strip()
    if published_time or event.get("identity_time_locked"):
        return _time_value_key(published_time)
    return _time_value_key(str(event.get("time") or event.get("start_at") or ""))


def _time_value_key(value: str) -> str:
    start = _START_TIME_RE.match(value)
    if start:
        return f"{int(start.group(1)):02d}:{start.group(2)}"
    start_at = value.strip()
    # ``start_at`` carries the same clock time as ``time`` when both exist; only
    # its time-of-day part matters here because the date is a separate field.
    return start_at[11:16] if len(start_at) >= 16 else "all-day"


def identity_tuple(event: Mapping[str, Any]) -> tuple[str, ...]:
    """Return the normalized fields that define one occurrence."""
    return (
        comparison_text(str(event.get("title") or "")),
        str(event.get("start_date") or event.get("date") or "").strip(),
        _time_key(event),
        _venue_key(event),
        comparison_text(str(event.get("city") or "")),
    )


def _digest(parts: Sequence[str]) -> str:
    payload = _FIELD_SEPARATOR.join(parts).encode("utf-8")
    return sha256(payload).hexdigest()[:DIGEST_LENGTH]


def content_fingerprint(event: Mapping[str, Any]) -> str:
    """Return a stable hash over the whole record, for tie-breaking only.

    Used exclusively to order colliding occurrences deterministically. It must
    never feed the id itself: it changes whenever any field is enriched.
    """
    payload = json.dumps(
        {
            key: event[key]
            for key in sorted(event)
            if key not in {"event_id", "preserved_event_id", "content_hash", "first_seen_at"}
        },
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    return sha256(payload.encode("utf-8")).hexdigest()


@performance.measured("identity.content_hash")
def content_hash(event: Mapping[str, Any]) -> str:
    """Return the public change-detection hash for an occurrence."""
    return content_fingerprint(event)


def event_id(event: Mapping[str, Any]) -> str:
    """Return the stable, readable id for one event occurrence.

    The occurrence date remains part of the digest so separate dates never
    collide, but it is deliberately absent from the readable URL segment.
    """
    preserved = str(event.get("preserved_event_id") or "").strip()
    if preserved:
        return preserved
    parts = identity_tuple(event)
    title_slug = comparison_text(str(event.get("title") or ""), separator="-")[:TITLE_SLUG_LIMIT].strip("-")
    segments = [segment for segment in (title_slug, _digest(parts)) if segment]
    return "-".join(segments)


def assign_event_ids(events: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Return the events with a unique ``event_id`` on every record.

    Two records sharing an identity tuple are the same occurrence and should
    have been merged by deduplication. When one slips through anyway — or in the
    astronomically unlikely case of a digest collision — the duplicates are
    suffixed in content order, so the outcome stays independent of feed order.
    """
    assigned = [{**event, "event_id": event_id(event)} for event in events]
    for record in assigned:
        record["previous_event_ids"] = [
            identifier for identifier in dict.fromkeys(record.get("previous_event_ids") or [])
            if identifier and identifier != record["event_id"]
        ][:20]
    by_id: dict[str, list[dict[str, Any]]] = {}
    for record in assigned:
        by_id.setdefault(record["event_id"], []).append(record)
    for base_id, group in by_id.items():
        if len(group) < 2:
            continue
        # Mutable prose, category and ranking fields must not exchange suffixes
        # after enrichment. Source identity plus its stable URL is the narrowest
        # deterministic discriminator available for accidental collisions.
        ordered = sorted(
            group,
            key=lambda record: (
                str(record.get("source_id") or record.get("source") or ""),
                str(record.get("link") or ""),
                str(record.get("organizer") or ""),
            ),
        )
        for position, record in enumerate(ordered):
            if position:
                record["event_id"] = f"{base_id}-{position + 1}"
    for record in assigned:
        record.pop("identity_venue", None)
        record.pop("identity_venue_locked", None)
        record.pop("identity_time", None)
        record.pop("identity_time_locked", None)
        record.pop("preserved_event_id", None)
    return assigned


def duplicate_event_ids(events: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    """Return every id used more than once, mapped to its number of uses."""
    counts: dict[str, int] = {}
    for event in events:
        identifier = str(event.get("event_id") or "")
        counts[identifier] = counts.get(identifier, 0) + 1
    return {identifier: count for identifier, count in counts.items() if count > 1}
