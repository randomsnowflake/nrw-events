import json
import unittest
from datetime import datetime
from unittest import mock

from nrw_events import common
from nrw_events.sources import flohmarkt

from tests.helpers import patch_window


class RheinauenFlohmarktSourceTests(unittest.TestCase):
    def setUp(self):
        patch_window(self, datetime(2026, 8, 1), datetime(2026, 8, 31))

    def test_fetch_prefers_visible_official_description_over_thin_jsonld(self):
        payload = {
            "@context": "https://schema.org",
            "@type": "Event",
            "name": "Flohmarkt in der Rheinaue",
            "description": "Flohmarkt in der Rheinaue",
            "url": flohmarkt._URL,
            "location": {"@type": "Place", "name": "Rheinaue", "address": {"addressLocality": "Bonn"}},
            "eventSchedule": [
                {
                    "@type": "Schedule",
                    "startDate": "2026-08-15",
                    "endDate": "2026-08-15",
                    "startTime": "08:00",
                    "endTime": "18:00",
                }
            ],
        }
        body = (
            '<div class="SP-Text"><div class="SP-Paragraph">'
            "<p>Der Flohmarkt in der Rheinaue gilt als einer der größten Flohmärkte in Deutschland.</p>"
            "<p>Von April bis Oktober werden die Stände aufgebaut; verkauft werden dürfen gebrauchte Waren und Kunsthandwerk.</p>"  # noqa: E501
            "</div></div>"
        )
        html = f'<script type="application/ld+json">{json.dumps(payload)}</script>{body}'

        with mock.patch.object(common, "fetch_url", return_value=html):
            events = flohmarkt.fetch()

        self.assertEqual(len(events), 1)
        self.assertIn("größten Flohmärkte in Deutschland", events[0]["description"])
        self.assertIn("gebrauchte Waren und Kunsthandwerk", events[0]["description"])


if __name__ == "__main__":
    unittest.main()
