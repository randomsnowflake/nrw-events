import unittest
from datetime import datetime
from unittest.mock import patch

from nrw_events import common
from nrw_events.sources import bonn
from tests.helpers import patch_window


class BonnCategoryMappingTests(unittest.TestCase):
    def setUp(self):
        patch_window(self, datetime(2026, 7, 27), datetime(2026, 8, 3))

    @staticmethod
    def _listing(category: str | None, title: str, description: str = "Öffentliche Veranstaltung") -> str:
        kicker = (
            f'<span class="SP-Kicker__text">{category}</span>'
            if category is not None
            else ""
        )
        return f"""
<article class="SP-Teaser">
  <a class="SP-Teaser__inner" href="/veranstaltungskalender/veranstaltungen/hauptkalender/extern/test.php">
    {kicker}
    <div class="SP-Scheduling"><span><span class="SP-Scheduling__date">28.07.2026</span></span></div>
    <h1 class="SP-Teaser__headline">{title}</h1>
    <div class="SP-Teaser__abstract">{description}</div>
  </a>
</article>
"""

    def test_curated_source_category_maps_directly_with_full_confidence(self):
        events = bonn._calendar_listing_events_from_html(
            self._listing("Musik/Konzert", "Rätselhafter Abend"),
            "Bonn.de Events",
        )

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["category_key"], "concert")
        self.assertEqual(events[0]["category_confidence"], 1.0)
        self.assertEqual(
            events[0]["category_reason"],
            "bonn-source-category:Musik/Konzert",
        )

    def test_mapping_covers_every_allowed_bonn_source_category(self):
        expected = {
            "Fest/Festival": "festival",
            "Musik/Konzert": "concert",
            "Kabarett": "stage",
            "Tanz": "stage",
            "Theater": "stage",
            "Ausstellungen": "exhibition",
            "Führungen/Rundgänge/Touren": "outdoor",
            "Tour": "outdoor",
            "Lesung": "talk",
            "Vorträge/Lesungen/Diskussionen": "talk",
            "Märkte/Messen": "market",
            "Film/Medien": "cinema",
            "Tag des offenen Denkmals": "festival",
            "Beethovenfest": "concert",
            "Weihnachtsmarkt": "market",
            "Wissenschaftsnacht-Vorträge": "talk",
        }
        self.assertEqual(bonn._SOURCE_CATEGORY_MAP, expected)
        self.assertEqual(set(expected), bonn._ALLOW)

    def test_unknown_source_category_warns_but_keeps_keyword_fallback_event(self):
        with patch.object(common, "log_source_error") as log_source_error:
            events = bonn._calendar_listing_events_from_html(
                self._listing("Neue Stadtkategorie", "Jazzabend am Rhein"),
                "Bonn.de Events",
            )

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["category_key"], "concert")
        self.assertNotEqual(events[0]["category_confidence"], 1.0)
        log_source_error.assert_called_once()
        source, error = log_source_error.call_args.args
        self.assertEqual(source, "Bonn.de Events category taxonomy")
        self.assertIn("Neue Stadtkategorie", str(error))

    def test_absent_source_category_keeps_keyword_classification_fallback(self):
        events = bonn._calendar_listing_events_from_html(
            self._listing(None, "Jazzabend am Rhein"),
            "Bonn.de Events",
        )

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["category_key"], "concert")
        self.assertLess(events[0]["category_confidence"], 1.0)


if __name__ == "__main__":
    unittest.main()
