"""Owning implementation of ai cache; core is a compatibility facade."""

from __future__ import annotations

import fcntl
import json
import sqlite3
import threading
import weakref
from collections.abc import Iterator, Mapping
from contextlib import closing, contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from . import ai_contracts as _impl_ai_contracts
from . import ai_policy as _impl_ai_policy
from . import ai_transport as _impl_ai_transport
from .identity import event_id
from .models import RawEvent, normalize_source_id

_EVENT_LOCKS_GUARD = threading.Lock()


_EVENT_LOCKS: weakref.WeakValueDictionary[str, threading.Lock] = weakref.WeakValueDictionary()


_DATABASE_SCHEMA_GUARD = threading.Lock()


_INITIALIZED_DATABASES: set[Path] = set()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


def _parse_timestamp(value: object) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value or ""))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _event_lock(cache_key: str) -> threading.Lock:
    with _EVENT_LOCKS_GUARD:
        lock = _EVENT_LOCKS.get(cache_key)
        if lock is None:
            lock = threading.Lock()
            _EVENT_LOCKS[cache_key] = lock
        return lock


@contextmanager
def _locked_database(path: Path, *, cache_key: str) -> Iterator[sqlite3.Connection]:
    path.parent.mkdir(parents=True, exist_ok=True)
    normalized_path = path.resolve()
    lock_path = path.with_suffix(path.suffix + ".lock")
    connection = sqlite3.connect(path, timeout=30)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA busy_timeout = 30000")
        # Schema setup and journal-mode negotiation are database-wide work.
        # Doing them for every cache hit serialized hundreds of otherwise cheap
        # lookups behind the flock. Initialize once per database and process;
        # a separate importer process still takes the cross-process lock once.
        with _DATABASE_SCHEMA_GUARD:
            if normalized_path not in _INITIALIZED_DATABASES:
                with lock_path.open("a+", encoding="utf-8") as lock:
                    fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
                    try:
                        connection.execute("PRAGMA journal_mode = WAL")
                        connection.execute(
                            """
                            CREATE TABLE IF NOT EXISTS ai_event_enrichment (
                                event_key TEXT NOT NULL,
                                input_hash TEXT NOT NULL,
                                pipeline_version TEXT NOT NULL,
                                facts_pipeline_version TEXT NOT NULL DEFAULT '',
                                source_id TEXT NOT NULL,
                                model TEXT NOT NULL,
                                stage1_json TEXT NOT NULL DEFAULT '',
                                stage2_json TEXT NOT NULL DEFAULT '',
                                stage1_attempts INTEGER NOT NULL DEFAULT 0,
                                stage2_attempts INTEGER NOT NULL DEFAULT 0,
                                negative_until TEXT NOT NULL DEFAULT '',
                                last_error TEXT NOT NULL DEFAULT '',
                                input_tokens INTEGER NOT NULL DEFAULT 0,
                                cached_input_tokens INTEGER NOT NULL DEFAULT 0,
                                output_tokens INTEGER NOT NULL DEFAULT 0,
                                cost_usd REAL NOT NULL DEFAULT 0,
                                created_at TEXT NOT NULL,
                                updated_at TEXT NOT NULL,
                                PRIMARY KEY (event_key, input_hash, pipeline_version)
                            )
                            """
                        )
                        columns = {
                            row[1]
                            for row in connection.execute(
                                "PRAGMA table_info(ai_event_enrichment)"
                            )
                        }
                        if "cost_usd" not in columns:
                            connection.execute(
                                "ALTER TABLE ai_event_enrichment "
                                "ADD COLUMN cost_usd REAL NOT NULL DEFAULT 0"
                            )
                        if "facts_pipeline_version" not in columns:
                            connection.execute(
                                "ALTER TABLE ai_event_enrichment ADD COLUMN "
                                "facts_pipeline_version TEXT NOT NULL DEFAULT ''"
                            )
                        connection.execute(
                            "CREATE INDEX IF NOT EXISTS ai_event_enrichment_source_pipeline_event "
                            "ON ai_event_enrichment(source_id, pipeline_version, event_key)"
                        )
                        stale_before = _timestamp(
                            datetime.now(timezone.utc) - timedelta(days=90)
                        )
                        connection.execute(
                            "DELETE FROM ai_event_enrichment "
                            "WHERE stage2_json = '' AND updated_at < ?",
                            (stale_before,),
                        )
                        connection.commit()
                        _INITIALIZED_DATABASES.add(normalized_path)
                    finally:
                        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        # Keep one in-process owner for an identical cache key without holding
        # the cross-process cache flock during provider I/O. Different events
        # remain fully concurrent; other processes may race and safely re-use
        # the same content-addressed result.
        with _event_lock(cache_key):
            yield connection
    finally:
        connection.close()


