"""Immutable per-import runtime dependencies."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable
from zoneinfo import ZoneInfo

from .config import RuntimeConfig


LOCAL_TIMEZONE = ZoneInfo("Europe/Berlin")


@dataclass(frozen=True, slots=True)
class EventWindow:
    start: datetime
    end: datetime

    @classmethod
    def from_days(cls, days_ahead: int, now: datetime | None = None) -> "EventWindow":
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
    clock: Callable[[], datetime] = datetime.now
