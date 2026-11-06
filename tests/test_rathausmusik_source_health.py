import unittest
from unittest.mock import patch

from nrw_events.sources import requested_venues


class RathausmusikSourceHealthTests(unittest.TestCase):
    def test_paused_series_is_not_scheduled_for_fetch(self):
        with patch.object(requested_venues.components, "run", return_value=[]) as run:
            self.assertEqual(requested_venues.fetch(), [])

        jobs = run.call_args.args[0]
        self.assertEqual(len(jobs), 4)
        self.assertFalse(any("rathausmusik.com" in job.url for job in jobs))
