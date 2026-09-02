import unittest
from datetime import datetime
from unittest.mock import patch

from nrw_events import report
from nrw_events.identity import event_id
from nrw_events.sources import coelln_konzept, harmonie, regional_html
from nrw_events.validation import canonicalize_event
from tests.helpers import patch_window


class ExplicitSourceVenueTests(unittest.TestCase):
    def setUp(self):
        patch_window(self, datetime(2026, 8, 1), datetime(2026, 8, 31))

    def test_harmonie_recovers_explicit_hosted_venue(self):
        event = self._event("Harmonie", "https://www.harmonie-bonn.de/event/hosted/")
        [enriched] = harmonie._fill_calendar_venues([event])

        self.assertEqual(enriched["venue"], "Harmonie Bonn")
        canonical = canonicalize_event(enriched)
        [published] = report.deduplicate([canonical])
        self.assertEqual(published.venue, "Harmonie Bonn")

    def test_harmonie_preserves_explicit_external_location(self):
        event = self._event(
            "Open Air", "https://www.harmonie-bonn.de/event/external/", venue="Kunstrasen Bonn"
        )

        [enriched] = harmonie._fill_calendar_venues([event])

        self.assertEqual(enriched["venue"], "Kunstrasen Bonn")

    def test_coelln_uses_explicit_page_location(self):
        [event] = coelln_konzept._events_from_listing(
            self._coelln_listing(),
            lambda _url: """
              <h2>Antikmarkt Bonn</h2>
              <p class="textmarkt">Antiquitäten und Design.</p>
              <h3>Standort:</h3>
              <p class="textmarkt">Friedensplatz, 53111 Bonn</p>
            """,
        )

        self.assertEqual(event["venue"], "Friedensplatz")
        self.assertEqual(event["venue_address"], "53111 Bonn")
        old_identity = dict(event, venue=event["venue"])
        old_identity.pop("identity_venue", None)
        old_identity.pop("identity_venue_locked", None)
        self.assertEqual(event_id(event), event_id(old_identity))
        canonical = canonicalize_event(event)
        [published] = report.deduplicate([canonical])
        self.assertTrue(published.venue)

    def test_coelln_does_not_turn_city_only_location_or_title_into_venue(self):
        [event] = coelln_konzept._events_from_listing(
            self._coelln_listing(),
            lambda _url: """
              <h2>Antikmarkt Bonn</h2>
              <p class="textmarkt">Antiquitäten und Design.</p>
              <h3>Standort:</h3><p class="textmarkt">53111 Bonn</p>
            """,
        )

        self.assertEqual(event["venue"], "")
        self.assertEqual(event["identity_venue"], "")
        old_identity = dict(event, venue="")
        old_identity.pop("identity_venue", None)
        old_identity.pop("identity_venue_locked", None)
        self.assertEqual(event_id(event), event_id(old_identity))

    def test_coelln_does_not_turn_directions_into_a_venue(self):
        self.assertEqual(
            coelln_konzept._explicit_location(
                "Anfahrt über Annostraße Navi: Annostr. 3, 53721 Siegburg"
            ),
            "",
        )

    def test_coelln_preserves_filtered_direction_identity(self):
        [event] = coelln_konzept._events_from_listing(
            self._coelln_listing(),
            lambda _url: """
              <h2>Antikmarkt Bonn</h2>
              <p class="textmarkt">Antiquitäten und Design.</p>
              <h3>Standort:</h3>
              <p class="textmarkt">Anfahrt über Annostraße Navi: Annostr. 3, 53721 Siegburg</p>
            """,
        )

        self.assertEqual(event["venue"], "")
        self.assertEqual(event["identity_venue"], "Anfahrt über Annostraße Navi:")
        old_identity = dict(event, venue="Anfahrt über Annostraße Navi:")
        old_identity.pop("identity_venue", None)
        old_identity.pop("identity_venue_locked", None)
        self.assertEqual(event_id(event), event_id(old_identity))

    def test_broeltal_recovers_all_explicit_phrase_shapes(self):
        cases = (
            (
                "Die Veranstaltung findet im Pfarrheim Winterscheid, Hauptstr. 19 statt.",
                {"venue": "Pfarrheim Winterscheid", "venue_address": "Hauptstr. 19"},
            ),
            (
                "Treffpunkt ist der Parkplatz an der Grundschule in Schönenberg.",
                {"venue": "Parkplatz an der Grundschule in Schönenberg"},
            ),
            (
                "Ort der Veranstaltung: Am Dorfhaus im Mehrgenerationenpark Schönenberg",
                {"venue": "Am Dorfhaus im Mehrgenerationenpark Schönenberg"},
            ),
            (
                "Die KULT PARTY steigt im Festzelt in Bruchhausen Röttgen ab 20:00 Uhr.",
                {"venue": "Festzelt Bruchhausen Röttgen"},
            ),
        )
        for prose, expected in cases:
            with self.subTest(prose=prose):
                self.assertEqual(regional_html._broeltal_explicit_place(prose), expected)

    def test_broeltal_keeps_explicit_venue_through_adapter_and_publication(self):
        listing = """
          <a class="list-group-item list-group-item-action" href="/termine/sommerfest.html">
            <h4>Sommerfest Schönenberg</h4><span>20.08.2026 10:00 Uhr</span>
          </a>
        """
        detail = """
          <h1>Sommerfest Schönenberg</h1>
          <time>20.08.2026</time>
          <div class="event-description">
            Ort der Veranstaltung: Am Dorfhaus im Mehrgenerationenpark Schönenberg
          </div>
        """

        [event] = regional_html._events_from_broeltal(
            listing,
            "https://www.broeltal.de",
            detail_fetcher=lambda _url: detail,
        )

        self.assertEqual(event["venue"], "Am Dorfhaus im Mehrgenerationenpark Schönenberg")
        canonical = canonicalize_event(event)
        [published] = report.deduplicate([canonical])
        self.assertEqual(
            published.venue,
            "Am Dorfhaus im Mehrgenerationenpark Schönenberg",
        )

    def test_broeltal_keeps_blank_without_event_scoped_evidence(self):
        self.assertEqual(
            regional_html._broeltal_explicit_place("Musik und Begegnung für die ganze Familie."),
            {},
        )

    def test_broeltal_binds_dedicated_page_copy_by_title_and_date(self):
        event = self._event(
            "Bröltaler Erntedankfest",
            "https://www.broeltal.de/termine/erntedankfest.html",
            city="Ruppichteroth",
        )
        event["start_date"] = "2026-09-04"
        detail = """
          <div class="tx-gbevents-pi1"><div class="card"><div class="card-body">
            <h4>Bröltaler Erntedankfest</h4><h4>04.09. – 07.09.2026</h4>
            <p>Die KULT PARTY steigt im Festzelt in Bruchhausen Röttgen ab 20:00 Uhr.</p>
          </div></div></div>
        """

        context = regional_html._broeltal_detail_context(detail, event)

        self.assertEqual(context["venue"], "Festzelt Bruchhausen Röttgen")

    def test_broeltal_does_not_cross_into_a_sibling_event_card_for_location(self):
        event = self._event(
            "Bröltaler Erntedankfest",
            "https://www.broeltal.de/termine/erntedankfest.html",
            city="Ruppichteroth",
        )
        event["start_date"] = "2026-09-04"
        detail = """
          <div class="tx-gbevents-pi1">
            <div class="card"><div class="card-body">
              <h4>Bröltaler Erntedankfest</h4><h4>04.09. – 07.09.2026</h4>
              <p>Musik und Begegnung für die ganze Familie.</p>
            </div></div>
            <div class="card"><div class="card-body">
              <h4>Winterfest</h4><h4>21.12.2026</h4>
              <p>Die Feier steigt im Festzelt in Fremdort ab 20:00 Uhr.</p>
            </div></div>
          </div>
        """

        context = regional_html._broeltal_detail_context(detail, event)

        self.assertEqual(context["venue"], "")

    def test_broeltal_card_boundary_overrides_cross_occurrence_exact_description(self):
        event = self._event(
            "Bröltaler Erntedankfest",
            "https://www.broeltal.de/termine/erntedankfest.html",
            city="Ruppichteroth",
        )
        event["start_date"] = "2026-09-04"
        detail = """
          <div class="tx-gbevents-pi1">
            <div class="card"><div class="card-body">
              <h4>Bröltaler Erntedankfest</h4><h4>04.09. – 07.09.2026</h4>
              <p>Musik und Begegnung für die ganze Familie.</p>
            </div></div>
            <div class="card"><div class="card-body">
              <h4>Winterfest</h4><h4>21.12.2026</h4>
              <p>Die Feier steigt im Festzelt in Fremdort ab 20:00 Uhr.</p>
            </div></div>
          </div>
        """
        with patch.object(
            regional_html.detail_enrichment,
            "extract_detail_context",
            return_value={
                "description": "Die Feier steigt im Festzelt in Fremdort ab 20:00 Uhr.",
                "exact_description": "Die Feier steigt im Festzelt in Fremdort ab 20:00 Uhr.",
            },
        ):
            context = regional_html._broeltal_detail_context(detail, event)

        self.assertEqual(context["venue"], "")

    def test_broeltal_does_not_use_same_page_footer_copy_as_event_location(self):
        event = self._event(
            "Bröltaler Erntedankfest",
            "https://www.broeltal.de/termine/erntedankfest.html",
            city="Ruppichteroth",
        )
        event["start_date"] = "2026-09-04"
        detail = """
          <main><h1>Bröltaler Erntedankfest</h1><p>04.09. – 07.09.2026</p></main>
          <footer>Besuchen Sie uns im Festzelt in Fremdort ab 20:00 Uhr.</footer>
        """

        context = regional_html._broeltal_detail_context(detail, event)

        self.assertEqual(context["venue"], "")

    def test_broeltal_ignores_footer_location_contamination(self):
        event = self._event(
            "Sommerfest", "https://www.broeltal.de/termine/sommerfest.html", city="Ruppichteroth"
        )
        detail = """
          <div class="event-description">Musik und Begegnung für die ganze Familie.</div>
          <footer>Treffpunkt ist der Parkplatz an der Grundschule in Schönenberg.</footer>
        """

        context = regional_html._broeltal_detail_context(detail, event)

        self.assertFalse(context.get("venue"))

    def test_broeltal_uses_exact_event_copy_for_location(self):
        event = self._event(
            "Sommerfest", "https://www.broeltal.de/termine/sommerfest.html", city="Ruppichteroth"
        )
        with patch.object(
            regional_html.detail_enrichment,
            "extract_detail_context",
            return_value={
                "description": "Treffpunkt ist der Parkplatz am Rathaus.",
                "exact_description": "Offizielle Beschreibung ohne Ortsangabe.",
            },
        ):
            context = regional_html._broeltal_detail_context("<html></html>", event)

        self.assertFalse(context.get("venue"))

    def test_broeltal_rejects_place_copy_from_another_occurrence(self):
        event = self._event(
            "Sommerfest", "https://www.broeltal.de/termine/sommerfest.html",
            city="Ruppichteroth",
        )
        detail = """
          <h1>Winterfest</h1><time>21.12.2026</time>
          <div class="event-description">
            Ort der Veranstaltung: Am Dorfhaus im Mehrgenerationenpark Schönenberg
          </div>
        """

        context = regional_html._broeltal_detail_context(detail, event)

        self.assertFalse(context.get("venue"))

    def test_broeltal_ignores_unrelated_jsonld_description_on_matching_page(self):
        event = self._event(
            "Sommerfest", "https://www.broeltal.de/termine/sommerfest.html",
            city="Ruppichteroth",
        )
        detail = """
          <h1>Sommerfest</h1><time>16.08.2026</time>
          <script type="application/ld+json">
          {"@type":"Event","name":"Winterfest","startDate":"2026-12-21",
           "description":"Ort der Veranstaltung: Am Dorfhaus im Mehrgenerationenpark Schönenberg"}
          </script>
        """

        context = regional_html._broeltal_detail_context(detail, event)

        self.assertFalse(context.get("venue"))

    def test_broeltal_clears_unrelated_jsonld_location_on_matching_page(self):
        event = self._event(
            "Sommerfest", "https://www.broeltal.de/termine/sommerfest.html",
            city="Ruppichteroth",
        )
        detail = """
          <h1>Sommerfest</h1><time>16.08.2026</time>
          <div class="event-description">Musik und Begegnung für die ganze Familie.</div>
          <script type="application/ld+json">
          {"@type":"Event","name":"Winterfest","startDate":"2026-12-21",
           "location":{"@type":"Place","name":"Dorfhaus Schönenberg",
             "address":{"@type":"PostalAddress","streetAddress":"Dorfstraße 7",
               "postalCode":"53809","addressLocality":"Ruppichteroth"}}}
          </script>
        """

        context = regional_html._broeltal_detail_context(detail, event)

        self.assertFalse(context.get("venue"))
        self.assertFalse(context.get("venue_address"))

    @staticmethod
    def _coelln_listing():
        return """
          <tr><td class="jahr" colspan="5">Termine 2026</td></tr>
          <tr><td class="datum">So 16. Aug.</td>
          <td class="markt"><a class="linkmarkt" href="markt/bonn.html">Antikmarkt Bonn</a></td></tr>
        """

    @staticmethod
    def _event(title, link, *, venue="", city="Bonn"):
        return {
            "title": title,
            "date": "2026-08-16",
            "start_date": "2026-08-16",
            "end_date": "2026-08-16",
            "start_at": "",
            "end_at": "",
            "time": "",
            "all_day": True,
            "venue": venue,
            "venue_address": "",
            "city": city,
            "description": "Offizielle Veranstaltungsbeschreibung.",
            "description_html": "",
            "description_source": "scraped",
            "link": link,
            "source": "Harmonie Bonn" if "harmonie" in link else "Bröltal / Ruppichteroth",
            "source_id": "harmonie-bonn" if "harmonie" in link else "broeltal-ruppichteroth-events",
            "category": "concert",
            "category_key": "concert",
            "score": 1.0,
            "price": "",
        }


if __name__ == "__main__":
    unittest.main()
