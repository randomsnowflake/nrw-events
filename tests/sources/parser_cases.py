import unittest
from datetime import datetime
from unittest.mock import patch

from scripts.nrw_events import common
from scripts.nrw_events.sources import SOURCES
from scripts.nrw_events.sources import (
    bonn, bonn_venues, bonnjetzt, bundeskunsthalle, koeln, regional_feeds,
    regional_ionas4, regional_tourism, requested_venues,
)


class SourceParserTests(unittest.TestCase):
    def setUp(self):
        self.old_today = common.TODAY
        self.old_end_date = common.END_DATE
        common.TODAY = datetime(2026, 6, 9)
        common.END_DATE = datetime(2026, 6, 21)

    def tearDown(self):
        common.TODAY = self.old_today
        common.END_DATE = self.old_end_date
    def test_bonn_events_json_tolerates_appended_server_log_noise(self):
        raw = '[{"title":"Bonner Konzert","category":["Musik/Konzert"],"startDate":"2026-06-12 20:00:00","endDate":"2026-06-12 22:00:00","locationName":"Harmonie","locationAddress":"Frongasse 28, 53121 Bonn","link":"https://www.bonn.de/event.php"}[2026-06-30T09:50:55.650330+02:00] sitekit-logger.ALERT: disk full'

        events = bonn._loads_event_items(raw)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["title"], "Bonner Konzert")

    def test_bonn_events_json_merges_rss_when_json_is_truncated(self):
        json_payload = '[{"title":"Bonner Konzert","category":["Musik/Konzert"],"startDate":"2026-06-12 20:00:00","endDate":"2026-06-12 22:00:00","locationName":"Harmonie","locationAddress":"Frongasse 28, 53121 Bonn","link":"https://www.bonn.de/event.php"}[2026-06-30T09:50:55.650330+02:00] sitekit-logger.ALERT: disk full'
        rss_payload = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <item><title>Eintritt frei: Ausstellung im Stadtmuseum</title><link>https://www.bonn.de/rss-event.php</link><pubDate>Fri, 12 Jun 2026 00:00:00 +0200</pubDate></item>
</channel></rss>"""

        def fake_fetch(url, *args, **kwargs):
            return rss_payload if "sp%3Aout=rss" in url else json_payload

        with patch("scripts.nrw_events.common.fetch_url", side_effect=fake_fetch), \
             patch("scripts.nrw_events.sources.bonn._venue_points", return_value={}):
            events = bonn.fetch_events_json()

        self.assertEqual([event["source"] for event in events], ["Bonn.de Events", "Bonn.de Events"])
        self.assertEqual(events[1]["title"], "Eintritt frei: Ausstellung im Stadtmuseum")
        self.assertEqual(events[1]["price"], "kostenlos")

    def test_bonn_events_json_preserves_free_admission_category(self):
        payload = [
            {
                "title": "SSF Bonn Play Stations Spiel und Spaß im Sportpark Nord",
                "description": "",
                "category": ["Bonn", "Sport", "Familien/Kinder", "Kostenlos"],
                "startDate": "2026-06-12 12:00:00",
                "endDate": "2026-06-12 17:00:00",
                "locationName": "Sportpark Nord",
                "locationAddress": "Kölnstr 250, 53117, Bonn",
                "link": "https://www.bonn.de/play-stations.php",
                "hasStartTime": True,
                "hasEndTime": True,
            },
            {
                "title": "Rallye im Botanischen Garten",
                "description": "",
                "category": ["Bonn", "Ferienaktion", "Kostenlos"],
                "startDate": "2026-06-13 14:00:00",
                "endDate": "2026-06-13 16:00:00",
                "locationName": "Haus der Jugend",
                "locationAddress": "Reuterstr. 100, 53129, Bonn",
                "link": "https://www.bonn.de/rallye.php",
                "hasStartTime": True,
                "hasEndTime": True,
            },
            {
                "title": "Limes-Geburtstag",
                "description": "Es werden kostenlose Führungen angeboten.",
                "category": ["Bonn", "Bonn-Information", "Kultur", "Tourismus"],
                "startDate": "2026-06-14 11:00:00",
                "endDate": "2026-06-14 16:00:00",
                "locationName": "Präsentationsfläche",
                "locationAddress": "Graurheindorfer Straße 10, 53111, Bonn",
                "link": "https://www.bonn.de/limes.php",
                "hasStartTime": True,
                "hasEndTime": True,
            },
        ]

        with patch("scripts.nrw_events.common.fetch_url", return_value=__import__("json").dumps(payload)), \
             patch("scripts.nrw_events.sources.bonn._venue_points", return_value={}):
            events = bonn.fetch_events_json()

        self.assertEqual([event["title"] for event in events], [
            "SSF Bonn Play Stations Spiel und Spaß im Sportpark Nord",
            "Rallye im Botanischen Garten",
            "Limes-Geburtstag",
        ])
        self.assertEqual([event["price"] for event in events], ["kostenlos", "kostenlos", "kostenlos"])
        self.assertEqual(events[0]["category_key"], "sports")
        self.assertEqual(events[1]["category_key"], "kids")
        self.assertEqual(events[2]["category_key"], "outdoor")
        self.assertTrue(all(event["score"] >= 0.4 for event in events))

    def test_bonn_events_use_calendar_listing_before_json(self):
        listing_payload = """
<nav class="SP-Pagination" data-sp-pagination="{&quot;max&quot;:1}"></nav>
<article class="SP-Teaser">
  <a class="SP-Teaser__inner" href="/veranstaltungskalender/veranstaltungen/hauptkalender/extern/kostenloser-Eintritt-Sundowner-Bar.php">
    <span class="SP-Kicker__text">Ausstellung | Fest/Festival</span>
    <div class="SP-Scheduling"><span><span class="SP-Scheduling__date">12.06.2026</span></span></div>
    <h1 class="SP-Teaser__headline">kostenloser Eintritt: Sundowner Bar auf dem Dach der Bundeskunsthalle</h1>
  </a>
</article>
<article class="SP-Teaser">
  <a class="SP-Teaser__inner" href="/veranstaltungskalender/veranstaltungen/hauptkalender/extern/Taste-Of-Woodstock-Musik-im-Park.php">
    <span class="SP-Kicker__text">Musik/Konzert</span>
    <div class="SP-Scheduling"><span><span class="SP-Scheduling__date">12.06.2026</span><span class="SP-Scheduling__time"> 19:00 Uhr</span></span></div>
    <h1 class="SP-Teaser__headline">Taste Of Woodstock - Musik im Park</h1>
  </a>
</article>
<article class="SP-Teaser">
  <a class="SP-Teaser__inner" href="/veranstaltungskalender/veranstaltungen/hauptkalender/extern/Chor-und-Orchester-des-Collegium-musicum-.php">
    <span class="SP-Kicker__text">Musik/Konzert</span>
    <div class="SP-Scheduling"><span><span class="SP-Scheduling__date">12.06.2026</span><span class="SP-Scheduling__time"> 20:30 Uhr</span></span></div>
    <h1 class="SP-Teaser__headline">Chor und Orchester des Collegium musicum</h1>
  </a>
</article>
""" + "".join(
            f"""
