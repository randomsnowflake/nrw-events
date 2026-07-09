import unittest
from datetime import datetime
from unittest import mock

from scripts.nrw_events import common, report
from scripts.nrw_events.validation import EventValidationError, validate_event


class DataIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.old_today, self.old_end_date = common.TODAY, common.END_DATE
        common.TODAY, common.END_DATE = datetime(2026, 6, 8), datetime(2026, 6, 30)

    def tearDown(self):
        common.TODAY, common.END_DATE = self.old_today, self.old_end_date

    def test_unknown_location_is_not_scored_as_bonn(self):
        event = common.make_event("Regional event", datetime(2026, 6, 12), None, "", "Unknown region", "",
                                  "https://example.test", "Test", "concert")
        self.assertIsNotNone(event)
        assert event is not None
        self.assertIsNone(event["distance_km"])
        self.assertEqual(event["location_confidence"], "unresolved")

    def test_cancelled_phrase_is_not_published(self):
        event = common.make_event(
            "Kabarettprogramm muss leider kurzfristig abgesagt werden!", datetime(2026, 6, 12), None,
            "Venue", "Bonn", "", "https://example.test", "Test", "stage",
        )
        self.assertIsNone(event)

    def test_ical_utc_time_converts_to_berlin_time(self):
        ical = """BEGIN:VCALENDAR
BEGIN:VEVENT
SUMMARY:UTC concert
DTSTART:20260612T180000Z
DTEND:20260612T200000Z
URL:https://example.test/event
END:VEVENT
END:VCALENDAR"""
        with mock.patch("scripts.nrw_events.common.fetch_url", return_value=ical):
            events = common.fetch_ical("https://example.test/events.ics", "Test", "Bonn", "concert")
        self.assertEqual(events[0]["time"], "20:00–22:00")
        self.assertEqual(events[0]["start_at"], "2026-06-12T20:00+02:00")

    def test_all_day_ical_end_date_is_exclusive(self):
        ical = """BEGIN:VCALENDAR
BEGIN:VEVENT
SUMMARY:Weekend exhibition
DTSTART;VALUE=DATE:20260612
DTEND;VALUE=DATE:20260615
END:VEVENT
END:VCALENDAR"""
        with mock.patch("scripts.nrw_events.common.fetch_url", return_value=ical):
            events = common.fetch_ical("https://example.test/events.ics", "Test", "Bonn")
        self.assertEqual(events[0]["end_date"], "2026-06-14")
        self.assertTrue(events[0]["all_day"])

    def test_deduplication_keeps_same_title_on_different_dates(self):
        events = [
            {"title": "Weekly concert", "city": "Bonn", "date": "2026-06-12", "score": 1.0},
            {"title": "Weekly concert", "city": "Bonn", "date": "2026-06-19", "score": 1.0},
        ]
        self.assertEqual(len(report.deduplicate(events)), 2)

    def test_validation_rejects_bad_link_and_nonfinite_score(self):
        base = {"title": "Event", "date": "2026-06-12", "source": "Test", "score": 1.0}
        with self.assertRaisesRegex(EventValidationError, "link_invalid"):
            validate_event({**base, "link": "javascript:alert(1)"})
        with self.assertRaisesRegex(EventValidationError, "score_invalid"):
            validate_event({**base, "score": float("nan")})

    def test_validation_neutralizes_legacy_bonn_fallback_for_unknown_city(self):
        event = validate_event({
            "title": "Unknown city event", "date": "2026-06-12", "source": "Legacy", "score": 1.5,
            "city": "Naturregion Sieg", "distance_km": 0,
        })
        self.assertIsNone(event["distance_km"])
        self.assertEqual(event["location_confidence"], "unresolved")
        self.assertLessEqual(event["score"], 0.3)
