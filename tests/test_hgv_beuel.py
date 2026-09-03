import unittest
from datetime import datetime
from unittest.mock import patch

from nrw_events import detail_enrichment
from nrw_events.sources import SOURCES, hgv_beuel
from nrw_events.validation import canonicalize_event

from tests.helpers import patch_window

LISTING_HTML = """
<main>
  <article class="post type-post status-publish">
    <header class="entry-header"><h2 class="entry-title"><a href="https://www.hgv-beuel.de/23-08-2026-here-comes-the-sun">23.08.2026: Konzert „Here comes the sun…“ (Eine Veranstaltung der Brotfabrik Bühne mit dem Brotfabrik Chor)</a></h2></header>
    <div class="entry-content"><div class="excerpt"><p>Zwei Chöre präsentieren ein Programm von Renaissance bis Rock.</p></div></div>
  </article>
  <article class="post type-post status-publish">
    <header class="entry-header"><h2 class="entry-title"><a href="https://www.hgv-beuel.de/29-08-2026-tarab-trio">29.08.2026: Konzert: Das Tarab Trio – Wege nach Kurdistan (Eine Veranstaltung der Brotfabrik Bühne)</a></h2></header>
    <div class="entry-content"><div class="excerpt"><p>Kurdische Volkslieder, Balkanrhythmen und persische Musik.</p></div></div>
  </article>
  <article class="post type-post status-publish">
    <header class="entry-header"><h2 class="entry-title"><a href="https://www.hgv-beuel.de/12-06-2026-samba-bom-ein-ausflug-in-brasiliens-musikalische-matrix">04.09.2026: Samba Bom – Ein Ausflug in Brasiliens musikalische Matrix</a></h2></header>
    <div class="entry-content"><div class="excerpt"><p>Die Band spielt Samba, Bossa und Forro.</p></div></div>
  </article>
  <article class="post type-post status-publish">
    <header class="entry-header"><h2 class="entry-title"><a href="https://www.hgv-beuel.de/07-09-2025-offene-probe">Entfällt: 07.09.2025: Offene Probe des Musikvereins Beuel</a></h2></header>
    <div class="entry-content"><div class="excerpt"><p>Ein historischer abgesagter Kalendereintrag.</p></div></div>
  </article>
</main>
"""

DETAILS = {
    "https://www.hgv-beuel.de/23-08-2026-here-comes-the-sun": """
      <article><div class="entry-content">
        <p>Mitten im Hochsommer singen zwei Chöre über die Sonne und den Frühling.</p>
        <p>Wann: Sonntag, 23. August, 19 Uhr</p><p>Wo: Museumshof</p>
        <p>Anmeldung: Der Eintritt ist frei, um eine Spende wird gebeten.</p>
      </div></article><aside><p>Neueste Veranstaltungen</p></aside>
    """,
    "https://www.hgv-beuel.de/29-08-2026-tarab-trio": """
      <article><div class="entry-content">
        <p>Das Tarab Trio verbindet kurdische Volkslieder mit Balkanrhythmen.</p>
        <p>Wann: Samstag, 29. August, 18 Uhr</p><p>Wo: Museumshof</p>
        <p>Anmeldung: Vorverkauf über Bonnticket: 18 €, ermäßigt 10 €.</p>
      </div></article>
    """,
    "https://www.hgv-beuel.de/12-06-2026-samba-bom-ein-ausflug-in-brasiliens-musikalische-matrix": """
      <article><div class="entry-content">
        <p>Die brasilianische Band spielt Samba, Bossa und Forro.</p>
        <p>Wann: Freitag, 04. September</p><p>Veranstaltungsort: Museumshof</p>
        <p>Eintrittspreis: Eintritt frei. Spenden sind willkommen.</p>
        <p>Wegen der schlechten Wetteraussichten wurde die Veranstaltung verschoben.</p>
      </div></article>
    """,
}