<article class="SP-Teaser">
  <a class="SP-Teaser__inner" href="/veranstaltungskalender/veranstaltungen/hauptkalender/extern/bonner-konzert-{index}.php">
    <span class="SP-Kicker__text">Musik/Konzert</span>
    <div class="SP-Scheduling"><span><span class="SP-Scheduling__date">12.06.2026</span><span class="SP-Scheduling__time"> 19:00 Uhr</span></span></div>
    <h1 class="SP-Teaser__headline">Bonner Konzert {index}</h1>
  </a>
</article>
"""
            for index in range(17)
        )

        def fake_fetch(url, *args, **kwargs):
            if "events-json" in url:
                raise AssertionError("Bonn.de Events must prefer the HTML calendar listing over JSON")
            if "veranstaltungskalender.php" in url:
                return listing_payload
            raise AssertionError(f"unexpected URL {url}")

        with patch("scripts.nrw_events.common.fetch_url", side_effect=fake_fetch), \
             patch("scripts.nrw_events.sources.bonn._venue_points", return_value={}):
            events = bonn.fetch_events()

        titles = [event["title"] for event in events]
        self.assertIn("Sundowner Bar auf dem Dach der Bundeskunsthalle", titles)
        self.assertIn("Taste Of Woodstock - Musik im Park", titles)
        self.assertIn("Chor und Orchester des Collegium musicum", titles)
        self.assertTrue(all(event["source"] == "Bonn.de Events" for event in events))
        self.assertEqual(
            next(event for event in events if event["title"] == "Chor und Orchester des Collegium musicum")["time"],
            "20:30",
        )

    def test_bonn_events_falls_back_when_only_free_listing_has_coverage(self):
        free_listing_payload = """
<nav class="SP-Pagination" data-sp-pagination="{&quot;max&quot;:1}"></nav>
""" + "".join(
            f"""
<article class="SP-Teaser">
  <a class="SP-Teaser__inner" href="/veranstaltungskalender/veranstaltungen/hauptkalender/extern/free-event-{index}.php">
    <span class="SP-Kicker__text">Musik/Konzert</span>
    <div class="SP-Scheduling"><span><span class="SP-Scheduling__date">12.06.2026</span></span></div>
    <h1 class="SP-Teaser__headline">Eintritt frei: Free Event {index}</h1>
  </a>
</article>
"""
            for index in range(20)
        )
        json_payload = __import__("json").dumps([{
            "title": "Paid Concert",
            "description": "Abendkonzert mit Eintritt",
            "category": ["Musik/Konzert"],
            "startDate": "2026-06-12 20:00:00",
            "endDate": "2026-06-12 22:00:00",
            "locationName": "Harmonie",
            "locationAddress": "Frongasse 28, 53121 Bonn",
            "link": "https://www.bonn.de/paid-concert.php",
            "hasStartTime": True,
            "hasEndTime": True,
        }])
        empty_listing_payload = '<nav class="SP-Pagination" data-sp-pagination="{&quot;max&quot;:1}"></nav>'
        empty_rss_payload = '<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel></channel></rss>'

        def fake_fetch(url, *args, **kwargs):
            if "events-json" in url:
                return json_payload
            if "sp%3Aout=rss" in url:
                return empty_rss_payload
            if "categories%5B1530%5D" in url:
                return free_listing_payload
            if "veranstaltungskalender.php" in url:
                return empty_listing_payload
            raise AssertionError(f"unexpected URL {url}")

        with patch("scripts.nrw_events.common.fetch_url", side_effect=fake_fetch), \
             patch("scripts.nrw_events.sources.bonn._venue_points", return_value={}):
            events = bonn.fetch_events()

        titles = [event["title"] for event in events]
        self.assertIn("Free Event 0", titles)
        self.assertIn("Paid Concert", titles)

    def test_bonn_calendar_listing_recovers_non_extern_municipal_events(self):
        common.TODAY = datetime(2026, 7, 9)
        common.END_DATE = datetime(2026, 7, 11)
        html = """
<article class="SP-Teaser SP-Teaser--illustrated">
  <a class="SP-Teaser__inner SPbg-accent--before" rel="bookmark" href="/veranstaltungskalender/veranstaltungen/hauptkalender/musikschule/sommerkonzert-des-bonner-jugendsinfonieorchesters-und-der-string-academy.php">
    <span class="SP-Kicker__text">Musik/Konzert</span>
    <div class="SP-Scheduling"><span><span class="SP-Scheduling__date">10.07.2026</span><span class="SP-Scheduling__time"> 18:00 Uhr</span></span></div>
    <h1 class="SP-Teaser__headline">Sommerkonzert</h1>
    <div class="SP-Teaser__abstract">Die String Academy und das Bonner Jugendsinfonieorchester laden zum gemeinsamen Sommerkonzert ein.</div>
  </a>
</article>
"""

        events = bonn._calendar_listing_events_from_html(html, "Bonn.de Events")

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["title"], "Sommerkonzert")
        self.assertEqual(events[0]["date"], "2026-07-10")
        self.assertEqual(events[0]["time"], "18:00")
        self.assertEqual(events[0]["category"], "Musik/Konzert")
        self.assertIn("musikschule/sommerkonzert", events[0]["link"])

    def test_bonn_detail_context_extracts_sparse_page_description_and_venue(self):
        html = """
<div class="SP-ArticleHeader__intro SP-Intro"><p>Sonderausstellung im Arithmeum</p></div>
<div data-sp-table class="SP-Paragraph">
  <p>Die Ausstellung zeigt historische Rechenschieber und Vermessung.</p>
</div>
<script id="EventSerializer-1" type="application/ld+json">
{"@context":"http://schema.org","@type":"Event","name":"Um drei Ecken gedacht","location":[{"@type":"Place","name":"Arithmeum - rechnen einst und heute","address":{"@type":"PostalAddress","addressLocality":"Bonn"}}]}
</script>
"""

        context = bonn._parse_detail_context(html)

        self.assertIn("Sonderausstellung im Arithmeum", context["description"])
        self.assertIn("Die Ausstellung zeigt", context["description"])
        self.assertEqual(context["venue"], "Arithmeum - rechnen einst und heute")
        self.assertEqual(context["city"], "Bonn")

    def test_bonn_detail_context_extracts_sparse_page_description_and_venue(self):
        html = """
<div class="SP-ArticleHeader__intro SP-Intro"><p>Sonderausstellung im Arithmeum</p></div>
<div data-sp-table class="SP-Paragraph">
  <p>Die Ausstellung zeigt historische Rechenschieber und Vermessung.</p>
</div>
<script id="EventSerializer-1" type="application/ld+json">
{"@context":"http://schema.org","@type":"Event","name":"Um drei Ecken gedacht","location":[{"@type":"Place","name":"Arithmeum - rechnen einst und heute","address":{"@type":"PostalAddress","addressLocality":"Bonn"}}]}
</script>
"""

        context = bonn._parse_detail_context(html)

        self.assertIn("Sonderausstellung im Arithmeum", context["description"])
        self.assertIn("Die Ausstellung zeigt", context["description"])
        self.assertEqual(context["venue"], "Arithmeum - rechnen einst und heute")
        self.assertEqual(context["city"], "Bonn")

    def test_kult41_events_manager_blocks_create_events(self):
        common.TODAY = datetime(2026, 7, 1)
        common.END_DATE = datetime(2026, 7, 31)
        html = """
