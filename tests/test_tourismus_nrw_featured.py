import json
import unittest
from dataclasses import replace
from datetime import datetime

from nrw_events import common, early_publication, runner
from nrw_events import report
from nrw_events.runtime import EventWindow
from nrw_events.sources import tourismus_nrw_featured
from tests.helpers import make_runner_env, patch_window


def page(*, identifier=11030, organizer="Bundesstadt Bonn", date_range="11.09.2026 - 15.09.2026"):
    event = {
        "@type": "Event",
        "identifier": identifier,
        "name": "Pützchens Markt",
        "description": "Dieser fremde redaktionelle Text darf nicht publiziert werden.",
        "organizer": {"@type": "Organization", "legalName": organizer},
        "isAccessibleForFree": "http://schema.org/True",
        "image": [{"url": "https://example.test/protected.jpg"}],
    }
    return (
        f'<table><caption>Laufzeit</caption><tr><td>{date_range}</td></tr></table>'
        f'<script type="application/ld+json">{json.dumps(event)}</script>'
    )


class TourismusNrwFeaturedTests(unittest.TestCase):
    def setUp(self):
        patch_window(self, datetime(2026, 8, 11), datetime(2026, 9, 7))

    def test_extracts_only_master_data_and_generates_own_copy(self):
        [event] = tourismus_nrw_featured.events_from_html(page())

        self.assertEqual(event["start_date"], "2026-09-11")
        self.assertEqual(event["end_date"], "2026-09-15")
        self.assertEqual(event["city"], "Bonn-Pützchen")
        self.assertEqual(event["organizer"], "Bundesstadt Bonn")
        self.assertEqual(event["description_source"], "generated")
        self.assertNotIn("fremde redaktionelle", event["description"])
        self.assertNotIn("protected.jpg", repr(event))
        self.assertTrue(event["early_publication"])
        self.assertTrue(early_publication.is_eligible(event))

    def test_rejects_a_changed_identity_or_unverified_organizer(self):
        self.assertEqual(tourismus_nrw_featured.events_from_html(page(identifier=999)), [])
        self.assertEqual(tourismus_nrw_featured.events_from_html(page(organizer="Unbekannt")), [])

    def test_rejects_a_missing_or_backwards_date_range(self):
        self.assertEqual(tourismus_nrw_featured.events_from_html(page(date_range="")), [])
        self.assertEqual(
            tourismus_nrw_featured.events_from_html(page(date_range="15.09.2026 - 11.09.2026")),
            [],
        )

    def test_policy_needs_exact_source_url_title_and_horizon(self):
        [event] = tourismus_nrw_featured.events_from_html(page())
        self.assertFalse(early_publication.is_eligible({**event, "link": "https://example.test/event"}))
        self.assertFalse(early_publication.is_eligible({**event, "title": "Anderer Markt"}))
        self.assertFalse(early_publication.is_eligible({**event, "start_date": "2028-09-11", "end_date": "2028-09-15"}))

    def test_runner_keeps_it_out_of_planner_events_but_publishes_announcement(self):
        [event] = tourismus_nrw_featured.events_from_html(page())
        with make_runner_env() as env:
            context = replace(
                env.context("early", series_ledger_json=""),
                window=EventWindow(datetime(2026, 8, 11), datetime(2026, 9, 7)),
            )
            result = runner.run_import(
                context,
                {"Tourismus NRW Pützchens Markt": lambda: [event]},
            )
            snapshot = runner.build_snapshot(result, context)

        self.assertEqual(result.events, ())
        self.assertEqual(len(result.early_announcements), 1)
        self.assertEqual(snapshot.metadata["event_count"], 0)
        self.assertEqual(snapshot.metadata["early_announcement_count"], 1)
        [published] = snapshot.metadata["early_announcements"]
        self.assertEqual(published["title"], "Pützchens Markt")
        self.assertIsInstance(published["ranking_features"], dict)
        self.assertTrue(published["event_id"].startswith("puetzchens-markt-2026-09-11-"))

    def test_event_moves_into_normal_window_without_a_second_copy(self):
        common.TODAY = datetime(2026, 8, 15)
        common.END_DATE = datetime(2026, 9, 11)
        [event] = tourismus_nrw_featured.events_from_html(page())
        with make_runner_env() as env:
            context = replace(
                env.context("normal", series_ledger_json=""),
                window=EventWindow(datetime(2026, 8, 15), datetime(2026, 9, 11)),
            )
            result = runner.run_import(
                context,
                {"Tourismus NRW Pützchens Markt": lambda: [event]},
            )

        self.assertEqual(len(result.events), 1)
        self.assertEqual(result.early_announcements, ())

    def test_normal_window_deduplicates_the_existing_bonn_annual_record(self):
        [event] = tourismus_nrw_featured.events_from_html(page())
        official_annual = {
            **event,
            "source": "Bundesstadt Bonn Stadtbezirksfeste",
            "source_id": "bonn-district-festivals",
            "link": "https://www.bonn.de/pressemitteilungen/september/puetzchens-markt.php",
            "early_publication": False,
        }
        left = runner.validate_event(event)
        right = runner.validate_event(official_annual)

        self.assertTrue(report.events_are_duplicates(left, right))
        self.assertEqual(len(report.deduplicate([left, right])), 1)


if __name__ == "__main__":
    unittest.main()
