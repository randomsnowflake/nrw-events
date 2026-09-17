"""Regressions from first-party responses captured 2026-09-17 (offline only)."""

import json
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

from nrw_events import common, report, validation
from nrw_events.sources import bonn, bonn_districts

from tests.helpers import patch_window

FIXTURES = Path(__file__).parent / "fixtures"
NEW_URL = "https://brueser-berg-2026.base44.app/"


class ImporterSeptember17Tests(unittest.TestCase):
    def setUp(self):
        patch_window(self, datetime(2026, 9, 17), datetime(2026, 10, 14))

    def test_official_replacement_preserves_local_events_and_source_identity(self):
        # Linked by https://brueser-berg.de/veranstaltungen-2025.html.
        page = (FIXTURES / "brueser_berg_bootstrap_20260917.html").read_text()
        payload = (FIXTURES / "brueser_berg_events_20260917.json").read_text()
        api = NEW_URL + "api/apps/6a71c68354b14b3b2e8741d7/entities/Event?sort=date&limit=500"

        def fetch(url, **kwargs):
            self.assertIn(url, (NEW_URL, api))
            return page if url == NEW_URL else payload

        with (
            patch.object(common, "fetch_url", side_effect=fetch) as request,
            patch.object(bonn_districts, "_enrich_brueser_berg_details", side_effect=lambda rows: rows),
            patch.object(common, "log_source_error") as warning,
        ):
            raw = bonn_districts.fetch_brueser_berg()
        warning.assert_not_called()
        self.assertEqual(request.call_count, 2)
        self.assertEqual(len(raw), 8)
        in_window = [e for e in raw if common.window_contains(
            datetime.fromisoformat(e["start_date"]),
            datetime.fromisoformat(e["end_date"]),
        )]
        self.assertEqual([e["title"] for e in in_window], [
            "Hofflohmarkt Bonn-Brüser Berg", "Energieberatung der Bonner Energie Agentur",
        ])
        canonical = [validation.validate_event(e) for e in in_window]
        self.assertTrue(all(canonical))
        self.assertEqual(len(report.deduplicate(canonical)), 2)
        self.assertTrue(all(e["source_id"] == "veranstaltungen-brueser-berg" for e in canonical))
        self.assertEqual(bonn_districts._brueser_berg_link({}), NEW_URL)

    def test_replacement_outage_is_still_reported(self):
        error = HTTPError(NEW_URL, 503, "Service Unavailable", {}, None)
        with (
            patch.object(common, "fetch_url", side_effect=error),
            patch.object(common, "log_source_error") as warning,
        ):
            self.assertEqual(bonn_districts.fetch_brueser_berg(), [])
        warning.assert_called_once_with(
            "Veranstaltungen Brüser Berg", error, source_id="veranstaltungen-brueser-berg",
        )

    def test_full_capture_reproduces_locality_and_occurrence_counts(self):
        payload = (FIXTURES / "brueser_berg_events_20260917.json").read_text()
        rows = json.loads(payload)
        self.assertEqual(len(rows), 90)
        local = [row for row in rows if bonn_districts._is_brueser_berg_row(row)]
        self.assertEqual(len(local), 8)
        self.assertEqual(len(rows) - len(local), 82)
        expected_dates = [
            "2026-10-11", "2026-10-13", "2026-10-17", "2026-11-05",
            "2026-11-06", "2026-11-08", "2026-11-22", "2026-12-05",
        ]
        patch_window(self, datetime(2026, 9, 17), datetime(2026, 12, 31))
        raw = bonn_districts.events_from_brueser_berg_json(payload)
        self.assertEqual([event["start_date"] for event in raw], expected_dates)
        canonical = [validation.validate_event(event) for event in raw]
        self.assertTrue(all(canonical))
        self.assertEqual(len(report.deduplicate(canonical)), 8)
        self.assertTrue(all(
            event["source_id"] == "veranstaltungen-brueser-berg" for event in canonical
        ))

    def test_captured_congress_cards_follow_existing_conference_exclusion(self):
        html = (FIXTURES / "bonn_congress_20260917.html").read_text()
        cards = bonn.rc.class_tag_blocks(html, "article", "SP-Teaser")
        self.assertEqual(len(cards), 4)
        for card in cards:
            with self.subTest(card=common.clean_html(card)[:160]), patch.object(common, "log_source_error") as warning:
                raw = bonn._listing_events_from_html(
                    '<article class="SP-Teaser">' + card + '</article>',
                    "Bonn.de Events", fetch_details=False,
                )
                self.assertEqual(raw, [])
                warning.assert_not_called()

    def test_both_conference_labels_block_even_with_allowed_or_free_topics(self):
        for label in ("Tagungen/Kongresse", "Tagung/Kongress"):
            self.assertIn(label, bonn._BLOCK)
            self.assertNotIn(label, bonn._SOURCE_CATEGORY_MAP)
            self.assertNotIn(label, bonn._ALLOW)
            self.assertNotIn(label, bonn._FREE_ACTIVITY_ALLOW)
            item = {
                "title": "Öffentliches Konzert", "description": "Eintritt frei",
                "category": [label, "Musik/Konzert", "Kostenlos"],
                "startDate": "2026-09-25 10:00:00", "endDate": "2026-09-25 12:00:00",
                "locationName": "Testort", "locationAddress": "Teststraße 1, Bonn",
                "link": "https://www.bonn.de/test.php",
            }
            with (
                patch.object(common, "fetch_url", return_value=json.dumps([item])),
                patch.object(bonn, "_venue_points", return_value={}),
                patch.object(bonn, "_fetch_rss_events", return_value=[]),
                patch.object(bonn, "_fetch_free_calendar_events", return_value=[]),
                patch.object(bonn, "_fetch_calendar_listing_events", return_value=[]),
                patch.object(common, "log_source_error") as warning,
            ):
                self.assertEqual(bonn.fetch_events_json(), [])
                warning.assert_not_called()
        self.assertEqual(bonn._unknown_source_categories({"Neue unbekannte Kategorie"}), {"Neue unbekannte Kategorie"})
