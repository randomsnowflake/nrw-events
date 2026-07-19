import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from nrw_events import common, config
from nrw_events.observability import configure_logging
from nrw_events.runtime import EventWindow, RunContext
from nrw_events.health import SourceResult, SourceStatus


class RuntimeConfigTests(unittest.TestCase):
    def test_contexts_keep_independent_immutable_windows(self):
        settings = config.RuntimeConfig(days_ahead=2)
        logger = configure_logging("test", "ERROR", "", "")
        first = RunContext(settings, EventWindow.from_days(2, datetime(2026, 1, 1)), "a", logger)
        second = RunContext(settings, EventWindow.from_days(2, datetime(2026, 2, 1)), "b", logger)
        self.assertEqual(first.window.start.strftime("%Y-%m-%d"), "2026-01-01")
        self.assertEqual(second.window.start.strftime("%Y-%m-%d"), "2026-02-01")
    def test_env_file_is_loaded_before_http_runtime_configuration(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env_file = Path(tmpdir) / "settings.env"
            env_file.write_text("NRW_EVENTS_HTTP_RETRY_ATTEMPTS=3\nNRW_EVENTS_BONN_DE_DELAY_SECONDS=4.5\n")
            with mock.patch.dict(os.environ, {"NRW_EVENTS_ENV_FILE": str(env_file)}, clear=True):
                config.load_env_file()
                settings = config.runtime_config()
                common.configure_runtime(settings, "test-run", common._LOGGER)

        self.assertEqual(common._HTTP_RETRY_ATTEMPTS, 3)
        self.assertEqual(common._HOST_THROTTLE_SECONDS_BY_SUFFIX["bonn.de"], 4.5)

    def test_invalid_runtime_setting_is_actionable(self):
        with mock.patch.dict(os.environ, {"NRW_EVENTS_SCORE_FLOOR": "not-a-number"}, clear=True):
            with self.assertRaisesRegex(ValueError, "NRW_EVENTS_SCORE_FLOOR"):
                config.runtime_config()

    def test_days_are_bounded(self):
        with self.assertRaisesRegex(ValueError, "days_ahead"):
            config.runtime_config(91)

    def test_previous_snapshot_path_is_configurable(self):
        with mock.patch.dict(os.environ, {
            "NRW_EVENTS_PREVIOUS_META_JSON": "/var/cache/nrw-events/last-good.json",
        }, clear=True):
            self.assertEqual(
                config.runtime_config().previous_meta_json,
                "/var/cache/nrw-events/last-good.json",
            )

    def test_transport_error_marks_source_degraded_even_when_fetcher_returns_empty(self):
        result = SourceResult(source="Blocked source")
        result.endpoint("https://example.test", error_type="HTTPError", error="405")
        result.finish([])
        self.assertEqual(result.status, SourceStatus.DEGRADED)

    def test_successful_retry_does_not_mark_source_degraded(self):
        result = SourceResult(source="Flaky source")
        result.endpoint("https://example.test", error_type="URLError", error="connection reset")
        result.endpoint("https://example.test", status=200)
        result.finish([{"title": "Recovered"}])

        self.assertEqual(result.status, SourceStatus.HEALTHY)
        self.assertEqual(result.endpoints["https://example.test"], {"attempts": 2, "status": 200})

    def test_parser_empty_endpoint_is_not_authoritative_healthy_empty(self):
        result = SourceResult(source="Fragile calendar")
        result.endpoint(
            "https://example.test/calendar",
            status=200,
            parser_empty=True,
            parsed_event_count=0,
        )
        result.finish([])

        self.assertEqual(result.status, SourceStatus.PARSER_EMPTY)
