"""KULT41 month previews captured from the official November calendar."""

import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from nrw_events.sources import bonn_venues
from nrw_events.validation import validate_event

from tests.helpers import patch_window


class Kult41CalendarTests(unittest.TestCase):
    def setUp(self):
        patch_window(self, datetime(2026, 10, 31), datetime(2026, 11, 27))
        # Exact list/preview cards from ?mo=11&yr=2026, fetched 2026-10-31.
        self.html = (Path(__file__).parent / "fixtures/kult41/november-calendar.html").read_text()

    def test_calendar_previews_recover_november_without_inheriting_adjacent_cards(self):
        events = bonn_venues.events_from_kult41(self.html)
        self.assertEqual(len(events), 4)
        self.assertFalse(any("Offenes Büro" in event["title"] for event in events))
        [jacobi] = [event for event in events if event["link"].endswith("/al-jacobi-ha-bi-ba")]
        self.assertEqual(jacobi["title"], "Al Jacobi & Ha Bi Ba")
        self.assertEqual(jacobi["start_date"], "2026-11-01")
        self.assertEqual(jacobi["time"], "19:00–22:00")
        self.assertEqual(jacobi["venue"], "KULT41")
        self.assertEqual(jacobi["category_key"], "concert")
        self.assertIn("distorted and fractured self-reflection", jacobi["description"])
        self.assertNotIn("Offenes Büro", jacobi["description"])
        self.assertEqual(jacobi["price"], "")
        [zef] = [event for event in events if event["link"].endswith("/zef-parisoto-yirsi")]
        self.assertEqual(zef["start_date"], "2026-11-14")
        for event in events:
            validate_event(event)

    def test_existing_list_card_survives_and_does_not_steal_preview_fields(self):
        listing = self.html.split('<div class="em-cal-event-content', 1)[0]
        [old] = bonn_venues.events_from_kult41(listing)
        events = bonn_venues.events_from_kult41(self.html)
        self.assertEqual(events[0], old)
        self.assertEqual(old["start_date"], "2026-10-31")
        self.assertEqual(old["price"], "Eintritt: 10€")

    def test_group_fetch_deduplicates_lists_previews_and_repeated_months(self):
        def kult41_jobs(jobs):
            return [event for job in jobs if job.url == bonn_venues._KULT41_URL for event in job.fetch()]

        with patch.object(bonn_venues.components, "run", side_effect=kult41_jobs), \
             patch.object(bonn_venues.common, "fetch_url", return_value=self.html) as fetch:
            events = bonn_venues.fetch()

        self.assertEqual(fetch.call_count, 2)
        self.assertEqual(len(events), 3)
        self.assertEqual([event["start_date"] for event in events], ["2026-10-31", "2026-11-01", "2026-11-14"])


if __name__ == "__main__":
    unittest.main()
