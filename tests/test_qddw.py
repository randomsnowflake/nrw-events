import json
import unittest
from datetime import datetime
from unittest import mock

from nrw_events import common
from nrw_events.sources import SOURCES, qddw


_PAGE = """
<aside id="block-11" class="widget widget_block">
<h3 class="wp-block-heading">Anstehende Veranstaltungen:<br>
Warther&nbsp;Kirmes 07. - 10. August<br>
Prunksitzung 29. Januar<br>
Karnevalsparty 06. Februar<br>
Vorbestellungen unter: Karten@qddw.de</h3>
</aside>
"""


class QddwSourceTests(unittest.TestCase):
    def test_source_is_registered(self):
        self.assertIs(SOURCES["KG Quer durch de Waat"], qddw.fetch)
        self.assertNotIn("Hennef", SOURCES)

    def test_parses_warther_kirmes_from_official_upcoming_events(self):
        with mock.patch.object(common, "TODAY", datetime(2026, 8, 1)), \
                mock.patch.object(common, "END_DATE", datetime(2026, 8, 31)):
            events = qddw.events_from_page(
                _PAGE,
                year=2026,
                source_url="https://quer-durch-de-waat.de/2026/01/frohes-neues-jahr-2026/",
            )

        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event["title"], "Warther Kirmes")
        self.assertEqual(event["start_date"], "2026-08-07")
        self.assertEqual(event["end_date"], "2026-08-10")
        self.assertEqual(event["city"], "Hennef")
        self.assertEqual(event["venue"], "Kirmesplatz Warth")
        self.assertEqual(event["source"], "KG Quer durch de Waat")
        self.assertEqual(event["source_id"], "qddw")
        self.assertEqual(
            event["link"],
            "https://quer-durch-de-waat.de/2026/01/frohes-neues-jahr-2026/",
        )
        self.assertIn("7. bis 10. August 2026", event["description"])

    def test_annual_page_year_is_not_rolled_forward(self):
        with mock.patch.object(common, "TODAY", datetime(2027, 8, 1)), \
                mock.patch.object(common, "END_DATE", datetime(2027, 8, 31)):
            events = qddw.events_from_page(
                _PAGE,
                year=2026,
                source_url="https://quer-durch-de-waat.de/2026/01/frohes-neues-jahr-2026/",
            )

        self.assertEqual(events[0]["start_date"], "2026-08-07")

    def test_fetch_uses_year_from_the_latest_official_new_year_post(self):
        posts = json.dumps([
            {
                "date": "2026-01-01T00:02:00",
                "link": "https://quer-durch-de-waat.de/2026/01/frohes-neues-jahr-2026/",
                "title": {"rendered": "Frohes Neues Jahr 2026"},
            }
        ])
        with mock.patch.object(common, "TODAY", datetime(2026, 8, 1)), \
                mock.patch.object(common, "END_DATE", datetime(2026, 8, 31)), \
                mock.patch.object(common, "fetch_url", side_effect=[posts, _PAGE]) as fetch:
            events = qddw.fetch()

        self.assertEqual(len(events), 1)
        self.assertIn("search=Frohes%20Neues%20Jahr", fetch.call_args_list[0].args[0])
        self.assertEqual(
            fetch.call_args_list[1].args[0],
            "https://quer-durch-de-waat.de/2026/01/frohes-neues-jahr-2026/",
        )

    def test_missing_current_source_page_is_not_guessed(self):
        posts = json.dumps([
            {
                "date": "2025-01-01T00:02:00",
                "link": "https://quer-durch-de-waat.de/2025/01/frohes-neues-jahr-2025/",
                "title": {"rendered": "Frohes Neues Jahr 2025"},
            }
        ])
        with mock.patch.object(common, "TODAY", datetime(2026, 8, 1)), \
                mock.patch.object(common, "END_DATE", datetime(2026, 8, 31)), \
                mock.patch.object(common, "fetch_url", return_value=posts) as fetch:
            events = qddw.fetch()

        self.assertEqual(events, [])
        self.assertEqual(fetch.call_count, 1)


if __name__ == "__main__":
    unittest.main()
