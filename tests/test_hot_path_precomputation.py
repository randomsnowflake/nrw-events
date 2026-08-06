import re
import unittest
from unittest import mock

from nrw_events import category_taxonomy, location
from nrw_events.validation import canonicalize_event


class HotPathPrecomputationTests(unittest.TestCase):
    def test_category_policy_patterns_are_reused_during_classification(self):
        keywords = [
            keyword
            for rule in category_taxonomy.RULES
            for keyword in rule.keywords
            if isinstance(keyword, category_taxonomy.Keyword)
        ]
        self.assertTrue(keywords)
        self.assertTrue(all(isinstance(keyword.pattern, re.Pattern) for keyword in keywords))

        with mock.patch.object(
            category_taxonomy.re,
            "compile",
            side_effect=AssertionError("compiled a regex in the event hot path"),
        ):
            result = category_taxonomy.categorize_event(
                "Kultur Konzert",
                "Sommerkonzert für Familien",
                "Live-Musik im Park",
            )

        self.assertEqual(result["key"], "concert")

    def test_location_matchers_are_precomputed_and_keep_city_priority(self):
        self.assertIsInstance(location._NON_AMBIGUOUS_CITY_PATTERN, re.Pattern)
        self.assertTrue(all(isinstance(pattern, re.Pattern) for pattern in location._AMBIGUOUS_CITY_PATTERNS.values()))

        with mock.patch.object(
            location.re,
            "compile",
            side_effect=AssertionError("compiled a regex in the event hot path"),
        ):
            self.assertEqual(location.guess_city_from_text("Bonn und Köln"), "köln")
            self.assertEqual(location.guess_city_from_text("Vortrag in Wissen"), "wissen")
            self.assertEqual(
                location.refine_city_from_text("Bonn", "Treffen in Vilich-Müldorf"),
                "Bonn-Vilich-Müldorf",
            )

    def test_validation_reuses_complete_canonical_category(self):
        event = {
            "title": "Sommerkonzert",
            "source": "Test",
            "source_id": "test",
            "date": "2026-08-15",
            "score": 1.0,
            "city": "Bonn",
            "category": "Musik",
            "category_key": "concert",
            "category_label": "Konzert",
        }
        with mock.patch.object(
            category_taxonomy,
            "categorize_event",
            side_effect=AssertionError("reclassified an already canonical event"),
        ):
            canonical = canonicalize_event(event)

        self.assertEqual(canonical.category_key, "concert")
        self.assertEqual(canonical.category_label, "Konzert")


if __name__ == "__main__":
    unittest.main()
