"""Live-source regressions captured during the 2026-09-10 refresh audit."""
import json
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch

from nrw_events import common
from nrw_events.sources import bonn, cinema_specials
from nrw_events.validation import validate_event

from tests import test_bonn_category_mapping as bonn_tests
from tests.helpers import patch_window


class SeptemberSourceRegressionTests(unittest.TestCase):
    def setUp(self):
        patch_window(self, datetime(2026, 9, 10), datetime(2026, 10, 7))

    def test_live_kinemathek_series_keep_specials_not_ordinary_screenings(self):
        html = (Path(__file__).parent / "fixtures" / "bonner_kinemathek_programme_20260910.html").read_text()
        fetch_detail = Mock(return_value="")
        events = cinema_specials._events_from_bonner_kinemathek(html, fetch_detail)
        canonical = [validate_event(event) for event in events]
        self.assertEqual(
            [(e["title"], e["start_date"], e["time"]) for e in canonical],
            [("Pink Movie Club: Grandma", "2026-09-11", "20:30"),
             ("Fahrradkino: Sommer auf Asphalt", "2026-09-17", "19:30")],
        )
        self.assertEqual(fetch_detail.call_count, 2)
        for event in canonical:
            self.assertEqual(event["category_key"], "cinema")
            self.assertEqual(event["venue"], "Kino in der Brotfabrik")
            self.assertEqual(event["description_source"], "generated")

    def test_kinemathek_named_series_do_not_expand_other_cinema_policy(self):
        self.assertFalse(cinema_specials._is_special_format("Pink Movie Club"))
        self.assertFalse(cinema_specials._is_special_format("Fahrradkino"))

    def test_live_book_json_preserves_workshop_topic_and_unknown_price(self):
        # Actual citykey record is outside the production window; widening only
        # this regression's window verifies its taxonomy without changing dates.
        patch_window(self, datetime(2026, 10, 10), datetime(2026, 10, 12))
        raw = (Path(__file__).parent / "fixtures" / "bonn_kaeptn_book_20260910.json").read_text()
        with (patch.object(common, "fetch_url", return_value=raw),
              patch.object(bonn, "_venue_points", return_value={}),
              patch.object(bonn, "_fetch_detail_context", return_value={}),
              patch.object(bonn, "_fetch_rss_events", return_value=[]),
              patch.object(bonn, "_fetch_free_calendar_events", return_value=[]),
              patch.object(bonn, "_fetch_calendar_listing_events", return_value=[]),
              patch.object(common, "log_source_error") as warning):
            events = bonn.fetch_events_json()
        self.assertEqual(len(events), 1)
        event = validate_event(events[0])
        self.assertEqual(event["title"], json.loads(raw)[0]["title"])
        self.assertEqual(event["start_date"], "2026-10-11")
        self.assertEqual(event["category_key"], "workshop")
        self.assertIsNone(event["admission"]["isFree"])
        warning.assert_not_called()

    def test_book_facet_alone_does_not_admit_an_event(self):
        html = bonn_tests.BonnCategoryMappingTests._listing(
            "Käpt´n Book", "Lesefest Käpt´n Book",
        ).replace("28.07.2026", "28.09.2026")
        with patch.object(common, "log_source_error") as warning:
            self.assertEqual(bonn._calendar_listing_events_from_html(html, "Bonn.de Events"), [])
        warning.assert_not_called()

    def test_kaeptn_book_is_neutral_not_topic_or_free_admission(self):
        self.assertIn("Käpt´n Book", bonn._KNOWN_SOURCE_CATEGORIES)
        self.assertNotIn("Käpt´n Book", bonn._ALLOW)
        self.assertNotIn("Käpt´n Book", bonn._SOURCE_CATEGORY_MAP)
        for category, expected in [("Aktion/Workshop", "workshop"), ("Lesung", "talk")]:
            for prose, admission in [("Öffentliche Veranstaltung", None),
                                     ("Eintritt: 8 Euro", False),
                                     ("Eintritt frei", True)]:
                with self.subTest(category=category, admission=admission):
                    html = bonn_tests.BonnCategoryMappingTests._listing(
                        category + ", Käpt´n Book", "Lesefest Käpt´n Book", prose,
                    ).replace("28.07.2026", "28.09.2026")
                    detail = bonn._parse_detail_context(
                        '<section class="SP-Text"><h2 id="eintritt">Eintritt</h2>'
                        f'<p>{prose}</p></section>'
                    ) if admission is not None else {}
                    with (patch.object(bonn, "_fetch_detail_context", return_value=detail),
                          patch.object(common, "log_source_error") as warning):
                        events = bonn._calendar_listing_events_from_html(html, "Bonn.de Events")
                    self.assertEqual(len(events), 1)
                    event = validate_event(events[0])
                    self.assertEqual(event["category_key"], expected)
                    self.assertIs(event["admission"]["isFree"], admission)
                    warning.assert_not_called()


if __name__ == "__main__":
    unittest.main()
