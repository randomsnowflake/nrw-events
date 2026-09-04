import json
import unittest
from datetime import datetime
from unittest.mock import patch

from nrw_events import common, normalization
from nrw_events.sources import SOURCES, bonn_districts

PAYLOAD = json.dumps([
    {
        "id": "local-1",
        "title": "Familienfest & Flohmarkt",
        "description": "Stadtteilfest mit Flohmarkt, Essen, Musik und Kinderprogramm.",
        "date": "2026-09-05",
        "time": "10:00",
        "end_time": "18:00",
        "location": "Brüser Berg Zentrum",
        "pdf_url": None,
        "link": "https://brueser-berg.de/familienfest",
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
        self.assertEqual(events[1]["link"], "https://brueser-berg.de/familienfest")
        self.assertEqual(events[1]["venue"], "Borsigallee")
        self.assertEqual(events[1]["end_date"], "2026-09-05")
        self.assertEqual(events[1]["end_at"], "2026-09-05T18:00+02:00")
        self.assertTrue(all(event["description"] for event in events))
        self.assertTrue(all(event["source_id"] == "veranstaltungen-brueser-berg" for event in events))
        self.assertTrue(all(event["score"] >= 0.4 for event in events))

    def test_reviewed_family_market_street_replaces_the_generic_pedestrian_zone(self):
        self.assertEqual(
            bonn_districts._brueser_berg_venue(
                "Familienfest mit Flohmarkt",
                "Fußgängerzone Brüser Berg",
            ),
            "Borsigallee",
        )

    def test_reviewed_family_market_street_does_not_override_new_exact_source_venue(self):
        self.assertEqual(
            bonn_districts._brueser_berg_venue(
                "Familienfest mit Flohmarkt",
                "Neue genaue Straße 12",
            ),
            "Neue genaue Straße 12",
        )

    def test_fetch_discovers_the_public_base44_entity_endpoint(self):
        page = '<script>const appId = "6a71c68354b14b3b2e8741d7";</script>'
        calls = []

        def fake_fetch(url, **kwargs):
            calls.append((url, kwargs))
            if url == bonn_districts.BRUESER_BERG_URL:
                return page
            if url.startswith(
                "https://brueser-berg-puls.base44.app/api/apps/"
                "6a71c68354b14b3b2e8741d7/entities/Event?"
            ):
                return PAYLOAD
            raise AssertionError(url)

        with patch.object(common, "fetch_url", side_effect=fake_fetch):
            events = bonn_districts.fetch_brueser_berg()

        self.assertEqual(len(events), 2)
        api_url, api_kwargs = calls[-1]
        self.assertIn("sort=date", api_url)
        self.assertIn("limit=500", api_url)
        self.assertEqual(api_kwargs["headers"]["X-App-Id"], "6a71c68354b14b3b2e8741d7")
        self.assertEqual(api_kwargs["expected_content_types"], ("application/json",))

    def test_fetch_rejects_a_changed_base44_bootstrap_page(self):
        with patch.object(common, "fetch_url", return_value="<html></html>"), \
                patch.object(common, "log_source_error") as log_source_error:
            self.assertEqual(bonn_districts.fetch_brueser_berg(), [])

        error = log_source_error.call_args.args[1]
        self.assertIsInstance(error, bonn_districts.regional_common.ParserEmptyError)
        self.assertIn("application id", str(error))

    def test_nbb_occurrence_uses_full_detail_copy_and_specific_url(self):
        event = {
            "title": "Digitale Nachhilfe",
            "start_date": "2026-09-01",
            "link": "https://www.nachbarschaftszentrum.info/termine/",
            "description": "Gekürzter Teaser […]",
            "time": "16:00",
        }
        calendar = """
<script type="application/ld+json">[{"@type":"Event","name":"Digitale Nachhilfe",
"url":"https://www.nachbarschaftszentrum.info/event/digitale-nachhilfe/2026-09-01/",
"startDate":"2026-09-01T16:00:00","endDate":"2026-09-01T18:00:00"}]</script>
"""
        detail = """
<div class="tribe-events-single-event-description tribe-events-content">
  <p>Hier werden Sie bei der Nutzung Ihres Smartphones unterstützt.</p>
  <p>Fragen zu WhatsApp, Bildern, Wegbeschreibungen und E-Mails sind willkommen.</p>
</div>
"""
        with patch.object(
            common, "fetch_detail_url", side_effect=[calendar, detail]
        ) as fetch_detail:
            [enriched] = bonn_districts._enrich_brueser_berg_details([event])

        self.assertNotIn("[…]", enriched["description"])
        self.assertIn("WhatsApp", enriched["description"])
        self.assertNotIn("Gekürzter Teaser", enriched["description_html"])
        self.assertIn("WhatsApp", enriched["description_html"])
        self.assertEqual(
            enriched["link"],
            "https://www.nachbarschaftszentrum.info/event/digitale-nachhilfe/2026-09-01/",
        )
        self.assertEqual(enriched["time"], "16:00–18:00")
        self.assertEqual(enriched["end_at"], "2026-09-01T18:00:00+02:00")
        self.assertEqual(fetch_detail.call_count, 2)

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

    def test_neighborhood_centre_has_verified_venue_identity(self):
        venue = normalization.resolve_venue("NBB", "Bonn-Brüser Berg")
        self.assertEqual(venue.venue_id, "bonn-nachbarschaftszentrum-brueser-berg")
        self.assertEqual(venue.venue_address, "Fahrenheitstraße 49, 53125 Bonn")
        self.assertEqual(venue.venue_district, "Bonn-Brüser Berg")


if __name__ == "__main__":
    unittest.main()
