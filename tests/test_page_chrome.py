import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from nrw_events import page_chrome


class PageChromeTests(unittest.TestCase):
    def test_short_program_copy_survives_calendar_widgets(self):
        for copy in ("Jazz und Soul.", "Rock und Pop", "Musik für Kinder"):
            with self.subTest(copy=copy):
                event = {"title": "Liveabend", "venue": "KULT41", "city": "Bonn",
                         "description": f"{copy}\n\nZum Kalender hinzufügen"}
                page_chrome.strip_page_chrome(event)
                self.assertEqual(event["description"], copy)
                self.assertIn(copy, event["description_html"])

    def test_wordpress_event_block_keeps_prose_and_price(self):
        event = {"title": "Tumult61", "venue": "KULT41", "city": "Bonn", "description": (
            "Wann\n\n08.10.26\n\n20:00 - 23:00\n\nEintritt: 0€\nZum Kalender hinzufügen ICS herunterladen Google Kalender "
            "iCalendar Office 365 Outlook Live\n\nWo\n\nKULT41\n\nHochstadenring 41, Bonn, Bonn, NRW, 53119, NRW\n\n"
            "Veranstaltungstyp\n\nKULT41\n\nTumult61\n\nDer Kneipenabend. Mit Musik, Kicker, Kultur und kühlen Getränken.")}
        self.assertTrue(page_chrome.strip_page_chrome(event))
        self.assertEqual(event["description"], "Eintritt: 0€\n\nDer Kneipenabend. Mit Musik, Kicker, Kultur und kühlen Getränken.")
        self.assertNotIn("Kalender", event["description_html"])

    def test_course_page_widgets_removed_registration_kept(self):
        event = {"title": "Die Tierwelt im Kottenforst", "venue": "Haus der Natur", "city": "Bonn", "description": (
            "Kurs in den Warenkorb legen\n\nKursnummer1820\nDozenten\nDr. Angelika Dauermann\n\n"
            "schriftliche Anmeldung erforderlich. Ort\n\nKursdetails drucken\n\nTermine als iCal-Datei\n\n"
            "Hier klicken, um Kartenansicht zu aktivieren.\n\nWaschbär, Wolf und Wildkatze ziehen in den Kottenforst ein.\n\nKurs weiterempfehlen\n\nFacebook")}
        page_chrome.strip_page_chrome(event)
        self.assertEqual(event["description"], "Dozenten: Dr. Angelika Dauermann\n\nschriftliche Anmeldung erforderlich.\n\n"
                         "Waschbär, Wolf und Wildkatze ziehen in den Kottenforst ein.")

    def test_inline_new_tab_hint_removed_and_plain_copy_untouched(self):
        event = {"title": "DASH", "description": "Anmeldung online (Öffnet in einem neuen Tab) möglich.",
                 "description_html": "<p>Anmeldung online (Öffnet in einem neuen Tab) möglich.</p>"}
        page_chrome.strip_page_chrome(event)
        self.assertEqual(event["description"], "Anmeldung online möglich.")
        self.assertEqual(event["description_html"], "<p>Anmeldung online möglich.</p>")
        plain = {"title": "Konzert", "description": "Datum und Ort stehen fest: ein Abend mit Chormusik.", "description_html": "<p>x</p>"}
        self.assertFalse(page_chrome.strip_page_chrome(plain))


if __name__ == "__main__":
    unittest.main()