def cache_pipeline_version(settings: _impl_ai_transport.AISettings) -> str:
    """Keep existing OpenAI cache keys stable while isolating other providers."""
    if settings.provider == "openai":
        return f"{_impl_ai_contracts.PIPELINE_VERSION}:{settings.model}"
    return (
        f"{_impl_ai_contracts.OPENROUTER_PIPELINE_VERSION}:{settings.provider}:{settings.model}:"
        f"facts-{settings.facts_reasoning_effort}:summary-{settings.summary_reasoning_effort}"
    )


def facts_cache_version(settings: _impl_ai_transport.AISettings) -> str:
    """Return the narrower compatibility key for reusable extracted facts."""
    return (
        f"{_impl_ai_contracts.FACTS_PIPELINE_VERSION}:{settings.provider}:{settings.model}:"
        f"facts-{settings.facts_reasoning_effort}"
    )


def _legacy_facts_pipeline_pattern(settings: _impl_ai_transport.AISettings) -> str:
    """Map the last combined namespaces to the first split facts version."""
    if _impl_ai_contracts.FACTS_PIPELINE_VERSION != _impl_ai_contracts._LEGACY_FACTS_PIPELINE_VERSION:
        return ""
    if settings.provider == "openai":
        return f"{_impl_ai_contracts._LEGACY_OPENAI_COMBINED_PIPELINE_VERSION}:{settings.model}"
    return (
        f"{_impl_ai_contracts._LEGACY_OPENROUTER_COMBINED_PIPELINE_VERSION}:{settings.provider}:{settings.model}:"
        f"facts-{settings.facts_reasoning_effort}:summary-%"
    )


