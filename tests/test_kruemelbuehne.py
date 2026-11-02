import json
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from nrw_events import config, http
from nrw_events.retention_policy import _publication_filter_reason
from nrw_events.sources import kruemelbuehne
from nrw_events.validation import canonicalize_event

from tests.helpers import patch_window

FIXTURE = Path(__file__).parent / "fixtures" / "kruemelbuehne_posts_20261101.json"


class KruemelbuehneTests(unittest.TestCase):
    def setUp(self):
        patch_window(self, datetime(2026, 11, 1), datetime(2026, 12, 31))

    def test_keeps_touring_plays_and_leaves_the_brotfabrik_to_its_own_calendar(self):
        events = kruemelbuehne.events_from_json(FIXTURE.read_text(encoding="utf-8"))
        by_title = {event["title"]: event for event in events}
        # The festival overview, the in-house "Der kleine Weihnachtsmann" and the Silvester party drop out.
        self.assertEqual(
            sorted(by_title),
            ["Alle seine Entlein", "Der Zauberer von Oz", "Peter und der Wolf", "Tigerwild!", "Weihnachtsfest mitohne Gans", "Zwerg Nase"],
        )
        oz = by_title["Der Zauberer von Oz"]
        self.assertEqual((oz["start_date"], oz["time"]), ("2026-11-07", "11:00"))
        self.assertEqual(oz["venue"], "Stadtteilverein Dransdorf")
        self.assertEqual(oz["venue_address"], "Lenaustraße 14, 53121 Bonn")
        self.assertEqual(oz["price"], "Eintritt auf Spendenbasis")
        self.assertEqual(oz["details"], {"age": "ab 4 Jahren"})
        self.assertIn("Wirbelsturm", oz["description"])
        tiger = by_title["Tigerwild!"]
        self.assertEqual(tiger["venue_address"], "Colmantstr. 14 – 16, Bonn")
        self.assertEqual(tiger["price"], "11,- / 8,-")
        self.assertEqual(by_title["Zwerg Nase"]["category_key"], "cinema")
        for event in events:
            canonical = canonicalize_event(event)
            self.assertEqual(canonical.source_id, "kruemelbuehne-bonn")
            self.assertEqual(canonical.city, "Bonn")
            self.assertTrue(canonical.link.startswith("https://www.brotfabrik-theater.de/"))
            self.assertEqual(_publication_filter_reason(canonical, config.RuntimeConfig()), "", event["title"])

    def test_fetch_records_an_empty_but_valid_post_list_as_healthy(self):
        with patch.object(http, "fetch_url", return_value=json.dumps([])):
            self.assertEqual(kruemelbuehne.fetch(), [])
        with patch.object(http, "fetch_url", return_value=FIXTURE.read_text(encoding="utf-8")):
            self.assertEqual(len(kruemelbuehne.fetch()), 6)


if __name__ == "__main__":
    unittest.main()
