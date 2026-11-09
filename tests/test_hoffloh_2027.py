"""Köln's numeric-date schedule captured from the organizer on 2026-11-09."""
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from nrw_events import common
from nrw_events.sources import hofflohmaerkte

from tests.helpers import patch_window


class HofflohNumericDateTests(unittest.TestCase):
    def setUp(self):
        patch_window(self, datetime(2026, 11, 9), datetime(2026, 12, 6))
        self.html = (Path(__file__).parent / "fixtures" / "hoffloh-koeln-2027-dates.html").read_text()

    def test_published_numeric_dates_are_candidates_outside_the_current_window(self):
        events = hofflohmaerkte._events_from_page(self.html)
        self.assertGreater(len(events), 20)
        first = events[0]
        self.assertEqual(first["title"], "Hofflohmarkt Longerich")
        self.assertEqual(first["start_date"], "2027-04-10")
        self.assertEqual(first["time"], "10:00–16:00")
        self.assertEqual(first["link"], hofflohmaerkte._URL)
        self.assertEqual(events[-1]["start_date"], "2027-10-17")
        with patch.object(hofflohmaerkte, "_fetch_page", return_value=self.html), \
                patch.object(common, "_record_endpoint") as record, \
                patch.object(common, "log_source_error") as warning:
            self.assertEqual(len(hofflohmaerkte.fetch()), len(events))
        self.assertFalse(record.call_args.kwargs["parser_empty"])
        self.assertFalse(warning.called)

    def test_numeric_full_year_and_invalid_dates(self):
        html = '<p>Sa. 10.04.2027 · 10 - 16 Uhr · <strong>Longerich</strong></p>'
        self.assertEqual(hofflohmaerkte._events_from_page(html)[0]["start_date"], "2027-04-10")
        self.assertEqual(hofflohmaerkte._events_from_page(html.replace("10.04.2027", "31.02.2027")), [])
        self.assertEqual(hofflohmaerkte._events_from_page(html.replace("10.04.2027", "31. April 2027")), [])
