import json
import unittest
from datetime import datetime
from unittest.mock import patch

from nrw_events import common
from nrw_events.sources import bonn
from tests.helpers import patch_window


class BonnCategoryMappingTests(unittest.TestCase):
    def setUp(self):
        patch_window(self, datetime(2026, 7, 27), datetime(2026, 8, 3))
        detail_context = patch.object(bonn, "_fetch_detail_context", return_value={})
        detail_context.start()
        self.addCleanup(detail_context.stop)

    @staticmethod
    def _listing(
        category: str | None,
        title: str,
        description: str = "Öffentliche Veranstaltung",
    ) -> str:
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

    @staticmethod
    def _json_item(category: list[str] | None, title: str, description: str = "") -> dict:
        return {
            "title": title,
            "description": description,
            "category": category,
            "startDate": "2026-07-28 20:00:00",
            "endDate": "2026-07-28 22:00:00",
            "locationName": "Testort",
            "locationAddress": "Teststraße 1, 53111 Bonn",
            "link": f"https://www.bonn.de/{title.replace(' ', '-')}.php",
            "hasStartTime": True,
            "hasEndTime": True,
        }

    def _fetch_json(self, items: list[dict]) -> list[dict]:
        with (
            patch.object(common, "fetch_url", return_value=json.dumps(items)),
            patch.object(bonn, "_venue_points", return_value={}),
            patch.object(bonn, "_fetch_rss_events", return_value=[]),
            patch.object(bonn, "_fetch_free_calendar_events", return_value=[]),
            patch.object(bonn, "_fetch_calendar_listing_events", return_value=[]),
        ):
            return bonn.fetch_events_json()

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

    def test_mapping_covers_only_topic_categories(self):
        expected = {
            "Fest/Festival": "festival",
            "Musik/Konzert": "concert",
            "Kabarett": "stage",
            "Kabarett/Comedy": "stage",
            "Tanz": "stage",
            "Theater": "stage",
            "Theater/Oper": "stage",
            "Ausstellungen": "exhibition",
            "Ausstellung": "exhibition",
            "Tour": "outdoor",
            "Führung/Rundgang": "outdoor",
            "Lesung": "talk",
            "Vorträge/Lesungen/Diskussionen": "talk",
            "Vortrag/Diskussion": "talk",
            "Märkte/Messen": "market",
            "Markt/Messe": "market",
            "Film/Medien": "cinema",
            "Aktion/Workshop": "workshop",
            "Kurs": "workshop",
            "Treffen/Austausch": "activities",
            "Karneval": "festival",
            "Gedenkveranstaltung": "other",
            "Tag des offenen Denkmals": "festival",
            "Beethovenfest": "concert",
            "Weihnachtsmarkt": "market",
            "Wissenschaftsnacht-Vorträge": "talk",
        }
        self.assertEqual(bonn._SOURCE_CATEGORY_MAP, expected)
        self.assertIn("Führungen/Rundgänge/Touren", bonn._ALLOW)
        self.assertNotIn("Führungen/Rundgänge/Touren", bonn._SOURCE_CATEGORY_MAP)
        self.assertTrue({
            "Ausgehen. Erleben.", "Veranstaltungen. Kalender.", "Barrierefreie Stadt."
        }.issubset(bonn._KNOWN_SOURCE_CATEGORIES))
        self.assertFalse({
            "Ausgehen. Erleben.", "Veranstaltungen. Kalender.", "Barrierefreie Stadt."
        } & bonn._ALLOW)

    def test_current_bonn_topic_categories_are_accepted_without_taxonomy_warning(self):
        categories = {
            "Führung/Rundgang": "outdoor",
            "Karneval": "festival",
            "Kurs": "workshop",
            "Treffen/Austausch": "activities",
            "Vortrag/Diskussion": "talk",
            "Kabarett/Comedy": "stage",
            "Theater/Oper": "stage",
            "Markt/Messe": "market",
            "Gedenkveranstaltung": "other",
        }
        html = "".join(
            self._listing(category, f"Test {index}")
            for index, category in enumerate(categories, start=1)
        )
        with patch.object(common, "log_source_error") as log_source_error:
            events = bonn._calendar_listing_events_from_html(html, "Bonn.de Events")

        self.assertEqual(len(events), len(categories))
        self.assertEqual(
            {event["category_key"] for event in events}, set(categories.values())
        )
        log_source_error.assert_not_called()

    def test_listing_rejects_blocked_unknown_and_absent_categories(self):
        html = "".join(
            (
                self._listing("Sitzung", "Jazzsitzung"),
                # Both spellings of the training category stay blocked, and
                # neither counts as unknown taxonomy.
                self._listing("Fortbildungen", "Jazz für Fachpublikum"),
                self._listing("Fortbildung", "Jazz für Fachpublikum, neu benannt"),
                self._listing("Neue Stadtkategorie", "Jazzabend unbekannt"),
                self._listing(None, "Jazzabend ohne Kategorie"),
                self._listing("Musik/Konzert", "Erlaubtes Konzert"),
            )
        )
        with patch.object(common, "log_source_error") as log_source_error:
            events = bonn._calendar_listing_events_from_html(html, "Bonn.de Events")

        self.assertEqual([event["title"] for event in events], ["Erlaubtes Konzert"])
        log_source_error.assert_called_once()
        source, error = log_source_error.call_args.args
        self.assertEqual(source, "Bonn.de Events category taxonomy")
        self.assertIn("Neue Stadtkategorie", str(error))

    def test_listing_guide_format_remains_classifier_driven(self):
        events = bonn._calendar_listing_events_from_html(
            self._listing(
                "Führungen/Rundgänge/Touren",
                "Jazzkonzert im Museum",
                "Live-Musik mit einem Jazzquartett",
            ),
            "Bonn.de Events",
        )

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["category_key"], "concert")
        self.assertFalse(events[0]["category_reason"].startswith("bonn-source-category:"))

    def test_json_rejects_blocked_unknown_and_absent_categories(self):
        items = [
            self._json_item(["Sitzung"], "Jazzsitzung"),
            self._json_item(["Neue Stadtkategorie"], "Jazzabend unbekannt"),
            self._json_item(None, "Jazzabend ohne Kategorie"),
            self._json_item(["Musik/Konzert"], "Erlaubtes Konzert"),
        ]
        with patch.object(common, "log_source_error") as log_source_error:
            events = self._fetch_json(items)

        self.assertEqual([event["title"] for event in events], ["Erlaubtes Konzert"])
        self.assertEqual(events[0]["category_key"], "concert")
        self.assertEqual(events[0]["category_confidence"], 1.0)
        log_source_error.assert_called_once()
        source, error = log_source_error.call_args.args
        self.assertEqual(source, "Bonn.de Events category taxonomy")
        self.assertIn("Neue Stadtkategorie", str(error))

    def test_json_guide_format_remains_classifier_driven(self):
        events = self._fetch_json(
            [
                self._json_item(
                    ["Führungen/Rundgänge/Touren"],
                    "Jazzkonzert im Museum",
                    "Live-Musik mit einem Jazzquartett",
                )
            ]
        )

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["category_key"], "concert")
        self.assertFalse(events[0]["category_reason"].startswith("bonn-source-category:"))


if __name__ == "__main__":
    unittest.main()
