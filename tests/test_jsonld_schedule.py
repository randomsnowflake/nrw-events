import json
import unittest
from datetime import datetime

from scripts.nrw_events import common


class JsonLdScheduleTests(unittest.TestCase):
    def setUp(self):
        self.old_today = common.TODAY
        self.old_end_date = common.END_DATE
        common.TODAY = datetime(2026, 6, 8)
        common.END_DATE = datetime(2026, 6, 21)

    def tearDown(self):
        common.TODAY = self.old_today
        common.END_DATE = self.old_end_date

    def test_ongoing_ranges_display_current_future_date_only(self):
        ev = common.make_event(
            "Long exhibition",
            datetime(2026, 4, 1),
            datetime(2026, 8, 1),
            "Museum",
            "Bonn",
            "Ongoing exhibition",
            "https://example.test/exhibition",
            "Museum",
            "ausstellung",
        )

        self.assertIsNotNone(ev)
        assert ev is not None
        self.assertEqual(ev["date"], "ongoing until 2026-08-01")
        self.assertNotIn("2026-04-01", ev["date"])

    def test_event_schedule_expands_future_appointments_and_skips_season_span(self):
        payload = {
            "@context": "https://schema.org",
            "@type": "Event",
            "name": "Rheinauen-Flohmarkt",
            "url": "https://www.bonn.de/flohmarkt-rheinaue.php",
            "description": "Flohmarkt in der Rheinaue",
            "location": {
                "@type": "Place",
                "name": "Rheinaue",
                "address": {"@type": "PostalAddress", "addressLocality": "Bonn"},
            },
            "startDate": "2026-04-18",
            "endDate": "2026-10-17",
            "eventSchedule": [
                {"@type": "Schedule", "startDate": "2026-04-18", "endDate": "2026-04-18", "startTime": "08:00", "endTime": "18:00"},
                {"@type": "Schedule", "startDate": "2026-06-20", "endDate": "2026-06-20", "startTime": "08:00", "endTime": "18:00"},
                {"@type": "Schedule", "startDate": "2026-07-18", "endDate": "2026-07-18", "startTime": "08:00", "endTime": "18:00"},
            ],
        }
        html = f'<script type="application/ld+json">{json.dumps(payload)}</script>'

        events = common.events_from_jsonld(html, "Rheinauen-Flohmarkt", "Bonn", "markt flohmarkt outdoor", 1.0, payload["url"])

        self.assertEqual([ev["date"] for ev in events], ["2026-06-20"])
        self.assertEqual(events[0]["time"], "08:00–18:00")
        self.assertNotIn("2026-04-18–2026-10-17", [ev["date"] for ev in events])


if __name__ == "__main__":
    unittest.main()
