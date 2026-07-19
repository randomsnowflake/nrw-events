import importlib
import unittest
from unittest.mock import patch

from nrw_events import common, dates, location, scoring
from nrw_events.health import SourceStatus
from nrw_events.models import CanonicalEvent, RawEvent
from nrw_events.source_types import SourceFetcher, TextParser
from nrw_events.validation import canonicalize_event
from nrw_events.sources import SOURCES, SOURCE_IDS, SOURCE_SPECS, harmonie


class ModuleBoundaryTests(unittest.TestCase):
    def test_common_compatibility_import_has_one_canonical_module_identity(self):
        self.assertIs(importlib.import_module("nrw_events.common"), common)
        self.assertEqual(common.__name__, "nrw_events.core")

    def test_common_facade_reexports_stable_location_and_scoring_helpers(self):
        self.assertIs(common.haversine, location.haversine)
        self.assertIs(common.category_score, scoring.category_score)
        self.assertIs(common.parse_date, dates.parse_date)

    def test_event_record_and_callable_contracts_are_importable(self):
        event: RawEvent = {"title": "Event", "source": "Test", "score": 1.0}
        self.assertEqual(event["title"], "Event")
        self.assertTrue(SourceFetcher)
        self.assertTrue(TextParser)

    def test_canonical_event_is_immutable_after_validation(self):
        event_date = common.TODAY.strftime("%Y-%m-%d")
        event = canonicalize_event({
            "title": "Event", "source": "Test", "date": event_date,
            "score": 1.0, "city": "Bonn",
        })
        self.assertIsInstance(event, CanonicalEvent)
        self.assertEqual(event["start_date"], event_date)
        with self.assertRaises(AttributeError):
            event.title = "Changed"

    def test_source_registry_has_unique_stable_ids(self):
        ids = [spec.id for spec in SOURCE_SPECS]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(set(SOURCES), set(SOURCE_IDS))

    def test_removed_sources_are_not_registered(self):
        self.assertNotIn("Songkick", SOURCES)
        self.assertNotIn("Rausgegangen Party", SOURCES)

    def test_harmonie_exposes_its_reachable_typed_success_result(self):
        with patch.object(harmonie.common, "fetch_ical", return_value=[{"title": "Concert"}]):
            result = harmonie.fetch()
        self.assertEqual(result.status, SourceStatus.HEALTHY)
        self.assertEqual(result.events, ({"title": "Concert"},))
