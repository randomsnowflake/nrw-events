"""Immutable per-import runtime dependencies."""

from __future__ import annotations

import logging
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

if TYPE_CHECKING:
    from .category_taxonomy import CategoryResult

from .config import RuntimeConfig

LOCAL_TIMEZONE = ZoneInfo("Europe/Berlin")


def local_now() -> datetime:
    return datetime.now(LOCAL_TIMEZONE)


@dataclass(frozen=True, slots=True)
class EventWindow:
    start: datetime
    end: datetime

    @classmethod
    def from_days(cls, days_ahead: int, now: datetime | None = None) -> EventWindow:
        current = now or datetime.now(LOCAL_TIMEZONE)
        if current.tzinfo is not None:
            current = current.astimezone(LOCAL_TIMEZONE).replace(tzinfo=None)
        start = current.replace(hour=0, minute=0, second=0, microsecond=0)
        return cls(start=start, end=start + timedelta(days=max(days_ahead - 1, 0)))


@dataclass(frozen=True, slots=True)
class RunContext:
    settings: RuntimeConfig
    window: EventWindow
    run_id: str
    logger: logging.Logger
    clock: Callable[[], datetime] = local_now


@dataclass(frozen=True, slots=True)
class RuntimeState:
    """Dependencies inherited by worker contexts and reset with one token."""
    settings: RuntimeConfig
    run_id: str
    logger: logging.Logger
    window: EventWindow | None = None
    category_cache: dict[str, CategoryResult] = field(default_factory=dict)


ACTIVE_RUNTIME: ContextVar[RuntimeState | None] = ContextVar('nrw_events_runtime', default=None)
