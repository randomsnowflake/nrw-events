import unittest
from datetime import datetime

from nrw_events.sources import regional_html, regional_sitekit, regional_venues
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
                "venue": "Balthasar-Neumann-Platz",
                "venue_address": "Balthasar-Neumann-Platz, 50321 Brühl",
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


if __name__ == "__main__":
    unittest.main()
