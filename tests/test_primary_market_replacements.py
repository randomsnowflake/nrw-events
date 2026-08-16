import unittest
from datetime import datetime

from nrw_events.sources import (
    SOURCES,
    regional_common,
    rieder_markets,
    rossel_wilberhofen,
    schmitt_markets,
)
from tests.helpers import patch_window


class PrimaryMarketReplacementTests(unittest.TestCase):
    def setUp(self):
        patch_window(self, datetime(2026, 8, 16), datetime(2026, 9, 13))

    def test_sources_are_registered(self):
        self.assertIs(
            SOURCES["Bürgerverein Rossel-Wilberhofen"],
            rossel_wilberhofen.fetch,
        )
        self.assertIs(SOURCES["Schmitt Veranstaltungen"], schmitt_markets.fetch)
        self.assertIs(SOURCES["Rieder Märkte"], rieder_markets.fetch)

    def test_rossel_wilberhofen_requires_news_and_calendar_corroboration(self):
        news = """
        <meta property="og:site_name" content="Bürgerverein Rossel-Wilberhofen">
        <h2>Traditionelles Rochusfest am 15. &amp; 16.08.</h2>
        <p>Am Sonntag, den 16. August findet zudem in der Zeit zwischen
        10.00-18.00 Uhr wieder ein Dorf-Flohmarkt im gesamten Ort statt.</p>
        """
        calendar = """
        <h2>Termine und Veranstaltungen</h2><h3>2026</h3>
        <tr><td>16.08.</td><td>ab 09:30</td><td>Rund um die Rochuskapelle
        in Wilberhofen</td><td>Traditionelles Rochusfest</td></tr>
        """

        [event] = rossel_wilberhofen._events_from_pages(news, calendar, strict=True)

        self.assertEqual(event["date"], "2026-08-16")
        self.assertEqual(event["time"], "10:00–18:00")
        self.assertEqual(event["city"], "Windeck")
        self.assertEqual(event["venue"], "Wilberhofen")
        self.assertEqual(event["source_id"], "rossel-wilberhofen-dorfflohmarkt")
        self.assertEqual(event["organizer"], "Bürgerverein Rossel-Wilberhofen")

        with self.assertRaises(regional_common.ParserEmptyError):
            rossel_wilberhofen._events_from_pages(news, calendar.replace("2026", ""), strict=True)

    def test_schmitt_parses_official_calendar_and_visitor_start(self):
        html = """
        <h2>Unsere Markttermine - Für weitere Infos einfach Termin anklicken!</h2>
        <p>Unser nächster Flohmarkt 16.8. 56626 Andernach, Kaufland
        Platzvergabe ab 6.00 Uhr! Verkauf ab 11.00 Uhr!</p>
        <div>16.08.2026</div><div><a href="/event-andernach">
        56626 Andernach, Kaufland, Koblenzer Straße 51</a></div>
        <div>23.08.2026</div><div><a href="/event-muelheim">
        56218 Mülheim- Kärlich, Kaufland, Industriestraße 4 Verkauf: ab 11.00 Uhr</a></div>
        """

        events = schmitt_markets._events_from_page(html, strict=True)

        self.assertEqual([event["city"] for event in events], ["Andernach", "Mülheim-Kärlich"])
        self.assertTrue(all(event["time"] == "11:00" for event in events))
        self.assertTrue(all(event["source_id"] == "schmitt-veranstaltungen" for event in events))
        self.assertEqual(events[0]["link"], "https://fmarkt.de/event-andernach")

    def test_schmitt_refuses_rows_without_a_visitor_start(self):
        html = """
        <h2>Unsere Markttermine - Für weitere Infos einfach Termin anklicken!</h2>
        <div>23.08.2026</div><div><a href="/event">
        56218 Mülheim-Kärlich, Kaufland, Industriestraße 4</a></div>
        """
        with self.assertRaisesRegex(regional_common.ParserEmptyError, "visitor start"):
            schmitt_markets._events_from_page(html, strict=True)

    def test_rieder_combines_dated_card_with_official_location_facts(self):
        terms = """
        <a href="https://www.rieder-maerkte.de/produkt/solingen-16-08-2026/">
        <h2 class="woocommerce-loop-product__title">16.08.2026
        Solingen-Aufderhöhe, REWE Ihr Kaufpark</h2></a>
        """
        location = """
        <h1>Solingen, REWE Ihr Kaufpark</h1>
        <p>Friedenstraße 96, 42699 Solingen</p>
        <p>Die offiziellen Verkaufszeiten sind an Sonn‐ &amp; Feiertagen von 11 bis 18 Uhr.</p>
        """

        [event] = rieder_markets._events_from_pages(terms, location, strict=True)

        self.assertEqual(event["date"], "2026-08-16")
        self.assertEqual(event["time"], "11:00–18:00")
        self.assertEqual(event["venue"], "REWE Ihr Kaufpark")
        self.assertEqual(event["venue_address"], "Friedenstraße 96")
        self.assertEqual(event["source_id"], "rieder-solingen-rewe")

    def test_rieder_refuses_uncorroborated_hours(self):
        terms = """
        <a href="/event"><h2>16.08.2026 Solingen-Aufderhöhe,
        REWE Ihr Kaufpark</h2></a>
        """
        location = "<p>Friedenstraße 96, 42699 Solingen</p>"
        with self.assertRaisesRegex(regional_common.ParserEmptyError, "address or hours"):
            rieder_markets._events_from_pages(terms, location, strict=True)


if __name__ == "__main__":
    unittest.main()
