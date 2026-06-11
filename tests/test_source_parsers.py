import unittest
from datetime import datetime
from unittest.mock import patch

from scripts.nrw_events import common
from scripts.nrw_events.sources import SOURCES
from scripts.nrw_events.sources import bonn, bonnjetzt, bundeskunsthalle, regional_tourism


class SourceParserTests(unittest.TestCase):
    def setUp(self):
        self.old_today = common.TODAY
        self.old_end_date = common.END_DATE
        common.TODAY = datetime(2026, 6, 9)
        common.END_DATE = datetime(2026, 6, 21)

    def tearDown(self):
        common.TODAY = self.old_today
        common.END_DATE = self.old_end_date

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
        }

        self.assertLessEqual(expected_sources, set(SOURCES))


if __name__ == "__main__":
    unittest.main()
