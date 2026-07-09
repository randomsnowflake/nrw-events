import unittest

from scripts.nrw_events import common, location, scoring
from scripts.nrw_events.models import EventRecord
from scripts.nrw_events.source_types import SourceFetcher, TextParser


class ModuleBoundaryTests(unittest.TestCase):
    def test_common_facade_reexports_stable_location_and_scoring_helpers(self):
        self.assertIs(common.haversine, location.haversine)
        self.assertIs(common.category_score, scoring.category_score)

    def test_event_record_and_callable_contracts_are_importable(self):
        event: EventRecord = {"title": "Event", "source": "Test", "score": 1.0}
        self.assertEqual(event["title"], "Event")
        self.assertTrue(SourceFetcher)
        self.assertTrue(TextParser)
