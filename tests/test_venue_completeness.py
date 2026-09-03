import unittest
from datetime import datetime

from nrw_events.sources import (
    bonn,
    bonn_districts,
    regional_common,
    regional_html,
    regional_sitekit,
    regional_tourism,
    regional_venues,
)

from tests.helpers import patch_window


class VenueCompletenessTests(unittest.TestCase):
    def setUp(self):
        patch_window(self, datetime(2026, 7, 31), datetime(2026, 8, 27))

    def test_sitekit_detail_context_uses_structured_place_and_address(self):
        html = """
        <script type="application/ld+json">
        {
          "@context": "https://schema.org",
          "@type": "Event",
          "name": "Wochenmarkt in Vochem",
          "location": [{
            "@type": "Place",
            "name": "Balthasar-Neumann-Platz",
            "address": {
              "streetAddress": "Balthasar-Neumann-Platz",
              "postalCode": "50321",
              "addressLocality": "Brühl",
              "addressCountry": "DE"
            }
          }]
        }
        </script>
        <div class="SP-Paragraph">Kleiner Stadtteilmarkt.</div>
        """

        self.assertEqual(
            regional_sitekit._detail_context(html),
            {
                "description": "Kleiner Stadtteilmarkt.",
                "description_html": "<p>Kleiner Stadtteilmarkt.</p>",
                "venue": "Balthasar-Neumann-Platz",
                "venue_address": "Balthasar-Neumann-Platz, 50321 Brühl",
            },
        )

    def test_sitekit_detail_context_recovers_explicit_meeting_point(self):
        html = """
        <script type="application/ld+json">
        {"@context":"https://schema.org","@type":"Event","location":[]}
        </script>
        <div class="SP-Paragraph">
          <p><strong>Treffpunkt</strong>: Umweltzentrum, Friesheimer Busch 1,
          50374 Erftstadt</p>
          <p>Leitung: Dr. Petra Perge</p>
        </div>
        """

        context = regional_sitekit._detail_context(html)

        self.assertIn("Treffpunkt: Umweltzentrum", context["description"])
        self.assertIn("<strong>Treffpunkt</strong>", context["description_html"])
        self.assertEqual(context["venue"], "Umweltzentrum")
        self.assertEqual(
            context["venue_address"],
            "Friesheimer Busch 1, 50374 Erftstadt",
        )

    def test_sitekit_structured_place_wins_over_prose_fallback(self):
        html = """
        <script type="application/ld+json">
        {
          "@context":"https://schema.org",
          "@type":"Event",
          "location":{"@type":"Place","name":"Rathaus","address":"Markt 1"}
        }
        </script>
        <div class="SP-Paragraph">Treffpunkt: Seiteneingang, Nebenstraße 2</div>
        """

        context = regional_sitekit._detail_context(html)

        self.assertEqual(context["venue"], "Rathaus")
        self.assertEqual(context["venue_address"], "Markt 1")

    def test_explicit_place_context_rejects_vague_or_city_only_labels(self):
        self.assertEqual(
            regional_common.explicit_place_context(
                "Beginn 13 Uhr, Treffpunkt: nach Vereinbarung",
                "Brühl",
            ),
            {},
        )
        self.assertEqual(
            regional_common.explicit_place_context(
                "Veranstaltungsort: Zülpich",
                "Zülpich",
            ),
            {},
        )
        self.assertEqual(
            regional_common.explicit_place_context(
                "Veranstaltungsort: Bornheim. Quelle: Veranstaltungskalender",
                "Bornheim",
            ),
            {},
        )
        self.assertEqual(
            regional_common.explicit_place_context(
                "Treffpunkt: Kirchturm St.",
                "Erftstadt",
            ),
            {},
        )

    def test_ahrtal_shapehub_recovers_explicit_meeting_point(self):
        html = """
        <a href="/de/events/winzer/eventtermin.html" class="shapehub-card-link">
          <div class="shapehub-date-badge">26.08.2026</div>
          <div class="shapehub-card-title">Dem Winzer in den Keller geschaut</div>
          <li class="shapehub-card-content-icon-line shapehub-location-line">
            <svg></svg><span>Bad Neuenahr-Ahrweiler</span>
          </li>
        </a>
        """

        def detail_fetcher(url):
            self.assertEqual(
                url,
                "https://www.ahrtal.com/de/events/winzer/eventtermin.html",
            )
            return """
            <div class="shapehub-detail-description">
              <p>Treffpunkt: Winzerhof Körtgen, Oberhutstraße 16,
              Bad Neuenahr-Ahrweiler</p>
              <p>Dauer: 1,5 Stunden</p>
            </div>
            """

        [event] = regional_tourism._events_from_shapehub(
            html,
            "Ahrtal",
            "https://www.ahrtal.com",
            "https://www.ahrtal.com/de/events",
            "Ahrweiler",
            "ahrtal kultur",
            0.86,
            detail_fetcher=detail_fetcher,
        )

        self.assertEqual(event["venue"], "Winzerhof Körtgen")
        self.assertEqual(
            event["venue_address"],
            "Oberhutstraße 16, Bad Neuenahr-Ahrweiler",
        )
        self.assertEqual(event["identity_venue"], "")
        self.assertTrue(event["identity_venue_locked"])

    def test_ahrtal_shapehub_prefers_structured_visible_venue_block(self):
        html = """
        <a href="/de/events/yoga/eventtermin.html" class="shapehub-card-link">
          <div class="shapehub-date-badge">26.08.2026</div>
          <div class="shapehub-card-title">Yoga meets Wine</div>
          <li class="shapehub-location-line"><span>Dernau</span></li>
        </a>
        """
        detail = """
        <div class="shapehub-detail-description">
          Yoga meets Wine in der Dagernova Eventhalle in Dernau.
        </div>
        <strong>Veranstaltungsort</strong>
        <div class="shapehub-address-line">
          <div class="col-12">Eventhalle Dagernova</div>
          <div class="col-12">Römerstraße 32</div>
          <div class="col-12">53507 Dernau</div>
        </div>
        <strong>Organisator</strong>
        <div class="shapehub-address-line">
          <div>Dagernova Ahr Weinmanufaktur</div>
          <div>Heerstraße 91-93</div>
        </div>
        """

        [event] = regional_tourism._events_from_shapehub(
            html,
            "Ahrtal",
            "https://www.ahrtal.com",
            "https://www.ahrtal.com/de/events",
            "Ahrweiler",
            "ahrtal kultur",
            0.86,
            detail_fetcher=lambda _url: detail,
        )

        self.assertEqual(event["venue"], "Eventhalle Dagernova")
        self.assertEqual(event["venue_address"], "Römerstraße 32, 53507 Dernau")

    def test_sitekit_visible_venue_section_fills_empty_jsonld_location(self):
        html = """
        <script type="application/ld+json">
        {"@type":"Event","name":"Vier Positionen","location":[{"@type":"Place"}]}
        </script>
        <section aria-labelledby="veranstaltungsort">
          <h2 id="veranstaltungsort">Veranstaltungsort</h2>
          <div>Villa Kaufmann</div>
          <p>Am Volkspark 1, 50321 Brühl</p>
        </section>
        """

        context = regional_sitekit._detail_context(html, "Brühl")

        self.assertEqual(context["venue"], "Villa Kaufmann")
        self.assertEqual(context["venue_address"], "Am Volkspark 1, 50321 Brühl")

    def test_sitekit_visible_venue_section_splits_one_line_address(self):
        html = """
        <section aria-labelledby="veranstaltungsort">
          <h2 id="veranstaltungsort">Veranstaltungsort</h2>
          <p>Tanzschule Breuer GbR, Kurfürstenstraße 31, 50321 Brühl</p>
          <h4>Kontakt</h4><a>Tanzschule Breuer</a>
        </section>
        """

        context = regional_sitekit._detail_context(html, "Brühl")

        self.assertEqual(context["venue"], "Tanzschule Breuer GbR")
        self.assertEqual(context["venue_address"], "Kurfürstenstraße 31, 50321 Brühl")

    def test_sitekit_kletterwald_title_is_a_narrow_venue_signal(self):
        context = regional_sitekit._detail_context(
            '<script type="application/ld+json">'
            '{"@type":"Event","location":[]}</script>',
            "Brühl",
            "Kletterwald Schwindelfrei: Outdoor-Erlebnistage",
        )

        self.assertEqual(context["venue"], "Kletterwald Schwindelfrei")

    def test_bonn_sports_detail_enrichment_recovers_jsonld_place_list(self):
        event = {
            "title": "Fußball-Sommercamp beim TV Rheindorf",
            "date": "2026-08-26",
            "start_date": "2026-08-26",
            "end_date": "2026-08-26",
            "description": "Das Sommercamp findet in Bonn statt.",
            "description_html": "",
            "description_source": "generated",
            "venue": "",
            "venue_address": "",
            "city": "Bonn",
            "link": "https://www.bonn.de/sommercamp.php",
            "source": "Bonn.de Sports",
        }
        detail = """
        <script type="application/ld+json">
        {
          "@type":"Event",
          "name":"Fußball-Sommercamp beim TV Rheindorf",
          "location":[{"@type":"Place","name":"TV Rheindorf","address":{
            "streetAddress":"Kopenhagener Str. 17",
            "postalCode":"53117","addressLocality":"Bonn"
          }}]
        }
        </script>
        """

        [enriched] = bonn._enrich_sport_details([event], lambda _url: detail)

        self.assertEqual(enriched["venue"], "TV Rheindorf")
        self.assertEqual(enriched["venue_address"], "Kopenhagener Str. 17 53117 Bonn")
        self.assertEqual(enriched["identity_venue"], "")
        self.assertTrue(enriched["identity_venue_locked"])

    def test_roleber_detail_context_can_recover_only_an_explicit_place(self):
        html = """
        <div class="tribe-events-single-event-description">
          <p>Training und Verpflegung sind inklusive.</p>
          <p>Treffpunkt: Sportplatz Roleber, Siebengebirgsstraße 181, Bonn</p>
        </div>
        """

        self.assertEqual(
            bonn_districts._roleber_detail_context(html),
            {
                "description": (
                    "Training und Verpflegung sind inklusive. Treffpunkt: "
                    "Sportplatz Roleber, Siebengebirgsstraße 181, Bonn"
                ),
                "venue": "Sportplatz Roleber",
                "venue_address": "Siebengebirgsstraße 181, Bonn",
            },
        )

    def test_clickaround_accepts_address_label_as_structured_venue(self):
        html = """
        <div class="ui dividing header">Donnerstag, 20. August 2026</div>
        <div class="item">
          <b>Adresse:</b> JUZ Live Club<br>
          <a href="/events/424" aria-label="Mehr Infos - Death Feast">Mehr Infos</a>
        </div></div>
        """

        events = regional_venues._events_from_clickaround(
            html, "https://events.example/core"
        )

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["venue"], "JUZ Live Club")

    def test_eitorf_uses_explicit_meeting_point_when_card_only_names_city(self):
        self.assertEqual(
            regional_html._eitorf_venue(
                "Eitorf",
                "Treffpunkt ist der REWE-Markt, Ecke Poststraße.",
            ),
            "REWE-Markt",
        )

    def test_eitorf_keeps_place_detail_after_city_prefix(self):
        self.assertEqual(
            regional_html._eitorf_venue(
                'Eitorf, "unter\'m Pavillon" am Markt',
                "Eitorf Live beginnt um 19 Uhr.",
            ),
            '"unter\'m Pavillon" am Markt',
        )

    def test_broeltal_recovers_explicit_named_address_from_detail_copy(self):
        self.assertEqual(
            regional_html._broeltal_named_address(
                "Kostenfrei!\nBücherei St. Servatius, Hauptstr. 19, "
                "53809 Ruppichteroth\nInfo und Anmeldung"
            ),
            {
                "venue": "Bücherei St. Servatius",
                "venue_address": "Hauptstr. 19, 53809 Ruppichteroth",
            },
        )


if __name__ == "__main__":
    unittest.main()