<div class="em-event em-item ">
  <div class="em-item-info">
    <h3 class="em-item-title"><a href="https://kult41.de/events/spiele-tumult-7">Spiele-Tumult</a></h3>
    <div class="em-event-meta em-item-meta">
      <div class="em-item-meta-line em-event-date em-event-meta-datetime">
        <span class="em-icon-calendar em-icon"></span>05.07.26
      </div>
      <div class="em-item-meta-line em-event-time em-event-meta-datetime">
        <span class="em-icon-clock em-icon"></span>15:00 - 20:00
      </div>
      <div class="em-item-meta-line em-event-time em-event-meta-datetime">
        <span class="em-icon-ticket em-icon"></span>Eintritt: €
      </div>
      <div class="em-item-meta-line em-item-taxonomy em-event-categories">
        <a href="https://kult41.de/events/categories/kulturcafe">Kulturcafe</a>
      </div>
    </div>
    <div class="em-item-desc">Der Spiele-Tumult im Kult41 ist zurück.</div>
  </div>
</div>
"""

        events = bonn_venues.events_from_kult41(html)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["title"], "Spiele-Tumult")
        self.assertEqual(events[0]["date"], "2026-07-05")
        self.assertEqual(events[0]["time"], "15:00–20:00")
        self.assertEqual(events[0]["venue"], "KULT41")
        self.assertEqual(events[0]["price"], "")

    def test_repair_cafes_calendar_articles_create_events_with_coords(self):
        common.TODAY = datetime(2026, 7, 1)
        common.END_DATE = datetime(2026, 7, 31)
        html = """
<article class='calendar-event future-event'>
  <h3 class='event-title summary'><button>Repair Café Venusberg &amp; Ippendorf</button></h3>
  <p><span class='mc-start-time dtstart'>
    <time class='value-title' datetime='2026-07-04T10:00:00+02:00'>10:00</time>
  </span><span class='end-time dtend'>
    <time class='value-title' datetime='2026-07-04T14:00:00+02:00'>14:00</time>
  </span></p>
  <div class='longdesc description'><h3>Repair Café im ev. Gemeindezentrum</h3></div>
  <div class="mc-location"><strong class="location-label"><a href="#"><span></span>Repaircafe Venusberg &amp; Ippendorf</a></strong>
    <a href='https://maps.google.com/maps?z=16&amp;daddr=50.699421N,7.093095E'>Karte<span> Repaircafe Venusberg &amp; Ippendorf</span></a>
  </div>
  <p class='mc-details'><a href='https://www.repaircafesbonn.de/mc-events/repair-cafe-venusberg-ippendorf/?mc_id=2171'>Weiterlesen</a></p>
</article>
"""

        events = bonn_venues.events_from_repair_cafes(html)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["title"], "Repair Café Venusberg & Ippendorf")
        self.assertEqual(events[0]["date"], "2026-07-04")
        self.assertEqual(events[0]["time"], "10:00–14:00")
        self.assertEqual(events[0]["venue"], "Repaircafe Venusberg & Ippendorf")
        self.assertGreater(events[0]["distance_km"], 0)

    def test_brotfabrik_today_program_creates_events(self):
        common.TODAY = datetime(2026, 7, 4)
        common.END_DATE = datetime(2026, 7, 4)
        html = """
<section id="programm">
  Kontaktimprovisation / Kommunikation durch Berührung Bib | 14:00 Uhr 04.07.2026
  LaClinicA: Sin cepillo de dientes Theater | 20:00 Uhr 04.07.2026
</section>
"""

        events = bonn_venues.events_from_brotfabrik(html)

        self.assertEqual([event["title"] for event in events], ["LaClinicA: Sin cepillo de dientes"])
        self.assertEqual(events[0]["time"], "20:00")

    def test_brotfabrik_api_items_create_events_and_skip_cancelled(self):
        common.TODAY = datetime(2026, 7, 4)
        common.END_DATE = datetime(2026, 7, 6)
        items = [
            {
                "Titel": "Hofkultur am 04.07. entfällt",
                "Beschreibung": "Am 04.07.2026 muss die Hofkultur leider ausfallen.",
                "Ort": "",
                "Datum": "2026-07-04",
                "Uhrzeit": "18:00:00",
                "Url": "",
                "Gewerk": "Hofkultur",
                "Datumbis": "0000-00-00",
            },
            {
                "Titel": "LaClinicA: Sin cepillo de dientes",
                "Beschreibung": "Infos und Tickets auf der Theater Seite",
                "Ort": "",
                "Datum": "2026-07-04",
                "Uhrzeit": "20:00:00",
                "Url": "https://www.brotfabrik-theater.de/sin-cepillo-de-dientes/",
                "Gewerk": "Theater",
                "Datumbis": "0000-00-00",
            },
        ]

        events = bonn_venues.events_from_brotfabrik_items(items)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["title"], "LaClinicA: Sin cepillo de dientes")
        self.assertEqual(events[0]["date"], "2026-07-04")
        self.assertEqual(events[0]["link"], "https://www.brotfabrik-theater.de/sin-cepillo-de-dientes/")

    def test_botanical_garden_event_links_create_events(self):
        common.TODAY = datetime(2026, 7, 1)
        common.END_DATE = datetime(2026, 7, 31)
        html = """
<a href="https://www.botgart.uni-bonn.de/de/ihr-besuch/veranstaltungen/2026/freundeskreis/teegarten-26">
  Exkursion Sonntag, 05.07.2026 11:00 Uhr Tschanara: Teegartenführung mit Teeverkostung
</a>
"""

        events = bonn_venues.events_from_botgart(html)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["title"], "Tschanara: Teegartenführung mit Teeverkostung")
        self.assertEqual(events[0]["date"], "2026-07-05")
        self.assertEqual(events[0]["source"], "Botanische Gärten Bonn")

    def test_bonner_muenster_music_pages_create_events(self):
        common.TODAY = datetime(2026, 7, 1)
        common.END_DATE = datetime(2026, 7, 31)
        html = """
<li class="list-entry teaser-tile tile-col col-12">
  <div class="teaser type-event">
    <a href="/detail/Orgelvesper-mit-Anthony-Halliday-2026.07.23/?instancedate=1784829600000">
      Bonner Münsterbasilika : Orgelvesper mit Anthony Halliday
    </a>
    <h3 class="headline">Bonner Münsterbasilika : Orgelvesper mit Anthony Halliday</h3>
    <a href="/detail/Orgelvesper-mit-Anthony-Halliday-2026.07.23/?instancedate=1784829600000">
      Do. 23. Juli 2026 20:00 - 21:30 Eintritt frei
    </a>
  </div>
</li>
"""

        events = bonn_venues.events_from_bonner_muenster(html)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["title"], "Orgelvesper mit Anthony Halliday")
        self.assertEqual(events[0]["venue"], "Bonner Münsterbasilika")
        self.assertEqual(events[0]["time"], "20:00–21:30")

    def test_vox_bona_ical_skips_non_regional_tour_dates(self):
        common.TODAY = datetime(2026, 7, 1)
        common.END_DATE = datetime(2026, 12, 31)
        ical = """
