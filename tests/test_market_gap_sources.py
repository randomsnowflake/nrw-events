import unittest
from datetime import datetime
from unittest.mock import patch

from nrw_events import common
from nrw_events.sources import bonner_weihnachtsmarkt, katharinenhof
from tests.helpers import patch_window


class MarketGapSourceTests(unittest.TestCase):
    def test_bonner_christmas_markets_use_exact_dates_hours_and_closures(self):
        patch_window(self, datetime(2026, 11, 18), datetime(2027, 1, 6))
        main_html = """
        <h1>Bonner Weihnachtsmarkt</h1>
        <h2>18. November bis 23. Dezember 2026</h2>
        <p>Alle Geschäfte 12.00 bis 21.00 Uhr</p>
        <p>Letzter Tag (23.12.2026) bis 20 Uhr geöffnet!</p>
        <p>Totensonntag (22.11.2026) ganztägig geschlossen.</p>
        <p>Münster-, Bottler- und Friedensplatz, Windeckstraße, Vivatsgasse,
        Poststraße und Remigiusplatz</p>
        """
        schedule_html = """
        <p>Dreikönigsmarkt auf dem Remigiusplatz in der Zeit vom
        27.12.2026 bis 06.01.2027. Der Markt bleibt am 01.01.2027 geschlossen.</p>
        <p>12 bis 20 Uhr (Sonntag bis Donnerstag)<br>
        12 bis 21 Uhr (Freitag und Samstag)<br>
        Silvester: 11 bis 17 Uhr<br>Neujahr: geschlossen</p>
        """

        events = bonner_weihnachtsmarkt._events_from_pages(main_html, schedule_html, strict=True)
        by_date = {event["start_date"]: event for event in events}

        self.assertNotIn("2026-11-22", by_date)
        self.assertEqual(by_date["2026-11-18"]["time"], "12:00–21:00")
        self.assertEqual(by_date["2026-12-23"]["time"], "12:00–20:00")
        self.assertEqual(by_date["2026-12-31"]["time"], "11:00–17:00")
        self.assertNotIn("2027-01-01", by_date)
        self.assertEqual(by_date["2027-01-02"]["time"], "12:00–21:00")
        self.assertEqual(by_date["2027-01-06"]["time"], "12:00–20:00")
        self.assertTrue(all(event["category_key"] == "market" for event in events))
        self.assertEqual(len(events), 45)

    def test_bonner_christmas_market_derives_shifted_dates_and_hours_from_copy(self):
        patch_window(self, datetime(2027, 11, 19), datetime(2028, 1, 7))
        main_html = """
        <p>Sommerfest: 1. Juni bis 2. Juni 2027</p>
        <h1>Bonner Weihnachtsmarkt</h1>
        <h2>19. November bis 24. Dezember 2027</h2>
        <p>Alle Geschäfte 13.00 bis 22.00 Uhr</p>
        <p>Letzter Tag (24.12.2027) bis 18 Uhr geöffnet!</p>
        <p>Totensonntag (21.11.2027) ganztägig geschlossen.</p>
        <p>Münster-, Bottler- und Friedensplatz, Windeckstraße, Vivatsgasse,
        Poststraße und Remigiusplatz</p>
        """
        schedule_html = """
        <p>Dreikönigsmarkt auf dem Remigiusplatz vom
        28.12.2027 bis 07.01.2028. Der Markt bleibt am 02.01.2028 geschlossen.</p>
        <p>13 bis 22 Uhr (Freitag und Samstag)<br>
        13 bis 19 Uhr (Sonntag bis Donnerstag)<br>
        Silvester: 10 bis 16 Uhr</p>
        """

        events = bonner_weihnachtsmarkt._events_from_pages(main_html, schedule_html, strict=True)
        by_date = {event["start_date"]: event for event in events}
        self.assertIn("2027-11-19", by_date)
        self.assertNotIn("2027-11-21", by_date)
        self.assertEqual(by_date["2027-12-24"]["time"], "13:00–18:00")
        self.assertEqual(by_date["2027-12-31"]["time"], "10:00–16:00")
        self.assertNotIn("2028-01-02", by_date)
        self.assertEqual(by_date["2028-01-03"]["time"], "13:00–19:00")
        self.assertEqual(by_date["2028-01-07"]["time"], "13:00–22:00")

    def test_katharinenhof_reads_only_flea_markets_from_first_party_json_ld(self):
        patch_window(self, datetime(2026, 8, 1), datetime(2026, 9, 30))
        html = """
        <script type="application/ld+json">{
          "@type":"Event","name":"Konrad empfiehlt: Flohmarkt im Katharinenhof",
          "url":"https://beikircher.de/events/flohmarkt-2/",
          "startDate":"2026-8-9T10:00+2:00","endDate":"2026-8-9T23:59+2:00",
          "description":"<p>Eintritt: 3 Eur.</p>",
          "location":[{"name":"Katharinenhof","address":{"streetAddress":
          "Venner Straße 51, 53177 Bonn Bad-Godesberg"}}]
        }</script>
        <script type="application/ld+json">{
          "@type":"Event","name":"Kabarett im Katharinenhof",
          "url":"https://beikircher.de/events/kabarett/",
          "startDate":"2026-8-10T19:30+2:00","endDate":"2026-8-10T23:59+2:00"
        }</script>
        """

        events = katharinenhof._events_from_page(html, strict=True)

        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event["title"], "Flohmarkt im Katharinenhof")
        self.assertEqual(event["time"], "10:00")
        self.assertEqual(event["price"], "3 €")
        self.assertEqual(event["venue"], "Katharinenhof")
        self.assertEqual(event["venue_address"], "Venner Straße 51")
        self.assertEqual(event["city"], "Bonn-Bad Godesberg")
        self.assertEqual(event["location_confidence"], "exact")
        self.assertIsNotNone(event["venue_latitude"])
        self.assertIsNotNone(event["venue_longitude"])
        self.assertGreaterEqual(event["score"], 0.4)
        self.assertEqual(event["source_id"], "katharinenhof-flohmarkt")

    def test_katharinenhof_fetch_is_registered_without_using_radio_data(self):
        with patch.object(common, "fetch_url", return_value="<html></html>"):
            self.assertEqual(katharinenhof.fetch(), [])


if __name__ == "__main__":
    unittest.main()
