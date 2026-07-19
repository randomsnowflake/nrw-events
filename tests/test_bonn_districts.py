import json
import unittest
from datetime import datetime
from unittest.mock import patch

from nrw_events import common
from nrw_events.sources import SOURCES, bonn_districts


BEUEL_HTML = """
<div class="yel"><a href="/events/#31.07.2026"><span class="title">Green Juice Festival 2026</span><br>
<b>Fr. 31.07. – 01.08. 23:59</b> | in 17 Tagen | <a href="/map/?q=Park Neu-Vilich">Park Neu-Vilich</a><br>
<a href="https://www.green-juice.de/festival/">externer Link</a></a></div>
<div class="yel"><a href="/events/#14.08.2026"><span class="title">Kirmes Oberkassel</span><br>
<b>Fr. 14.08. – 18.08. 23:59</b> | in 31 Tagen | <a href="/map/?q=Oberkassel">Oberkassel</a><br>
Ab Freitag wird gemeinsam im Stadtteil gefeiert.<br><small>externer Link: <a href="https://example.test/kirmes">mehr</a></small></a></div>
"""

BAD_GODESBERG_HTML = """
<article class="post-6621 kalender type-kalender">
  <h2>19 April, 2026 -</h2><h2>19 April, 2026</h2>
  <h4><a href="https://bad-godesberg.info/veranstaltungen_st/familien-flohmarkt">Familien Flohmarkt</a></h4>
</article>
"""


class BonnDistrictSourceTests(unittest.TestCase):
    def setUp(self):
        self.today = patch.object(common, "TODAY", datetime(2026, 4, 1))
        self.end = patch.object(common, "END_DATE", datetime(2026, 12, 31))
        self.today.start()
        self.end.start()

    def tearDown(self):
        self.end.stop()
        self.today.stop()

    def test_all_sources_are_registered_separately(self):
        self.assertIs(SOURCES["Bürgerverein Vilich-Müldorf"], bonn_districts.fetch_vilich_mueldorf)
        self.assertIs(SOURCES["Beuel.net"], bonn_districts.fetch_beuel)
        self.assertIs(SOURCES["Bad Godesberg Stadtmarketing"], bonn_districts.fetch_bad_godesberg)
        self.assertIs(SOURCES["Hardtberg Kultur"], bonn_districts.fetch_hardtberg)
        self.assertIs(SOURCES["BSV Roleber"], bonn_districts.fetch_roleber)

    def test_new_districts_have_resolvable_coordinates(self):
        for city in (
            "Bonn-Beuel", "Bonn-Bad Godesberg", "Bonn-Duisdorf",
            "Bonn-Oberkassel", "Bonn-Pützchen", "Bonn-Roleber",
            "Bonn-Vilich", "Bonn-Vilich-Müldorf",
        ):
            coordinates, confidence, source = common.resolve_location(city)
            self.assertIsNotNone(coordinates, city)
            self.assertEqual(confidence, "known_city")
            self.assertEqual(source, "configured_city")

    def test_beuel_parser_uses_specific_districts_and_description_fallbacks(self):
        events = bonn_districts.events_from_beuel_html(BEUEL_HTML)

        self.assertEqual([event["city"] for event in events], ["Bonn-Vilich", "Bonn-Oberkassel"])
        self.assertTrue(all(event["description"] for event in events))
        self.assertIn("findet", events[0]["description"])
        self.assertIn("gemeinsam", events[1]["description"])
        self.assertEqual(events[0]["start_date"], "2026-07-31")
        self.assertEqual(events[0]["end_date"], "2026-08-01")
        self.assertEqual(events[0]["time"], "")

    def test_bonn_district_refinement_prefers_specific_configured_place(self):
        self.assertEqual(
            common.refine_city_from_text(
                "Bonn-Beuel", "Spielplatz Bonn Beuel, Vilich-Müldorf"
            ),
            "Bonn-Vilich-Müldorf",
        )
        self.assertEqual(
            common.refine_city_from_text("Bonn-Hardtberg", "Turnhalle in Duisdorf"),
            "Bonn-Duisdorf",
        )
        self.assertEqual(
            common.refine_city_from_text("Köln", "Ausflug nach Vilich"),
            "Köln",
        )

    def test_bad_godesberg_combines_calendar_date_with_detail_copy(self):
        descriptions = {
            "https://bad-godesberg.info/veranstaltungen_st/familien-flohmarkt":
                "Auf der Rigal'schen Wiese wird nach Herzenslust getrödelt."
        }
        events = bonn_districts.events_from_bad_godesberg_html(BAD_GODESBERG_HTML, descriptions)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["date"], "2026-04-19")
        self.assertEqual(events[0]["city"], "Bonn-Bad Godesberg")
        self.assertIn("Herzenslust", events[0]["description"])

    def test_hardtberg_rest_parser_keeps_event_time_and_excerpt(self):
        raw = json.dumps([{
            "date": "2026-07-19T17:00:00",
            "link": "https://www.hardtbergkultur.de/2026/07/19/farbspuren/",
            "title": {"rendered": "Vernissage FARBspuren"},
            "excerpt": {"rendered": "<p>Eine vielfältige Auswahl neuer Arbeiten.</p>"},
            "content": {"rendered": ""},
        }])
        events = bonn_districts.events_from_hardtberg_json(raw)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["city"], "Bonn-Duisdorf")
        self.assertEqual(events[0]["time"], "17:00")
        self.assertIn("vielfältige Auswahl", events[0]["description"])

    def test_ical_wrappers_guarantee_a_description(self):
        empty_event = {
            "title": "Wochenmarkt", "description": "", "start_date": "2026-07-14",
            "time": "15:00", "venue": "Mühlenbachhalle", "city": "Bonn-Vilich-Müldorf",
        }
        with patch.object(bonn_districts.common, "fetch_ical", return_value=[empty_event]):
            events = bonn_districts.fetch_vilich_mueldorf()

        self.assertIn("Wochenmarkt", events[0]["description"])
        self.assertIn("Mühlenbachhalle", events[0]["description"])

    def test_roleber_replaces_low_signal_registration_copy(self):
        event = {
            "title": "Sommer: Fußballcamp", "description": "featured by Stegis Kicker",
            "start_date": "2026-07-27", "time": "08:00–16:00", "venue": "",
            "city": "Bonn-Roleber", "link": "https://bsvroleber.de/event/sommer-fussballcamp/",
            "score": 0.23,
        }
        with patch.object(bonn_districts.common, "fetch_ical", return_value=[event]), \
                patch.object(bonn_districts.common, "fetch_detail_url", return_value=""):
            events = bonn_districts.fetch_roleber()

        self.assertIn("findet", events[0]["description"])
        self.assertIn("Bonn-Roleber", events[0]["description"])
        self.assertEqual(events[0]["score"], 0.45)


if __name__ == "__main__":
    unittest.main()
