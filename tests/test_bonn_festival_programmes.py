import unittest
from datetime import datetime
from unittest.mock import patch

from nrw_events.identity import event_id
from nrw_events.sources import bonn, bonn_districts, bonn_festival_programmes, lupe_events
from nrw_events.validation import validate_event

from tests.helpers import patch_window


class BonnFestivalProgrammeTests(unittest.TestCase):
    def test_beuel_market_uses_the_whole_official_edition_and_keeps_its_id(self):
        patch_window(self, datetime(2026, 11, 28), datetime(2026, 12, 25))
        context = {
            "description": "Der Nikolaus besucht den Markt an allen drei Tagen.",
            "venue_address": "Hermannstraße 35, 53225 Bonn-Beuel",
            "schedules": [
                {"startDate": day, "endDate": day, "startTime": opening, "endTime": closing}
                for day, opening, closing in (
                    ("2026-11-27", "14:00", "21:00"),
                    ("2026-11-28", "11:00", "21:00"),
                    ("2026-11-29", "11:00", "19:00"),
                )
            ],
        }
        with patch.object(bonn, "_fetch_detail_context", return_value=context), \
             patch.object(bonn, "_fetch_rss_events", return_value=[]), \
             patch.object(bonn, "_fetch_json_items", return_value=[]), \
             patch.object(bonn, "_fetch_rss_window_events", return_value=[]) as fallback, \
             patch.object(bonn, "_enrich_listing_details", side_effect=lambda events: events):
            [raw] = bonn.fetch_events()
        fallback.assert_called_once_with("Bonn.de Events", {bonn.BEUEL_NIKOLAUSMARKT_URL})
        event = validate_event(raw)
        self.assertEqual(event_id(event.to_dict()), "nikolausmarkt-beuel-cd57aa7bea")
        self.assertEqual((event.start_date, event.end_date), ("2026-11-27", "2026-11-29"))
        self.assertEqual(event.source_id, "bonn-de-events")
        self.assertEqual(
            [slot["start_at"] for slot in event.daily_schedule],
            ["2026-11-27T14:00:00+01:00", "2026-11-28T11:00:00+01:00", "2026-11-29T11:00:00+01:00"],
        )

    def test_nachlese_uses_the_organizers_start_day(self):
        html = "<p>anschl. Weihnachtliche Nachlese vom 28. - 30.12.26</p>"
        [event] = bonn_districts._bad_godesberg_nachlese(html)
        self.assertEqual((event["start_date"], event["end_date"]), ("2026-12-28", "2026-12-30"))
        self.assertEqual(event["link"], bonn_districts.BAD_GODESBERG_HOME)
        self.assertEqual(bonn_districts._bad_godesberg_nachlese("Weihnachtliche Nachlese vom 30. - 28.12.26"), [])

    def test_replaced_calendar_editions_do_not_suppress_later_years(self):
        godesberg = bonn._BAD_GODESBERG_NIKOLAUSMARKT_URL
        self.assertTrue(bonn._replaced_calendar_occurrence({"link": godesberg, "start_date": "2026-12-25"}))
        self.assertFalse(bonn._replaced_calendar_occurrence({"link": godesberg, "start_date": "2027-12-25"}))
        balloon = bonn._BALLONFESTIVAL_2027_URL + "?loc=de"
        self.assertTrue(bonn._replaced_calendar_occurrence({"link": balloon, "start_date": "2027-06-12"}))

    def test_lupe_rest_does_not_publish_the_artificial_next_morning(self):
        patch_window(self, datetime(2026, 7, 1), datetime(2026, 7, 28))
        raw = {
            "title": "Kirmes in Ippendorf 2026",
            "start_date": "2026-07-03 05:00:00",
            "end_date": "2026-07-06 04:59:59",
            "url": "https://lupe-events.de/event/kirmes-in-ippendorf-2026",
            "venue": [],
            "all_day": True,
        }
        with patch.object(lupe_events.http, "fetch_json", return_value={"events": [raw], "total_pages": 1}):
            [event] = lupe_events.fetch().events
        self.assertEqual((event["start_date"], event["end_date"]), ("2026-07-03", "2026-07-05"))
        self.assertEqual(event["venue"], "Bernhard-Berzheim-Platz")
        self.assertTrue(event["all_day"])

    def test_lannesdorf_keeps_the_inclusive_last_day(self):
        raw = {
            "title": "Kirmes",
            "start_date": "2026-06-12 00:00:00",
            "end_date": "2026-06-15 23:59:59",
            "url": "https://ortsausschuss-lannesdorf.de/kalender/kirmes-2026/",
            "venue": {"venue": "Dorfplatz Lannesdorf", "city": "50.664294, 7.172635"},
            "all_day": True,
        }
        with patch.object(lupe_events.http, "fetch_json", return_value={"events": [raw], "total_pages": 1}):
            [event] = lupe_events.fetch_lannesdorf().events
        self.assertEqual((event["title"], event["city"]), ("Kirmes Lannesdorf", "Bonn-Lannesdorf"))
        self.assertEqual(event["end_date"], "2026-06-15")

    def test_beuel_organizer_page_reads_only_the_dated_spring_festival(self):
        html = "<h1>Veranstaltungen</h1>2027 19. - 21. März 2027 Unsere Veranstaltungen Beueler Frühlingsfest"
        [event] = bonn_festival_programmes._beueler_fruehlingsfest(html)
        self.assertEqual((event["start_date"], event["end_date"]), ("2027-03-19", "2027-03-21"))
        self.assertEqual(bonn_festival_programmes._beueler_fruehlingsfest(html.replace("Frühlingsfest", "Bürgerfest")), [])

    def test_reviewed_programmes_only_emit_confirmed_dates_inside_the_window(self):
        patch_window(self, datetime(2027, 6, 1), datetime(2027, 6, 28))
        outcome = bonn_festival_programmes.fetch_reviewed()
        self.assertEqual(
            [(e["title"], e["start_date"], e["end_date"]) for e in outcome.events],
            [("Ballonfestival Bonn", "2027-06-11", "2027-06-13"),
             ("Bonner Töpfermarkt", "2027-06-12", "2027-06-13")],
        )
        self.assertTrue(all("abwechslungsreiches-veranstaltungsjahr" not in event["link"] for event in outcome.events))
