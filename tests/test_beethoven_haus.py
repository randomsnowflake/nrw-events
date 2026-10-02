import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from nrw_events.sources import SOURCE_IDS, SOURCES, beethoven_haus

from tests.helpers import patch_window

FIXTURES = Path(__file__).parent / "fixtures" / "beethoven-haus"
LIST_URL = "https://www.beethoven.de/de/termine/list?bymonth=20261001"
DETAIL_URL = "https://www.beethoven.de/de/termine/view/5640450304114688/Kammerkonzert"


class BeethovenHausSourceTests(unittest.TestCase):
    def setUp(self):
        patch_window(self, datetime(2026, 10, 1), datetime(2026, 10, 31))

    def test_month_list_skips_daily_exhibition_entries_and_reads_detail_prices(self):
        requested = []

        def fake_fetch(url, **_kwargs):
            requested.append(url)
            if url == LIST_URL:
                return (FIXTURES / "list-2026-10.html").read_text(encoding="utf-8")
            if url == DETAIL_URL:
                return (FIXTURES / "kammerkonzert-detail.html").read_text(encoding="utf-8")
            raise AssertionError(f"unexpected URL {url}")

        with patch.dict("os.environ", {"NRW_EVENTS_DETAIL_CACHE_TTL_HOURS": "0"}), \
                patch("nrw_events.http.fetch_url", side_effect=fake_fetch):
            events = beethoven_haus.fetch()

        self.assertEqual([event["title"] for event in events], ["Kammerkonzert: Leonkoro Quartett"])
        event = events[0]
        self.assertEqual(event["start_at"], "2026-10-04T18:00+02:00")
        self.assertEqual(event["venue"], "Beethoven-Haus Bonn")
        self.assertIn("Leonkoro Quartett", event["description"])
        self.assertEqual(
            event["price"],
            "Karten: € 40 | € 20 (Schüler, Studierende etc.) zzgl. Gebühren",
        )
        self.assertEqual(event["link"], DETAIL_URL)
        self.assertEqual(event["source_id"], "beethoven-haus-bonn")
        self.assertEqual(event["category_key"], "concert")
        self.assertEqual(requested, [LIST_URL, DETAIL_URL])

    def test_detail_mentioning_the_hall_sets_the_verified_hall_venue(self):
        context = beethoven_haus._parse_detail(
            '<div class="termin-descr"><p>Jazz im Kammermusiksaal mit Trio.</p></div>'
        )
        self.assertEqual(context["venue"], "Kammermusiksaal Beethoven-Haus")

    def test_source_is_registered_with_stable_id(self):
        self.assertIs(SOURCES["Beethoven-Haus Bonn"], beethoven_haus.fetch)
        self.assertEqual(SOURCE_IDS["Beethoven-Haus Bonn"], "beethoven-haus-bonn")


if __name__ == "__main__":
    unittest.main()
