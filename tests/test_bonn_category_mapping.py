import json
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from nrw_events import common, report, validation
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

    def test_listing_tags_parse_when_class_and_href_attributes_are_reordered(self):
        html = self._listing("Musik/Konzert", "Reihenfolgefestes Konzert")
        html = html.replace('<article class="SP-Teaser">', '<article data-source="city" class="SP-Teaser">')
        html = html.replace(
            '<a class="SP-Teaser__inner" href="/veranstaltungskalender/veranstaltungen/hauptkalender/extern/test.php">',
            '<a href="/veranstaltungskalender/veranstaltungen/hauptkalender/extern/test.php" rel="bookmark" class="SP-Teaser__inner">',
        )

        events = bonn._calendar_listing_events_from_html(html, "Bonn.de Events")

        self.assertEqual([event["title"] for event in events], ["Reihenfolgefestes Konzert"])

    def test_listing_midnight_placeholder_is_not_published_as_a_real_time(self):
        html = self._listing("Ausstellungen", "Ganztägige Ausstellung").replace(
            '<span class="SP-Scheduling__date">28.07.2026</span>',
            '<span class="SP-Scheduling__date">28.07.2026</span>'
            '<span class="SP-Scheduling__time">00:00 Uhr</span>',
        )

        event = bonn._calendar_listing_events_from_html(html, "Bonn.de Events")[0]

        self.assertEqual(event["time"], "")
        self.assertTrue(event["all_day"])

    def test_conditional_museum_free_tag_does_not_mark_daily_occurrence_free(self):
        municipal_url = (
            "https://www.bonn.de/veranstaltungskalender/veranstaltungen/"
            "hauptkalender/extern/test.php"
        )
        primary_url = "https://www.kunstmuseum-bonn.de/de/ausstellungen/aki-inomata/"
        context = {
            "venue": "Kunstmuseum Bonn",
            "city": "Bonn",
            "description": (
                "Freier Eintritt für alle an jedem ersten Sonntag im Monat. "
                "Kinder und Jugendliche bis einschließlich 18 Jahre haben immer freien Eintritt."
            ),
            "description_html": "",
            "primary_url": primary_url,
        }
        with patch.object(bonn, "_fetch_detail_context", return_value=context):
            [event] = bonn._calendar_listing_events_from_html(
                self._listing(
                    "Ausstellungen, Kostenlos",
                    "Aki Inomata: Mit-werden",
                    "Aki Inomata: Mit-werden",
                ),
                "Bonn.de Events",
            )

        self.assertEqual(event["price"], "")
        self.assertEqual(event["link"], primary_url)
        self.assertEqual(event["source_links"], [municipal_url, primary_url])

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
        neutral_facets = {
            "Ausgehen. Erleben.",
            "Veranstaltungen. Kalender.",
            "Barrierefreie Stadt.",
            "Gleichstellung",
            "Kinder (10 bis 14 Jahre)",
            "Nachhaltigkeits-Hub Region Bonn",
        }
        self.assertTrue(neutral_facets.issubset(bonn._KNOWN_SOURCE_CATEGORIES))
        self.assertFalse(neutral_facets & bonn._ALLOW)

    def test_september_audience_and_hub_facets_do_not_change_admission_or_format(self):
        for facet in ("Kinder (10 bis 14 Jahre)", "Nachhaltigkeits-Hub Region Bonn"):
            with self.subTest(facet=facet), patch.object(common, "log_source_error") as warning:
                self.assertNotIn(facet, bonn._FREE_ACTIVITY_ALLOW)
                events = self._fetch_json([
                    self._json_item([facet, "Musik/Konzert"], "Öffentliches Konzert"),
                    self._json_item([facet], "Unbestimmtes Angebot"),
                    self._json_item([facet, "Musik/Konzert", "Sitzung"], "Gesperrtes Angebot"),
                ])
                self.assertEqual([event["title"] for event in events], ["Öffentliches Konzert"])
                self.assertEqual(events[0]["category_key"], "concert")
                self.assertNotEqual(events[0].get("price"), "kostenlos")
                warning.assert_not_called()
        self.assertEqual(bonn._unknown_source_categories({"Unbekannte neue Facette"}), {"Unbekannte neue Facette"})

    def test_grundschule_and_portal_are_neutral_facets_not_event_formats(self):
        for facet, topic, category in (
            ("Grundschule", "Ausstellungen", "exhibition"),
            ("Portal", "Musik/Konzert", "concert"),
        ):
            with self.subTest(facet=facet), patch.object(common, "log_source_error") as warning:
                self.assertIn(facet, bonn._KNOWN_SOURCE_CATEGORIES)
                self.assertNotIn(facet, bonn._ALLOW | bonn._FREE_ACTIVITY_ALLOW)
                events = self._fetch_json([
                    self._json_item([facet, topic], "Öffentliches Ereignis"),
                    self._json_item([facet], "Unbestimmtes Angebot"),
                ])
                self.assertEqual([event["title"] for event in events], ["Öffentliches Ereignis"])
                self.assertEqual(events[0]["category_key"], category)
                warning.assert_not_called()

    def test_captured_september_facets_preserve_each_occurrence_disposition(self):
        # Exact records selected from https://www.bonn.de/citykey/events-json.php
        # on 2026-09-15; all source fields and co-occurring categories preserved.
        items = json.loads((Path(__file__).parent / "fixtures" /
                            "bonn_september_facets_20260915.json").read_text())
        expected = {
            (337441, "2026-09-26"): None,
            (337441, "2026-10-24"): None,
            (337441, "2026-11-28"): None,
            (337441, "2027-01-30"): None,
            (337441, "2027-02-27"): None,
            (337379, "2026-10-11"): "stage",
            (337411, "2026-10-10"): None,
            (337554, "2026-10-12"): None,
        }
        self.assertEqual({(item["uid"], item["startDate"][:10]) for item in items}, set(expected))
        self.assertEqual(len(items), 8)
        accepted = []
        for item in items:
            key = (item["uid"], item["startDate"][:10])
            with self.subTest(occurrence=key), patch.object(common, "log_source_error") as warning:
                # Place each occurrence in-window so filtering cannot pass merely
                # because the October-to-February dates are outside September.
                start = datetime.fromisoformat(item["startDate"])
                patch_window(self, start - timedelta(days=1), start + timedelta(days=1))
                events = self._fetch_json([item])
                warning.assert_not_called()
                if expected[key] is None:
                    self.assertEqual(events, [])
                else:
                    self.assertEqual(len(events), 1)
                    event = validation.validate_event(events[0])
                    self.assertIsNotNone(event)
                    self.assertEqual(event["title"], item["title"])
                    self.assertEqual(event["start_date"], key[1])
                    self.assertEqual(event["category_key"], expected[key])
                    self.assertEqual(event["price"], "kostenlos")
                    accepted.append(event)
        self.assertEqual(len(accepted), 1)
        self.assertEqual(len(report.deduplicate(accepted)), 1)

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
        self.assertEqual(log_source_error.call_args.kwargs["error_type"], "CategoryTaxonomyWarning")

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
        self.assertEqual(log_source_error.call_args.kwargs["error_type"], "CategoryTaxonomyWarning")

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

    def test_stadtbibliothek_is_a_known_neutral_source_facet(self):
        with patch.object(common, "log_source_error") as log_source_error:
            events = self._fetch_json([
                self._json_item(
                    ["Vorträge/Lesungen/Diskussionen", "Stadtbibliothek"],
                    "Eva Wlodarek",
                )
            ])

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["category_key"], "talk")
        log_source_error.assert_not_called()

    def test_counselling_office_is_a_known_neutral_source_facet(self):
        with patch.object(common, "log_source_error") as log_source_error:
            events = self._fetch_json([
                self._json_item(
                    [
                        "Vorträge/Lesungen/Diskussionen",
                        "Beratungsstelle für Eltern, Kinder und Jugendliche",
                    ],
                    "Eva Wlodarek",
                )
            ])

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["category_key"], "talk")
        log_source_error.assert_not_called()


if __name__ == "__main__":
    unittest.main()
