"""Owning implementation of ai settings; core is a compatibility facade."""

from __future__ import annotations

import os
from pathlib import Path

from . import ai_contracts as _impl_ai_contracts
from . import ai_transport as _impl_ai_transport
from . import config


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    normalized = raw.strip().casefold()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off", ""}:
        return False
    raise ValueError(f"{name} must be a boolean (0/1, false/true), got {raw!r}")


def _env_reasoning_effort(name: str, default: str = "none") -> str:
    effort = os.environ.get(name, default).strip().casefold() or default
    allowed = {"none", "minimal", "low", "medium", "high", "xhigh", "max"}
    if effort not in allowed:
        raise ValueError(f"{name} must be one of {', '.join(sorted(allowed))}, got {effort!r}")
    return effort


def settings_from_env() -> _impl_ai_transport.AISettings:
    """Load bounded AI settings without ever logging the API key."""
    cache_dir = os.environ.get("NRW_EVENTS_CACHE_DIR", "").strip()
    default_cache = (
        Path(cache_dir).expanduser() / "ai-enrichment.sqlite3"
        if cache_dir
        else config.default_state_dir() / "ai-enrichment.sqlite3"
    )
    cache_db = Path(os.environ.get("NRW_EVENTS_AI_CACHE_DB", str(default_cache))).expanduser()
    max_attempts = int(os.environ.get("NRW_EVENTS_AI_MAX_ATTEMPTS", "2"))
    negative_hours = float(os.environ.get("NRW_EVENTS_AI_NEGATIVE_CACHE_HOURS", "168"))
    timeout = float(os.environ.get("NRW_EVENTS_AI_TIMEOUT_SECONDS", "180"))
    batch_timeout = float(os.environ.get("NRW_EVENTS_AI_BATCH_TIMEOUT_SECONDS", "600"))
    workers = int(os.environ.get("NRW_EVENTS_AI_WORKERS", "8"))
    max_events = int(os.environ.get("NRW_EVENTS_AI_MAX_EVENTS", "0"))
    max_new_cache_rows_per_day = int(
        os.environ.get("NRW_EVENTS_AI_MAX_NEW_CACHE_ROWS_PER_DAY", "150")
    )
    if not 1 <= max_attempts <= 5:
        raise ValueError("NRW_EVENTS_AI_MAX_ATTEMPTS must be between 1 and 5")
    if not 0 <= negative_hours <= 24 * 30:
        raise ValueError("NRW_EVENTS_AI_NEGATIVE_CACHE_HOURS must be between 0 and 720")
    if not 5 <= timeout <= 300:
        raise ValueError("NRW_EVENTS_AI_TIMEOUT_SECONDS must be between 5 and 300")
    if not 5 <= batch_timeout <= 3_600:
        raise ValueError("NRW_EVENTS_AI_BATCH_TIMEOUT_SECONDS must be between 5 and 3600")
    if not 1 <= workers <= 16:
        raise ValueError("NRW_EVENTS_AI_WORKERS must be between 1 and 16")
    if not 0 <= max_events <= 100_000:
        raise ValueError("NRW_EVENTS_AI_MAX_EVENTS must be between 0 and 100000")
    if not 0 <= max_new_cache_rows_per_day <= 100_000:
        raise ValueError(
            "NRW_EVENTS_AI_MAX_NEW_CACHE_ROWS_PER_DAY must be between 0 and 100000"
        )
    provider = os.environ.get("NRW_EVENTS_AI_PROVIDER", "openai").strip().casefold()
    if provider not in {"openai", "openrouter"}:
        raise ValueError("NRW_EVENTS_AI_PROVIDER must be openai or openrouter")
    default_model = _impl_ai_contracts.DEFAULT_OPENROUTER_MODEL if provider == "openrouter" else _impl_ai_contracts.DEFAULT_MODEL
    key_name = "OPENROUTER_API_KEY" if provider == "openrouter" else "OPENAI_API_KEY"
    return _impl_ai_transport.AISettings(
        enabled=_env_bool("NRW_EVENTS_AI_ENRICHMENT", True),
        api_key=os.environ.get(key_name, "").strip(),
        model=os.environ.get("NRW_EVENTS_AI_MODEL", default_model).strip() or default_model,
        cache_db=cache_db,
        provider=provider,
        max_attempts=max_attempts,
        negative_cache_hours=negative_hours,
        timeout_seconds=timeout,
        batch_timeout_seconds=batch_timeout,
        workers=workers,
        max_events=max_events,
        max_new_cache_rows_per_day=max_new_cache_rows_per_day,
        facts_reasoning_effort=_env_reasoning_effort("NRW_EVENTS_AI_FACTS_REASONING_EFFORT"),
        summary_reasoning_effort=_env_reasoning_effort(
            "NRW_EVENTS_AI_SUMMARY_REASONING_EFFORT",
            "low" if provider == "openrouter" else "none",
        ),
        allow_data_collection=_env_bool("NRW_EVENTS_AI_ALLOW_DATA_COLLECTION", False),
    )
