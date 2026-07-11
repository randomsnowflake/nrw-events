import unittest

from scripts.nrw_events import common, location, scoring
from scripts.nrw_events.models import CanonicalEvent, RawEvent
from scripts.nrw_events.source_types import SourceFetcher, TextParser
from scripts.nrw_events.validation import canonicalize_event
from scripts.nrw_events.sources import SOURCES, SOURCE_IDS, SOURCE_SPECS


class ModuleBoundaryTests(unittest.TestCase):
    def test_common_facade_reexports_stable_location_and_scoring_helpers(self):
        self.assertIs(common.haversine, location.haversine)
        self.assertIs(common.category_score, scoring.category_score)

    def test_event_record_and_callable_contracts_are_importable(self):
        event: RawEvent = {"title": "Event", "source": "Test", "score": 1.0}
        self.assertEqual(event["title"], "Event")
        self.assertTrue(SourceFetcher)
        self.assertTrue(TextParser)

    def test_canonical_event_is_immutable_after_validation(self):
        event = canonicalize_event({
            "title": "Event", "source": "Test", "date": "2026-07-12",
            "score": 1.0, "city": "Bonn",
        })
        self.assertIsInstance(event, CanonicalEvent)
        self.assertEqual(event["start_date"], "2026-07-12")
        with self.assertRaises(AttributeError):
            event.title = "Changed"

    def test_source_registry_has_unique_stable_ids(self):
        ids = [spec.id for spec in SOURCE_SPECS]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(set(SOURCES), set(SOURCE_IDS))
