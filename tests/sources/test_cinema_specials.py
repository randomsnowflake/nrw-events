import json
import unittest
from datetime import datetime
from unittest.mock import patch

from scripts.nrw_events import common
from scripts.nrw_events.health import SourceResult
from scripts.nrw_events.sources import SOURCES, cinema_specials
from scripts.nrw_events.validation import validate_event


class CinemaSpecialSourceTests(unittest.TestCase):
    def setUp(self):
        self.old_today = common.TODAY
        self.old_end_date = common.END_DATE
        common.TODAY = datetime(2026, 7, 19)
        common.END_DATE = datetime(2026, 9, 30)

    def tearDown(self):
        common.TODAY = self.old_today
        common.END_DATE = self.old_end_date

    def assert_valid_cinema_events(self, events):
        self.assertTrue(events)
        for event in events:
            validated = validate_event(event)
            self.assertEqual(validated["category_key"], "cinema")
            self.assertTrue(validated["title"])
            self.assertTrue(validated["start_date"])
            self.assertTrue(validated["link"])

    def test_source_is_registered(self):
        self.assertIn("Curated cinema specials", SOURCES)

    def test_bonner_kinemathek_rejects_ordinary_screenings(self):
        listing = """
<div class="em-event em-item em-list-item" data-href="/programm/festival-film/">
  <h3 class="em-item-title"><a href="/programm/festival-film/">Ukrainische Filmtage: Film A</a></h3>
  <div class="em-item-meta-line em-event-date"><span></span>21.07.2026</div>
  <div class="em-item-meta-line em-event-time"><span class="em-icon-clock"></span>18:00</div>
  <div class="em-item-meta-line em-event-time"><span class="em-icon-tag"></span><a>Ukrainische Filmtage</a></div>
  <div class="em-item-meta-line em-event-location"><span></span>Kino in der Brotfabrik</div>
</div>
<div class="em-event em-item em-list-item" data-href="/programm/normaler-film/">
  <h3 class="em-item-title"><a href="/programm/normaler-film/">Normaler Film</a></h3>
  <div class="em-item-meta-line em-event-date"><span></span>22.07.2026</div>
  <div class="em-item-meta-line em-event-time"><span class="em-icon-clock"></span>20:00</div>
  <div class="em-item-meta-line em-event-time"><span class="em-icon-tag"></span><a>Deutsches Kino</a></div>
  <div class="em-item-meta-line em-event-location"><span></span>Kino in der Brotfabrik</div>
</div>
"""
        detail = """
<div class="em-event-notes"><p>Im Film führt ein regelmäßig tagender Stadtrat ein wiederkehrendes Gespräch.</p></div>
<div class="em-event-location">Kino in der Brotfabrik</div>
<a class="ticketbtn" href="https://tickets.example/festival-film">Tickets</a>
"""

        events = cinema_specials._events_from_bonner_kinemathek(listing, lambda _: detail)

        self.assertEqual([event["title"] for event in events], ["Ukrainische Filmtage: Film A"])
        self.assertEqual(events[0]["time"], "18:00")
        self.assertEqual(
            events[0]["link"],
            "https://www.bonnerkinemathek.de/programm/festival-film/",
        )
        self.assert_valid_cinema_events(events)

    def test_rex_filmbuehne_keeps_specials_and_expands_series_occurrences(self):
        html = """
<div class="vorschau">
  <div class="row film"><h2 class="col-md-12">Jim-Jarmusch-Reihe</h2></div>
  <div class="row film_termin"><h4 class="col-md-12 termin">Ab Juli im Rex</h4></div>
  <div class="filmbox row"><div class="beschreibung col-md-12">
    <strong>Mittwoch, 22.07. um 20:15 Uhr im Rex-Kino<br>The Dead Don't Die OmU<br></strong>
    Eine Retrospektive mit Filmen von Jim Jarmusch.
    <strong>Montag, 27.07. um 20:45 Uhr im Rex-Kino<br>Only Lovers Left Alive<br></strong>
  </div></div>
</div>
<div class="vorschau">
  <div class="row film"><h2 class="col-md-12">Normaler Kinofilm</h2></div>
  <div class="row film_termin"><h4 class="col-md-12 termin">Ab 23.07. im Rex</h4></div>
  <div class="filmbox row"><div class="beschreibung col-md-12">Regulärer Kinostart.</div></div>
</div>
"""

        events = cinema_specials._events_from_rex_filmbuehne(html)

        self.assertEqual(
            [event["title"] for event in events],
            [
                "Jim-Jarmusch-Reihe: The Dead Don't Die OmU",
                "Jim-Jarmusch-Reihe: Only Lovers Left Alive",
            ],
        )
        self.assertEqual([event["date"] for event in events], ["2026-07-22", "2026-07-27"])
        self.assertEqual([event["time"] for event in events], ["20:15", "20:45"])
        self.assertTrue(all(event["venue"] == "Rex-Lichtspieltheater" for event in events))
        self.assertTrue(all(event["source_id"] == "rex-filmbuehne-specials" for event in events))
        self.assert_valid_cinema_events(events)

    def test_rex_filmbuehne_detects_guest_screening_without_importing_release_date(self):
        html = """
<div class="vorschau">
  <div class="row film"><h2 class="col-md-12">To My Sisters</h2></div>
  <div class="row film_termin"><h4 class="col-md-12 termin">Ab 23.07. | Preview: Mittwoch, 22.07. um 18:15 Uhr in der Neuen Filmbühne</h4></div>
  <div class="filmbox row"><div class="beschreibung col-md-12">
    <em><strong>Mittwoch, 22.07. um 18:15 Uhr in Anwesenheit des Regisseurs in der Neuen Filmbühne.</strong></em>
    Eine einmalige Vorstellung mit anschließendem Gespräch.
  </div></div>
</div>
"""

        events = cinema_specials._events_from_rex_filmbuehne(html)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["title"], "To My Sisters")
        self.assertEqual(events[0]["date"], "2026-07-22")
        self.assertEqual(events[0]["time"], "18:15")
        self.assertEqual(events[0]["venue"], "Neue Filmbühne")
        self.assertIn("Anwesenheit des Regisseurs", events[0]["description"])
        self.assert_valid_cinema_events(events)

    def test_stummfilmtage_builds_dates_from_tabs_and_skips_empty_day(self):
        html = """
<section id="spielplan-calendar">
  <div>Programm <br/>August 2026</div>
  <a data-w-tab="Tab 1"><div class="headline-date">13</div></a>
  <a data-w-tab="Tab 2"><div class="headline-date">14</div></a>
  <div class="tabs-content w-tab-content">
    <div data-w-tab="Tab 1" class="w-tab-pane">
      <div role="listitem" class="collection-item-10 w-dyn-item">
        <a href="/filmsammlung/a"><div class="cms-datum">21:15</div>
        <h3 class="cms-headline">Restaurierter Stummfilm</h3>
        <div class="cms-text">Deutschland 1926, 80 Min.</div>
        <div class="cms-text">Live am Flügel</div></a>
      </div>
    </div>
    <div data-w-tab="Tab 2" class="w-tab-pane">
      <div role="listitem" class="collection-item-10 w-dyn-item">
        <a href="/filmsammlung/empty"><div class="cms-datum">21:15</div>
        <h3 class="cms-headline">Keine Filmvorführung</h3></a>
      </div>
    </div>
  </div>
</section>
"""

        events = cinema_specials._events_from_stummfilmtage(html)

        self.assertEqual([event["title"] for event in events], ["Restaurierter Stummfilm"])
        self.assertEqual(events[0]["date"], "2026-08-13")
        self.assertEqual(events[0]["price"], "kostenlos")
        self.assertNotIn("Rahmenprogramm", events[0]["description"])
        self.assert_valid_cinema_events(events)

    def test_filmhaus_requires_special_tag_and_converts_utc(self):
        records = [
            {
                "title": "Open-Air-Film",
                "slug": "open-air-film",
                "date": "2026-07-20T19:45:00.000Z",
                "end_date": "2026-07-20T21:45:00.000Z",
                "description": "<p>Projektion auf dem Parkplatz.</p>",
                "tags": [{"title": "Kino"}, {"title": "Open Air"}],
            },
            {
                "title": "Normale Vorstellung",
                "slug": "normal",
                "date": "2026-07-21T18:00:00.000Z",
                "description": "<p>Reguläre Vorstellung.</p>",
                "tags": [{"title": "Kino"}, {"title": "Allgemein"}],
            },
        ]

        events = cinema_specials._events_from_filmhaus_json(json.dumps(records))

        self.assertEqual([event["title"] for event in events], ["Open-Air-Film"])
        self.assertEqual(events[0]["time"], "21:45–23:45")
        self.assertEqual(events[0]["venue"], "Filmhaus Köln – Open-Air-Kino")
        self.assert_valid_cinema_events(events)

    def test_kurzfilmwanderung_extracts_live_event_details(self):
        html = """
<p><strong>Was ist die Kurzfilmwanderung Bonn?</strong><br>
Ein mobiles Kurzfilmfestival mit Projektionen im öffentlichen Raum.</p>
<h1><strong>KURZFILMWANDERUNG BONN 2026<br>
<em>Stadtspaziergang | Open-Air-Kino | Begegnung</em><br>
Sa, 19. September 2026 ab</strong> 20<strong> Uhr<br>Bonn Auerberg</strong></h1>
"""

        events = cinema_specials._events_from_kurzfilmwanderung(html)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["date"], "2026-09-19")
        self.assertEqual(events[0]["time"], "20:00")
        self.assertEqual(events[0]["venue"], "Bonn Auerberg")
        self.assert_valid_cinema_events(events)

    def test_kulturbad_feed_keeps_only_special_cinema_formats(self):
        feed_events = [{
            "title": "Open Air Kino: Kultfilm",
            "description": "",
            "category": "cinema-special kino film festival open air",
            "date": "2026-07-20",
            "source_id": "ruengsdorfer-kulturbad",
        }]
        with patch("scripts.nrw_events.common.fetch_ical", return_value=feed_events) as fetch_ical:
            events = cinema_specials._fetch_kulturbad_cinema()

        self.assertEqual(events, feed_events)
        kwargs = fetch_ical.call_args.kwargs
        self.assertEqual(kwargs["source_id"], "ruengsdorfer-kulturbad")
        self.assertTrue(kwargs["event_filter"]({"SUMMARY": "Open Air Kino"}, None, None))
        self.assertFalse(kwargs["event_filter"]({"SUMMARY": "Chansonabend"}, None, None))

    def test_partial_failure_warning_matches_child_source_id(self):
        result = SourceResult("Curated cinema specials")
        common.set_source_context(result)
        try:
            with patch(
                "scripts.nrw_events.common.fetch_url",
                side_effect=TimeoutError("timed out"),
            ):
                events = cinema_specials._fetch_optional_html(
                    "Bonner Kinemathek",
                    "bonner-kinemathek",
                    "https://example.invalid/events",
                    lambda _html: [],
                )
        finally:
            common.set_source_context(None)

        self.assertEqual(events, [])
        self.assertEqual(result.warnings[0]["source"], "Bonner Kinemathek")
        self.assertEqual(result.warnings[0]["source_id"], "bonner-kinemathek")


if __name__ == "__main__":
    unittest.main()
