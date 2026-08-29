import unittest
from datetime import datetime
from unittest.mock import patch

from nrw_events import common, report
from nrw_events.sources import bonn, bonn_districts
from tests.helpers import patch_window


class BonnPressFestivalTests(unittest.TestCase):
    def setUp(self):
        patch_window(self, datetime(2026, 8, 1), datetime(2026, 8, 31))

    def test_keeps_comma_inside_hyphenated_official_market_name(self):
        html = """
        <ul>
          <li>
            Antik-, Kunst- &amp; Designmarkt Bonn, Friedensplatz,
            Bottlerplatz, Vivatsgasse, Poststraße, 16. August 2026, Rhein-Antik
          </li>
        </ul>
        """

        with patch.object(common, "fetch_url", return_value=html):
            events = bonn.fetch_press_festivals()

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["title"], "Antik-, Kunst- & Designmarkt Bonn")
        self.assertEqual(events[0]["date"], "2026-08-16")
        self.assertEqual(events[0]["category_key"], "market")

    def test_parses_shared_month_ranges_as_one_multi_day_event(self):
        self.addCleanup(patch.stopall)
        patch.object(common, "TODAY", datetime(2026, 11, 1)).start()
        patch.object(common, "END_DATE", datetime(2026, 12, 31)).start()
        html = """
        <ul>
          <li>Nikolausmarkt Beuel, Hermannstraße, 27. bis 29. November 2026, Bundesstadt Bonn</li>
          <li>Kessenicher Herbstmarkt, Pützstraße, 3. und 4. Oktober 2026, Ortsausschuss</li>
        </ul>
        """

        with patch.object(common, "fetch_url", return_value=html):
            events = bonn.fetch_press_festivals()

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["title"], "Nikolausmarkt Beuel")
        self.assertEqual(events[0]["start_date"], "2026-11-27")
        self.assertEqual(events[0]["end_date"], "2026-11-29")
        self.assertEqual(events[0]["date"], "2026-11-27")
        self.assertEqual(events[0]["venue"], "Hermannstraße")
        self.assertEqual(events[0]["category_key"], "market")

    def test_press_release_contract_classifies_ambiguous_district_festival_title(self):
        html = """
        <ul><li>
          Buntes Treiben Oberkassel (im Rahmen des Schützenfestes),
          Parkplatz Königswinterer Str./Ecke Kastellstraße,
          15. bis 18. August 2026, Uwe Wernecke
        </li></ul>
        """

        with patch.object(common, "fetch_url", return_value=html):
            events = bonn.fetch_press_festivals()

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["category_key"], "festival")
        self.assertEqual(events[0]["category_confidence"], 1.0)
        self.assertEqual(events[0]["category_reason"], "source:default:festival")

    def test_finds_annual_release_outside_the_historical_december_path(self):
        html = "<ul><li>Sommerfest Bonn, Marktplatz, 16. August 2026</li></ul>"

        def fetch(url, **_kwargs):
            if "/november/" in url:
                return html
            raise FileNotFoundError(url)

        with patch.object(common, "fetch_url", side_effect=fetch):
            events = bonn.fetch_press_festivals()

        self.assertEqual(len(events), 1)
        self.assertIn("/november/", events[0]["link"])

    def test_poppelsdorfer_strassenfest_uses_reviewed_primary_detail_page(self):
        self.addCleanup(patch.stopall)
        patch.object(common, "END_DATE", datetime(2026, 9, 19)).start()
        html = """
        <ul><li>
          Poppelsdorfer Straßenfest, Clemens-August-Straße,
          19. September 2026, Ortsbund Poppelsdorf
        </li></ul>
        """

        with patch.object(common, "fetch_url", return_value=html):
            [event] = bonn.fetch_press_festivals()

        self.assertEqual(event["source"], "Bonn.de Events")
        self.assertEqual(event["source_id"], "bonn-de-events")
        self.assertEqual(event["link_kind"], "detail")
        self.assertEqual(event["discovered_via"], ["bonn-district-festivals"])
        self.assertEqual(
            event["link"],
            "https://www.bonn.de/veranstaltungskalender/veranstaltungen/"
            "hauptkalender/extern/Poppelsdorfer-Strassenfest-.php",
        )

    def test_poppelsdorfer_primary_resolution_is_limited_to_2026_occurrence(self):
        self.addCleanup(patch.stopall)
        patch.object(common, "TODAY", datetime(2027, 9, 1)).start()
        patch.object(common, "END_DATE", datetime(2027, 9, 30)).start()
        html = """
        <ul><li>
          Poppelsdorfer Straßenfest, Clemens-August-Straße,
          18. September 2027, Ortsbund Poppelsdorf
        </li></ul>
        """

        with patch.object(common, "fetch_url", return_value=html):
            [event] = bonn.fetch_press_festivals()

        self.assertEqual(event["source"], "Bonn district festivals")
        self.assertNotIn("Poppelsdorfer-Strassenfest-", event["link"])

    def test_corrects_reviewed_beuel_festival_dates_and_keeps_published_alias(self):
        self.addCleanup(patch.stopall)
        patch.object(common, "END_DATE", datetime(2026, 9, 30)).start()
        html = """
        <ul><li>
          Fest der Beueler Vereine – Promenadenfest,
          Rheinufer Beuel, China-Schiff bis Bahnhöfchen,
          29. und 30. August 2026, Interessengemeinschaft Beueler Vereine
        </li></ul>
        """

        with patch.object(common, "fetch_url", return_value=html):
            [event] = bonn.fetch_press_festivals()

        self.assertEqual(event["start_date"], "2026-09-05")
        self.assertEqual(event["end_date"], "2026-09-06")
        self.assertEqual(event["source_id"], "beuel-net")
        self.assertEqual(event["link_kind"], "detail")
        self.assertIn("beuel.net/2026/06/18/", event["link"])
        self.assertNotIn("29.", event["description"])
        self.assertEqual(
            event["previous_event_ids"],
            ["fest-der-beueler-vereine-promenadenfest-2026-08-29-5fa6836fc1"],
        )

    def test_corrected_beuel_festival_collapses_with_primary_calendar_record(self):
        self.addCleanup(patch.stopall)
        patch.object(common, "END_DATE", datetime(2026, 9, 30)).start()
        press_html = """
        <ul><li>
          Fest der Beueler Vereine – Promenadenfest,
          Rheinufer Beuel, China-Schiff bis Bahnhöfchen,
          29. und 30. August 2026, Interessengemeinschaft Beueler Vereine
        </li></ul>
        """
        beuel_html = """
        <div class="yel"><a href="/events/#05.09.2026">
          <span class="title">🍻 Promenadenfest und Beuelfest</span><br>
          <b>Sa. 05.09.2026 – So. 06.09.2026</b> |
          <a href="/map/?q=Rheinufer">Rheinufer</a><br>
          <a href="https://beuelhats.de/veranstaltungen">externer Link</a>
        </a></div>
        """
        with patch.object(common, "fetch_url", return_value=press_html):
            [corrected] = bonn.fetch_press_festivals()
        [primary] = bonn_districts.events_from_beuel_html(beuel_html)
        primary["source_id"] = "beuel-net"

        [deduped] = report.deduplicate([corrected, primary])

        self.assertEqual(deduped["start_date"], "2026-09-05")
        self.assertEqual(deduped["end_date"], "2026-09-06")
        self.assertIn(
            "fest-der-beueler-vereine-promenadenfest-2026-08-29-5fa6836fc1",
            deduped["previous_event_ids"],
        )


if __name__ == "__main__":
    unittest.main()
