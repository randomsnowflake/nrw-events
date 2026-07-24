import json
import unittest
from datetime import datetime
from unittest.mock import patch

from nrw_events import common, report
from nrw_events.sources import (
    SOURCES,
    coelln_konzept,
    grote_hiller,
    hofflohmaerkte,
    kinderflohmarkt,
)
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
        self.assertIn("Meiersheide 20", event["venue"])
        self.assertIn("13.09.2026", event["description"])
        self.assertEqual(
            event["link"],
            "https://www.grote-hiller.de/unsere-maerkte/hennef-meiersheide-maedelsmarkt/",
        )

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
        self.assertIn("Reichshof-Denklingen", events[0]["venue"])

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
        self.assertEqual(events[0]["date"], "2026-07-25–2026-07-26")
        self.assertEqual(events[0]["city"], "Köln")
        self.assertEqual(events[0]["time"], "11:00–17:00")
        self.assertIn("mehr als 150 Ständen", events[0]["description"])
        self.assertEqual(events[1]["date"], "2027-08-07–2027-08-08")
        self.assertEqual(events[1]["city"], "Linz Am Rhein")


if __name__ == "__main__":
    unittest.main()
