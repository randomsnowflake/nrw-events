import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.nrw_events import common, config


class RuntimeConfigTests(unittest.TestCase):
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
