import unittest
from datetime import datetime

from nrw_events.sources import requested_venues
from tests.helpers import patch_window


LISTING = """
<div class="col module"><div class="event-single">
  <div class="event-info"><h3 class="event-headline market">Markt/Messe</h3>
  <h4>{title}</h4></div>
  <div class="row event-date"><div><span class="date">{date}</span></div>
  <div><a href="{link}"><i></i></a></div></div>
</div></div></div>
"""


class BrueckenforumMarketTests(unittest.TestCase):
    def setUp(self):
        patch_window(self, datetime(2026, 7, 1), datetime(2026, 12, 31))

    def test_detail_page_enriches_maedelsflohmarkt(self):
        link = "https://www.brueckenforum.de/events/maedelsflohmarkt-77/"
        listing = LISTING.format(title="Mädelsflohmarkt", date="06/09/2026", link=link)
        detail = """
        <span class="date">06/09/2026 - EINLASS: 15:00:00</span>
        <h1>Mädelsflohmarkt</h1>
        <div>Von Mädels für Mädels. Trödeln in entspannter Atmosphäre.</div>
        <div>Eintritt 4€<br>Tickets nur an der Tageskasse<br>
        Einlass von 15:00 - 18:45 Uhr</div>
        """

        events = requested_venues._events_from_brueckenforum(
            listing, detail_fetcher=lambda url: detail
        )

        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event["time"], "15:00–18:45")
        self.assertEqual(event["price"], "4 €")
        self.assertEqual(event["venue"], "Brückenforum Bonn")
        self.assertIn("Trödeln", event["description"])
        self.assertEqual(event["link"], link)

    def test_beuel_rathaus_detail_gets_canonical_identity_and_free_price(self):
        link = "https://www.brueckenforum.de/events/ausser-haus-floh-und-troedelmarkt-auf-dem-rathausplatz-2/"
        listing = LISTING.format(
            title="Außer Haus: Floh- und Trödelmarkt auf dem Rathausplatz",
            date="26/07/2026",
            link=link,
        )
        detail = """
        <span class="date">26/07/2026</span>
        <h1>Außer Haus: Floh- und Trödelmarkt auf dem Rathausplatz</h1>
        <div>Der Markt findet auf dem Beueler Rathausplatz statt.</div>
        <div>Eintritt für Besucher: Kostenlos<br>Zeitraum: Immer von 11-17 Uhr</div>
        """

        event = requested_venues._events_from_brueckenforum(
            listing, detail_fetcher=lambda url: detail
        )[0]

        self.assertEqual(event["title"], "Floh- und Trödelmarkt Beueler Rathausplatz")
        self.assertEqual(event["venue"], "Beueler Rathausplatz (Möhneplatz)")
        self.assertEqual(event["time"], "11:00–17:00")
        self.assertEqual(event["price"], "kostenlos")


if __name__ == "__main__":
    unittest.main()
