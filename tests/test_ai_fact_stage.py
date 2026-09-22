import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock

from nrw_events.ai_fact_stage import extract_facts


class FactStageTests(unittest.TestCase):
    def test_cached_facts_do_not_call_transport_or_cache_writer(self):
        connection, api = Mock(), Mock()
        facts = {"title": "Saved"}
        row = {"stage1_attempts": 0}
        result = extract_facts(connection, row, facts=facts, payload={}, api=api,
                               configured=SimpleNamespace(max_attempts=3),
                               current_time=datetime.now(timezone.utc), routing=None,
                               configured_timeout_seconds=None)
        self.assertIs(result.facts, facts)
        self.assertIs(result.row, row)
        self.assertEqual(api.mock_calls, [])
        self.assertEqual(connection.mock_calls, [])

    def test_exhausted_attempts_do_not_restart_model_request(self):
        connection, api = Mock(), Mock()
        row = {"stage1_attempts": 3}
        result = extract_facts(connection, row, facts=None, payload={}, api=api,
                               configured=SimpleNamespace(max_attempts=3),
                               current_time=datetime.now(timezone.utc), routing=None,
                               configured_timeout_seconds=None)
        self.assertIsNone(result.facts)
        self.assertEqual(api.mock_calls, [])
        self.assertEqual(connection.mock_calls, [])
