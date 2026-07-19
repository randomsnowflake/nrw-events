import unittest
from datetime import datetime
from unittest.mock import patch

from scripts.nrw_events import common
from scripts.nrw_events.sources import (
    junges_theater_bonn,
    kleines_theater,
    theater_bonn,
    theater_im_ballsaal,
    theater_marabu,
    tik_bonn,
)
from scripts.nrw_events.validation import canonicalize_event


class BonnTheaterSourceTests(unittest.TestCase):
    def setUp(self):
        self.old_today = common.TODAY
        self.old_end_date = common.END_DATE
        common.TODAY = datetime(2026, 7, 19)
        common.END_DATE = datetime(2026, 12, 31)

    def tearDown(self):
        common.TODAY = self.old_today
        common.END_DATE = self.old_end_date

    def assert_canonical(self, event, source_id):
        canonical = canonicalize_event(event)
        self.assertEqual(canonical.city, "Bonn")
        self.assertEqual(canonical.source_id, source_id)
        self.assertTrue(canonical.title)
        self.assertTrue(canonical.start_date)
        self.assertTrue(canonical.venue)
        self.assertTrue(canonical.description)
        self.assertTrue(canonical.link.startswith("https://"))

    def test_kleines_theater_fetches_each_month_and_deduplicates_overlap(self):
        sample = common.make_event(
            "Der Tatortreiniger", datetime(2026, 9, 4, 19, 30), None,
            "Kleines Theater", "Bonn", "Eine Theaterkomödie.",
            "https://kleinestheater.eu/event/der-tatortreiniger/",
            "Kleines Theater Bad Godesberg", "theater bühne", source_id="kleines-theater",
        )
        with patch.object(common, "fetch_ical", return_value=[sample]) as fetch_ical:
            events = kleines_theater.fetch()
        self.assertEqual(len(events), 1)
        self.assertEqual(fetch_ical.call_count, 6)
        self.assertIn("tribe-bar-date=2026-07-01", fetch_ical.call_args_list[0].args[0])
        self.assertIn("tribe-bar-date=2026-12-01", fetch_ical.call_args_list[-1].args[0])
        self.assertTrue(all(
            call.kwargs["source_id"] == "kleines-theater"
            for call in fetch_ical.call_args_list
        ))
        self.assert_canonical(events[0], "kleines-theater")

    def test_kleines_theater_keeps_plays_out_of_outdoor_sports_and_concert(self):
        events = kleines_theater._correct_stage_formats([
            {
                "title": "MARILYN & ICH – Komödie",
                "description": "Open-Air-Aufführung im Garten.",
                "category": "theater bühne",
                "category_key": "outdoor",
            },
            {
                "title": "MACBETH – William Shakespeare",
                "description": "Ein erbitterter Kampf um die Krone.",
                "category": "theater bühne",
                "category_key": "sports",
            },
            {
                "title": "2:22 Uhr – eine Geistergeschichte",
                "description": "Mystery Thriller auf der Bühne.",
                "category": "theater bühne",
                "category_key": "concert",
            },
            {
                "title": "MUSIK unter der ZEDER – The Rhythm Section Band",
                "description": "Live im Kleinen Theater.",
                "category": "theater bühne",
                "category_key": "stage",
            },
        ])

        self.assertEqual(
            [event["category_key"] for event in events],
            ["stage", "stage", "stage", "concert"],
        )

    def test_theater_bonn_uses_ticket_link_and_factual_description(self):
        payload = [{
            "id": 42,
            "title": "Die Zauberflöte",
            "date_full": "04.09.2026",
            "date_time": "19:30 Uhr",
            "description": "",
            "status": "",
            "categories": [{"name": "Oper"}],
            "tags": [{"name": "Oper"}, {"name": "Opernhaus"}],
            "genre_names": ["Oper"],
            "ticket": {"url": "https://tickets.theater-bonn.de/42", "ticket_info": "Tickets verfügbar"},
        }, {
            "id": 43, "title": "Abgesagt", "date_full": "05.09.2026",
            "date_time": "20:00 Uhr", "status": "abgesagt",
        }]
        events = theater_bonn.events_from_payload(payload)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["venue"], "Opernhaus")
        self.assertEqual(events[0]["time"], "19:30")
        self.assertEqual(events[0]["category_key"], "stage")
        self.assertEqual(events[0]["link"], "https://tickets.theater-bonn.de/42")
        self.assertIn("Veranstaltungsort", events[0]["description"])
        self.assert_canonical(events[0], "theater-bonn")

    def test_junges_theater_parses_regular_and_kulturgarten_rows(self):
        html = """
        <div class="event-list-rowflex"><div class="cal-date">05.09.2026</div>
          <div class="event-flex-item one">
            <div class="cal-list-item clearfix">
              <div class="event-title"><a href="stuecke/das-neinhorn/">Das NEINhorn</a></div>
              <div class="tickets pull-left"><div>15:00 Uhr</div></div><div class="tickets pull-right"></div>
            </div>
            <div class="cal-list-item clearfix">
              <div class="event-title"><a href="stuecke/das-neinhorn/">Das NEINhorn</a></div>
              <div class="tickets pull-left"><div>17:00 Uhr</div></div><div class="tickets pull-right"></div>
            </div>
          </div>
          <div class="event-flex-item two">
            <div class="cal-list-item clearfix">
              <div class="event-title"><a href="stuecke/pettersson/">JTB im Kulturgarten am PostTower</a></div>
              <div class="tickets pull-left"><div>18:00 Uhr</div>PETTERSSON UND FINDUS</div>
              <div class="tickets pull-right"><a class="ticket-button" href="https://tickets.example/pettersson">Tickets</a></div>
            </div>
          </div>
          <div class="event-flex-item three"></div>
        </div>
        """
        events = junges_theater_bonn.events_from_html(
            html, lambda _url: "Eine fantasievolle Familienaufführung auf der Bühne."
        )
        self.assertEqual(
            [event["title"] for event in events],
            ["Das NEINhorn", "Das NEINhorn", "PETTERSSON UND FINDUS"],
        )
        self.assertEqual([event["time"] for event in events[:2]], ["15:00", "17:00"])
        self.assertEqual(events[2]["venue"], "JTB im Kulturgarten am PostTower")
        self.assertEqual(events[2]["time"], "18:00")
        self.assertEqual(events[2]["link"], "https://tickets.example/pettersson")
        self.assertNotIn("fantasievolle Familienaufführung", events[2]["description"])
        self.assert_canonical(events[0], "junges-theater-bonn")

    def test_marabu_keeps_bonn_performances_and_filters_touring_dates(self):
        html = """
        <li class="spieltermin-item">
          <div class="spieltermin-datum">FR<span>04</span>SEP</div>
          <div class="spieltermin-meta"><span>19:00 Uhr</span></div>
          <div class="spieltermin-title"><a href="https://www.theater-marabu.de/radio-350/">Radio 350</a></div>
          <div class="spieltermin-submeta">Altersempfehlung ab 14 Jahren | Bonn, Theater Marabu</div>
          <a class="getTicket" data-vorstellung="Radio 350 | 04.09.2026 | 19:00 Uhr"></a>
        </li>
        <li class="spieltermin-item">
          <div class="spieltermin-datum">SA<span>05</span>SEP</div>
          <div class="spieltermin-title"><a href="/tour/">Gastspiel</a></div>
          <div class="spieltermin-submeta">Berlin, Theater an der Parkaue</div>
        </li>
        """
        events = theater_marabu.events_from_html(
            html, lambda _url: "Ein dokumentarisches Theaterstück über Jugend und Radio."
        )
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["title"], "Radio 350")
        self.assertIn("ab 14 Jahren", events[0]["description"])
        self.assert_canonical(events[0], "theater-marabu")

    def test_ballsaal_parses_table_row_and_preserves_primary_link(self):
        html = """
        <table><tr><td>Fr</td><td>04.09.</td><td>18.30</td>
          <td><a href="https://theater-im-ballsaal.de/stueck/radioballett/">Radioballett</a><br>Eine Performance im öffentlichen Raum.</td>
          <td>Performance / Tanztheater</td>
          <td><a href="https://tickets.example/radioballett">Tickets</a></td>
        </tr></table>
        """
        events = theater_im_ballsaal.events_from_html(
            html, lambda _url: "Eine choreografische Performance für das Publikum."
        )
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["link"], "https://theater-im-ballsaal.de/stueck/radioballett/")
        self.assertEqual(events[0]["time"], "18:30")
        self.assertEqual(events[0]["category_key"], "stage")
        self.assert_canonical(events[0], "theater-im-ballsaal")

    def test_tik_reads_event_date_time_and_description_from_rss(self):
        rss = """<?xml version="1.0" encoding="UTF-8"?>
        <rss xmlns:content="http://purl.org/rss/1.0/modules/content/" version="2.0"><channel><item>
          <title>Die Physiker</title>
          <link>https://tik-bonn.de/die-physiker/</link>
          <pubDate>Fri, 04 Sep 2026 00:00:00 +0200</pubDate>
          <content:encoded><![CDATA[<p>Beginn 20:00 Uhr. Eine schwarze Komödie von Dürrenmatt.</p>]]></content:encoded>
        </item></channel></rss>"""
        events = tik_bonn.events_from_rss(rss)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["time"], "20:00")
        self.assertEqual(events[0]["category_key"], "stage")
        self.assertIn("schwarze Komödie", events[0]["description"])
        self.assert_canonical(events[0], "tik-theater-im-keller")


if __name__ == "__main__":
    unittest.main()
