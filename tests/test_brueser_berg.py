import json
import unittest
from datetime import datetime
from unittest.mock import patch

from nrw_events import common
from nrw_events.sources import SOURCES, bonn_districts


PAYLOAD = json.dumps([
    {
        "id": "local-1",
        "title": "Familienfest & Flohmarkt",
        "description": "Stadtteilfest mit Flohmarkt, Essen, Musik und Kinderprogramm.",
        "event_date": "2026-09-05",
        "event_time": "10:00:00",
        "location": "Brüser Berg Zentrum",
        "pdf_url": None,
        "contribution_link": None,
    },
    {
        "id": "local-2",
        "title": "Figuren gestalten",
        "description": "Workshop im Atelier.",
        "event_date": "2026-08-04",
        "event_time": "10:30:00",
        "location": "Atelier der Stadtteilkultur",
        "pdf_url": "https://files.example.test/figuren.pdf",
        "contribution_link": None,
    },
    {
        "id": "outside-1",
        "title": "Konzert am Rhein",
        "description": "Großes Konzert.",
        "event_date": "2026-08-06",
        "event_time": "19:00:00",
        "location": "KUNST!RASEN Bonn",
        "pdf_url": None,
        "contribution_link": "https://example.test/concert",
    },
])


class BrueserBergSourceTests(unittest.TestCase):
    def setUp(self):
        self.today = patch.object(common, "TODAY", datetime(2026, 8, 1))
        self.end = patch.object(common, "END_DATE", datetime(2026, 12, 31))
        self.today.start()
        self.end.start()

    def tearDown(self):
        self.end.stop()
        self.today.stop()

    def test_source_is_registered_separately(self):
        self.assertIs(SOURCES["Veranstaltungen Brüser Berg"], bonn_districts.fetch_brueser_berg)

    def test_parser_keeps_only_local_rows_and_uses_specific_district(self):
        events = bonn_districts.events_from_brueser_berg_json(PAYLOAD)

        self.assertEqual([event["title"] for event in events], ["Figuren gestalten", "Familienfest & Flohmarkt"])
        self.assertTrue(all(event["city"] == "Bonn-Brüser Berg" for event in events))
        self.assertEqual(events[0]["time"], "10:30")
        self.assertEqual(events[0]["link"], "https://files.example.test/figuren.pdf")
        self.assertEqual(events[1]["link"], bonn_districts.BRUESER_BERG_URL)
        self.assertTrue(all(event["description"] for event in events))
        self.assertTrue(all(event["source_id"] == "veranstaltungen-brueser-berg" for event in events))
        self.assertTrue(all(event["score"] >= 0.4 for event in events))

    def test_fetch_discovers_the_public_supabase_read_endpoint(self):
        page = '<script type="module" src="/assets/index-AbCd1234.js"></script>'
        bundle = 'const C="https://projectref.supabase.co",A="eyJpublic-anon-token",d=createClient(C,A);'
        calls = []

        def fake_fetch(url, **kwargs):
            calls.append((url, kwargs))
            if url == bonn_districts.BRUESER_BERG_URL:
                return page
            if url.endswith("/assets/index-AbCd1234.js"):
                return bundle
            if url.startswith("https://projectref.supabase.co/rest/v1/events?"):
                return PAYLOAD
            raise AssertionError(url)

        with patch.object(common, "fetch_url", side_effect=fake_fetch):
            events = bonn_districts.fetch_brueser_berg()

        self.assertEqual(len(events), 2)
        api_url, api_kwargs = calls[-1]
        self.assertIn("select=", api_url)
        self.assertEqual(api_kwargs["headers"]["apikey"], "eyJpublic-anon-token")
        self.assertEqual(api_kwargs["expected_content_types"], ("application/json",))

    def test_parser_rejects_changed_or_empty_payloads(self):
        for payload in ('{"events": []}', '[]'):
            with self.subTest(payload=payload):
                with self.assertRaises(bonn_districts.regional_common.ParserEmptyError):
                    bonn_districts.events_from_brueser_berg_json(payload)

    def test_brueser_berg_has_resolvable_coordinates(self):
        coordinates, confidence, source = common.resolve_location("Bonn-Brüser Berg")
        self.assertIsNotNone(coordinates)
        self.assertEqual(confidence, "known_city")
        self.assertEqual(source, "configured_city")


if __name__ == "__main__":
    unittest.main()
