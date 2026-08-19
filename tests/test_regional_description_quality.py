import unittest
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime
from unittest.mock import patch

from nrw_events import http, report
from nrw_events.sources import (
    bonn_venues,
    regional_feeds,
    regional_html,
    regional_ionas4,
    regional_tourism,
    requested_venues,
    ruhrguide,
)
from tests.helpers import patch_window


class RegionalDescriptionQualityTests(unittest.TestCase):
    def setUp(self):
        patch_window(self, datetime(2026, 7, 13), datetime(2026, 7, 26))

    def test_ionas4_listing_calendars_use_bounded_web_unlocker_fallbacks(self):
        with patch.object(
            http,
            "fetch_url_with_brightdata_fallback",
            side_effect=["[]"] * len(regional_ionas4._CALENDARS),
        ) as fetcher:
            events = regional_ionas4.fetch()

        self.assertEqual(events, [])
        self.assertEqual(fetcher.call_count, len(regional_ionas4._CALENDARS))
        for call, (_, url, _, _) in zip(fetcher.call_args_list, regional_ionas4._CALENDARS):
            self.assertEqual(call.args, (url,))
            self.assertEqual(
                call.kwargs["allowed_hosts"],
                (urllib.parse.urlsplit(url).hostname,),
            )
            self.assertTrue(call.kwargs["fallback_on_timeout"])
            self.assertEqual(call.kwargs["fallback_statuses"], (408, 429, 500, 502, 503, 504))

    def test_ionas4_uses_detail_description_location_and_direct_link(self):
        items = [{
            "id": "9697:0",
            "start": "2026-07-18T18:00",
            "end": "2026-07-18T00:00",
            "title": "Der Ahrweinbau im Fokus",
            "website": "",
            "category": {"name": "Veranstaltung"},
            "tags": [],
            "location": {"name": None},
        }]
        detail_html = """
<div class="integration-details__field tvm-event--description">
  <p>Bildvortrag über Geschichte, Gegenwart und Zukunft des Ahrweinbaus.</p>
  <p>Mit Verkostung erlesener Weine und Brotzeit.</p>
</div>
<p class="integration-details__field tvm-event--location">
  <a>Zehnthof, Zehnthofstr. 2, 53489 Sinzig</a>
</p>
<script>navigator.clipboard.writeText(
  "https://tourismus.sinzig.de/kalender/2026-07-18-der-ahrweinbau-im-fokus/9697:0"
);</script>
"""
        requested = []

        events = regional_ionas4._events_from_items(
            items,
            "Sinzig",
            "https://tourismus.sinzig.de/kalender/",
            0.82,
            detail_fetcher=lambda url: requested.append(url) or detail_html,
        )

        self.assertEqual(len(events), 1)
        self.assertIn("Geschichte, Gegenwart und Zukunft", events[0]["description"])
        self.assertEqual(events[0]["venue"], "Zehnthof")
        self.assertEqual(events[0]["venue_address"], "Zehnthofstr. 2, 53489 Sinzig")
        self.assertEqual(events[0]["time"], "18:00")
        self.assertEqual(events[0]["end_at"], events[0]["start_at"])
        self.assertEqual(
            events[0]["link"],
            "https://tourismus.sinzig.de/kalender/2026-07-18-der-ahrweinbau-im-fokus/9697:0",
        )
        self.assertIn("eventId=9697%3A0", requested[0])

    def test_ionas4_resolves_relative_detail_links_against_the_calendar_origin(self):
        events = regional_ionas4._events_from_items(
            [{
                "id": "29905:0",
                "start": "2026-07-21T18:00",
                "end": "2026-07-21T20:00",
                "title": "Heizungsgespräche",
                "website": "",
                "category": {"name": "Klimaschutz"},
                "tags": [],
                "location": {"name": "Rathaus"},
            }],
            "Bad Honnef",
            "https://meinbadhonnef.de/kalender/veranstaltungen/",
            0.98,
            detail_fetcher=lambda _url: """
                <script>navigator.clipboard.writeText(
                  "/stadt-bad-honnef/startseite/klima/aktuelles/"
                );</script>
            """,
        )

        self.assertEqual(
            events[0]["link"],
            "https://meinbadhonnef.de/stadt-bad-honnef/startseite/klima/aktuelles/",
        )

    def test_ionas4_treats_all_day_midnight_end_as_exclusive(self):
        requested = []
        events = regional_ionas4._events_from_items(
            [{
                "id": "83656:0",
                "start": "2026-07-11T00:00",
                "end": "2026-07-13T00:00",
                "allDay": True,
                "title": "Blaulichtfest in Ringen",
                "website": "",
                "category": {"name": "Fest"},
                "tags": [],
                "location": {"name": "Feuerwehrhaus Ringen"},
            }],
            "Grafschaft",
            "https://www.gemeinde-grafschaft.de/kalender/kalendergrafschaft/",
            0.9,
            detail_fetcher=lambda url: requested.append(url) or "",
        )

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["end_date"], "2026-07-12")
        self.assertEqual(requested, [])

    def test_ionas4_collapses_consecutive_all_day_occurrences_into_one_run(self):
        items = [
            {
                "id": f"21682:{index}",
                "start": f"2026-08-{28 + index:02d}T00:00",
                "end": f"2026-08-{29 + index:02d}T00:00",
                "allDay": True,
                "title": "Freiluftgalerie Rhöndorf",
                "website": "https://freiluftgalerierhoendorf.de/",
                "category": None,
                "tags": [],
                "location": {"name": "Ziepchensplatz, Bad Honnef-Rhöndorf"},
            }
            for index in range(3)
        ]

        events = regional_ionas4._events_from_items(
            items,
            "Bad Honnef",
            "https://meinbadhonnef.de/kalender/veranstaltungen/",
            0.98,
            detail_fetcher=None,
            source_id="ionas4-bad-honnef",
        )

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["start_date"], "2026-08-28")
        self.assertEqual(events[0]["end_date"], "2026-08-30")
        self.assertTrue(events[0]["all_day"])
        self.assertEqual(events[0]["category_key"], "exhibition")

    def test_ionas4_keeps_nonconsecutive_all_day_occurrences_separate(self):
        items = [
            {
                "id": f"500:{index}",
                "start": start,
                "end": end,
                "allDay": True,
                "title": "Aktionstag",
                "website": "https://example.test/aktionstag",
                "category": {"name": "Fest"},
                "tags": [],
                "location": {"name": "Marktplatz"},
            }
            for index, (start, end) in enumerate((
                ("2026-08-20T00:00", "2026-08-21T00:00"),
                ("2026-08-27T00:00", "2026-08-28T00:00"),
                ("2026-09-03T00:00", "2026-09-04T00:00"),
            ))
        ]

        events = regional_ionas4._events_from_items(
            items,
            "Bad Honnef",
            "https://meinbadhonnef.de/kalender/veranstaltungen/",
            0.98,
            detail_fetcher=None,
            source_id="ionas4-bad-honnef",
        )

        self.assertEqual(len(events), 3)

    def test_ionas4_replaces_a_description_that_only_repeats_the_title(self):
        items = [{
            "id": "83680:0",
            "start": "2026-07-26",
            "end": "2026-07-26",
            "title": "Sportwoche 2026",
            "website": "",
            "category": {"name": "Sport"},
            "tags": [{"name": "Sportwoche 2026"}],
            "location": {"name": "Sportplatz Leimersdorf"},
        }]

        events = regional_ionas4._events_from_items(
            items,
            "Grafschaft",
            "https://www.gemeinde-grafschaft.de/kalender/kalendergrafschaft/",
            0.9,
            detail_fetcher=lambda _url: "",
        )

        self.assertEqual(len(events), 1)
        self.assertNotEqual(events[0]["description"], events[0]["title"])
        self.assertIn("26.07.2026", events[0]["description"])
        self.assertIn("Sportplatz Leimersdorf", events[0]["description"])

    def test_ionas4_preserves_a_short_fact_and_adds_event_context(self):
        items = [{
            "id": "83680:0",
            "start": "2026-07-26",
            "end": "2026-07-26",
            "title": "Sportwoche 2026",
            "website": "",
            "category": {"name": "Sport"},
            "tags": [],
            "location": {"name": "Sportplatz Leimersdorf"},
        }]
        detail_html = """
<div class="integration-details__field tvm-event--description">Eintritt frei</div>
<div class="integration-details__field tvm-event--location">Sportplatz Leimersdorf</div>
"""

        events = regional_ionas4._events_from_items(
            items,
            "Grafschaft",
            "https://www.gemeinde-grafschaft.de/kalender/kalendergrafschaft/",
            0.9,
            detail_fetcher=lambda _url: detail_html,
        )

        self.assertEqual(len(events), 1)
        self.assertTrue(events[0]["description"].startswith("Eintritt frei. "))
        self.assertIn("26.07.2026", events[0]["description"])
        self.assertIn("Sportplatz Leimersdorf", events[0]["description"])

    def test_bad_honnef_is_registered_for_ionas_detail_enrichment(self):
        self.assertIsNotNone(regional_ionas4._detail_fetcher_for_city("Bad Honnef"))

    def test_kult41_category_extraction_stops_before_trailing_page_content(self):
        html = """
<div class="em-event em-item ">
  <h3 class="em-item-title"><a href="https://kult41.de/events/show">Show</a></h3>
  <div class="em-event-date"><span></span>21.07.26</div>
  <div class="em-event-time"><span class="em-icon-clock"></span>19:00 - 22:00</div>
  <div class="em-item-taxonomy em-event-categories">
    <a href="https://kult41.de/events/categories/konzert">Konzert</a>
  </div>
  <div class="em-item-desc">Live-Musik.</div>
</div>
<aside>
  <a href="https://kult41.de/events/categories/ausstellung">Ausstellung</a>
  <a href="https://kult41.de/events/categories/literatur">Literatur</a>
</aside>
"""

        events = bonn_venues.events_from_kult41(html)

        self.assertEqual(len(events), 1)
        self.assertIn("Konzert", events[0]["category"])
        self.assertNotIn("Ausstellung", events[0]["category"])
        self.assertNotIn("Literatur", events[0]["category"])

    def test_botanical_garden_uses_official_detail_description(self):
        listing_html = """
<a href="https://www.botgart.uni-bonn.de/de/ihr-besuch/veranstaltungen/2026/gruene-schule/sonntagsfuehrungen/sonntagsfuehrung-19-juli">
  Führung Sonntag, 19.07.2026 11:00 Uhr Sonntagsführung 19. Juli
</a>
"""
        detail_html = """
<div id="event-description">
  Kommen Sie mit auf einen Spaziergang durch die Botanischen Gärten und erfahren
  Sie Wissenswertes über die Pflanzen-Highlights der Saison.
</div>
"""

        events = bonn_venues.events_from_botgart(
            listing_html,
            detail_fetcher=lambda _url: detail_html,
        )

        self.assertEqual(len(events), 1)
        self.assertIn("Spaziergang durch die Botanischen Gärten", events[0]["description"])
        self.assertNotEqual(events[0]["description"], "Führung")

    def test_botanical_garden_detail_failure_still_returns_useful_text(self):
        listing_html = """
<a href="https://www.botgart.uni-bonn.de/de/ihr-besuch/veranstaltungen/2026/gruene-schule/sonntagsfuehrungen/sonntagsfuehrung-19-juli">
  Führung Sonntag, 19.07.2026 11:00 Uhr Sonntagsführung 19. Juli
</a>
"""

        events = bonn_venues.events_from_botgart(
            listing_html,
            detail_fetcher=lambda _url: (_ for _ in ()).throw(TimeoutError("detail timeout")),
        )

        self.assertEqual(len(events), 1)
        self.assertIn("19.07.2026", events[0]["description"])
        self.assertIn("Botanischen Gärten Bonn", events[0]["description"])

    def test_kunstmuseum_uses_detail_body_instead_of_only_format(self):
        listing_html = """
<a href="https://www.kunstmuseum-bonn.de/de/besuch/kalender/gem%c2%b7einsam-12/">
  <figure><figcaption class="teaser-caption">
    <p class="teaser-date">Mi. 15.07.2026, 17:30 Uhr</p>
    <h4 class="teaser-title">GEM·EINSAM</h4>
    <p class="teaser-meta">Workshop</p>
  </figcaption></figure>
</a>
"""
        detail_html = """
<div class="post-body">
  <p>In der Sammlung begegnen wir expressionistischen Künstler:innen und den
  Geschichten hinter ihren Werken.</p>
  <p>Im Workshop gestalten wir eigene ausdrucksstarke Porträts.</p>
</div>
"""

        events = requested_venues._events_from_kunstmuseum_bonn(
            listing_html,
            detail_fetcher=lambda _url: detail_html,
        )

        self.assertEqual(len(events), 1)
        self.assertIn("expressionistischen Künstler:innen", events[0]["description"])
        self.assertNotEqual(events[0]["description"], "Workshop")

    def test_kunstmuseum_reads_labeled_cost_from_detail_page(self):
        patch_window(self, datetime(2026, 8, 18), datetime(2026, 8, 20))
        listing_html = """
<a href="https://www.kunstmuseum-bonn.de/de/besuch/kalender/glow-and-create/">
  <figure><figcaption class="teaser-caption">
    <p class="teaser-date">Mi. 19.08.2026, 17:00 Uhr</p>
    <h4 class="teaser-title">GLOW AND CREATE: SCHWARZLICHT-MALEREI IM MUSEUM</h4>
    <p class="teaser-meta">Workshop</p>
  </figcaption></figure>
</a>
"""
        detail_html = """
<div class="post-body">
  <p>Gestalte im Schwarzlicht mit Neonfarben dein eigenes Kunstwerk.</p>
</div>
<div class="post-info-content">
  <h5>Angebot für</h5><p>Erwachsene</p>
  <h5>Format</h5><p>Workshop</p>
  <h5>Kosten</h5><p>22 Euro (inkl. einem Getränk)</p>
  <h5>Dauer</h5><p>2 Stunden</p>
</div>
"""

        [event] = requested_venues._events_from_kunstmuseum_bonn(
            listing_html,
            detail_fetcher=lambda _url: detail_html,
        )

        self.assertEqual(event["price"], "22 Euro (inkl. einem Getränk)")
        self.assertEqual(event["admission_basis"], "explicit")

    def test_kunstmuseum_detail_failure_still_returns_useful_text(self):
        listing_html = """
<a href="https://www.kunstmuseum-bonn.de/de/besuch/kalender/gem%c2%b7einsam-12/">
  <figure><figcaption class="teaser-caption">
    <p class="teaser-date">Mi. 15.07.2026, 17:30 Uhr</p>
    <h4 class="teaser-title">GEM·EINSAM</h4>
    <p class="teaser-meta">Workshop</p>
  </figcaption></figure>
</a>
"""

        events = requested_venues._events_from_kunstmuseum_bonn(
            listing_html,
            detail_fetcher=lambda _url: (_ for _ in ()).throw(TimeoutError("detail timeout")),
        )

        self.assertEqual(len(events), 1)
        self.assertIn("Workshop", events[0]["description"])
        self.assertIn("15.07.2026", events[0]["description"])
        self.assertIn("Kunstmuseum Bonn", events[0]["description"])

    def test_ruhrguide_discards_source_description_and_does_not_fetch_detail_copy(self):
        events = [{
            "title": "Conni – Das Musical!",
            "start_date": "2026-07-19",
            "end_date": "2026-07-19",
            "time": "15:00",
            "venue": "Theater am Tanzbrunnen, Köln",
            "city": "Köln",
            "link": "https://www.ruhr-guide.de/veranstaltung/conni-das-musical/",
            "description": "Langer redaktioneller Ruhr-Guide-Text.",
            "description_html": "<p>Langer redaktioneller Ruhr-Guide-Text.</p>",
            "description_source": "scraped",
        }]

        [minimal] = ruhrguide._keep_only_master_data(events)

        self.assertNotIn("redaktioneller", minimal["description"])
        self.assertIn("19.07.2026", minimal["description"])
        self.assertIn("Theater am Tanzbrunnen", minimal["description"])
        self.assertEqual(minimal["description_source"], "generated")

    def test_ruhrguide_expands_long_tour_span_into_explicit_local_dates(self):
        broad = {
            "title": "Conni – Das Musical!",
            "start_date": "2025-10-25", "end_date": "2027-05-23",
            "time": "15:00", "venue": "Theater am Tanzbrunnen", "city": "Köln",
            "link": "https://www.ruhr-guide.de/veranstaltung/conni/",
            "description": "Redaktioneller Tourtext.", "category": "Familie", "score": 0.68,
        }
        detail = """
        <div class="wpem-single-event-body-content">
          <p>Die redaktionelle Musicalbeschreibung darf nicht veröffentlicht werden.</p>
          <p>Samstag 18.07.2026 – Theater am Tanzbrunnen, Köln (Vorstellungen um 13.00 &amp; 16.00 Uhr)</p>
          <p>Sonntag 10.01.2027 – Stadthalle, Essen (Vorstellung um 14.00 Uhr)</p>
        </div><!-- Event description section end -->
        """

        events = ruhrguide._expand_tour_ranges([broad], lambda _url: detail)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["start_date"], "2026-07-18")
        self.assertEqual(events[0]["end_date"], "2026-07-18")
        self.assertEqual(events[0]["time"], "13:00")
        self.assertEqual(events[0]["time_note"], "Weitere Vorstellungen: 16:00 Uhr")
        self.assertEqual(events[0]["venue"], "Theater am Tanzbrunnen")
        self.assertEqual(events[0]["city"], "Köln")
        self.assertNotIn("redaktionelle", events[0]["description"].casefold())

    def test_ruhrguide_drops_unverifiable_long_tour_span(self):
        broad = {
            "title": "Tour ohne Einzeldaten",
            "start_date": "2025-10-25", "end_date": "2027-05-23",
            "time": "", "venue": "Erster Tourort", "city": "Köln",
            "link": "https://www.ruhr-guide.de/veranstaltung/tour/",
            "description": "", "category": "Kultur", "score": 0.65,
        }

        self.assertEqual(
            ruhrguide._expand_tour_ranges([broad], lambda _url: "<html>keine Einzeldaten</html>"),
            [],
        )

    def test_eitorf_cards_are_enriched_from_their_detail_page(self):
        listing = """
        <a class="card" href="/veranstaltungen/submited-events/eitorfer-weinfest/" data-date="2026-07-14">
          <p class="title">Eitorfer Weinfest</p>
          <p class="subtitle">14. Juli • 16:00 Uhr</p>
          <p class="subtitle event-place">Eitorf</p>
        </a>
        """
        detail = """
        <section class="section single-page"><div class="content">
          <div class="intro-text"><p>Weinfest mit drei Tagen Programm.</p></div>
          <div class="text"><p><strong>Unser Programm</strong><br>Freitag und Samstag DJ Gabor.</p></div>
          <div class="event-page-info">
            <p class="subtitle event-place">Parkplatz vor dem Sportplatz Eitorf</p>
            <p class="subtitle event-price">Preis: freier Eintritt</p>
          </div>
        </div></section>
        """

        [event] = regional_html._events_from_eitorf_cards(
            listing, "https://www.eitorf.de", detail_fetcher=lambda _url: detail,
        )

        self.assertIn("DJ Gabor", event["description"])
        self.assertIn("<strong>Unser Programm</strong>", event["description_html"])
        self.assertEqual(event["venue"], "Parkplatz vor dem Sportplatz Eitorf")
        self.assertEqual(event["price"], "freier Eintritt")

    def test_broeltal_cards_are_enriched_before_the_shared_batch_budget(self):
        listing = """
        <a class="list-group-item list-group-item-action" href="/aktuelles/termine/veranstaltung/repair.html">
          <h5>Döörper Repair-Café</h5><span>15.07.2026 - 10:15 bis 12:15 Uhr</span>
        </a>
        """
        detail = """
        <div class="tx-gbevents-pi1"><div class="card"><div class="card-body">
          <h5>Döörper Repair-Café</h5>
          <p>Das Team hilft bei der gemeinsamen Instandsetzung defekter Gegenstände.</p>
          <p>Der Zugang erfolgt vom Mehrgenerationenpark aus.</p>
        </div></div></div>
        """

        [event] = regional_html._events_from_broeltal(
            listing, "https://www.broeltal.de", detail_fetcher=lambda _url: detail,
        )

        self.assertIn("Instandsetzung defekter Gegenstände", event["description"])
        self.assertIn("Mehrgenerationenpark", event["description_html"])
        self.assertEqual(event["description_source"], "scraped")

    def test_brueckenforum_keeps_visitor_copy_and_ignores_stand_fee(self):
        detail = """
        <section id="single-event-header"><div class="module">
          <h4>Achtung: Die Veranstaltung findet auf dem Beueler Rathausplatz statt.<br>
            Die Gewerbegemeinschaft richtet dort einen Floh- und Trödelmarkt aus.</h4>
          <p>Eintritt für Besucher: Kostenlos<br>Zeitraum: Immer von 11-17 Uhr</p>
          <p>Ausstellende können ihren Stand für 10€ pro laufendem Meter buchen.</p>
        </div></section>
        """

        context = requested_venues._brueckenforum_detail_context(detail)

        self.assertIn("Beueler Rathausplatz", context["description"])
        self.assertIn("Floh- und Trödelmarkt", context["description"])
        self.assertEqual(context["price"], "kostenlos")
        self.assertEqual(context["time"], "11:00–17:00")
        self.assertNotIn("10€", context["description"])

    def test_rathausmusik_creates_direct_rich_primary_occurrences(self):
        html = """
        <div class="xr_txt xr_s6" style="position:absolute;top:1383px">
          <span>13. August</span><span>Second Arrangement</span>
        </div>
        <div class="xr_txt xr_s4" style="position:absolute;top:1503px">
          <span>Second Arrangement ist eine zehnköpfige Band aus Köln und Bonn.</span>
          <span>Rock, Jazz und Pop treffen auf einen authentischen Bläsersatz.</span>
        </div>
        <div class="xr_txt xr_s6" style="position:absolute;top:1800px">
          <span>13. August Second Arrangement</span>
        </div>
        <div class="xr_txt xr_s6" style="position:absolute;top:1915px">
          <span>20. August First Lane</span>
        </div>
        <div class="xr_txt xr_s4" style="position:absolute;top:1979px">
          <span>First Lane ist eine Bonner Melodic-Rock-Band mit eigenen Songs und Balladen.</span>
        </div>
        """

        events = requested_venues._events_from_rathausmusik(html)

        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["source_id"], "rathausmusik")
        self.assertEqual(events[0]["time"], "18:00–20:00")
        self.assertIn("zehnköpfige Band", events[0]["description"])
        self.assertIn("authentischen Bläsersatz", events[0]["description_html"])
        self.assertNotIn("First Lane", events[0]["description"])

    def test_rathausmusik_supersedes_the_sparse_beuel_discovery_row(self):
        direct = {
            "title": "Musik auf der Rathaustreppe: Second Arrangement",
            "start_date": "2026-08-13", "end_date": "2026-08-13",
            "start_at": "2026-08-13T18:00+02:00", "end_at": "2026-08-13T20:00+02:00",
            "date": "2026-08-13", "time": "18:00–20:00",
            "venue": "Beueler Rathausplatz", "city": "Bonn",
            "source": "Musik auf der Rathaustreppe", "source_id": "rathausmusik",
            "score": 1.48,
            "description": "Ausführliche Beschreibung der zehnköpfigen Band.",
        }
        discovery = {
            "title": "Musik auf der Rathaustreppe: Second Arrangement (Steely Dan Tribute)",
            "start_date": "2026-08-13", "end_date": "2026-08-13",
            "start_at": "2026-08-13T18:00+02:00", "end_at": "",
            "date": "2026-08-13", "time": "18:00",
            "venue": "Möhneplatz", "city": "Bonn",
            "source": "beuelhats.de", "source_id": "beuel-net",
            "score": 1.4, "description": "Kurzer Termintext.",
        }

        [winner] = report.deduplicate([discovery, direct])

        self.assertEqual(winner["source_id"], "rathausmusik")
        self.assertIn("zehnköpfigen Band", winner["description"])

    def test_linz_parser_uses_current_cards_and_rich_detail_copy(self):
        listing_html = """
<div class="standardteaser">
  <div class="teaserimage">
    <a href="/startseite/tourismus-freizeit/veranstaltungen/events/2026-07-15-00-00/Struenzer-Strand_Linz-am-Rhein/event.html">
      <div class="focuspoint"><img src="very-long-image-path.jpg"></div>
    </a>
  </div>
  <div class="teaserinfo">
    <strong>15.07.2026</strong>
    <div class="h3"><a href="/startseite/tourismus-freizeit/veranstaltungen/events/2026-07-15-00-00/Struenzer-Strand_Linz-am-Rhein/event.html">Strünzer Strand</a></div>
    <div class="teasertext"><p>auf dem Linzer Marktplatz</p></div>
  </div>
</div>
"""
        detail_html = """
<div class="container descriptionbox">
  <h1>Strünzer Strand</h1>
  <span class="centered">
    <p>Ein Teil des historischen Marktplatzes wird in den Strünzer Strand verwandelt.</p>
    <p>Sand, Liegestühle, Sonnenschirme und Palmen wecken Urlaubsfeeling.</p>
  </span>
</div>
<div class="infobox">
  <div class="d-flex align-items-baseline"><i class="icon-pin"></i> Marktplatz Linz</div>
  <div class="d-flex align-items-baseline event-time"><i class="icon-clock"></i></div>
</div>
"""

        events = regional_tourism._events_from_linz(
            listing_html,
            detail_fetcher=lambda _url: detail_html,
        )

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["title"], "Strünzer Strand")
        self.assertIn("historischen Marktplatz", events[0]["description"])
        self.assertEqual(events[0]["venue"], "Marktplatz Linz")
        self.assertEqual(events[0]["time"], "")
        self.assertTrue(events[0]["all_day"])

    def test_unkel_turns_sparse_rss_fields_into_a_readable_description(self):
        item = ET.fromstring("""
<item>
  <title>Unkel Live: Konzert mit The End of Blue</title>
  <link>https://rhein.info/veranstaltungen/unkel-live-konzert-mit-the-end-of-blue/</link>
  <pubDate>Thu, 16 Jul 2026 22:00:00 +0000</pubDate>
  <description>17. Juli 2026 - 0:00 &lt;br/&gt;Weinhaus zur Traube &lt;br/&gt;Lühlingsgasse 5 &lt;br/&gt;Unkel</description>
</item>
""")

        event = regional_feeds._event_from_unkel_item(item)

        self.assertIsNotNone(event)
        self.assertEqual(
            event and event["description"],
            (
                "„Unkel Live: Konzert mit The End of Blue“ findet am 17.07.2026 "
                "im Weinhaus zur Traube, Lühlingsgasse 5, Unkel statt."
            ),
        )
        self.assertEqual(
            event and event["link"],
            "https://rhein.info/veranstaltungen/unkel-live-konzert-mit-the-end-of-blue/",
        )


if __name__ == "__main__":
    unittest.main()