BEGIN:VCALENDAR
BEGIN:VEVENT
DTSTART:20260712T160000
DTEND:20260712T180000
SUMMARY:Felix Mendelssohn Bartholdy · ELIAS
LOCATION:Kreuzkirche Bonn, An der Evangelischen Kirche, Bonn
URL:https://vox-bona.de/kalender/elias/
END:VEVENT
BEGIN:VEVENT
DTSTART:20261204T190000
SUMMARY:VOX BONA zu Gast in Stuttgart: Stunde der Kirchenmusik
LOCATION:Stiftskirche Stuttgart, Stiftstraße 12, Stuttgart
URL:https://vox-bona.de/kalender/stuttgart/
END:VEVENT
END:VCALENDAR
"""

        with patch("scripts.nrw_events.common.fetch_url", return_value=ical):
            events = bonn_venues._fetch_vox_bona()

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["title"], "Felix Mendelssohn Bartholdy · ELIAS")

    def test_koeln_open_data_accepts_decimal_comma_coordinates(self):
        payload = {
            "items": [{
                "title": "Kölner Konzert",
                "beginndatum": "2026-06-12",
                "endedatum": "2026-06-12",
                "latitude": "50,94701",
                "longitude": "6,95831",
                "veranstaltungsort": "Kölner Bühne",
                "description": "Konzert",
                "preis": "",
                "uhrzeit": "20:00 Uhr",
                "stadtteil": "Innenstadt",
                "link": "https://example.test/koeln",
            }]
        }
        with patch("scripts.nrw_events.common.fetch_url", return_value=__import__("json").dumps(payload)):
            events = koeln.fetch()

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["title"], "Kölner Konzert")
        self.assertGreater(events[0]["distance_km"], 0)

    def test_koeln_open_data_truncates_verbose_price_text(self):
        payload = {
            "items": [{
                "title": "Kinderführung im Botanischen Garten",
                "beginndatum": "2026-06-12",
                "endedatum": "2026-06-12",
                "latitude": "50,94701",
                "longitude": "6,95831",
                "veranstaltungsort": "Botanischer Garten",
                "description": "Führung",
                "preis": "Die Teilnahme an der Führung kostet sieben Euro für Erwachsene und vier Euro für Kinder, Schüler*innen und Studierende, für Mitglieder des Freundeskreises, Inhaber*innen von KölnPass, Behindertenausweis und mit der Ehrenamtskarte NRW.",
                "uhrzeit": "15:00 Uhr",
                "stadtteil": "Innenstadt",
                "link": "https://example.test/koeln-price",
            }]
        }
        with patch("scripts.nrw_events.common.fetch_url", return_value=__import__("json").dumps(payload)):
            events = koeln.fetch()

        self.assertEqual(len(events), 1)
        self.assertEqual(len(events[0]["price"]), 160)
        self.assertTrue(events[0]["price"].endswith("…"))

    def test_ecmaps_tiles_create_events_from_dated_destination_one_cards(self):
        html = """
        <div class="tile tile--one-quarter tile--single-height">
          <a href="/event/street-food-festival-eitorf" class="tile__link">
            <div class="tile__addon">
              <span class="tile__label-text tile__addon-icon-label">13.06.2026</span>
            </div>
            <p class="typo-m header__line header__head"> Street Food Festival Eitorf </p>
            <span class="icontext__text">Sekundarschule Eitorf, Eitorf</span>
          </a>
        </div>
        """

        events = common.events_from_ecmaps_tiles(
            html, "Naturregion Sieg", "Naturregion Sieg",
            "natur outdoor markt kultur", 0.9, "https://naturregion-sieg.de")

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["title"], "Street Food Festival Eitorf")
        self.assertEqual(events[0]["date"], "2026-06-13")
        self.assertEqual(events[0]["venue"], "Sekundarschule Eitorf, Eitorf")
        self.assertEqual(events[0]["city"], "Eitorf")
        self.assertEqual(events[0]["link"], "https://naturregion-sieg.de/event/street-food-festival-eitorf")

    def test_wp_event_manager_listing_uses_location_city_and_event_time(self):
        html = """
        <div class="event_listing">
          <a href="https://www.ruhr-guide.de/veranstaltung/open-air-am-rhein/" class="wpem-event-action-url">
            <div class="wpem-event-title">
              <h3 class="wpem-heading-text">Open Air am Rhein</h3>
            </div>
            <div class="wpem-event-date-time">
              <span class="wpem-event-date-time-text">
                17.06.2026 @ 19:00 - 17.06.2026 @ 22:30
              </span>
            </div>
            <div class="wpem-event-location">
              <span class="wpem-event-location-text">Düsseldorf, Rheinufer</span>
            </div>
          </a>
        </div>
        """

        events = common.events_from_wp_event_manager_listing(
            html, "Ruhr-Guide", "events ruhrgebiet nrw konzert kultur", 0.65)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["title"], "Open Air am Rhein")
        self.assertEqual(events[0]["date"], "2026-06-17")
        self.assertEqual(events[0]["time"], "19:00-22:30")
        self.assertEqual(events[0]["city"], "Düsseldorf")

    def test_ical_prefers_ionas_event_page_from_attachment_over_homepage_url(self):
        ical = """
BEGIN:VCALENDAR
BEGIN:VEVENT
DTSTART:20260612T190000
DTEND:20260612T235900
SUMMARY:Jazzig in die Ferne swingen
DESCRIPTION:Jazz in 7 Sprachen
LOCATION:Haus Helvetia
URL:https://wachtberg-evangelisch.de/
ATTACH;FMTTYPE=image/jpeg;X-COPYRIGHT="Grafik: Ioanna Giannaki":https://www.wachtberg.de/kalender/veranstaltungen/terminformulare/2026/06-juni/2026-06-12-jazzig-in-die-ferne-swingen/grafik.jpg?cid=toj.828s
CATEGORIES:Jazz
END:VEVENT
END:VCALENDAR
"""

        with patch("scripts.nrw_events.common.fetch_url", return_value=ical):
            events = common.fetch_ical("https://www.wachtberg.de/kalender/event.ics", "Wachtberg", "Wachtberg")

        self.assertEqual(len(events), 1)
        self.assertEqual(
            events[0]["link"],
            "https://www.wachtberg.de/kalender/veranstaltungen/terminformulare/2026/06-juni/2026-06-12-jazzig-in-die-ferne-swingen/",
        )

    def test_bundeskunsthalle_uses_individual_exhibition_detail_links(self):
        html = """
<section>
  <h2>Peter Hujar<br>Eyes Open in the Dark</h2>
  <h3><span>27 February to 23 August 2026</span></h3>
  <a href="/en/hujar" aria-label="This button will take you to the exhibition page with further information." class="btn">More Information</a>
  <a href="https://bundeskunsthalle.ticketfritz.de/Shop/Index/tagesticket/28596">Buy Tickets</a>