def _ensure_row(connection: sqlite3.Connection, *, event_key: str, digest: str, source_id: str, settings: _impl_ai_transport.AISettings, now: datetime) -> sqlite3.Row:
    stamp = _timestamp(now)
    pipeline_version = cache_pipeline_version(settings)
    facts_version = facts_cache_version(settings)
    identity = (event_key, digest, pipeline_version)
    row = connection.execute(
        """SELECT * FROM ai_event_enrichment
           WHERE event_key = ? AND input_hash = ? AND pipeline_version = ?""",
        identity,
    ).fetchone()
    if row is None:
        # Different event locks can enter concurrently. Serialize the count and
        # insertion so the daily cost fuse cannot be exceeded by a cache storm.
        connection.execute("BEGIN IMMEDIATE")
        try:
            row = connection.execute(
                """SELECT * FROM ai_event_enrichment
                   WHERE event_key = ? AND input_hash = ? AND pipeline_version = ?""",
                identity,
            ).fetchone()
            if row is None and settings.max_new_cache_rows_per_day:
                day_start = now.astimezone(timezone.utc).date().isoformat()
                created_today = connection.execute(
                    """SELECT COUNT(*) FROM ai_event_enrichment
                       WHERE pipeline_version = ? AND created_at >= ?""",
                    (pipeline_version, f"{day_start}T00:00:00+00:00"),
                ).fetchone()[0]
                if created_today >= settings.max_new_cache_rows_per_day:
                    connection.rollback()
                    raise _impl_ai_contracts.AICacheMissBudgetExceeded(
                        "daily AI cache-miss budget reached "
                        f"({created_today}/{settings.max_new_cache_rows_per_day})"
                    )
            if row is None:
                connection.execute(
                    """
                    INSERT INTO ai_event_enrichment
                        (event_key, input_hash, pipeline_version, facts_pipeline_version,
                         source_id, model, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event_key, digest, pipeline_version, facts_version,
                        source_id, settings.model, stamp, stamp,
                    ),
                )
                connection.commit()
                row = connection.execute(
                    """SELECT * FROM ai_event_enrichment
                       WHERE event_key = ? AND input_hash = ? AND pipeline_version = ?""",
                    identity,
                ).fetchone()
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise
    if row is None:
        raise _impl_ai_contracts.AIEnrichmentError("AI cache row could not be created")
    if not row["facts_pipeline_version"]:
        # The current pre-split row was produced by exactly this facts code.
        # Label it lazily, without changing its summary cache identity.
        connection.execute(
            """UPDATE ai_event_enrichment SET facts_pipeline_version = ?
               WHERE event_key = ? AND input_hash = ? AND pipeline_version = ?""",
            (facts_version, *identity),
        )
        connection.commit()
        row = connection.execute(
            """SELECT * FROM ai_event_enrichment
               WHERE event_key = ? AND input_hash = ? AND pipeline_version = ?""",
            identity,
        ).fetchone()
    return row


def _reuse_compatible_facts(
    connection: sqlite3.Connection,
    row: sqlite3.Row,
    *,
    settings: _impl_ai_transport.AISettings,
    now: datetime,
) -> sqlite3.Row:
    """Seed a new summary namespace from a compatible successful facts row."""
    if row["stage1_json"]:
        return row
    cached = connection.execute(
        """SELECT stage1_json
             FROM ai_event_enrichment
            WHERE event_key = ? AND input_hash = ? AND source_id = ? AND model = ?
              AND (
                    facts_pipeline_version = ?
                    OR (facts_pipeline_version = '' AND pipeline_version LIKE ?)
                  )
              AND stage1_json != ''
              AND pipeline_version != ?
         ORDER BY updated_at DESC
            LIMIT 1""",
        (
            row["event_key"], row["input_hash"], row["source_id"], row["model"],
            facts_cache_version(settings), _legacy_facts_pipeline_pattern(settings),
            row["pipeline_version"],
        ),
    ).fetchone()
    if cached is None:
        return row
    try:
        facts = json.loads(cached["stage1_json"])
    except (TypeError, json.JSONDecodeError):
        return row
    if not isinstance(facts, dict):
        return row
    connection.execute(
        """UPDATE ai_event_enrichment
              SET stage1_json = ?, updated_at = ?
            WHERE event_key = ? AND input_hash = ? AND pipeline_version = ?""",
        (
            cached["stage1_json"], _timestamp(now), row["event_key"],
            row["input_hash"], row["pipeline_version"],
        ),
    )
    connection.commit()
    return connection.execute(
        """SELECT * FROM ai_event_enrichment
           WHERE event_key = ? AND input_hash = ? AND pipeline_version = ?""",
        (row["event_key"], row["input_hash"], row["pipeline_version"]),
    ).fetchone()


def _record_success(connection: sqlite3.Connection, row: sqlite3.Row, *, stage: int, payload: Mapping[str, Any], usage: _impl_ai_contracts.Usage, now: datetime) -> sqlite3.Row:
    field = "stage1_json" if stage == 1 else "stage2_json"
    attempts = "stage1_attempts" if stage == 1 else "stage2_attempts"
    connection.execute(
        f"""UPDATE ai_event_enrichment SET {field} = ?, {attempts} = {attempts} + 1,
            negative_until = '', last_error = '', input_tokens = input_tokens + ?,
            cached_input_tokens = cached_input_tokens + ?, output_tokens = output_tokens + ?,
            cost_usd = cost_usd + ?,
            updated_at = ? WHERE event_key = ? AND input_hash = ? AND pipeline_version = ?""",
        (
            json.dumps(payload, ensure_ascii=False, sort_keys=True),
            usage.input_tokens,
            usage.cached_input_tokens,
            usage.output_tokens,
            usage.cost_usd,
            _timestamp(now), row["event_key"], row["input_hash"], row["pipeline_version"],
        ),
    )
    connection.commit()
    return connection.execute(
        """SELECT * FROM ai_event_enrichment WHERE event_key = ? AND input_hash = ? AND pipeline_version = ?""",
        (row["event_key"], row["input_hash"], row["pipeline_version"]),
    ).fetchone()


def _record_failure(connection: sqlite3.Connection, row: sqlite3.Row, *, stage: int, error: Exception, usage: _impl_ai_contracts.Usage, settings: _impl_ai_transport.AISettings, now: datetime, terminal: bool) -> sqlite3.Row:
    attempts = "stage1_attempts" if stage == 1 else "stage2_attempts"
    negative_until = ""
    if terminal:
        if isinstance(error, _impl_ai_contracts.AIEnrichmentError) and error.transient:
            negative_until = _timestamp(
                now + timedelta(hours=_impl_ai_contracts._TRANSIENT_FAILURE_CACHE_HOURS)
            )
        else:
            negative_until = (
                _timestamp(now + timedelta(hours=settings.negative_cache_hours))
                if settings.negative_cache_hours > 0
                else "9999-12-31T23:59:59+00:00"
            )
    connection.execute(
        f"""UPDATE ai_event_enrichment SET {attempts} = {attempts} + 1,
            negative_until = ?, last_error = ?, input_tokens = input_tokens + ?,
            cached_input_tokens = cached_input_tokens + ?, output_tokens = output_tokens + ?,
            cost_usd = cost_usd + ?,
            updated_at = ? WHERE event_key = ? AND input_hash = ? AND pipeline_version = ?""",
        (
            negative_until, str(error)[:500], usage.input_tokens, usage.cached_input_tokens,
            usage.output_tokens, usage.cost_usd, _timestamp(now), row["event_key"], row["input_hash"],
            row["pipeline_version"],
        ),
    )
    connection.commit()
    return connection.execute(
        """SELECT * FROM ai_event_enrichment WHERE event_key = ? AND input_hash = ? AND pipeline_version = ?""",
        (row["event_key"], row["input_hash"], row["pipeline_version"]),
    ).fetchone()


def _reset_expired_failure_window(connection: sqlite3.Connection, row: sqlite3.Row, now: datetime) -> sqlite3.Row:
    negative_until = _parse_timestamp(row["negative_until"])
    if not negative_until or negative_until > now:
        return row
    stage = 2 if row["stage1_json"] else 1
    field = "stage2_attempts" if stage == 2 else "stage1_attempts"
    connection.execute(
        f"""UPDATE ai_event_enrichment SET {field} = 0, negative_until = '', last_error = '',
            updated_at = ? WHERE event_key = ? AND input_hash = ? AND pipeline_version = ?""",
        (_timestamp(now), row["event_key"], row["input_hash"], row["pipeline_version"]),
    )
    connection.commit()
    return connection.execute(
        """SELECT * FROM ai_event_enrichment WHERE event_key = ? AND input_hash = ? AND pipeline_version = ?""",
        (row["event_key"], row["input_hash"], row["pipeline_version"]),
    ).fetchone()


def _reuse_cached_success(event: RawEvent, settings: _impl_ai_transport.AISettings) -> RawEvent:
    """Use the latest unambiguous accepted summary when fresh AI is unavailable.

    Exact event ids are preferred. A cross-id fallback is allowed only when one
    historical event key has the same source, title, date bounds and time. This
    covers venue/detail enrichment changing the public identity hash without
    allowing one same-day performance to borrow another one's copy.
    """
    # Cache results may fill blank identity fields. Preserve the identity from
    # before applying them even when this helper is called directly by the
    # batch cap/deadline paths instead of through enrich_event().
    safe_event = _impl_ai_policy.strip_restricted_copy(event)
    safe_event["preserved_event_id"] = event_id(event)
    if not settings.cache_db.is_file():
        return safe_event
    source_id = normalize_source_id(event.get("source_id") or event.get("source"))
    current_key = event_id(event)
    pipeline_version = cache_pipeline_version(settings)
    try:
        with closing(sqlite3.connect(settings.cache_db, timeout=30)) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA busy_timeout = 30000")
            exact_rows = connection.execute(
                """SELECT event_key, stage1_json, stage2_json
                     FROM ai_event_enrichment
                    WHERE source_id = ? AND pipeline_version = ?
                      AND event_key = ? AND stage2_json != ''
                 ORDER BY updated_at DESC""",
                (source_id, pipeline_version, current_key),
            ).fetchall()
            for row in exact_rows:
                try:
                    result = json.loads(row["stage2_json"])
                except (TypeError, json.JSONDecodeError):
                    continue
                if isinstance(result, dict) and _impl_ai_policy._clean_nullable(result.get("ai_summary"), 4000):
                    return _impl_ai_policy._apply_result(safe_event, result)
            alias_rows = []
            if source_id in _impl_ai_contracts._BONN_CACHE_CONTINUITY_SOURCE_IDS:
                alias_rows = connection.execute(
                    """SELECT event_key, stage1_json, stage2_json
                         FROM ai_event_enrichment
                        WHERE source_id IN (?, ?) AND source_id != ?
                          AND pipeline_version = ? AND event_key = ?
                          AND stage2_json != ''
                     ORDER BY updated_at DESC""",
                    (
                        *_impl_ai_contracts._BONN_CACHE_CONTINUITY_SOURCE_IDS,
                        source_id,
                        pipeline_version,
                        current_key,
                    ),
                ).fetchall()
            for row in alias_rows:
                try:
                    result = json.loads(row["stage2_json"])
                    facts = json.loads(row["stage1_json"])
                except (TypeError, json.JSONDecodeError):
                    continue
                if (
                    isinstance(result, dict)
                    and _impl_ai_policy._clean_nullable(result.get("ai_summary"), 4000)
                    and isinstance(facts, dict)
                    and _impl_ai_policy._cached_occurrence_matches(event, facts, result)
                ):
                    return _impl_ai_policy._apply_result(safe_event, result)
            cross_rows = connection.execute(
                """SELECT event_key, stage1_json, stage2_json
                     FROM ai_event_enrichment
                    WHERE source_id = ? AND pipeline_version = ?
                      AND event_key != ? AND stage2_json != ''
                      AND CASE WHEN json_valid(stage1_json)
                               THEN json_extract(stage1_json, '$.title') END = ?
                      AND CASE WHEN json_valid(stage1_json)
                               THEN json_extract(stage1_json, '$.start_date') END = ?
                 ORDER BY updated_at DESC""",
                (
                    source_id,
                    pipeline_version,
                    current_key,
                    str(event.get("title") or ""),
                    str(event.get("start_date") or event.get("date") or ""),
                ),
            ).fetchall()
    except sqlite3.Error:
        return safe_event

    cross_key_matches: dict[str, Mapping[str, Any]] = {}
    for row in cross_rows:
        try:
            result = json.loads(row["stage2_json"])
            facts = json.loads(row["stage1_json"])
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(result, dict) or not _impl_ai_policy._clean_nullable(result.get("ai_summary"), 4000):
            continue
        if isinstance(facts, dict) and _impl_ai_policy._cached_occurrence_matches(event, facts, result):
            key_identity = _impl_ai_policy._historical_event_key_identity(row["event_key"])
            if key_identity not in cross_key_matches:
                cross_key_matches[key_identity] = result
    if len(cross_key_matches) == 1:
        return _impl_ai_policy._apply_result(safe_event, next(iter(cross_key_matches.values())))
    return safe_event
