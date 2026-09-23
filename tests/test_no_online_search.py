"""Online-search providers cannot be scheduled, even with stale configuration."""

import os
import subprocess
import sys
import unittest
from pathlib import Path

from nrw_events import runner


class NoOnlineSearchTests(unittest.TestCase):
    def test_legacy_credentials_cannot_register_a_search_provider(self):
        environment = {
            **os.environ,
            "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "scripts"),
            "EXA_API_KEY": "unused-test-value",
            "XAI_API_KEY": "unused-test-value",
            "NRW_EVENTS_ENABLE_GROK": "yes",
            "NRW_EVENTS_EXA_QUERIES": "10",
        }
        script = """
import importlib.util
from nrw_events.sources import SOURCE_IDS, SOURCE_FETCHERS
assert not {'exa-search', 'grok-search'} & set(SOURCE_IDS.values())
assert not {'Exa Search', 'Grok Search'} & set(SOURCE_FETCHERS)
assert importlib.util.find_spec('nrw_events.sources.search') is None
assert 'bonn-de-events' in SOURCE_IDS.values()
"""
        result = subprocess.run(
            [sys.executable, "-c", script], env=environment,
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_explicit_search_selection_is_rejected_before_an_import(self):
        for source in ("exa-search", "grok-search"):
            with self.subTest(source=source), self.assertRaisesRegex(ValueError, "unknown source"):
                runner._parse_cli(["nrw-events", "7", "--source", source])
