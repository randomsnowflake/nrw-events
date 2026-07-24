import json
import unittest
from datetime import datetime
from unittest.mock import patch

from nrw_events import common, report
from nrw_events.sources import SOURCES, geide, grote_hiller, hoffloh_bonn, okken
from tests.helpers import patch_window


class BonnMarketSourceTests(unittest.TestCase):
    def setUp(self):
        patch_window(self, datetime(2026, 8, 1), datetime(2026, 12, 31))

    def test_first_party_bonn_market_sources_are_registered(self):
        self.assertIs(SOURCES["HofFloh Bonn"], hoffloh_bonn.fetch)
        self.assertIs(SOURCES["Okken Märkte"], okken.fetch)
        self.assertIs(SOURCES["Geide Märkte"], geide.fetch)

    def test_grote_hiller_keeps_regional_postal_city_and_rejects_unknown_town(self):
        def listing(market_id, date, title, venue):
            return f"""
              <div id="markt{market_id}" class="listing">
                <mark>{date}</mark>
                <h3 class="h2">{title}</h3>
                <img src="/marker-1.svg"><span>{venue}</span>
                <a href="/unsere-maerkte/test-{market_id}/">Details</a>
              </div>
            """

        html = listing(
            1,
            "16.08.2026",
            "Siegburg, Trödelmarkt beim KAUFLAND",
            "53721 Siegburg, Wilhelm-Ostwald-Straße 1",
        ) + listing(
            2,
            "16.08.2026",
            "Siegen, Trödelmarkt beim Globus",
            "57072 Siegen, Eiserfelder Str. 170",
        )

        events = grote_hiller._events_from_listing(html, "https://www.grote-hiller.de/troedelmaerkte/")

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["city"], "Siegburg")
        self.assertEqual(events[0]["title"], "Siegburg, Trödelmarkt beim KAUFLAND")

    def test_hoffloh_keeps_scheduled_neighborhoods_and_skips_planned_entries(self):
        payload = {
            "items": [
                {
                    "id": "scheduled-1",
                    "city": "Bonn",
                    "districtName": "Friesdorf",
                    "title": "Bonn-Friesdorf",
                    "featuredDate": "2026-08-15",
                    "startTime": "10:00",
                    "endTime": "16:00",
                    "count": 4,
                },
                {
                    "id": "planned-1",
                    "city": "Bonn",
                    "districtName": "Auerberg",
                    "title": "Bonn-Auerberg",
                    "featuredDate": "",
                    "startTime": "10:00",
                    "endTime": "16:00",
                    "count": 0,
                },
            ]
        }

        events = hoffloh_bonn._events_from_payload(payload)

        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event["title"], "Hofflohmarkt Bonn-Friesdorf")
        self.assertEqual(event["date"], "2026-08-15")
        self.assertEqual(event["time"], "10:00–16:00")
        self.assertEqual(event["venue"], "Stadtteil Friesdorf")
        self.assertEqual(event["city"], "Bonn")
        self.assertEqual(event["category_key"], "market")
        self.assertEqual(event["link"], "https://www.hoffloh.de/events/scheduled-1")

    def test_hoffloh_fetches_every_api_page(self):
        first = {
            "items": [{
                "id": "one", "city": "Bonn", "districtName": "Friesdorf",
                "featuredDate": "2026-08-15", "startTime": "10:00", "endTime": "16:00",
            }],
            "totalPages": 2,
        }
        second = {
            "items": [{
                "id": "two", "city": "Bonn", "districtName": "Küdinghoven",
                "featuredDate": "2026-08-30", "startTime": "10:00", "endTime": "16:00",
            }],
            "totalPages": 2,
        }
        with patch.object(common, "fetch_url", side_effect=[json.dumps(first), json.dumps(second)]) as fetch_url:
            events = hoffloh_bonn.fetch()

        self.assertEqual(len(events), 2)
        self.assertIn("page=2", fetch_url.call_args_list[1].args[0])

    def test_okken_emits_explicit_in_window_occurrence_from_organizer_page(self):
        html = """
        <div class="elementor-heading-title">Termine:</div>
        <span class="elementor-icon-list-text">12. Juli 2026 von 11 - 16 Uhr</span>
        <span class="elementor-icon-list-text">9. August 2026 von 11 - 16 Uhr</span>
        <span class="elementor-icon-list-text">11. Oktober 2026 von 11 - 16 Uhr</span>
        <p>Viele private Händler aus der Region präsentieren hier wahre Schätze.</p>
        <span class="elementor-icon-list-text">
          REWE Center Bonn-Beuel, Am Weidenbach 31, 53229 Bonn
        </span>
        <p>Der Eintritt für Besucher ist kostenlos!</p>
        """
        patch_window(self, datetime(2026, 8, 1), datetime(2026, 8, 15))

        events = okken._events_from_page(html)

        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event["title"], "Der MSD-Flohmarkt in Bonn-Beuel")
        self.assertEqual(event["date"], "2026-08-09")
        self.assertEqual(event["time"], "11:00–16:00")
        self.assertEqual(event["venue"], "REWE Center Bonn-Beuel, Am Weidenbach 31")
        self.assertEqual(event["link"], "https://okkengmbh.de/flohmarkt-bonn/")
        self.assertEqual(event["price"], "kostenlos")

    def test_okken_and_bonn_duplicate_resolves_to_direct_organizer(self):
        base = {
            "title": "Der MSD-Flohmarkt in Bonn-Beuel",
            "start_date": "2026-08-09",
            "end_date": "2026-08-09",
            "date": "2026-08-09",
            "city": "Bonn",
            "venue": "REWE Pützchen",
            "score": 1.0,
            "description": "Flohmarkt",
            "price": "kostenlos",
            "time": "11:00–16:00",
            "start_at": "",
            "end_at": "",
        }
        deduped = report.deduplicate([
            {
                **base,
                "source": "Bonn.de Events",
                "link": (
                    "https://www.bonn.de/veranstaltungskalender/veranstaltungen/"
                    "hauptkalender/extern/Der-MSD-Flohmarkt-in-Bonn-Beuel.php"
                ),
            },
            {
                **base,
                "venue": "REWE Center Bonn-Beuel, Am Weidenbach 31",
                "source": "Okken Märkte",
                "link": "https://okkengmbh.de/flohmarkt-bonn/",
            },
        ])

        self.assertEqual(len(deduped), 1)
        self.assertEqual(deduped[0]["source"], "Okken Märkte")
        self.assertEqual(deduped[0]["link"], "https://okkengmbh.de/flohmarkt-bonn/")

    def test_antikmarkt_title_variants_resolve_to_direct_organizers(self):
        def event(title, city, date, source, link, venue="", score=1.0, end_date=None):
            return {
                "title": title,
                "start_date": date,
                "end_date": end_date or date,
                "date": date if not end_date else f"{date}–{end_date}",
                "city": city,
                "venue": venue,
                "score": score,
                "description": title,
                "price": "",
                "time": "",
                "start_at": "",
                "end_at": "",
                "source": source,
                "link": link,
            }

        deduped = report.deduplicate([
            event(
                "Antik- und Trödelmarkt Bad Godesberg",
                "Bonn-Bad Godesberg",
                "2026-08-02",
                "Bonn district festivals",
                "https://www.bonn.de/presse/veranstaltungsjahr",
                score=1.3,
            ),
            event(
                "Antik- und Trödelmarkt",
                "Bad Godesberg",
                "2026-08-02",
                "Bad Godesberg Stadtmarketing",
                "https://bad-godesberg.info/antikmarkt",
                venue="Bad Godesberger Innenstadt",
            ),
            event(
                "Antik-, Kunst- & Designmarkt Bonn",
                "Bonn",
                "2026-08-16",
                "Bonn district festivals",
                "https://www.bonn.de/presse/veranstaltungsjahr",
                score=1.3,
            ),
            event(
                "Antikmarkt Bonn",
                "Bonn",
                "2026-08-16",
                "Cölln Konzept",
                "https://www.coelln-konzept.de/markt/antikmarkt_bonn.html",
                venue="Friedensplatz",
            ),
            event(
                "Antik- und Trödelmarkt Linz am Rhein",
                "Linz am Rhein",
                "2026-08-08",
                "Linz am Rhein",
                "https://www.linz.de/antikmarkt",
                venue="Innenstadt Linz am Rhein",
            ),
            event(
                "Antikmarkt - Linz am Rhein",
                "Linz am Rhein",
                "2026-08-08",
                "Cölln Konzept",
                "https://www.coelln-konzept.de/markt/antik_linz.html",
                end_date="2026-08-09",
            ),
        ])

        self.assertEqual(len(deduped), 3)
        self.assertEqual(
            {item["link"] for item in deduped},
            {
                "https://bad-godesberg.info/antikmarkt",
                "https://www.coelln-konzept.de/markt/antikmarkt_bonn.html",
                "https://www.coelln-konzept.de/markt/antik_linz.html",
            },
        )

    def test_weekly_lampert_occurrences_stay_separate_and_troedelfabrik_stays_distinct(self):
        base = {
            "city": "Bonn",
            "score": 1.0,
            "description": "Flohmarkt",
            "price": "kostenlos",
            "time": "08:00–14:00",
            "start_at": "",
            "end_at": "",
            "source": "Lampert Märkte",
            "link": "https://lampert-maerkte.de/53121-bonn-an-der-ehem-biskuithalle/",
        }
        events = [
            {
                **base,
                "title": "Flohmarkt Bonn Siemensstraße",
                "venue": "Ehemalige Biskuithalle, Siemensstraße 26",
                "start_date": "2026-08-08", "end_date": "2026-08-08", "date": "2026-08-08",
            },
            {
                **base,
                "title": "Flohmarkt Bonn Siemensstraße",
                "venue": "Ehemalige Biskuithalle, Siemensstraße 26",
                "start_date": "2026-08-15", "end_date": "2026-08-15", "date": "2026-08-15",
            },
            {
                **base,
                "title": "Antik- und Trödelfabrik Bonn",
                "venue": "Siemensstraße 25",
                "start_date": "2026-08-08", "end_date": "2026-08-08", "date": "2026-08-08",
                "source": "Other",
                "link": "https://example.test/troedelfabrik",
            },
        ]

        deduped = report.deduplicate(events)

        self.assertEqual(len(deduped), 3)
        self.assertEqual(
            {event["venue"] for event in deduped},
            {"Ehemalige Biskuithalle, Siemensstraße 26", "Siemensstraße 25"},
        )

    def test_geide_parses_page_year_dates_address_and_direct_links(self):
        html = """
        <a href="bonn-nord.html?file=files/pdf/2026/Termine-2026-Bonn-Nord.pdf">Download</a>
        <div class="event-itm"><div class="event">
          <div class="header"><span>Nov</span><strong>08</strong></div>
          <h3>Bonn-Nord</h3>
          <div class="teaser"><p>OBI- und EDEKA-Parkplatz</p></div>
          <a class="overlay-lnk" href="bonn-nord/bonn-nord-68.html"></a>
        </div></div>
        <p>Der Verkauf der Ware auf dem Markt ist von 11 Uhr bis 18 Uhr möglich.</p>
        <h3>Adresse</h3><p>Bornheimer Str. 166 - 53119 Bonn</p>
        <div class="copyright">© Geide Trödelmärkte 2026</div>
        """

        events = geide._events_from_page(html, "https://www.geide-maerkte.de/bonn-nord.html")

        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event["title"], "Trödelmarkt Bonn-Nord")
        self.assertEqual(event["date"], "2026-11-08")
        self.assertEqual(event["time"], "11:00–18:00")
        self.assertEqual(event["venue"], "OBI/EDEKA, Bornheimer Straße 166")
        self.assertEqual(
            event["link"],
            "https://www.geide-maerkte.de/bonn-nord/bonn-nord-68.html",
        )

    def test_geide_refuses_yearless_or_addressless_pages(self):
        valid = """
        <a href="files/pdf/2026/Termine-2026.pdf">Download</a>
        <div class="event-itm"><div class="header"><span>Nov</span><strong>08</strong></div>
        <h3>Bonn-Nord</h3></div>
        <h3>Adresse</h3><p>Bornheimer Str. 166 - 53119 Bonn</p>
        """
        url = "https://www.geide-maerkte.de/bonn-nord.html"

        self.assertEqual(geide._events_from_page(valid.replace("2026", "Termine"), url), [])
        self.assertEqual(geide._events_from_page(valid.replace("Bornheimer Str. 166", ""), url), [])


if __name__ == "__main__":
    unittest.main()
