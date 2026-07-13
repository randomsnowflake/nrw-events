import unittest
import xml.etree.ElementTree as ET
from datetime import datetime

from scripts.nrw_events import common
from scripts.nrw_events.sources import (
    bonn_venues,
    regional_feeds,
    regional_ionas4,
    regional_tourism,
    requested_venues,
    ruhrguide,
)


class RegionalDescriptionQualityTests(unittest.TestCase):
    def setUp(self):
        self.old_today = common.TODAY
        self.old_end_date = common.END_DATE
        common.TODAY = datetime(2026, 7, 13)
        common.END_DATE = datetime(2026, 7, 26)

    def tearDown(self):
        common.TODAY = self.old_today
        common.END_DATE = self.old_end_date

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
        self.assertEqual(events[0]["venue"], "Zehnthof, Zehnthofstr. 2, 53489 Sinzig")
        self.assertEqual(events[0]["time"], "18:00")
        self.assertEqual(events[0]["end_at"], events[0]["start_at"])
        self.assertEqual(
            events[0]["link"],
            "https://tourismus.sinzig.de/kalender/2026-07-18-der-ahrweinbau-im-fokus/9697:0",
        )
        self.assertIn("eventId=9697%3A0", requested[0])

    def test_ionas4_treats_all_day_midnight_end_as_exclusive(self):
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
            detail_fetcher=lambda _url: "",
        )

        self.assertEqual(events, [])

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

    def test_ruhrguide_uses_event_jsonld_description(self):
        events = [{
            "title": "Conni – Das Musical!",
            "start_date": "2026-07-19",
            "time": "15:00",
            "venue": "Theater am Tanzbrunnen, Köln",
            "city": "Köln",
            "link": "https://www.ruhr-guide.de/veranstaltung/conni-das-musical/",
            "description": "",
        }]
        detail_html = """
<script type="application/ld+json">
{
  "@type": "Event",
  "name": "Conni – Das Musical!",
  "description": "Das Cocomico-Musical geht auf große Tournee. Conni feiert Geburtstag und das Publikum wird Teil der Inszenierung.",
  "startDate": "2026-07-19T15:00:00"
}
</script>
"""

        enriched = ruhrguide._enrich_missing_descriptions(
            events,
            detail_fetcher=lambda _url: detail_html,
        )

        self.assertIn("Publikum wird Teil der Inszenierung", enriched[0]["description"])

    def test_ruhrguide_detail_failure_still_returns_useful_text(self):
        events = [{
            "title": "Conni – Das Musical!",
            "start_date": "2026-07-19",
            "time": "15:00",
            "venue": "Theater am Tanzbrunnen, Köln",
            "city": "Köln",
            "link": "https://www.ruhr-guide.de/veranstaltung/conni-das-musical/",
            "description": "",
        }]

        enriched = ruhrguide._enrich_missing_descriptions(
            events,
            detail_fetcher=lambda _url: (_ for _ in ()).throw(TimeoutError("detail timeout")),
        )

        self.assertIn("19.07.2026", enriched[0]["description"])
        self.assertIn("Theater am Tanzbrunnen", enriched[0]["description"])

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
        self.assertEqual(event and event["link"], "https://rhein.info/unkel/")


if __name__ == "__main__":
    unittest.main()
