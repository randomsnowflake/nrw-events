"""Shared test fixtures for mutable legacy compatibility state."""

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import tempfile
from typing import Callable, Iterator
from unittest.mock import patch

from nrw_events import common, config
from nrw_events.observability import configure_logging
from nrw_events.runtime import EventWindow, RunContext


def default_window() -> EventWindow:
    """Return the shared fixed window used by cross-run retention tests."""
    return EventWindow(datetime(2026, 6, 8), datetime(2026, 6, 10))


def make_event(**overrides) -> dict:
    """Build a complete mutable raw-event fixture with narrow overrides."""
    event = {
        "title": "Test event",
        "source": "Test Source",
        "date": "2026-06-09",
        "start_date": "2026-06-09",
        "end_date": "2026-06-09",
        "city": "Bonn",
        "venue": "Test venue",
        "description": "Test description",
        "link": "https://example.test/event",
        "score": 1.0,
        "category": "Kultur",
    }
    event.update(overrides)
    return event


@dataclass(frozen=True)
class RunnerEnvironment:
    root: Path
    previous_path: Path

    def context(
        self,
        run_id: str = "fixture",
        *,
        clock: Callable[[], datetime] = datetime.now,
        **settings_overrides,
    ) -> RunContext:
        """Create a fixed runner context backed by this isolated directory."""
        settings = config.RuntimeConfig(
            previous_meta_json=str(self.previous_path),
            **settings_overrides,
        )
        return RunContext(
            settings,
            default_window(),
            run_id,
            configure_logging(run_id, "CRITICAL", "", ""),
            clock=clock,
        )


@contextmanager
def make_runner_env() -> Iterator[RunnerEnvironment]:
    """Yield isolated paths and runner context helpers, then remove them."""
    with tempfile.TemporaryDirectory(prefix="nrw-events-runner-test-") as tmpdir:
        root = Path(tmpdir)
        yield RunnerEnvironment(root=root, previous_path=root / "previous.json")


def patch_window(testcase, today, end_date) -> None:
    """Patch the legacy window and always restore it through test cleanups."""
    today_patch = patch.object(common, "TODAY", today)
    end_patch = patch.object(common, "END_DATE", end_date)
    today_patch.start()
    end_patch.start()
    testcase.addCleanup(end_patch.stop)
    testcase.addCleanup(today_patch.stop)
