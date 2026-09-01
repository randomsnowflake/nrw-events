import unittest
from datetime import datetime

from nrw_events import common
from nrw_events.validation import validate_event
from tests.helpers import patch_window


class StructuredTimeRangeTests(unittest.TestCase):
    def setUp(self):
        patch_window(self, datetime(2026, 8, 1), datetime(2026, 8, 31))

    def _event(self, time_text, *, start=None, end=None, source="SiteKit regional"):
        day = datetime(2026, 8, 14)
        event = common.make_event(
            "Sommerprogramm",
            start or day,
            day if end is None else end,
            "Rathaus",
            "Brühl",
            "Offizieller Veranstaltungstermin.",
            "https://example.test/sommerprogramm",
            source,
            "kultur",
            time_text=time_text,
            source_id="structured-time-test",
        )
        self.assertIsNotNone(event)
        return event

    def test_sitekit_clock_range_sets_distinct_structured_start_and_end(self):
        event = self._event("10:30–14:30")

        self.assertEqual(event["time"], "10:30–14:30")
        self.assertEqual(event["start_at"], "2026-08-14T10:30+02:00")
        self.assertEqual(event["end_at"], "2026-08-14T14:30+02:00")
        self.assertFalse(validate_event(event).quality_warnings)

    def test_koeln_single_clock_keeps_end_unknown(self):
        event = self._event("19:00", source="Köln Open Data")

        self.assertEqual(event["start_at"], "2026-08-14T19:00+02:00")
        self.assertEqual(event["end_at"], "")
        self.assertFalse(validate_event(event).quality_warnings)

    def test_overnight_clock_range_rolls_end_into_next_day(self):
        event = self._event("21:00–04:00")

        self.assertEqual(event["start_at"], "2026-08-14T21:00+02:00")
        self.assertEqual(event["end_at"], "2026-08-15T04:00+02:00")
        self.assertEqual(event["end_date"], "2026-08-15")
        self.assertFalse(validate_event(event).quality_warnings)

    def test_blank_or_null_clock_stays_all_day_without_structured_times(self):
        for time_text in ("", None):
            with self.subTest(time_text=time_text):
                event = self._event(time_text)

                self.assertTrue(event["all_day"])
                self.assertEqual(event["start_at"], "")
                self.assertEqual(event["end_at"], "")

    def test_multiple_slot_note_does_not_invent_one_structured_occurrence(self):
        note = "Vorstellungen: 10:00, 14:00 und 18:00 Uhr"
        event = self._event(note)

        self.assertEqual(event["time"], "")
        self.assertEqual(event["time_note"], note)
        self.assertFalse(event["all_day"])
        self.assertEqual(event["start_at"], "")
        self.assertEqual(event["end_at"], "")
        self.assertFalse(validate_event(event).quality_warnings)


if __name__ == "__main__":
    unittest.main()
