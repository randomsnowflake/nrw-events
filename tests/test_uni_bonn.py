import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from nrw_events import common
from nrw_events.sources import SOURCES, SOURCE_IDS, uni_bonn
from tests.helpers import patch_window


CHOIR_URL = (
    "https://www.uni-bonn.de/de/veranstaltungen/"
    "sommerkonzert-internationaler-chor-1"
)
FIXTURES = Path(__file__).parent / "fixtures" / "uni-bonn"
ICAL = (FIXTURES / "calendar.ics").read_text(encoding="utf-8")
DETAIL_HTML = (FIXTURES / "choir-detail.html").read_text(encoding="utf-8")


class UniBonnSourceTests(unittest.TestCase):
    def setUp(self):
        patch_window(self, datetime(2026, 7, 19), datetime(2026, 8, 2))

    def test_official_calendar_enriches_choir_event_with_detail_venue(self):
        requested = []

        def fake_fetch(url, **_kwargs):
            requested.append(url)
            if url == uni_bonn._ICAL_URL:
                return ICAL
            if url == CHOIR_URL:
                return DETAIL_HTML
            raise AssertionError(f"unexpected URL {url}")

        with patch.dict("os.environ", {"NRW_EVENTS_DETAIL_CACHE_TTL_HOURS": "0"}), \
                patch.object(common, "fetch_url", side_effect=fake_fetch):
            events = uni_bonn.fetch()

        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event["title"], "Internationaler Chor: Sommerkonzert")
        self.assertEqual(event["date"], "2026-07-20")
        self.assertEqual(event["time"], "20:00–21:15")
        self.assertEqual(event["start_at"], "2026-07-20T20:00+02:00")
        self.assertEqual(event["end_at"], "2026-07-20T21:15+02:00")
        self.assertEqual(
            event["venue"],
            "Hörsaalzentrum Campus Poppelsdorf, Hörsaal 1",
        )
        self.assertEqual(event["city"], "Bonn")
        self.assertIn("Liedern aus aller Welt", event["description"])
        self.assertEqual(event["price"], "kostenlos")
        self.assertEqual(event["link"], CHOIR_URL)
        self.assertEqual(event["source"], "Universität Bonn")
        self.assertEqual(event["source_id"], "uni-bonn")
        self.assertEqual(event["category_key"], "concert")
        self.assertEqual(requested, [uni_bonn._ICAL_URL, CHOIR_URL])

    def test_detail_failure_keeps_complete_ical_record(self):
        with patch.object(common, "log_source_error"):
            events = uni_bonn._enrich_details(
                [{
                    "title": "Campus event",
                    "description": "Description from iCal",
                    "venue": "",
                    "link": "https://www.uni-bonn.de/de/veranstaltungen/campus-event",
                }],
                detail_fetcher=lambda _url: (_ for _ in ()).throw(TimeoutError("detail timeout")),
            )

        self.assertEqual(events[0]["description"], "Description from iCal")
        self.assertEqual(events[0]["venue"], "")

    def test_mixed_free_and_paid_admission_preserves_all_price_tiers(self):
        price = "Freier Eintritt für Mitglieder; 2,00 Euro ermäßigt; 3,00 Euro regulär"

        event = uni_bonn._merge_context(
            {"title": "Campus event", "description": "Öffentliche Veranstaltung"},
            {"price": price},
        )

        self.assertEqual(event["price"], price)

    def test_long_duration_is_only_kept_for_exhibitions(self):
        start = datetime(2026, 6, 18, 18)

        self.assertFalse(uni_bonn._valid_duration(
            {"SUMMARY": "Juneteenth Lecture", "DESCRIPTION": "Public lecture"},
            start,
            datetime(2026, 12, 6, 21, 30),
        ))
        self.assertTrue(uni_bonn._valid_duration(
            {"SUMMARY": "Kunstkammer", "DESCRIPTION": "Neue Sonderausstellung"},
            start,
            datetime(2027, 5, 27),
        ))

    def test_detail_context_deduplicates_identical_venue_and_room(self):
        html = """
        <div class="content-item"><div class="item-title">Ort</div>
        <div class="item-value">Zoom (Link wird später veröffentlicht)</div></div>
        <div class="content-item"><div class="item-title">Raum</div>
        <div class="item-value">Zoom</div></div>
        """

        self.assertEqual(
            uni_bonn._parse_detail_context(html)["venue"],
            "Zoom (Link wird später veröffentlicht)",
        )

    def test_welcome_days_categories_are_talks_not_cinema_or_sports(self):
        event = {
            "title": "Welcome Days: Info Wohnraum",
            "description": "Informationen und Fragerunde",
            "category": "International Office,Welcome Days,Studierende",
            "category_key": "cinema",
        }

        uni_bonn._correct_categories([event])

        self.assertEqual(event["category_key"], "talk")

    def test_detail_parser_keeps_nested_markup_and_reorders_address_first_location(self):
        html = """
        <div class="content-item">
          <div class="item-title">Ort:</div>
          <div class="item-value">
            Brühler Str. 7, 53119 Bonn, <strong>Transfer Center enaCom</strong>
          </div>
        </div>
        """

        context = uni_bonn._parse_detail_context(html)

        self.assertEqual(
            context["venue"],
            "Transfer Center enaCom, Brühler Str. 7, 53119 Bonn",
        )

    def test_reviewed_hands_on_series_are_workshops(self):
        events = [
            {
                "title": "Cake Baking Evening",
                "description": (
                    "Backe gemeinsam mit anderen Studierenden leckere Kuchen "
                    "und tauscht Rezepte aus."
                ),
                "category": "International Office,Campus International,Internationaler Club",
                "category_key": "other",
            },
            {
                "title": "Game Design Studio Session - Part 16",
                "description": (
                    "Spieleentwickler*innen stellen Spielideen vor, probieren Prototypen "
                    "aus und entwickeln analoge Spielformate weiter."
                ),
                "category": "Games Community,Innovationen",
                "category_key": "other",
            },
        ]

        uni_bonn._correct_categories(events)

        self.assertEqual([event["category_key"] for event in events], ["workshop", "workshop"])
        self.assertTrue(all(event["category_confidence"] == 1.0 for event in events))
        self.assertTrue(all(event["category_reason"] == "source:workshop" for event in events))

    def test_unrelated_social_and_game_events_are_not_forced_to_workshop(self):
        events = [
            {
                "title": "Chit-Chat Lounge",
                "description": "Gemeinsam andere Studierende kennenlernen.",
                "category": "International Club",
                "category_key": "activities",
            },
            {
                "title": "Public Game Night",
                "description": "Spiele ausprobieren und andere Menschen treffen.",
                "category": "Games Community",
                "category_key": "activities",
            },
        ]

        uni_bonn._correct_categories(events)

        self.assertEqual([event["category_key"] for event in events], ["activities", "activities"])

    def test_source_is_registered_with_stable_id(self):
        self.assertIs(SOURCES["Universität Bonn"], uni_bonn.fetch)
        self.assertEqual(SOURCE_IDS["Universität Bonn"], "uni-bonn")


if __name__ == "__main__":
    unittest.main()