</section>
"""

        with patch("scripts.nrw_events.common.fetch_url", return_value=html):
            events = bundeskunsthalle.fetch()

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["title"], "Peter Hujar Eyes Open in the Dark")
        self.assertEqual(events[0]["link"], "https://www.bundeskunsthalle.de/en/hujar")

    def test_search_fallback_requires_a_concrete_date(self):
        event = common.search_result_event(
            "Veranstaltungen Bonn dieses Wochenende – Alle Termine",
            "https://www.anzeigenmarkt-bonn.de/events/wochenende/",
            "Listing page for upcoming events in Bonn without a concrete date or venue",
            "Exa Search",
            0.58,
        )

        self.assertIsNone(event)

    def test_search_fallback_keeps_in_window_dated_events(self):
        event = common.search_result_event(
            "Album Release-Konzert Cumulus – Brotfabrik Bühne Bonn",
            "https://www.brotfabrik-theater.de/album-release-konzert-cumulus/",
            "10. Juni 2026 20.00 Uhr Konzert Theatersaal Bonn",
            "Exa Search",
            0.58,
        )

        self.assertIsNotNone(event)
        self.assertEqual(event and event["date"], "2026-06-10")

    def test_date_for_window_rolls_over_new_year(self):
        from scripts.nrw_events.sources import regional_common as rc

        # Late-December run: a January date must resolve to the coming year,
        # not the year that is ending (which would be dropped as stale).
        common.TODAY = datetime(2026, 12, 28)
        common.END_DATE = datetime(2027, 1, 4)
        self.assertEqual(rc.date_for_window(3, 1), datetime(2027, 1, 3))
        # A mid-year run keeps the current year.
        common.TODAY = datetime(2026, 6, 9)
        common.END_DATE = datetime(2026, 6, 21)
        self.assertEqual(rc.date_for_window(15, 6), datetime(2026, 6, 15))

    def test_regional_range_dates_handles_compact_start_without_year(self):
        from scripts.nrw_events.sources import regional_common as rc

        start, end = rc.range_dates("06.07. – 10.07.2026")

        self.assertEqual(start, datetime(2026, 7, 6))
        self.assertEqual(end, datetime(2026, 7, 10))

    def test_ionas4_uses_public_calendar_when_item_has_no_detail_website(self):
        events = regional_ionas4._events_from_items(
            [
                {
                    "title": "Boule auf der Insel Grafenwerth",
                    "start": "2026-06-12T14:00",
                    "end": "2026-06-12T17:00",
                    "website": "",
                    "location": {"name": "Insel Grafenwerth"},
                    "category": {"name": "Sport"},
                    "tags": [],
                }
            ],
            "Bad Honnef",
            "https://meinbadhonnef.de/kalender/veranstaltungen/",
            0.98,
        )

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["link"], "https://meinbadhonnef.de/kalender/veranstaltungen/")

    def test_unkel_rss_uses_working_municipality_event_listing_url(self):
        import xml.etree.ElementTree as ET

        item = ET.fromstring("""
<item>
  <title>Konzert am Salmenfang</title>
  <link>https://rhein.info/veranstaltungen/dead-detail/</link>
  <description>20.06.2026&lt;br/&gt;Unkel&lt;br/&gt;Konzert am Rhein</description>
</item>
""")

        event = regional_feeds._event_from_unkel_item(item)

        self.assertIsNotNone(event)
        self.assertEqual(event and event["link"], "https://rhein.info/unkel/")

    def test_ahrtal_shapehub_uses_event_detail_url_for_cards(self):
        html = """
<a href="/de/events/stale-detail/eventtermin.html" class="shapehub-card-link">
  <div class="shapehub-date-badge">20.06.2026</div>
  <div class="shapehub-card-title">Ahrtal Konzert</div>
  <span>Ahrweiler</span>
</a>
"""

        events = regional_tourism._events_from_shapehub(
            html,
            "Ahrtal",
            "https://www.ahrtal.com",
            "https://www.ahrtal.com/de/events",
            "Ahrweiler",
            "ahrtal kultur konzert",
            0.86,
        )

        self.assertEqual(len(events), 1)
        self.assertEqual(
            events[0]["link"],
            "https://www.ahrtal.com/de/events/stale-detail/eventtermin.html",
        )

    def test_ahrtal_shapehub_uses_listing_for_same_day_cards(self):
        html = """
<a href="/de/events/detail-removed-on-event-day/eventtermin.html" class="shapehub-card-link">
  <div class="shapehub-date-badge">09.06.2026</div>
  <div class="shapehub-card-title">Ahrtal Führung heute</div>
  <span>Ahrweiler</span>
</a>
"""

        events = regional_tourism._events_from_shapehub(
            html,
            "Ahrtal",
            "https://www.ahrtal.com",
            "https://www.ahrtal.com/de/events",
            "Ahrweiler",
            "ahrtal kultur",
            0.86,
        )

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["link"], "https://www.ahrtal.com/de/events")

    def test_bonn_sport_page_teasers_create_dated_events(self):
        html = """
<li class="SP-TeaserList__item"><article class="SP-Teaser SP-Teaser--textual SP-Teaser--hasFeatureIcons">
  <a class="SP-Teaser__inner SPbg-accent--before" rel="bookmark"
     href="/veranstaltungskalender/veranstaltungen/hauptkalender/extern/Fahrradexkursion-durch-das-Klimaviertel-Bonn.php?p=sig%3Aabc">
    <div class="SP-Teaser__text"><header class="SP-Teaser__header">
      <div class="SP-Teaser__kicker SP-Kicker"><span class="SP-Kicker__text">Aktion/Workshop | Fortbildung</span></div>
      <div class="SP-Scheduling SP-Teaser__scheduling SPc-accent"><span><span class="SP-Scheduling__date">13.06.2026</span><span class="SP-Scheduling__time"> 12:00 Uhr</span></span></div>
      <h1 class="SP-Teaser__headline">Fahrradexkursion durch das Klimaviertel Bonn</h1>
    </header></div>
  </a>
</article></li>
"""

        events = bonn.events_from_sport_teasers(html)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["title"], "Fahrradexkursion durch das Klimaviertel Bonn")
        self.assertEqual(events[0]["date"], "2026-06-13")
        self.assertEqual(events[0]["time"], "12:00")
        self.assertEqual(
            events[0]["link"],
            "https://www.bonn.de/veranstaltungskalender/veranstaltungen/hauptkalender/extern/Fahrradexkursion-durch-das-Klimaviertel-Bonn.php",
        )
        self.assertEqual(events[0]["source"], "Bonn.de Sports")

    def test_bonnjetzt_skips_gruene_jugend_events(self):
        html = """
<article itemtype="https://schema.org/Event">
  <a href="/event/grune-jugend-bonn-aktiventreffen-9" itemprop="url">
    <h2 class="title p-name">Grüne Jugend Bonn Aktiventreffen</h2>
  </a>
  <time datetime="2026-06-12" itemprop="startDate">Fr., 12. Juni, 18:00</time>
  <time itemprop="endDate" content="2026-06-12T20:00:00"></time>
  <span itemprop="name">Kreisgeschäftsstelle der Grünen Bonn</span>
  <div itemprop="address">Bonn</div>
  <span class="v-chip__content">Bonn</span>
  <span class="v-chip__content">Grüne</span>
  <span class="v-chip__content">Politik</span>
</article>
"""

        with patch("scripts.nrw_events.common.fetch_url", return_value=html):
            events = bonnjetzt.fetch()

        self.assertEqual(events, [])

    def test_bonnjetzt_keeps_multiday_events_that_end_in_window(self):
        html = """
