import os
import tempfile
import unittest
from contextvars import copy_context
from datetime import datetime
from pathlib import Path
from unittest import mock

from nrw_events import common, config, report
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
            env_file.write_text(
                "NRW_EVENTS_HTTP_RETRY_ATTEMPTS=3\n"
                "NRW_EVENTS_BONN_DE_DELAY_SECONDS=4.5\n"
                "NRW_EVENTS_DESCRIPTION_MAX_CHARS=12\n"
            )
            with mock.patch.dict(os.environ, {"NRW_EVENTS_ENV_FILE": str(env_file)}, clear=True):
                config.load_env_file()
                settings = config.runtime_config()
                common.configure_runtime(settings, "test-run", common._LOGGER)

        runtime = common._runtime_state()
        self.assertEqual(runtime.settings.http_retry_attempts, 3)
        self.assertEqual(runtime.settings.bonn_de_delay_seconds, 4.5)
        self.assertEqual(runtime.settings.description_max_chars, 12)
        self.assertLessEqual(len(common.concise_description("A long description for testing.")), 13)
        common._RUNTIME_STATE.set(None)

    def test_runtime_contexts_keep_radius_isolated_without_mutating_static_config(self):
        logger = configure_logging("test", "ERROR", "", "")
        first = copy_context()
        second = copy_context()
        first.run(common.configure_runtime, config.RuntimeConfig(radius_km=10), "first", logger)
        second.run(common.configure_runtime, config.RuntimeConfig(radius_km=120), "second", logger)
        self.assertEqual(first.run(common.runtime_radius_km), 10)
        self.assertEqual(second.run(common.runtime_radius_km), 120)
        self.assertEqual(config.MAX_RADIUS_KM, 75)

    def test_failed_fallback_cache_configuration_does_not_leak_runtime_state(self):
        logger = configure_logging("test", "ERROR", "", "")
        token = common.configure_runtime(config.RuntimeConfig(radius_km=12), "first", logger)
        try:
            missing = str(Path(tempfile.gettempdir()) / "missing-category-fallback.json")
            with self.assertRaises(FileNotFoundError):
                common.configure_runtime(
                    config.RuntimeConfig(radius_km=99, category_fallback_cache=missing),
                    "failed",
                    logger,
                )
            self.assertEqual(common.runtime_radius_km(), 12)
        finally:
            common.reset_runtime(token)

    def test_report_can_render_the_completed_run_radius_after_context_reset(self):
        rendered = report.format_report([], radius_km=15)
        self.assertIn("**Radius:** 15km from Bonn", rendered)

    def test_invalid_description_limit_is_an_actionable_configuration_error(self):
        with mock.patch.dict(os.environ, {"NRW_EVENTS_DESCRIPTION_MAX_CHARS": "700x"}, clear=True):
            with self.assertRaisesRegex(ValueError, "NRW_EVENTS_DESCRIPTION_MAX_CHARS"):
                config.runtime_config()

    def test_parallelism_and_timeout_budgets_are_configurable(self):
        with mock.patch.dict(os.environ, {
            "NRW_EVENTS_SOURCE_WORKERS": "20",
            "NRW_EVENTS_SOURCE_TIMEOUT_SECONDS": "90",
            "NRW_EVENTS_HTTP_REQUEST_BUDGET_SECONDS": "30",
        }, clear=True):
            settings = config.runtime_config()

        self.assertEqual(settings.source_workers, 20)
        self.assertEqual(settings.source_timeout_seconds, 90)
        self.assertEqual(settings.http_request_budget_seconds, 30)

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
