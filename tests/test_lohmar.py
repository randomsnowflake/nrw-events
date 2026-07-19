import unittest
from datetime import datetime

from nrw_events.sources import regional_html
from tests.helpers import patch_window


LISTING_HTML = """
<div class="article articletype-0 even">
  <div class="date">
    <time datetime="2026-07-14">
      <strong>Di. 14.07.2026</strong><br />
      18:00 Uhr
    </time>
  </div>
  <div class="header">
    <h3>
      <a href="erlebnisfaktoren-natur-und-sport-freizeit-und-tourismus/veranstaltungen/veranstaltung-details/va14590-offener-maker-treff/">
        <span itemprop="headline">Offener Maker Treff</span>
      </a>
    </h3>
    Veranstalter: Netz.Werk.Stadt.<br />
    Veranstaltungsort: Netz.Werk.Stadt., Hauptstr. 71
  </div>
  <div class="teaser-text">
    <p>Entdecke die Welt des Laser-Cutting und 3D-Drucks beim Offenen Maker Treff in Lohmar!</p>
    <p>Jeden Dienstag kannst du eigene Ideen umsetzen. <a href="/details">mehr</a></p>
  </div>
</div>
"""


class LohmarParserTests(unittest.TestCase):
    def setUp(self):
        patch_window(self, datetime(2026, 7, 13), datetime(2026, 7, 26))

    def test_listing_teaser_populates_description_without_detail_request(self):
        def unexpected_detail_request(_url):
            raise AssertionError("listing teaser should avoid a detail request")

        [event] = regional_html._events_from_lohmar(
            LISTING_HTML,
            detail_fetcher=unexpected_detail_request,
        )

        self.assertIn("Laser-Cutting und 3D-Druck", event["description"])
        self.assertIn("eigene Ideen umsetzen", event["description"])
        self.assertNotIn("mehr", event["description"])
        self.assertEqual(event["time"], "18:00")
        self.assertEqual(event["venue"], "Netz.Werk.Stadt., Hauptstr. 71")
        self.assertTrue(event["link"].startswith("https://www.lohmar.de/"))

    def test_empty_listing_teaser_uses_detail_body(self):
        listing = LISTING_HTML.replace(
            '<div class="teaser-text">\n'
            '    <p>Entdecke die Welt des Laser-Cutting und 3D-Drucks beim Offenen Maker Treff in Lohmar!</p>\n'
            '    <p>Jeden Dienstag kannst du eigene Ideen umsetzen. <a href="/details">mehr</a></p>\n'
            '  </div>',
            '<div class="teaser-text"><p>Offener Maker Treff</p></div>',
        )
        detail = """
        <div class="news-text-wrap" itemprop="articleBody">
          <p>Alle Bürgerinnen und Bürger ab 14 Jahren sind willkommen.</p>
        </div>
        """

        [event] = regional_html._events_from_lohmar(
            listing,
            detail_fetcher=lambda _url: detail,
        )

        self.assertEqual(
            event["description"],
            "Alle Bürgerinnen und Bürger ab 14 Jahren sind willkommen.",
        )

    def test_missing_detail_copy_still_produces_a_useful_description(self):
        listing = LISTING_HTML.replace(
            '<div class="teaser-text">\n'
            '    <p>Entdecke die Welt des Laser-Cutting und 3D-Drucks beim Offenen Maker Treff in Lohmar!</p>\n'
            '    <p>Jeden Dienstag kannst du eigene Ideen umsetzen. <a href="/details">mehr</a></p>\n'
            '  </div>',
            '<div class="teaser-text"></div>',
        )

        [event] = regional_html._events_from_lohmar(
            listing,
            detail_fetcher=lambda _url: "<html></html>",
        )

        self.assertIn("Offener Maker Treff", event["description"])
        self.assertIn("18:00 Uhr", event["description"])
        self.assertIn("Netz.Werk.Stadt.", event["description"])


if __name__ == "__main__":
    unittest.main()
