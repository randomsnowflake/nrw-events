"""Wave of Joy: public rows of the choir calendar, clocks from the ticket form."""

import json
import unittest
from datetime import datetime
from unittest.mock import patch

from nrw_events import report
from nrw_events.sources import wave_of_joy
from nrw_events.validation import canonicalize_event

from tests.helpers import patch_window

# Live payload of https://www.wave-of-joy.de/php/termine.php on 2026-10-13 (internal rows shortened).
CALENDAR = json.dumps([
    {"date": "06. November 2026", "location": "Lutherkirche Bonn",
     "text": "1. Jubiläumskonzert 30 Jahre Wave of Joy", "intern": False, "active": True},
    {"date": "07. November 2026", "location": "Lutherkirche Bonn",
     "text": "2. Jubiläumskonzert 30 Jahre Wave of Joy", "intern": False, "active": True},
    {"date": "16. Dezember 2026", "location": "Probenraum", "text": "Weihnachtsfeier",
     "intern": True, "active": True},
    {"date": "15.-17. Januar 2027", "location": " ", "text": "Chorwochenende mit Anna",
     "intern": True, "active": True},
])
# Labels of the reservation form chunk (Angular template literals).
TICKET_FORM = (
    't(52,"6. November 20:00 Uhr (Einlass 19:30)"),o()(),'
    't(56,"7. November 18:00 Uhr (Einlass 17:30)"),o()(),'
    't(59,"Ticketanzahl (Erw. 15 \\u20AC)"),t(64,"Ticketanzahl (Kinder/Jgdl. 0 \\u20AC)")'
)


def _canonical_concerts():
    return [canonicalize_event(event) for event in wave_of_joy.events_from_calendar(CALENDAR, TICKET_FORM)]


class WaveOfJoyParserTests(unittest.TestCase):
    def setUp(self):
        patch_window(self, datetime(2026, 10, 13), datetime(2026, 12, 31))

    def test_public_rows_take_clock_doors_and_price_from_the_ticket_form(self):
        first, second = wave_of_joy.events_from_calendar(CALENDAR, TICKET_FORM)
        self.assertEqual(first["title"], "Jubiläumskonzert 30 Jahre Wave of Joy")
        self.assertEqual(first["start_at"], "2026-11-06T20:00+01:00")
        self.assertEqual(first["time_note"], "Einlass 19:30 Uhr")
        self.assertEqual(first["price"], "Erw. 15 €; Kinder/Jgdl. 0 €")
        self.assertEqual(first["venue"], "Lutherkirche Bonn")
        self.assertEqual(first["source_id"], "wave-of-joy")
        self.assertEqual(second["start_at"], "2026-11-07T18:00+01:00")

    def test_without_ticket_form_dates_stay_untimed_and_unpriced(self):
        first, _ = wave_of_joy.events_from_calendar(CALENDAR)
        self.assertEqual(first["date"], "2026-11-06")
        self.assertFalse(first.get("time"))
        self.assertFalse(first.get("price"))

    def test_public_date_range_is_one_multi_day_event(self):
        row = {"date": "19./20. Dezember 2026", "location": "Hardtberg",
               "text": "Auftritt Adventsmarkt", "intern": False, "active": True}
        [event] = wave_of_joy.events_from_calendar(json.dumps([row]))
        self.assertEqual((event["start_date"], event["end_date"]), ("2026-12-19", "2026-12-20"))

    def test_only_internal_rows_are_a_healthy_empty_calendar(self):
        internal = json.dumps([row for row in json.loads(CALENDAR) if row["intern"]])
        with patch.object(wave_of_joy.http, "fetch_url", return_value=internal), \
             patch.object(wave_of_joy, "_ticket_form", return_value=""), \
             patch.object(wave_of_joy.run_state, "log_source_error") as log_error:
            self.assertEqual(wave_of_joy.fetch(), [])
        log_error.assert_not_called()


class WaveOfJoyPrimarySourceTests(unittest.TestCase):
    def setUp(self):
        patch_window(self, datetime(2026, 10, 13), datetime(2026, 12, 31))

    def test_choir_calendar_absorbs_retitled_civic_and_district_listings(self):
        civic = canonicalize_event({
            "title": "Melodies From Heaven - 30 Jahre Wave Of Joy", "start_date": "2026-11-06",
            "end_date": "2026-11-06", "start_at": "2026-11-06T20:00+01:00", "time": "20:00",
            "venue": "Lutherkirche, Reuterstr. 11", "city": "Bonn", "category": "Konzert", "score": 9,
            "source": "Bonn.de Events", "source_id": "bonn-de-events",
            "link": "https://www.bonn.de/veranstaltungskalender/wave-of-joy.php",
        })
        # Beuel.net publishes under the organizer's domain, so authority alone ties.
        district = canonicalize_event({
            "title": "Gospelkonzert Wave of Joy", "start_date": "2026-11-07", "end_date": "2026-11-07",
            "venue": "Lutherkirche", "city": "Bonn", "category": "Konzert", "score": 9,
            "source": "wave-of-joy.de", "source_id": "beuel-net", "link": "https://www.beuel.net/wave-of-joy",
        })
        merged = report.deduplicate([civic, district, *_canonical_concerts()])
        self.assertEqual([event.source_id for event in merged], ["wave-of-joy", "wave-of-joy"])
        self.assertEqual({event.link for event in merged}, {wave_of_joy._PAGE_URL})
        self.assertEqual(sorted(event.time for event in merged), ["18:00", "20:00"])

    def test_other_day_or_other_performer_stays_separate(self):
        concert, _ = _canonical_concerts()
        for updates in (
            {"title": "Konzert mit Wave Of Joy", "start_date": "2026-11-08", "end_date": "2026-11-08",
             "start_at": "2026-11-08T20:00+01:00"},
            {"title": "Orgelkonzert"},
        ):
            other = canonicalize_event({
                "title": "", "start_date": "2026-11-06", "end_date": "2026-11-06",
                "start_at": "2026-11-06T20:00+01:00", "time": "20:00", "venue": "Lutherkirche Bonn",
                "city": "Bonn", "category": "Konzert", "score": 9, "source": "Bonn.de Events",
                "source_id": "bonn-de-events", "link": "https://www.bonn.de/x.php", **updates,
            })
            with self.subTest(updates=updates):
                self.assertEqual(len(report.deduplicate([concert, other])), 2)


if __name__ == "__main__":
    unittest.main()
