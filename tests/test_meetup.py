import unittest
from unittest.mock import patch

from nrw_events.sources import meetup


class MeetupSourceTests(unittest.TestCase):
    def test_valid_empty_group_calendar_is_not_parser_degradation(self):
        groups = [("inactive-group", "Bonn", "meetup", 0.9)]
        with patch.object(meetup.config, "MEETUP_GROUPS", groups), patch.object(
            meetup.common, "fetch_ical", return_value=[]
        ) as fetch_ical:
            events = meetup.fetch()

        self.assertEqual(events, [])
        self.assertTrue(fetch_ical.call_args.kwargs["empty_calendar_is_valid"])

    def test_platform_description_is_replaced_with_master_data(self):
        groups = [("bonn-group", "Bonn", "meetup", 0.9)]
        upstream_event = {
            "title": "Open Data Bonn",
            "description": "Längerer von einem Mitglied eingestellter Plattformtext.",
            "description_html": "<p>Längerer von einem Mitglied eingestellter Plattformtext.</p>",
            "description_source": "scraped",
            "start_date": "2026-08-05",
            "end_date": "2026-08-05",
            "time": "19:00",
            "venue": "Testhalle",
            "city": "Bonn",
            "link": "https://www.meetup.com/bonn-group/events/123/",
        }
        with patch.object(meetup.config, "MEETUP_GROUPS", groups), patch.object(
            meetup.common, "fetch_ical", return_value=[upstream_event]
        ):
            [event] = meetup.fetch()

        self.assertNotIn("Mitglied", event["description"])
        self.assertEqual(event["description_source"], "generated")
        self.assertIn("Open Data Bonn", event["description"])


if __name__ == "__main__":
    unittest.main()