<article itemtype="https://schema.org/Event">
  <a href="/event/bonner-sommer-ausstellung" itemprop="url">
    <h2 class="title p-name">Bonner Sommer Ausstellung</h2>
  </a>
  <time datetime="2026-06-01" itemprop="startDate">Mo., 1. Juni</time>
  <time itemprop="endDate" content="2026-06-12T20:00:00"></time>
  <span itemprop="name">Kunsthaus Bonn</span>
  <div itemprop="address">Bonn</div>
  <span class="v-chip__content">Ausstellung</span>
  <span class="v-chip__content">Kultur</span>
</article>
"""

        with patch("scripts.nrw_events.common.fetch_url", return_value=html):
            events = bonnjetzt.fetch()

        self.assertEqual(len(events), 1)
        self.assertFalse(common.is_junk_event(events[0]))

    def test_bonnjetzt_emits_date_only_iso_date_and_keeps_time_label(self):
        html = """
<article itemtype="https://schema.org/Event">
  <a href="/event/garagenflohmarkt" itemprop="url">
    <h2 class="title p-name">Garagenflohmarkt</h2>
  </a>
  <time datetime="2026-06-12 11:00" itemprop="startDate">Freitag, 12. Juni, 11:00-16:00</time>
  <time itemprop="endDate" content="2026-06-12T16:00:00"></time>
  <span itemprop="name">Bonn Hoholz</span>
  <div itemprop="address">Bonn</div>
  <span class="v-chip__content">Flohmarkt</span>
