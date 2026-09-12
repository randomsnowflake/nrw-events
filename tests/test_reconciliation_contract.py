"""Cross-run identity decisions shared with the standalone consumer."""
import copy
import json
import unittest
from pathlib import Path

from nrw_events.identity_reconciliation import _reconcile_published_ids


class ReconciliationContractTests(unittest.TestCase):
    def test_shared_decisions_and_input_order(self) -> None:
        vectors = json.loads((Path(__file__).parent / 'data/reconciliation-vectors.json').read_text())
        for vector in vectors:
            for reverse in (False, True):
                with self.subTest(case=vector['name'], reverse=reverse):
                    current = copy.deepcopy(vector['current'])
                    expected = list(vector['producer'])
                    if reverse:
                        current.reverse()
                        expected.reverse()
                    result = _reconcile_published_ids(current, vector['previous'])
                    self.assertEqual([event.get('preserved_event_id') for event in result], expected)
                    ids = [event.get('preserved_event_id') for event in result if event.get('preserved_event_id')]
                    self.assertEqual(len(ids), len(set(ids)))
                    for original, event in zip(current, result, strict=True):
                        for field in ('identity_time', 'identity_time_locked', 'identity_venue', 'identity_venue_locked'):
                            self.assertEqual(event.get(field), original.get(field))
                    if vector['name'] == 'locked-identity':
                        self.assertIn('old-alias', result[0]['previous_event_ids'])