class HgvBeuelSourceTests(unittest.TestCase):
    def setUp(self):
        patch_window(self, datetime(2026, 8, 22), datetime(2026, 9, 30))

    def test_listing_and_bounded_details_create_complete_occurrences(self):
        fetched = []

        events = hgv_beuel.events_from_html(
            LISTING_HTML,
            detail_fetcher=lambda url: fetched.append(url) or DETAILS[url],
        )

        self.assertEqual(len(events), 4)
        self.assertEqual(fetched, list(DETAILS))
        self.assertEqual(
            [event["title"] for event in events[:3]],
            [
                "Here comes the sun…",
                "Das Tarab Trio – Wege nach Kurdistan",
                "Samba Bom – Ein Ausflug in Brasiliens musikalische Matrix",
            ],
        )
        self.assertEqual([event["time"] for event in events[:3]], ["19:00", "18:00", ""])
        self.assertTrue(all(event["venue"] == "Heimatmuseum Beuel" for event in events))
        self.assertTrue(all(event["venue_address"] == "Wagnergasse 2-4, 53225 Bonn" for event in events))
        self.assertTrue(all(event["source_id"] == "hgv-beuel" for event in events))
        self.assertNotIn("Neueste Veranstaltungen", events[0]["description"])
        self.assertEqual(events[0]["price"], "kostenlos")
        self.assertEqual(events[0]["admission_basis"], "explicit")
        self.assertEqual(events[1]["price"], "18 €, ermäßigt 10 €")
        self.assertEqual(events[2]["status"], "scheduled")
        self.assertNotIn("wurde die Veranstaltung verschoben", events[2]["description"])
        self.assertEqual(events[3]["status"], "cancelled")

    def test_weather_postponement_note_is_suppressed_only_for_reviewed_replacement(self):
        listing = """
        <article class="post type-post status-publish">
          <h2 class="entry-title"><a href="https://www.hgv-beuel.de/10-09-2026-testkonzert">10.09.2026: Testkonzert</a></h2>
          <div class="excerpt"><p>Ein angekündigtes Konzert.</p></div>
        </article>
        """
        detail = """
        <div class="entry-content">
          <p>Das Testkonzert im Museumshof.</p>
          <p>Wann: Donnerstag, 10. September, 19 Uhr</p>
          <p>Wegen der schlechten Wetteraussichten wurde die Veranstaltung verschoben.</p>
        </div>
        """

        [event] = hgv_beuel.events_from_html(listing, detail_fetcher=lambda _url: detail)

        self.assertEqual(event["status"], "postponed")
        self.assertIn("wurde die Veranstaltung verschoben", event["description"])

    def test_detail_failure_keeps_listing_description_and_occurrence(self):
        with patch.object(hgv_beuel.common, "log_source_error") as log_error:
            events = hgv_beuel.events_from_html(
                LISTING_HTML,
                detail_fetcher=lambda _url: (_ for _ in ()).throw(TimeoutError("slow")),
            )

        self.assertEqual(len(events), 4)
        self.assertIn("Renaissance bis Rock", events[0]["description"])
        self.assertEqual(log_error.call_count, 3)

    def test_records_survive_canonical_validation_with_location_and_admission(self):
        [event, *_] = hgv_beuel.events_from_html(
            LISTING_HTML,
            detail_fetcher=lambda url: DETAILS[url],
        )

        canonical = canonicalize_event(event)

        self.assertEqual(canonical.source_id, "hgv-beuel")
        self.assertEqual(canonical.venue, "Heimatmuseum Beuel")
        self.assertEqual(canonical.venue_address, "Wagnergasse 2-4, 53225 Bonn")
        self.assertEqual(canonical.price, "kostenlos")
        self.assertEqual(canonical.category_key, "concert")

    def test_shared_detail_pass_does_not_refetch_adapter_owned_pages(self):
        events = hgv_beuel.events_from_html(
            LISTING_HTML,
            detail_fetcher=lambda url: DETAILS[url],
        )

        with patch.object(detail_enrichment.common, "fetch_detail_url") as fetch_detail:
            enriched = detail_enrichment.enrich_events(events)

        self.assertEqual(enriched, events)
        fetch_detail.assert_not_called()

    def test_malformed_recognizable_listing_reports_parser_empty(self):
        malformed = '<article class="post type-post"><h2 class="entry-title">Redesigned card</h2></article>'
        with patch.object(hgv_beuel.common, "fetch_url", return_value=malformed), patch.object(
            hgv_beuel.common, "log_source_error"
        ) as log_error:
            events = hgv_beuel.fetch()

        self.assertEqual(events, [])
        self.assertEqual(log_error.call_args.kwargs["source_id"], "hgv-beuel")
        self.assertIn("parser returned no event records", str(log_error.call_args.args[1]))

    def test_source_is_registered(self):
        self.assertIs(SOURCES["Heimatmuseum Beuel"], hgv_beuel.fetch)


if __name__ == "__main__":
    unittest.main()
