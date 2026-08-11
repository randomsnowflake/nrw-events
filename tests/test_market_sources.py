import json
import unittest
from datetime import datetime
from unittest.mock import patch

from nrw_events import common, http, report
from nrw_events.sources import (
    SOURCES,
    coelln_konzept,
    grote_hiller,
    hofflohmaerkte,
    kinderflohmarkt,
)
from nrw_events.validation import canonicalize_event
from tests.helpers import patch_window


class MarketSourceTests(unittest.TestCase):
    def setUp(self):
        patch_window(self, datetime(2026, 7, 19), datetime(2027, 12, 31))

    def test_new_sources_are_registered_with_expected_authority(self):
        for source in (
            "Kinderflohmarkt.com",
            "Grote & Hiller",
            "Hofflohmärkte Köln",
            "Cölln Konzept",
        ):
            self.assertIn(source, SOURCES)
        self.assertEqual(report.source_authority("Kinderflohmarkt.com"), 1)
        self.assertEqual(report.source_authority("Grote & Hiller"), 3)
        self.assertEqual(report.source_authority("Hofflohmärkte Köln"), 3)
        self.assertEqual(report.source_authority("Cölln Konzept"), 3)

    def test_kinderflohmarkt_keeps_structured_description_time_and_location(self):
        item = {
            "@context": "https://schema.org",
            "@type": "Event",
            "name": "Kindersachenbasar Rund ums Kind",
            "startDate": "2026-09-19T12:00:00",
            "endDate": "2026-09-19T15:00:00",
            "description": "Vorsortierter Baby- und Kindersachenbasar mit Kleidung und Spielzeug.",
            "url": "https://kinderflohmarkt.com/de/termin/123/",
            "location": {
                "@type": "Place",
                "name": "Evangelische Kita Christuskirche",
                "address": {"addressLocality": "Plittersdorf"},
            },
        }
        html = f'<script type="application/ld+json">{json.dumps(item)}</script>'

        with patch.object(common, "fetch_url", return_value=html):
            events = kinderflohmarkt.fetch()

        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event["city"], "Bonn-Plittersdorf")
        self.assertEqual(event["time"], "12:00–15:00")
        self.assertEqual(event["category_key"], "market")
        self.assertIn("Kleidung und Spielzeug", event["description"])
        self.assertEqual(event["venue"], "Evangelische Kita Christuskirche")

    def test_grote_hiller_parses_direct_detail_link_and_factual_copy(self):
        html = """
        <div id="markt1" class="row listing">
          <mark>So, 13.09.2026</mark>
          11:00 - <span>15:00 Uhr</span>
          <h3 class="h2">Hennef, Mehrzweckhalle &quot;Meiersheide&quot; Mädelsmarkt</h3>
          <img src="/assets/marker-1.svg"><span>53773 Hennef, Meiersheide 20</span>
          <a href="/unsere-maerkte/hennef-meiersheide-maedelsmarkt/">Infos</a>
        </div>
        """

        events = grote_hiller._events_from_listing(html, "https://www.grote-hiller.de/maedelsflohmaerkte/")

        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event["date"], "2026-09-13")
        self.assertEqual(event["time"], "11:00–15:00")
        self.assertEqual(event["city"], "Hennef")
        self.assertEqual(event["venue"], "")
        self.assertEqual(event["venue_address"], "53773 Hennef, Meiersheide 20")
        self.assertIn("13.09.2026", event["description"])
        self.assertEqual(
            event["link"],
            "https://www.grote-hiller.de/unsere-maerkte/hennef-meiersheide-maedelsmarkt/",
        )

    def test_grote_hiller_extracts_visitor_admission_not_seller_fees(self):
        html = """
        <div id="markt1" class="row listing">
          <mark>So, 06.09.2026</mark>
          11:00 - <span>15:00 Uhr</span>
          <h3 class="h2">Bergisch Gladbach, &quot;Bergischer Löwe&quot; Mädelsmarkt</h3>
          <img src="/assets/marker-1.svg"><span>51465 Bergisch Gladbach, Konrad-Adenauer-Platz 1</span>
          <a href="/unsere-maerkte/bergisch-gladbach-bergischer-loewe-maedelsmarkt/">Infos</a>
        </div>
        """
        detail_html = """
        <h3>Standgeld</h3>
        <p>Gebrauchtware: 15,- € der laufende Meter. Jede weitere Verkäuferin
        muss 4,- € Eintritt zahlen.</p>
        <p>Eintritt: 4,00€ pro Person. Kinder bis 12 Jahre frei.</p>
        """

        events = grote_hiller._enrich_visitor_admission(
            grote_hiller._events_from_listing(
                html,
                "https://www.grote-hiller.de/maedelsflohmaerkte/",
            ),
            detail_fetcher=lambda _url: detail_html,
        )

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["price"], "4,00 €")
        self.assertEqual(events[0]["admission_basis"], "explicit")
        canonical = canonicalize_event(events[0])
        self.assertEqual(canonical.admission["amount"], 4.0)
        self.assertFalse(canonical.admission["isFree"])

    def test_grote_hiller_detail_fetches_stop_when_batch_budget_is_exhausted(self):
        html = """
        <div id="markt1" class="row listing">
          <mark>So, 06.09.2026</mark>
          11:00 - <span>15:00 Uhr</span>
          <h3 class="h2">Bergisch Gladbach Mädelsmarkt</h3>
          <img src="/assets/marker-1.svg"><span>51465 Bergisch Gladbach, Markt 1</span>
          <a href="/unsere-maerkte/bergisch-gladbach-maedelsmarkt/">Infos</a>
        </div>
        """
        events = grote_hiller._events_from_listing(
            html, "https://www.grote-hiller.de/maedelsflohmaerkte/",
        )
        fetched = []

        with patch.dict("os.environ", {"NRW_EVENTS_DETAIL_BATCH_TIMEOUT_SECONDS": "0"}):
            grote_hiller._enrich_visitor_admission(
                events, detail_fetcher=lambda url: fetched.append(url) or "",
            )

        self.assertEqual(fetched, [])
        self.assertEqual(events[0]["price"], "")

    def test_grote_hiller_normalizes_denklingen_to_reichshof(self):
        html = """
        <div id="markt1" class="row listing">
          <mark>So, 26.07.2026</mark>
          11:00 - <span>17:00 Uhr</span>
          <h3 class="h2">Denklingen, Stadtflohmarkt, Rund ums Rathaus und auf dem Burghof</h3>
          <img src="/assets/marker-1.svg"><span>51580 Reichshof-Denklingen, Hauptstr. 12</span>
          <a href="/unsere-maerkte/denklingen-stadtflohmarkt-rund-ums-rathaus-und-auf-dem-burghof/">Infos</a>
        </div>
        """

        events = grote_hiller._events_from_listing(html, "https://www.grote-hiller.de/stadtflohmaerkte/")

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["city"], "Reichshof")
        self.assertEqual(events[0]["venue"], "")
        self.assertEqual(events[0]["venue_address"], "51580 Reichshof-Denklingen, Hauptstr. 12")

    def test_hofflohmaerkte_parses_neighborhood_date_and_hours(self):
        html = """
        <p>Sa. 22. August 2026 · 10 - 16 Uhr · <strong>Königsdorf (Frechen)<br/></strong>
        So. 6. September 2026 · 11 - 16 Uhr · <strong>Agnesviertel<br/></strong></p>
        """

        events = hofflohmaerkte._events_from_page(html)

        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["city"], "Frechen")
        self.assertEqual(events[0]["time"], "10:00–16:00")
        self.assertEqual(events[1]["title"], "Hofflohmarkt Agnesviertel")
        self.assertTrue(all(event["description"] for event in events))

    def test_hofflohmaerkte_recovers_rate_limits_through_web_unlocker(self):
        html = (
            "<h1>Hofflohmärkte Köln</h1>"
            "<p>Sa. 22. August 2026 · 10 - 16 Uhr · "
            "<strong>Königsdorf (Frechen)<br/></strong></p>"
        )

        with patch.object(
            http,
            "fetch_url_with_brightdata_fallback",
            return_value=html,
        ) as fetcher:
            events = hofflohmaerkte.fetch()

        fetcher.assert_called_once_with(
            hofflohmaerkte._URL,
            timeout=20,
            allowed_hosts=("www.hofflohmaerkte.de",),
            required_body_markers=("Hofflohmärkte Köln",),
            fallback_on_timeout=True,
        )
        self.assertEqual([event["title"] for event in events], ["Hofflohmarkt Königsdorf (Frechen)"])

    def test_coelln_konzept_uses_each_table_year_and_detail_quality(self):
        listing = """
        <tr><td class="jahr" colspan="5">Termine 2026</td></tr>
        <tr><td class="datum">Sa/So 25./26.Jul.</td>
        <td class="markt"><a class='linkmarkt' href="markt/altstadt.html">Flohmarkt Kölner Altstadt</a></td></tr>
        <tr><td class="jahr" colspan="5">Termine 2027</td></tr>
        <tr><td class="datum">Sa/So 07./08.Aug.</td>
        <td class="markt"><a class='linkmarkt' href="markt/antik_linz.html">Antikmarkt - Linz am Rhein</a></td></tr>
        """
        detail = """
        <h2>Flohmarkt Kölner Altstadt</h2>
        <p class='textmarkt'>Einer der ältesten Flohmärkte von NRW mit mehr als 150 Ständen.
        Veranstaltung geht von 11 bis 17:00 Uhr.</p>
        <h3>Standort:</h3><p class='textmarkt'>Kölner Altstadt, Rheinpromenade,<br>50668 Köln</p>
        """
        linz_detail = """
        <h2>Antikmarkt - Linz am Rhein</h2>
        <p class='textmarkt'>Antikmarkt in der historischen Altstadt.</p>
        <h3>Standort:</h3><p class='textmarkt'>53545 Linz am Rhein</p>
        """

        def detail_fetcher(url):
            return linz_detail if "antik_linz" in url else detail

        events = coelln_konzept._events_from_listing(listing, detail_fetcher)

        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["date"], "2026-07-25")
        self.assertEqual(events[0]["end_date"], "2026-07-26")
        self.assertEqual(events[0]["city"], "Köln")
        self.assertEqual(events[0]["time"], "11:00–17:00")
        self.assertIn("mehr als 150 Ständen", events[0]["description"])
        self.assertEqual(events[1]["date"], "2027-08-07")
        self.assertEqual(events[1]["end_date"], "2027-08-08")
        self.assertEqual(events[1]["city"], "Linz Am Rhein")


if __name__ == "__main__":
    unittest.main()
