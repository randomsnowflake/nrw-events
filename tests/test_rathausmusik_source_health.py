import unittest
from datetime import datetime
from unittest.mock import patch

from nrw_events import common
from nrw_events.sources import regional_common, requested_venues


class RathausmusikSourceHealthTests(unittest.TestCase):
    def setUp(self):
        # Exercise the grouped adapter's real Rathausmusik job without other calendars.
        jobs = patch.object(
            requested_venues.components, "run",
            side_effect=lambda jobs: next(
                job for job in jobs if job.url == "http://www.rathausmusik.com"
            ).fetch(),
        )
        jobs.start()
        self.addCleanup(jobs.stop)

    def test_empty_is_expected_only_after_the_2026_schedule_expires(self):
        for day, expected_empty in (
            (datetime(2026, 9, 30), False),
            (datetime(2026, 10, 1), True),
            (datetime(2026, 10, 27), True),
            (datetime(2026, 12, 31), True),
            (datetime(2027, 1, 1), False),
            (datetime(2027, 7, 1), False),
        ):
            with self.subTest(day=day), \
                 patch.object(common, "TODAY", day), \
                 patch.object(common, "END_DATE", day), \
                 patch.object(common, "fetch_url", return_value="<html></html>"), \
                 patch.object(common, "_record_endpoint") as record, \
                 patch.object(common, "log_source_error") as error:
                self.assertEqual(requested_venues.fetch(), [])
                self.assertEqual(record.call_args.kwargs["parser_empty"], not expected_empty)
                if expected_empty:
                    error.assert_not_called()
                else:
                    self.assertIsInstance(error.call_args.args[1], regional_common.ParserEmptyError)
                    self.assertEqual(error.call_args.kwargs["source_id"], "rathausmusik")

    def test_transport_failure_is_still_reported_after_the_season(self):
        day = datetime(2026, 10, 27)
        failure = TimeoutError("upstream unavailable")
        with patch.object(common, "TODAY", day), \
             patch.object(common, "END_DATE", day), \
             patch.object(common, "fetch_url", side_effect=failure), \
             patch.object(common, "log_source_error") as error:
            self.assertEqual(requested_venues.fetch(), [])

        error.assert_called_once_with(
            "Musik auf der Rathaustreppe", failure, source_id="rathausmusik",
        )