</article>
"""

        with patch("scripts.nrw_events.common.fetch_url", return_value=html):
            events = bonnjetzt.fetch()

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["date"], "2026-06-12")
        self.assertEqual(events[0]["time"], "Freitag, 12. Juni, 11:00-16:00")

    def test_make_event_skips_regular_wochenmarkt_entries(self):
        event = common.make_event(
            "Wochenmarkt in Bonn-Duisdorf",
            datetime(2026, 6, 12, 8),
            datetime(2026, 6, 12, 13),
            "Duisdorfer Rathausplatz",
            "Bonn",
            "Regelmäßiger Wochenmarkt mit regionalen Waren",
            "https://www.bonn.de/veranstaltungen/wochenmarkt-duisdorf.php",
            "Bonn.de",
            "markt wochenmarkt",
        )

        self.assertIsNone(event)

    def test_make_event_keeps_special_markets_that_mention_wochenmarkt(self):
        event = common.make_event(
            "Wochenmarkt-Spezial: Feierabendmarkt und Flohmarkt",
            datetime(2026, 6, 12, 18),
            datetime(2026, 6, 12, 22),
            "Marktplatz",
            "Bonn",
            "Spezialmarkt mit Live-Musik nach dem regulären Wochenmarkt",
            "https://www.bonn.de/veranstaltungen/feierabendmarkt-flohmarkt.php",
            "Bonn.de",
            "markt wochenmarkt flohmarkt",
        )

        self.assertIsNotNone(event)

    def test_make_event_preserves_free_admission_from_raw_description(self):
        cases = [
            (
                "Moovy NRW",
                "Mit dem kostenfreien Tanzfilm-Workshop im Kunsthaus. Sämtliche Angebote sind kostenlos.",
                "kostenlos",
            ),
            (
                "Uta Rings - In den Farbräumen",
                "Die Fensterinstallation wird gezeigt. Editionen von Künstlerinnen und Künstlern. Eintritt frei",
                "kostenlos",
            ),
            (
                "Wanderung mit Anmeldung",
                "Rucksackverpflegung bitte mitbringen. Es wird um eine kurze Anmeldung (kostenlos) gebeten.",
                "",
            ),
        ]

        for title, description, expected_price in cases:
            with self.subTest(title=title):
                event = common.make_event(
                    title,
                    datetime(2026, 6, 12, 18),
                    datetime(2026, 6, 12, 20),
                    "Testort",
                    "Bonn",
                    description,
                    "https://example.test/event",
                    "Test",
                    "kultur workshop",
                )

                self.assertIsNotNone(event)
                self.assertEqual(event["price"], expected_price)

    def test_make_event_skips_routine_and_political_calendar_noise(self):
        cases = [
            (
                "Sitzung des Ausschusses für Umwelt",
                "Rathaus",
                "Tagesordnung und politische Beratung der Stadtverordneten",
                "https://www.bonn.de/ratsinformationssystem/sitzung.php",
                "Politik Sitzung Ausschuss",
            ),
            (
                "Sprechtag Seniorenvertretung",
                "Stadthaus",
                "Regelmäßige Beratung ohne Kulturprogramm",
                "https://www.bonn.de/sprechstunde-seniorenvertretung.php",
                "Beratung",
            ),
            (
                "Seniorengymnastik im Quartier",
                "Begegnungszentrum",
                "Wöchentlich wiederkehrender Kurs",
                "https://www.bonn.de/seniorengymnastik.php",
                "Sport Kurs",
            ),
        ]

        for title, venue, description, link, category in cases:
            with self.subTest(title=title):
                event = common.make_event(
                    title,
                    datetime(2026, 6, 12, 10),
                    datetime(2026, 6, 12, 11),
                    venue,
                    "Bonn",
                    description,
                    link,
                    "Bonn.de",
                    category,
                )
                self.assertIsNone(event)

    def test_make_event_keeps_actual_political_culture_events(self):
        event = common.make_event(
            "Kabarett zur Kommunalwahl und Ratssitzung",
            datetime(2026, 6, 12, 20),
            datetime(2026, 6, 12, 22),
            "Pantheon",
            "Bonn",
            "Satirischer Bühnenabend über Fraktion und Ausschuss mit Musik",
            "https://www.pantheon.de/event/kabarett-kommunalwahl-ratssitzung",
            "Pantheon",
            "Kabarett Theater",
        )

        self.assertIsNotNone(event)

    def test_make_event_sanitizes_scraper_time_artifacts(self):
        cases = [
            (datetime(2026, 6, 12, 19, 0), datetime(2026, 6, 12, 23, 59), "", "19:00"),
            (datetime(2026, 6, 12, 19, 0), datetime(2026, 6, 13, 0, 0), "", "19:00"),
            (datetime(2026, 6, 12, 16, 18), datetime(2026, 6, 12, 16, 30), "", "16:15"),
            (datetime(2026, 6, 12, 19, 31), None, "", "19:30"),
            (datetime(2026, 6, 12, 19, 31), datetime(2026, 6, 12, 22, 30), "", "19:30–22:30"),
            (datetime(2026, 6, 12, 20, 0), datetime(2026, 6, 12, 22, 30), "", "20:00–22:30"),
            (None, None, "16:18 bis 16:30", "16:15"),
            (None, None, "19:31 bis 22:30", "19:30 bis 22:30"),
            (None, None, "19:00 bis 23:59", "19:00"),
        ]

        for start_dt, end_dt, time_text, expected in cases:
            with self.subTest(time_text=time_text, start=start_dt, end=end_dt):
                event = common.make_event(
                    "Testkonzert",
                    start_dt,
                    end_dt,
                    "Pantheon",
                    "Bonn",
                    "Live-Musik",
                    "https://www.pantheon.de/event/testkonzert",
                    "Pantheon",
                    "Konzert",
                    time_text=time_text,
                )
                self.assertIsNotNone(event)
                self.assertEqual(event and event["time"], expected)

    def test_make_event_keeps_timed_events_on_window_end_date(self):
        event = common.make_event(
            "Abendkonzert am letzten Fenstertag",
            datetime(2026, 6, 21, 20),
            datetime(2026, 6, 21, 22),
            "Pantheon",
            "Bonn",
            "Live-Musik",
            "https://www.pantheon.de/programm/#t1",
            "Pantheon",
            "Konzert",
        )

        self.assertIsNotNone(event)
        self.assertEqual(event and event["date"], "2026-06-21")
        self.assertEqual(event and event["time"], "20:00–22:00")

    def test_make_event_suppresses_raw_api_outbound_links(self):
        links = [
            "https://example.org/events.json",
            "https://example.org/api/events/123",
            "https://example.org/?eventId=123&format=json",
        ]

        for link in links:
            with self.subTest(link=link):
                event = common.make_event(
                    "Jazzsommer Bonn",
                    datetime(2026, 6, 12, 20),
                    datetime(2026, 6, 12, 22),
                    "Pantheon",
                    "Bonn",
                    "Live-Musik",
                    link,
                    "Test",
                    "Konzert",
                )
                self.assertIsNotNone(event)
                self.assertEqual(event and event["link"], "")

    def test_make_event_hard_blocks_static_attraction_and_stammtisch_noise(self):
        cases = [
            (
                "Phantasialand Brühl",
                "Brühl",
                "Freizeitpark-Seite aus dem kommunalen Kalender",
                "https://www.bruehl.de/veranstaltungskalender/veranstaltungen/hauptkalender/phantasialand-bruehl.php",
                "kommunal kultur markt ausstellung konzert führung",
            ),
            (
                "Inklusiver Stammtisch",
                "Bonn",
                "Offener Treff ohne Kulturprogramm",
                "https://example.org/events/inklusiver-stammtisch",
                "Soziales Treffpunkt",
            ),
        ]

        for title, venue, description, link, category in cases:
            with self.subTest(title=title):
                event = common.make_event(
                    title,
                    datetime(2026, 6, 12, 18),
                    datetime(2026, 6, 12, 20),
                    venue,
                    "Bonn",
                    description,
                    link,
                    "SiteKit regional",
                    category,
                )
                self.assertIsNone(event)

    def test_make_event_skips_recurring_course_and_club_listing_noise(self):
        cases = [
            (
                "FELDENKRAIS-KURS MIT BETTINA HEYNE",
                "Der Kurs findet immer freitags statt. Der Kurs kostet 48 Euro und beinhaltet 6 Kurseinheiten.",
                "Workshop Kurs",
            ),
            (
                "UMGANG MIT SMARTPHONE, TABLET UND PC",
                "In der Begegnungsstätte CLUB gibt es heute Tipps. Anmeldungen werden unter der Telefonnummer entgegengenommen.",
                "Beratung",
            ),
            (
                "Foto Club Wachtberg - Clubabend",
                "Regelmäßiger Clubabend zum Austausch von Fotoamateuren.",
                "Treffpunkt",
            ),
        ]

        for title, description, category in cases:
            with self.subTest(title=title):
                event = common.make_event(
                    title,
                    datetime(2026, 6, 12, 18),
                    datetime(2026, 6, 12, 20),
                    "Begegnungsstätte",
                    "Bonn",
                    description,
                    "https://example.org/event",
                    "Test",
                    category,
                )
                self.assertIsNone(event)

    def test_make_event_truncates_very_long_descriptions(self):
        event = common.make_event(
            "Sommerkonzert",
            datetime(2026, 6, 12, 20),
            datetime(2026, 6, 12, 22),
            "Pantheon",
            "Bonn",
            "Live-Musik " + ("sehr lange Beschreibung " * 80),
            "https://www.pantheon.de/event/sommerkonzert",
            "Pantheon",
            "Konzert",
        )

        self.assertIsNotNone(event)
        self.assertLessEqual(len(event and event["description"]), common.DESCRIPTION_MAX_CHARS + 1)

    def test_make_event_skips_cancelled_or_postponed_events(self):
        cases = [
            ("-ABGESAGT- Jazzabend im Pantheon", "Heute leider abgesagt"),
            ("Veranstaltung fällt leider aus! Erleben Sie ein Stück lebendiger Geschichte", "Die historische Wassermühle öffnet ihre Tore."),
            ("Konzert im Park", "Die Veranstaltung entfällt krankheitsbedingt."),
            ("Lesung mit Autorin", "Der Termin fällt aus und wird nachgeholt."),
            ("Theaterabend verschoben", "Neuer Termin folgt"),
        ]

        for title, description in cases:
            with self.subTest(title=title):
                event = common.make_event(
                    title,
                    datetime(2026, 6, 12, 20),
                    datetime(2026, 6, 12, 22),
                    "Pantheon",
                    "Bonn",
                    description,
                    "https://www.pantheon.de/event/jazzabend",
                    "Pantheon",
                    "Konzert Kultur",
                )
                self.assertIsNone(event)

    def test_make_event_keeps_live_events_with_non_status_cancel_words(self):
        cases = [
            ("Ausstellung: Abgesagte Pläne der Stadtgeschichte", "Historische Ausstellung."),
            ("Performance: Fällt aus dem Rahmen", "Der Eintritt entfällt für Mitglieder."),
        ]

        for title, description in cases:
            with self.subTest(title=title):
                event = common.make_event(
                    title,
                    datetime(2026, 6, 12, 18),
                    datetime(2026, 6, 12, 20),
                    "Stadtmuseum",
                    "Bonn",
                    description,
                    "https://stadtmuseum.example/events/ausstellung",
                    "Stadtmuseum",
                    "Ausstellung",
                )
                self.assertIsNotNone(event)

    def test_make_event_normalizes_known_venue_casing(self):
        event = common.make_event(
            "Sommerkonzert",
            datetime(2026, 6, 12, 20),
            datetime(2026, 6, 12, 22),
            "stadthalle remagen",
            "remagen",
            "Live-Musik",
            "https://example.org/events/sommerkonzert",
            "Test",
            "Konzert",
        )

        self.assertIsNotNone(event)
        self.assertEqual(event and event["venue"], "Stadthalle Remagen")
        self.assertEqual(event and event["city"], "Remagen")

    def test_bad_muenstereifel_skips_broad_recurring_listing_ranges(self):
        html = """
        <div class="veranst_singleItem clearfix">
          <div class="veranst_singleItem_Headline_Dateline">01.01.2026 - 31.12.2026</div>
          <div class="veranst_singleItem_Headline">Montagswanderung</div>
          <div class="veranst_singleItem_Ort">Rathaus</div>
          <a href="/tourismus/veranstaltungen/montagswanderung">mehr</a>
        </div></div></div>
        <div class="veranst_singleItem clearfix">
          <div class="veranst_singleItem_Headline_Dateline">13.06.2026 - 14.06.2026</div>
          <div class="veranst_singleItem_Headline">Historisches Stadtfest</div>
          <div class="veranst_singleItem_Ort">Altstadt</div>
          <a href="/tourismus/veranstaltungen/stadtfest">mehr</a>
        </div></div></div>
        """

        events = regional_tourism._events_from_bad_muenstereifel(html)

        self.assertEqual([event["title"] for event in events], ["Historisches Stadtfest"])
        self.assertEqual(events[0]["date"], "2026-06-13–2026-06-14")

    def test_kunstmuseum_bonn_calendar_teasers_create_events(self):
        html = """
        <a href="https://www.kunstmuseum-bonn.de/de/besuch/kalender/eroeffnung-human-ai-art-award-2026/">
          <figure>
            <figcaption class="teaser-caption">
              <p class="teaser-date">Sa. 20.06.2026, 19:00 Uhr</p>
              <h4 class="teaser-title">ERÖFFNUNG – HUMAN AI ART AWARD 2026</h4>
              <p class="teaser-meta">Eröffnung</p>
            </figcaption>
          </figure>
        </a>
        """

        events = requested_venues._events_from_kunstmuseum_bonn(html)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["title"], "ERÖFFNUNG – HUMAN AI ART AWARD 2026")
        self.assertEqual(events[0]["date"], "2026-06-20")
        self.assertEqual(events[0]["time"], "19:00")
        self.assertEqual(events[0]["venue"], "Kunstmuseum Bonn")

    def test_sankt_augustin_mec_articles_create_events_and_junk_filter_applies(self):
        html = """
        <article class="mec-event-article mec-clear" itemscope>
          <div class="mec-event-content">
            <h3 class="mec-event-title">
              <a class="mec-color-hover" href="https://www.sankt-augustin.de/veranstaltungen/hunnenlager-2/?occurrence=2026-06-20&time=1781960400">Hunnenlager</a>
            </h3>
            <div class="mec-event-description">Der Hunnen aus dem Siegtal <span>...</span></div>
          </div>
          <span class="mec-start-date-label" itemprop="startDate">20 Juni</span>
          <div class="mec-time-details"><span class="mec-start-time">13:00</span></div>
          <div class="mec-venue-details"> <span>Stadtpark am Jugendzentrum</span>
            <address class="mec-event-address"><span>Bonner Straße 104, 53757 Sankt Augustin</span></address>
          </div>
        </article>
        <article class="mec-event-article mec-clear" itemscope>
          <h3 class="mec-event-title">
            <a href="https://www.sankt-augustin.de/veranstaltungen/wer-rastet-der-rostet-23/?occurrence=2026-06-22">Wer rastet, der rostet</a>
          </h3>
          <div class="mec-event-description">Gleichgewichts- und Sturzprophylaxe-Kur.</div>
          <span class="mec-start-date-label" itemprop="startDate">22 Juni</span>
          <div class="mec-time-details"><span class="mec-start-time">10:00</span> - <span class="mec-end-time">11:00</span></div>
          <div class="mec-venue-details"> <span>Ballettsaal Musikschule</span></div>
        </article>
        """

        events = requested_venues._events_from_sankt_augustin(html)

        self.assertEqual([event["title"] for event in events], ["Hunnenlager"])
        self.assertEqual(events[0]["date"], "2026-06-20")
        self.assertEqual(events[0]["time"], "13:00")
        self.assertEqual(events[0]["city"], "Sankt Augustin")

    def test_pantheon_program_items_create_anchor_links_and_skip_rescheduled_old_date(self):
        html = """
        <a href="/programm/?date=2026-06">Juni 2026</a>
        <li id="t597636">
          <div class="event-date-1">20.</div><div class="event-date-3">Juni</div><div class="event-time">18:00</div>
          <div class="event-location outwards"><a name="t597636">Pantheon</a></div>
          <h2 class="event-title">Dr. Pop Special</h2>
          <h3 class="event-type">Veranstaltung wurde vom 20.6. auf den 28.8. verlegt</h3>
          <div class="event-message event-reschedule"><p>VERSCHOBEN! Neuer Termin am "28.08.2026 20:00"</p></div>
        </li>
        <li id="t637485">
          <div class="event-date-1">21.</div><div class="event-date-3">Juni</div><div class="event-time">17:30</div>
          <div class="event-location outwards"><a name="t637485">Pantheon</a></div>
          <h2 class="event-title">International Voices Choir - Let's Go to the Movies</h2>
        </li>
        """

        events = requested_venues._events_from_pantheon(html)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["title"], "International Voices Choir - Let's Go to the Movies")
        self.assertEqual(events[0]["date"], "2026-06-21")
        self.assertEqual(events[0]["link"], "https://www.pantheon.de/programm/#t637485")

    def test_springmaus_program_cards_create_events(self):
        html = """
        <div class="bg-[#aba199] p-2 mb-2.5">
          <a class="flex text-lg sm:text-xl leading-tight divide-x-2 divide-brand-primary mb-2" href="events/alles-bleibt-anders-1348.html">
            <div class="pr-2">Sa <span class="">20.</span> Jun. 2026</div>
            <div class="pl-2">20:00 Uhr</div>
          </a>
          <h3 class="text-2xl leading-tight font-bold mb-1">
            <a href="events/alles-bleibt-anders-1348.html">Springmaus Improvisationstheater - Alles bleibt anders</a>
          </h3>
          <div class="leading-tight">ab 30,00 €</div>
        </div>
        """

        events = requested_venues._events_from_springmaus(html)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["title"], "Springmaus Improvisationstheater - Alles bleibt anders")
        self.assertEqual(events[0]["date"], "2026-06-20")
        self.assertEqual(events[0]["time"], "20:00")

    def test_brueckenforum_cards_skip_abiball_and_keep_public_events(self):
        html = """
        <div class="col module"><div class="event-single">
          <div class="event-info">
            <h3 class="event-headline live-show">LIVE-SHOW</h3><span class="d-block">–</span>
            <h4>Summer Dance Show · 2026</h4>
          </div>
          <div class="row event-date"><div><span class="date">20/06/2026</span></div>
            <div><a href="https://www.brueckenforum.de/events/summer-dance-show-%c2%b7-2026/"><i></i></a></div>
          </div>
        </div></div></div>
        <div class="col module"><div class="event-single">
          <div class="event-info">
            <h3 class="event-headline abiball">Ball/Abiball</h3><span class="d-block">–</span>
            <h4>Abiball Helmholtz Gymnasium</h4>
          </div>
          <div class="row event-date"><div><span class="date">28/06/2026</span></div>
            <div><a href="https://www.brueckenforum.de/events/abiball-helmholtz-gymnasium/"><i></i></a></div>
          </div>
        </div></div></div>
        """

        events = requested_venues._events_from_brueckenforum(html)

        self.assertEqual([event["title"] for event in events], ["Summer Dance Show · 2026"])
        self.assertEqual(events[0]["date"], "2026-06-20")

    def test_requested_sources_are_registered(self):
        expected_sources = {
            "Naturregion Sieg",
            "Troisdorf",
            "Ruhr-Guide",
            "ionas4 regional",
            "SiteKit regional",
            "Standard regional feeds",
            "Regional HTML calendars",
            "Deskline regional",
            "Regional venues",
            "Bonn.de Sports",
            "Requested venue calendars",
        }

        self.assertLessEqual(expected_sources, set(SOURCES))

def case_class(name, predicate):
    """Build a discoverable case class from this fixture/case library."""
    selected = {
        method_name for method_name in SourceParserTests.__dict__
        if method_name.startswith("test_") and predicate(method_name)
    }
    return type(name, (SourceParserTests,), {
        "__module__": __name__,
        **{method_name: None for method_name in SourceParserTests.__dict__
           if method_name.startswith("test_") and method_name not in selected},
    })


if __name__ == "__main__":
    unittest.main()
