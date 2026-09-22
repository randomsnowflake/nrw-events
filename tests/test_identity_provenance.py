import unittest

from nrw_events.validation import EventValidationError, canonical_identity_provenance


class IdentityProvenanceTests(unittest.TestCase):
    def test_detail_link_added_once_without_mutating_source_lists(self):
        source = {"link": "https://example.test/e", "link_kind": "detail",
                  "source_links": ["https://example.test/e"],
                  "merged_event_ids": ["m", "m"], "previous_event_ids": [" a ", "a"]}
        result = canonical_identity_provenance(source)
        self.assertEqual(result["source_links"], ["https://example.test/e"])
        self.assertEqual(result["merged_event_ids"], ["m"])
        self.assertEqual(result["previous_event_ids"], ["a"])
        self.assertEqual(source["merged_event_ids"], ["m", "m"])
        self.assertEqual(source["previous_event_ids"], [" a ", "a"])

    def test_unsafe_source_link_keeps_reason_code(self):
        with self.assertRaisesRegex(EventValidationError, "^source_links_invalid$"):
            canonical_identity_provenance({"link": "", "source_links": ["javascript:alert(1)"]})
