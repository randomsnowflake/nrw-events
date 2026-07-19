"""Shared test fixtures for mutable legacy compatibility state."""

from unittest.mock import patch

from nrw_events import common


def patch_window(testcase, today, end_date) -> None:
    """Patch the legacy window and always restore it through test cleanups."""
    today_patch = patch.object(common, "TODAY", today)
    end_patch = patch.object(common, "END_DATE", end_date)
    today_patch.start()
    end_patch.start()
    testcase.addCleanup(end_patch.stop)
    testcase.addCleanup(today_patch.stop)
