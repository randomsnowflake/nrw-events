import unittest
from datetime import datetime

from nrw_events.sources import (
    SOURCES,
    geide,
    krewelshof,
    melan,
    regional_common,
    rheinbach_flohmarkt,
)
from tests.helpers import patch_window


class FirstPartyMarketSourceTests(unittest.TestCase):
    def setUp(self):
        patch_window(self, datetime(2026, 7, 26), datetime(2026, 12, 31))

    def test_new_first_party_sources_are_registered(self):
        self.assertIs(SOURCES["Krewelshof Kindersachen-Flohmarkt"], krewelshof.fetch)
        self.assertIs(SOURCES["Melan Märkte"], melan.fetch)
        self.assertIs(SOURCES["Rheinbach Flohmarkt"], rheinbach_flohmarkt.fetch)

    def test_melan_parses_only_target_regional_cards(self):
        html = """
        <div class="date-markets js-filter-date-item" data-date="09d.08m.2026Y">
          <h1 class="date">Sonntag 09.08.2026</h1>
          <a href="/fuer-alle/standorte/details/~/Aachen-PORTA/" class="market-list-item">
            <h2>Aachen PORTA</h2><p>11:00 - 18:00 Uhr</p><p>52070 Aachen</p>
          </a>
          <a href="/fuer-alle/standorte/details/~/Bornheim-PORTA-28r/" class="item market-list-item last">
            <h2>Bornheim PORTA</h2><p>11:00 - 18:00 Uhr</p>
            <p>Alexander-Bell-Straße 2 53332 Bornheim (Rheinland)</p>
          </a>
        </div>
        <div class="date-markets js-filter-date-item" data-date="16d.08m.2026Y">
          <h1 class="date">Sonntag 16.08.2026</h1>
          <a href="/fuer-alle/standorte/details/~/St-Augustin-METRO-27o/" class="item market-list-item last">
            <h2>St. Augustin METRO</h2><p>11:00 - 18:00 Uhr</p>
            <p>Einsteinstraße 28 53757 St. Augustin</p>
          </a>
        </div>
        """

        events = melan._events_from_page(html)

        self.assertEqual(
            [(event["date"], event["city"]) for event in events],
            [("2026-08-09", "Bornheim"), ("2026-08-16", "Sankt Augustin")],
        )
        self.assertEqual(events[0]["venue"], "PORTA, Alexander-Bell-Straße 2")
        self.assertEqual(events[1]["venue"], "METRO, Einsteinstraße 28")
        self.assertTrue(all(event["time"] == "11:00–18:00" for event in events))
        self.assertEqual(
            events[0]["link"],
            "https://www.melan.de/fuer-alle/standorte/details/~/Bornheim-PORTA-28r/",
        )

    def test_melan_refuses_target_card_without_first_party_address(self):
        html = """
        <div class="date-markets js-filter-date-item">
          <h1 class="date">Sonntag 09.08.2026</h1>
          <a href="/bornheim" class="market-list-item">
            <h2>Bornheim PORTA</h2><p>11:00 - 18:00 Uhr</p>
          </a>
        </div>
        """
        self.assertEqual(melan._events_from_page(html), [])

    def test_melan_strict_mode_requires_every_configured_location(self):
        html = """
        <div class="date-markets js-filter-date-item">
          <h1 class="date">Sonntag 16.08.2026</h1>
          <a href="/st-augustin" class="market-list-item">
            <h2>St. Augustin METRO</h2><p>11:00 - 18:00 Uhr</p>
            <p>Einsteinstraße 28 53757 St. Augustin</p>
          </a>
        </div>
        """
        with self.assertRaisesRegex(regional_common.ParserEmptyError, "Bornheim PORTA"):
            melan._events_from_page(html, strict=True)

    def test_geide_parses_regional_location_from_config(self):
        url = "https://www.geide-maerkte.de/bonn-alfter-oedekoven.html"
        html = """
        <a href="files/pdf/2026/Termine-2026.pdf">Termine</a>
        <div class="event-itm"><div class="event">
          <div class="header"><span>Aug</span><strong>02</strong></div>
          <h3>Alfter-Oedekoven</h3><div class="teaser"><p>OBI-Baumarkt</p></div>
          <a class="overlay-lnk" href="bonn-alfter-oedekoven/alfter-oedekoven-16.html"></a>
        </div></div>
        <h3>Adresse</h3><p>Alfterer Str. 35-37 - 53347 Alfter Oedekoven</p>
        """

        [event] = geide._events_from_page(html, url)

        self.assertEqual(event["title"], "Trödelmarkt Alfter-Oedekoven am OBI")
        self.assertEqual(event["date"], "2026-08-02")
        self.assertEqual(event["time"], "11:00–18:00")
        self.assertEqual(event["city"], "Alfter")
        self.assertEqual(event["venue"], "OBI, Alfterer Straße 35–37")
        self.assertEqual(event["source_id"], "geide-alfter-obi")
        self.assertEqual(
            event["link"],
            "https://www.geide-maerkte.de/bonn-alfter-oedekoven/alfter-oedekoven-16.html",
        )

    def test_geide_keeps_hennef_two_day_market_as_daily_occurrences(self):
        url = "https://www.geide-maerkte.de/hennef-sieg.html"
        html = """
        <a href="files/pdf/2026/Termine-2026.pdf">Termine</a>
        <div class="event-itm"><div class="event">
          <div class="header"><span>Okt</span><strong>03</strong></div>
          <a class="overlay-lnk" href="hennef-sieg/hennef-sieg.html"></a>
        </div></div>
        <div class="event-itm"><div class="event">
          <div class="header"><span>Okt</span><strong>04</strong></div>
          <a class="overlay-lnk" href="hennef-sieg/hennef-sieg.html"></a>
        </div></div>
        <h3>Adresse</h3><p>Frankfurter Straße - Marktplatz - 53773 Hennef (Sieg)</p>
        """

        events = geide._events_from_page(html, url)

        self.assertEqual([event["date"] for event in events], ["2026-10-03", "2026-10-04"])
        self.assertTrue(all(event["city"] == "Hennef" for event in events))
        self.assertTrue(all(event["source_id"] == "geide-hennef-stadtflohmarkt" for event in events))

    def test_all_new_geide_locations_enforce_configured_address_and_identity(self):
        cases = {
            "https://www.geide-maerkte.de/bonn-alfter-oedekoven.html": (
                "Alfterer Str. 35-37 - 53347 Alfter Oedekoven",
                "Alfter", "OBI, Alfterer Straße 35–37", "geide-alfter-obi",
            ),
            "https://www.geide-maerkte.de/bonn-alfter-oedekoven-rewe-markt.html": (
                "Ziegelweg 1 53347 Alfter-Oedekoven",
                "Alfter", "REWE, Ziegelweg 1", "geide-alfter-rewe",
            ),
            "https://www.geide-maerkte.de/sankt-augustin-hit-markt.html": (
                "Alte Heerstraße 53 - 53757 Sankt Augustin",
                "Sankt Augustin", "HIT-Markt, Alte Heerstraße 53", "geide-sankt-augustin-hit",
            ),
            "https://www.geide-maerkte.de/siegburg.html": (
                "Flohmärkte am OBI-Baumarkt in Siegburg Adresse / - 53721 Siegburg",
                "Siegburg", "OBI-Baumarkt Siegburg", "geide-siegburg-obi",
            ),
            "https://www.geide-maerkte.de/hennef-sieg.html": (
                "Frankfurter Straße - Marktplatz - 53773 Hennef (Sieg)",
                "Hennef", "Marktplatz und Frankfurter Straße", "geide-hennef-stadtflohmarkt",
            ),
        }
        card = """
        <a href="files/pdf/2026/Termine-2026.pdf">Termine</a>
        <div class="event-itm"><div class="event">
          <div class="header"><span>Aug</span><strong>02</strong></div>
          <a class="overlay-lnk" href="detail.html"></a>
        </div></div>
        """
        for url, (address, city, venue, source_id) in cases.items():
            with self.subTest(url=url):
                [event] = geide._events_from_page(f"{card}<p>{address}</p>", url)
                self.assertEqual(event["city"], city)
                self.assertEqual(event["venue"], venue)
                self.assertEqual(event["source_id"], source_id)

    def test_geide_configured_hours_fail_closed_after_schedule_year(self):
        url = "https://www.geide-maerkte.de/sankt-augustin-hit-markt.html"
        html = """
        <a href="files/pdf/2027/Termine-2027.pdf">Termine</a>
        <div class="event-itm"><div class="event">
          <div class="header"><span>Aug</span><strong>02</strong></div>
          <a class="overlay-lnk" href="detail.html"></a>
        </div></div>
        <p>Alte Heerstraße 53 - 53757 Sankt Augustin</p>
        """
        self.assertEqual(geide._events_from_page(html, url), [])

    def test_krewelshof_parses_explicit_schedule_and_ignores_conflicting_prose(self):
        html = """
        <h3>Nächster Flohmarkttermin: 26. Juli 2026</h3>
        <p>Am 22. Februar findet der Flohmarkt NUR auf dem Krewelshof in Lohmar statt.</p>
        <p>Achtung im Oktober und November: 26. Oktober und 23. November.</p>
        <h3>Sonntag</h3><h3>26. Juli</h3>
        <h3>Samstag</h3><h3>29. August</h3>
        <h3>Samstag</h3><h3>26. Sept.</h3>
        <h3>Samstag</h3><h3>24. Oktober</h3>
        <h3>Samstag</h3><h3>28. November</h3>
        <h3>Im Dezember</h3><h3>keine Termine</h3>
        <p>auf dem Krewelshof in der Eifel/Mechernich und dem Krewelshof in Köln/Lohmar</p>
        <p>Flohmarkt-Zeitraum für Besuchende: zwischen 09:00 und 15:00 Uhr</p>
        """

        events = krewelshof._events_from_page(html)

        self.assertEqual(
            [event["date"] for event in events],
            ["2026-07-26", "2026-08-29", "2026-09-26", "2026-10-24", "2026-11-28"],
        )
        self.assertTrue(all(event["city"] == "Lohmar" for event in events))
        self.assertTrue(all(event["time"] == "09:00–15:00" for event in events))
        self.assertTrue(all(event["source_id"] == "krewelshof-lohmar" for event in events))

    def test_krewelshof_refuses_yearless_or_locationless_page(self):
        valid = """
        <h3>Nächster Flohmarkttermin: 26. Juli 2026</h3>
        <h3>Sonntag</h3><h3>26. Juli</h3><h3>Im Dezember</h3>
        <p>Krewelshof in Köln/Lohmar</p>
        <p>zwischen 09:00 und 15:00 Uhr</p>
        """
        yearless = valid.replace("2026", "") + "<footer>© 2026 Krewelshof</footer>"
        self.assertEqual(krewelshof._events_from_page(yearless), [])
        self.assertEqual(krewelshof._events_from_page(valid.replace("Köln/Lohmar", "Eifel/Mechernich")), [])

    def test_rheinbach_parses_only_dated_official_flea_markets(self):
        html = """
        <p>Die Flohmärkte im Freizeitpark Rheinbach werden von der Stadt veranstaltet.</p>
        <h3>Nächster Flohmarkttermin:</h3>
        <ul>
          <li>Samstag, 23. Mai 2026</li>
          <li>Samstag, 27. Juni 2026</li>
          <li>Samstag, 22. August 2026</li>
          <li>Samstag, 26. September 2026</li>
        </ul>
        <h2>Reservierung</h2>
        <h2>Verkaufszeiten</h2><p>von 9:00 Uhr bis 16:00 Uhr</p>
        <p>Zugelassen sind weiterhin nur Privatanbieter, keine Gewerbetreibenden.</p>
        <p>Außer Neuwaren, Lebensmittel, selbst Hergestelltem und Kunstobjekten</p>
        <p>Münstereifeler Str. 69 53359 Rheinbach</p>
        <p>Das Bürgerfest ist Samstag, 5. Dezember 2026.</p>
        """

        events = rheinbach_flohmarkt._events_from_page(html)

        self.assertEqual(
            [event["date"] for event in events],
            ["2026-08-22", "2026-09-26"],
        )
        self.assertTrue(all(event["title"] == "Flohmarkt im Freizeitpark Rheinbach" for event in events))
        self.assertTrue(all(event["time"] == "09:00–16:00" for event in events))
        self.assertTrue(all(event["venue"] == "Freizeitpark Rheinbach, Münstereifeler Straße 69" for event in events))
        self.assertTrue(all(event["source_id"] == "rheinbach-freizeitpark-flohmarkt" for event in events))

    def test_rheinbach_refuses_page_without_private_flea_market_contract(self):
        html = """
        <h3>Nächster Flohmarkttermin:</h3>
        <li>Samstag, 22. August 2026</li>
        <h2>Reservierung</h2>
        <h2>Verkaufszeiten</h2>
        <p>von 9:00 Uhr bis 16:00 Uhr</p>
        <p>Münstereifeler Str. 69 53359 Rheinbach</p>
        """
        self.assertEqual(rheinbach_flohmarkt._events_from_page(html), [])

        allowed_goods = html + """
        <p>Es sind nur Privatanbieter zugelassen.</p>
        <p>Neuwaren, Lebensmittel und Kunstobjekte sind ausdrücklich erlaubt.</p>
        """
        self.assertEqual(rheinbach_flohmarkt._events_from_page(allowed_goods), [])

    def test_new_sources_respect_the_inclusive_28_day_window(self):
        # 28 calendar days including 26 July end on 22 August.
        patch_window(self, datetime(2026, 7, 26), datetime(2026, 8, 22))
        krewel_html = """
        <h3>Nächster Flohmarkttermin: 26. Juli 2026</h3>
        <h3>Sonntag 26. Juli</h3><h3>Samstag 22. August</h3>
        <h3>Sonntag 23. August</h3>
        <h3>Im Dezember keine Termine</h3>
        <p>Krewelshof in Köln/Lohmar</p><p>zwischen 09:00 und 15:00 Uhr</p>
        """
        self.assertEqual(
            [event["date"] for event in krewelshof._events_from_page(krewel_html)],
            ["2026-07-26", "2026-08-22"],
        )


if __name__ == "__main__":
    unittest.main()
