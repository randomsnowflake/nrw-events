"""Regression coverage for ordinary records reaching importer handlers."""
import io
import json
import logging
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from nrw_events.ai_decisions import LOGGER as AI_LOGGER
from nrw_events.observability import configure_logging


class LoggingContextTests(unittest.TestCase):
    def test_handlers_supply_missing_context_and_preserve_explicit_values(self):
        logger = logging.getLogger("nrw_events")
        old_state = (logger.handlers[:], logger.filters[:], logger.level, logger.propagate)
        self.addCleanup(self.restore_logger, logger, old_state)
        with tempfile.TemporaryDirectory() as directory:
            text_path = Path(directory) / "import.log"
            json_path = Path(directory) / "import.jsonl"
            stderr = io.StringIO()
            with mock.patch("sys.stderr", stderr), mock.patch.object(
                logging.Handler, "handleError"
            ) as handle_error:
                configure_logging("active-run", "INFO", str(text_path), str(json_path))
                AI_LOGGER.warning("decision %s", "fallback")
                AI_LOGGER.info("explicit context", extra={
                    "run_id": "explicit-run", "source": "explicit-source",
                })
                AI_LOGGER.info("partial context", extra={"source": "partial-source"})
                logger.info("ordinary parent")
                handle_error.assert_not_called()
            for output in (stderr.getvalue(), text_path.read_text()):
                self.assertIn("run=active-run source=nrw_events.ai_decisions decision fallback", output)
                self.assertIn("run=explicit-run source=explicit-source explicit context", output)
                self.assertIn("run=active-run source=partial-source partial context", output)
                self.assertIn("run=active-run source=nrw_events ordinary parent", output)
            rows = {row["message"]: row for row in map(json.loads, json_path.read_text().splitlines())}
            self.assertEqual(rows["decision fallback"]["run_id"], "active-run")
            self.assertEqual(rows["decision fallback"]["source"], AI_LOGGER.name)
            self.assertEqual(rows["explicit context"]["run_id"], "explicit-run")
            self.assertEqual(rows["explicit context"]["source"], "explicit-source")
            self.assertEqual(rows["partial context"]["run_id"], "active-run")
            self.assertEqual(rows["partial context"]["source"], "partial-source")
            self.assertEqual(rows["ordinary parent"]["source"], logger.name)
            # Close durable handlers before removing their temporary directory.
            for handler in logger.handlers:
                handler.close()

    @staticmethod
    def restore_logger(logger, state):
        for handler in logger.handlers:
            handler.close()
        logger.handlers, logger.filters, logger.level, logger.propagate = state
