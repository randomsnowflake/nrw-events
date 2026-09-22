import unittest

from nrw_events.event_evidence import build_evidence_index


class EventEvidenceTests(unittest.TestCase):
    def test_preserves_recorded_basis_without_inventing_merge_or_publication(self):
        result = build_evidence_index([{
            "event_id": "e", "source_id": "s", "admission_basis": "explicit-source",
            "admission": {"type": "free"}, "previous_event_ids": ["old"],
            "description_source": "source-page",
        }], run_id="r", events_path="/saved/events.json")
        row = result["events"][0]
        self.assertEqual(row["decisions"][0]["reason"], "explicit-source")
        self.assertEqual(row["fieldProvenance"], {"description_source": "source-page"})
        self.assertEqual(row["rawPaths"], ["/saved/events.json"])
        for name in ("mergedIds", "publishedRoute", "generationId"):
            self.assertNotIn(name, row)

    def test_field_value_alone_is_not_a_policy_reason(self):
        row = build_evidence_index([{"event_id": "e", "price": "free"}],
                                   run_id="r", events_path="/saved")[ "events"][0]
        self.assertNotIn("decisions", row)

    def test_missing_and_duplicate_identity_fail(self):
        for events in ([{}], [{"event_id": "e"}, {"event_id": "e"}]):
            with self.assertRaises(ValueError):
                build_evidence_index(events, run_id="r", events_path="/saved")
