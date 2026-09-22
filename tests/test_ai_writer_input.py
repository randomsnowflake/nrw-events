import copy
import unittest

from nrw_events.ai_writer_input import prepare_writer_input


class WriterInputTests(unittest.TestCase):
    def test_private_decisions_do_not_reach_writer_and_structured_locks_remain(self):
        fields = ("title", "start_date", "end_date", "time", "time_note", "venue",
                  "venue_address", "city", "organizer", "price", "availability",
                  "category_key", "series_title")
        payload = dict.fromkeys(fields, "")
        original = {"time": "12:00", "venue": "Bonn", "admission": {"basis": "structured"},
                    "category_key": "concert", "category_confidence": 0.9}
        facts = {"is_concrete_event": True, "event_evidence": "calendar",
                 "_jev": {"category": "concert"}, "title": "Concert"}
        before = copy.deepcopy((original, facts, payload))
        result = prepare_writer_input(original, facts, payload)
        self.assertEqual(result.facts, {"title": "Concert"})
        for key in ("locked_time", "locked_venue", "locked_admission", "locked_category"):
            self.assertTrue(result.payload["field_policy"][key])
        self.assertEqual((original, facts, payload), before)
