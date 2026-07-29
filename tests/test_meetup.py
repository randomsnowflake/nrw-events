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


if __name__ == "__main__":
    unittest.main()
