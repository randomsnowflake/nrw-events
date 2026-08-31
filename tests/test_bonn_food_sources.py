import json
import unittest
from datetime import datetime
from unittest.mock import patch

from nrw_events import common
from nrw_events.health import SourceStatus
from nrw_events.sources import CUSTOM_SOURCES, bonn_food
from nrw_events.validation import validate_event
from tests.helpers import patch_window


class BonnFoodSourceTests(unittest.TestCase):
    def setUp(self):
        patch_window(self, datetime(2026, 7, 19), datetime(2026, 12, 31))

    def assert_food_events(self, events, expected_count):
        self.assertEqual(len(events), expected_count)
        for event in events:
            self.assertEqual(event["category_key"], "food")
            self.assertEqual(event["category_label"], "Food & Genuss")
            validate_event(event)

    def test_all_primary_sources_are_individually_registered(self):
        expected = {
            "Craftquelle Bonn", "BFF Bonner Schifffahrt", "vomFASS Bonn",
            "Biertasting Bonn", "Ludwig's Bonn", "Redüttchen", "Street Food Bonn",
            "Street Food Festival Original", "Choco Dealer",
        }
        self.assertTrue(expected.issubset(CUSTOM_SOURCES))

    def test_abbreviated_german_months_use_the_shared_date_vocabulary(self):
        self.assertEqual(bonn_food._parse_german_date("3. Sept. 2026"), datetime(2026, 9, 3))
        self.assertEqual(bonn_food._parse_german_date("4. Okt. 2026"), datetime(2026, 10, 4))

    def test_craftquelle_uses_listing_and_detail_data(self):
        listing = """
        <table><tr><th>Datum</th><th>Thema</th><th>Leitung</th><th>Ticketpreis</th></tr>
        <tr><td><a href="/produkt/wild-beers-tasting-07-08-26/">7. August 26</a> (11 Plätze)</td>
        <td>Wild Beers – außergewöhnliche Biere</td><td>Christoph Steinhauer</td><td>44,90 Euro</td></tr></table>
        """
        detail = """
        <div>Beschreibung Beschreibung Beim Wild Beer Tasting werden außergewöhnliche Biere verkostet.
        Leitung: Christoph Steinhauer Ort: Brauwerkstatt Bonn, Hermannstraße 104, 53225 Bonn
        Biersommelier Christoph Steinhauer führt durch den Abend.
        Beginn: 7. August 2026, 20:00 Uhr Ende ca. 23:00 Uhr</div>
        """
        events = bonn_food.events_from_craftquelle(listing, lambda _url: detail)
        self.assert_food_events(events, 1)
        self.assertEqual(events[0]["time"], "20:00–23:00")
        self.assertEqual(events[0]["price"], "44,90 Euro")
        self.assertEqual(events[0]["venue"], "Brauwerkstatt Bonn")
        self.assertEqual(events[0]["venue_address"], "Hermannstraße 104, 53225 Bonn")
        self.assertTrue(events[0]["link"].endswith("wild-beers-tasting-07-08-26/"))

    def test_bff_finds_events_nested_inside_jsonld(self):
        item = {
            "@context": "https://schema.org",
            "mainEntity": {"items": [{
                "@type": "Event", "name": "Grillabend auf dem Rhein",
                "startDate": "2026-09-04T19:00:00+02:00",
                "endDate": "2026-09-04T22:00:00+02:00",
                "description": "Grillabend mit Buffet auf dem Rhein.",
                "location": {"@type": "Place", "name": "Bonn Alter Zoll",
                             "address": {"streetAddress": "Brassertufer", "postalCode": "53111", "addressLocality": "Bonn"}},
                "offers": {"price": 24.7, "priceCurrency": "EUR", "url": "https://shop.bff-bonn.com/grillabend"},
            }]},
        }
        html = f'<script type="application/ld+json">{json.dumps(item)}</script>'
        events = bonn_food.events_from_bff(html)
        self.assert_food_events(events, 1)
        self.assertEqual(events[0]["time"], "19:00–22:00")
        self.assertEqual(events[0]["price"], "24,70 EUR")
        self.assertEqual(events[0]["link"], "https://shop.bff-bonn.com/grillabend")

    def test_bff_falls_back_to_visible_booking_timetable(self):
        html = """
        <div class='wp-block-columns block-timetable month_2026-09-01 anzeige frei'>
          <div class='datum'><span class='datum'>04.09.2026</span><span class='uhrzeit'> 19:00 Uhr</span></div>
          <div class='linie_bezeichnung'>Grillabend auf dem Rhein - in Bonn</div>
          <div class='abfahrt_station'>Bonn Alter Zoll - Landebrücke 4</div>
          <div class='infotext'>Grillabend mit Buffet auf dem Rhein.</div>
          <div class='weiterlesen_link'><a href='https://shop.bff-bonn.com/grillabend'>Weiterlesen</a></div>
          <hr class='divider'>
        """

        events = bonn_food.events_from_bff(html)

        self.assert_food_events(events, 1)
        self.assertEqual(events[0]["time"], "19:00")
        self.assertEqual(events[0]["venue"], "Bonn Alter Zoll - Landebrücke 4")
        self.assertEqual(events[0]["link"], "https://shop.bff-bonn.com/grillabend")

    def test_vomfass_filters_to_bonn_and_enriches_from_food_event_schema(self):
        listing = """
        <article data-event-card data-city="bonn" data-partner="vomfass-bonn" data-date="2026-09-11">
          <span class="ef-card__time">19:30</span><h3 class="ef-card__title"><a href="/pages/tasting-events/gin-bonn">Gin Tasting</a></h3>
          <div class="ef-card__price">€99,95 p. P.</div></article>
        <article data-event-card data-city="jena" data-partner="vomfass-jena" data-date="2026-09-12">
          <h3 class="ef-card__title"><a href="/pages/tasting-events/gin-jena">Gin Tasting Jena</a></h3></article>
        """
        detail_item = {
            "@type": "FoodEvent", "name": "Gin Tasting",
            "startDate": "2026-09-11T19:30:00+0200", "endDate": "2026-09-11T21:30:00+0200",
            "description": "Geführtes Gin-Tasting mit verschiedenen Longdrinks.",
            "url": "https://www.vomfass.de/pages/tasting-events/gin-bonn",
            "location": {"name": "vomFASS Bonn", "address": {"streetAddress": "Friedrichstraße 49", "postalCode": "53111", "addressLocality": "Bonn"}},
        }
        detail = f'<script type="application/ld+json">{json.dumps(detail_item)}</script>'
        events = bonn_food.events_from_vomfass(listing, lambda _url: detail)
        self.assert_food_events(events, 1)
        self.assertEqual(events[0]["time"], "19:30–21:30")
        self.assertEqual(events[0]["price"], "99,95 EUR")
        self.assertEqual(events[0]["venue"], "vomFASS Bonn")
        self.assertEqual(events[0]["venue_address"], "Friedrichstraße 49, 53111 Bonn")

    def test_vomfass_fetches_remaining_shopify_section_pages_and_details(self):
        first_page = """
        <div data-ef-results data-ef-section-id="template--123__main"
             data-ef-page="1" data-ef-pages="2" data-ef-page-param="page_abc">
          <article data-event-card data-city="jena" data-partner="vomfass-jena" data-date="2026-09-12">
            <h3 class="ef-card__title"><a href="/pages/tasting-events/gin-jena">Gin Tasting Jena</a></h3>
          </article>
        </div>
        """
        second_page = """
        <div data-ef-results data-ef-section-id="template--123__main"
             data-ef-page="2" data-ef-pages="2" data-ef-page-param="page_abc">
          <article data-event-card data-city="bonn" data-partner="vomfass-bonn" data-date="2026-09-11">
            <span class="ef-card__time">19:30</span><h3 class="ef-card__title"><a href="/pages/tasting-events/gin-bonn">Gin Tasting</a></h3>
            <div class="ef-card__price">€99,95 p. P.</div>
          </article>
        </div>
        """
        patch_window(self, datetime(2026, 7, 20), datetime(2026, 12, 31))
        with patch.object(
            common,
            "fetch_url_with_brightdata",
            side_effect=[first_page, second_page],
        ) as listing_fetch, patch.object(
            common,
            "fetch_detail_url",
            return_value="",
        ) as detail_fetch:
            events = bonn_food.fetch_vomfass()

        self.assert_food_events(events, 1)
        self.assertEqual(
            [call.args[0] for call in listing_fetch.call_args_list],
            [
                "https://www.vomfass.de/pages/tastings",
                "https://www.vomfass.de/pages/tastings?section_id=template--123__main&page_abc=2",
            ],
        )
        for call in listing_fetch.call_args_list:
            self.assertEqual(call.kwargs["timeout"], 25)
            self.assertEqual(call.kwargs["allowed_hosts"], ("www.vomfass.de",))
            self.assertEqual(call.kwargs["required_body_markers"], ("data-event-card",))
        self.assertTrue(detail_fetch.call_args.kwargs["brightdata"])
        self.assertNotIn("brightdata_fallback", detail_fetch.call_args.kwargs)
        self.assertEqual(
            detail_fetch.call_args.kwargs["allowed_hosts"],
            ("www.vomfass.de",),
        )
        self.assertEqual(
            detail_fetch.call_args.kwargs["required_body_markers"],
            ("application/ld+json",),
        )

    def test_vomfass_skips_network_refresh_outside_monday(self):
        patch_window(self, datetime(2026, 7, 24), datetime(2026, 12, 31))
        with patch.object(
            common,
            "fetch_url_with_brightdata",
            side_effect=AssertionError("network request on scheduled skip"),
        ):
            result = bonn_food.fetch_vomfass()

        self.assertEqual(result.status, SourceStatus.SCHEDULED_SKIP)
        self.assertIn("Mondays", result.disabled_reason)

    def test_biertasting_derives_weekday_times_and_prices(self):
        html = """
        <p>Terminliste Tastings bis Dezember 2026</p>
        <p>Ort: Atelier Zwei Zwei Drei, Mainzer Str. 223, Bonn-Mehlem Preis: 39€ p.P</p>
        <p>September Freitag, 4. September 8 Biere – 8 Stile (39€)
        Oktober Sonntag, 18. Oktober Bier &amp; Käse (54€)</p><h2>Informationen &amp; Links</h2>
        """
        events = bonn_food.events_from_biertasting(html)
        self.assert_food_events(events, 2)
        self.assertEqual([event["time"] for event in events], ["20:00–23:00", "18:00–21:00"])
        self.assertEqual(events[1]["price"], "54 EUR")
        self.assertEqual(events[0]["city"], "Bonn")

    def test_ludwigs_uses_detail_time_and_description(self):
        listing = """
        <div class="card no-r"><div class="card-media"><a href="/veranstaltungen/termin/2026/09/wine-dine"></a></div>
        <div class="card-body"><p class="small mb-1">22. September</p><h3>Wine &amp; Dine</h3>
        <p>Vier Gänge und passende Weine.</p><a href="/veranstaltungen/termin/2026/09/wine-dine">Mehr erfahren</a></div></div>
        """
        detail = """<main><h1>Wine &amp; Dine</h1><p>Spitzenküche und ausgewählte Weine treffen aufeinander.</p>
        <p>Erleben Sie am 22.09.2026 ab 18 Uhr einen außergewöhnlichen Genussabend.</p></main>"""
        events = bonn_food.events_from_ludwigs(listing, lambda _url: detail)
        self.assert_food_events(events, 1)
        self.assertEqual(events[0]["time"], "18:00")
        self.assertIn("Spitzenküche", events[0]["description"])

    def test_reduettchen_expands_exact_dates_and_ignores_placeholders(self):
        listing = """
        <div class="av_one_third"><h2>Gourmet BBQ</h2><p>24. &amp; 25. Juli 2026</p>
        <a href="https://reduettchen.de/gourmet-bbq/">Mehr erfahren</a></div>
        <div class="av_one_third"><h2>Gänseessen</h2><p>November 2026</p><p>Bald buchbar</p></div>
        """
        detail = """<main><h1>Gourmet BBQ</h1><p>Genuss aus Smoker und Grill.</p>
        <p>Datum: 24. &amp; 25. Juli 2026</p><p>200 pro Person (alles inklusive)</p>
        <p>Kurfürstenallee 1, 53177 Bonn-Bad Godesberg</p></main>"""
        events = bonn_food.events_from_reduettchen(listing, lambda _url: detail)
        self.assert_food_events(events, 2)
        self.assertEqual([event["start_date"] for event in events], ["2026-07-24", "2026-07-25"])
        self.assertTrue(all(event["price"] == "200 EUR" for event in events))

    def test_street_food_expands_date_ranges_and_normalizes_bonn_city(self):
        html = """
        <h2>Nächste Termine</h2><p>28. - 30.08.2026 Street Food Festival - Bonn Bad Godesberg</p>
        <p>16. - 18.10.2026 Street Food Festival - Troisdorf</p><h2>Veranstalter</h2>
        """
        events = bonn_food.events_from_street_food(html)
        self.assert_food_events(events, 2)
        self.assertEqual(events[0]["date"], "2026-08-28")
        self.assertEqual(events[0]["end_date"], "2026-08-30")
        self.assertEqual(events[0]["city"], "Bonn-Bad Godesberg")
        self.assertEqual(events[1]["city"], "Troisdorf")
        self.assertTrue(all(event["all_day"] for event in events))

    def test_street_food_bonn_empty_landing_is_healthy_outside_window(self):
        self.assertTrue(bonn_food._street_food_bonn_expected_empty(
            '<div class="sfdatum">28. - 30. August 2027</div>'
        ))
        self.assertTrue(bonn_food._street_food_bonn_expected_empty(
            '<div class="sfdatum">Returning in 2027</div>'
        ))
        self.assertFalse(bonn_food._street_food_bonn_expected_empty(
            '<div class="sfdatum">24. - 26. Juli 2026</div>'
        ))

    def test_street_food_merges_both_organiser_pages_without_duplicates(self):
        bonn_page = """
        <h2>Nächste Termine</h2><p>28. - 30.08.2026 Street Food Festival - Bonn Bad Godesberg</p>
        <p>16. - 18.10.2026 Street Food Festival - Troisdorf</p>
        <h2>Veranstalter</h2><p>WEvent UG (haftungsbeschränkt) Rudolf-Diesel-Straße 16c</p>
        """
        siegburg_page = """
        <p>Am Markt, Siegburg</p><h2>Nächste Termine</h2>
        <p>28.-30.08.2026 Street Food Festival Bad Godesberg</p>
        <h2>Veranstalter</h2><p>WEvent UG (haftungsbeschränkt)</p>
        """
        with patch.object(
            common, "fetch_url", side_effect=[bonn_page, siegburg_page],
        ) as fetch:
            events = bonn_food.fetch_street_food()

        self.assertEqual(
            [call.args[0] for call in fetch.call_args_list],
            ["https://www.street-food-bonn.de/", "https://www.streetfood-siegburg.de/"],
        )
        self.assert_food_events(events, 2)
        self.assertEqual(
            [(event["date"], event["city"]) for event in events],
            [("2026-08-28", "Bonn-Bad Godesberg"),
             ("2026-10-16", "Troisdorf")],
        )
        self.assertEqual(
            [event["end_date"] for event in events],
            ["2026-08-30", "2026-10-18"],
        )
        self.assertTrue(all(
            event["link"] == "https://www.street-food-bonn.de/"
            for event in events
        ))

    def test_street_food_keeps_bonn_events_when_siegburg_page_fails(self):
        bonn_page = """
        <h2>Nächste Termine</h2>
        <p>28. - 30.08.2026 Street Food Festival - Bonn Bad Godesberg</p>
        <h2>Veranstalter</h2>
        """
        with patch.object(
            common, "fetch_url", side_effect=[bonn_page, TimeoutError("offline")],
        ), patch.object(common, "log_source_error") as log_error:
            events = bonn_food.fetch_street_food()

        self.assert_food_events(events, 1)
        self.assertEqual(events[0]["city"], "Bonn-Bad Godesberg")
        self.assertEqual(events[0]["link"], "https://www.street-food-bonn.de/")
        log_error.assert_called_once()

    def test_street_food_keeps_siegburg_page_provenance(self):
        siegburg_page = """
        <h2>Nächste Termine</h2>
        <p>12. - 13.09.2026 Street Food Festival Siegburg</p>
        <h2>Veranstalter</h2>
        """
        events = bonn_food.events_from_street_food(
            siegburg_page,
            source_url="https://www.streetfood-siegburg.de/",
        )

        self.assert_food_events(events, 1)
        self.assertEqual(events[0]["city"], "Siegburg")
        self.assertEqual(
            events[0]["link"], "https://www.streetfood-siegburg.de/"
        )

    def test_original_street_food_takes_dates_from_html_not_stale_jsonld(self):
        item = {
            "@context": "http://schema.org", "@type": "FoodEvent",
            "name": "Street Food Festival Bonn",
            "startDate": "2025-10-02T4:00PM", "endDate": "2025-10-05T08:00PM",
            "description": "Das Original Street Food Festival am Bonn Beueler Rheinufer.",
            "location": {"@type": "Place", "name": "Rheinufer Bonn-Beuel", "address": {
                "@type": "PostalAddress", "streetAddress": "Rheinaustraße",
                "addressLocality": "Bonn", "postalCode": "53225", "addressCountry": "DE"}},
        }
        html = (
            f'<script type="application/ld+json">{json.dumps(item)}</script>'
            '<h3 style="text-align:center;">01. - 04. Oktober 2026</h3>'
            '<h3>Do. 01.10. 16:00 – 22:00 Uhr</h3>'
        )
        events = bonn_food.events_from_original_street_food(html)
        self.assert_food_events(events, 1)
        self.assertEqual(events[0]["date"], "2026-10-01")
        self.assertEqual(events[0]["end_date"], "2026-10-04")
        # Postcode 53225 in the venue resolves the Beueler Rheinufer.
        self.assertEqual(events[0]["city"], "Bonn-Beuel")
        self.assertEqual(events[0]["venue"], "Rheinufer Bonn-Beuel")
        self.assertEqual(events[0]["venue_address"], "Rheinaustraße, 53225 Bonn")
        self.assertEqual(events[0]["link"], "https://street-food-festival.de/bonn")

    def test_original_street_food_publishes_nothing_without_a_readable_html_date(self):
        item = {
            "@type": "FoodEvent", "name": "Street Food Festival Bonn",
            "startDate": "2025-10-02T4:00PM",
            "location": {"@type": "Place", "name": "Rheinufer Bonn-Beuel"},
        }
        html = f'<script type="application/ld+json">{json.dumps(item)}</script><h3>Bald mehr</h3>'
        self.assertEqual(bonn_food.events_from_original_street_food(html), [])

    def test_original_street_food_recognizes_visible_stale_listing_without_jsonld(self):
        html = """
        <h1>Bonn</h1>
        <h2>Das Original Street Food Festival</h2>
        <h2>Street Food Festival in Bonn</h2>
        <h3>02. - 05. Oktober 2025</h3>
        <h2>LOCATION: Rheinufer Bonn Beuel</h2>
        """

        events = bonn_food.events_from_original_street_food(html)

        self.assert_food_events(events, 1)
        self.assertEqual(events[0]["title"], "Street Food Festival in Bonn")
        self.assertEqual(events[0]["start_date"], "2025-10-02")
        self.assertEqual(events[0]["end_date"], "2025-10-05")

    def test_choco_dealer_reads_slot_cards_from_the_booking_listing(self):
        html = """
        <div class="row events-cards"><div class="col-lg-4"><div class="card h-100 events-card">
          <a href="https://choco-dealer.com/SCHOKOLADEN-TASTING-FUER-EINSTEIGER/EVENT-BBA?slotId=019eacb7">
          <div class="h4 netzp-events-title mt-1 mb-1">DIE WELT DER SCHOKOLADE ENTDECKEN - EINSTEIGER</div></a>
          <span class="ms-1">31.07.26, 19:00 - 20:30 </span><small>(Europe/Berlin)</small><br>
          <b class="ms-1">CHOCO DEALER SHOP</b> | Elsässer Str.8 / 53175 Bonn<br>
          <b class="ms-1">19 Plätze verfügbar</b>
          <div class="card-text lead mt-3 mb-2">Der perfekte Einstieg in die Welt der Schokolade...</div>
        </div></div>
        <div class="col-lg-4"><div class="card h-100 events-card">
          <a href="/WEIN-SCHOKOLADEN-TASTING/EVENTWS?slotId=019ea74e">
          <div class="h4 netzp-events-title mt-1 mb-1">WEIN &amp; SCHOKOLADEN TASTING</div></a>
          <span class="ms-1">07.08.26, 19:00 - 22:00 </span>
          <b class="ms-1">CHOCO DEALER SHOP</b> | Elsässer Str.8, 53175 Bonn
          <div class="card-text lead mt-3 mb-2">Erlebe Genuss in Perfektion.</div>
        </div></div>
        <div class="col-lg-4"><div class="card h-100 events-card">
          <div class="h4 netzp-events-title mt-1 mb-1">PRIVATES TASTING AUF ANFRAGE</div>
          <div class="card-text lead mt-3 mb-2">Ohne festen Termin.</div>
        </div></div></div>
        """
        events = bonn_food.events_from_choco_dealer(html)
        self.assert_food_events(events, 2)
        self.assertEqual(
            events[0]["title"],
            "Schokoladen Tasting: DIE WELT DER SCHOKOLADE ENTDECKEN - EINSTEIGER",
        )
        self.assertEqual([event["time"] for event in events], ["19:00–20:30", "19:00–22:00"])
        self.assertEqual([event["start_date"] for event in events], ["2026-07-31", "2026-08-07"])
        self.assertTrue(all(event["city"] == "Bonn-Bad Godesberg" for event in events))
        self.assertEqual(events[0]["venue"], "CHOCO DEALER SHOP")
        self.assertEqual(events[0]["venue_address"], "Elsässer Str.8 / 53175 Bonn")
        self.assertEqual(
            events[1]["link"],
            "https://choco-dealer.com/WEIN-SCHOKOLADEN-TASTING/EVENTWS?slotId=019ea74e",
        )
        self.assertTrue(events[0]["description"].endswith("Schokolade"))


if __name__ == "__main__":
    unittest.main()
