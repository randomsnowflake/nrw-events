import unittest
from datetime import datetime

from nrw_events.title_normalization import normalize_event_title, title_looks_truncated


class TitleNormalizationTests(unittest.TestCase):
    def test_repairs_known_markup_source_without_joining_normal_phrases(self):
        self.assertEqual(
            normalize_event_title("Amazônia I ndigenous W orlds", source="Bundeskunsthalle"),
            "Amazônia Indigenous Worlds",
        )
        self.assertEqual(normalize_event_title("A Cultural History", source="Other"), "A Cultural History")

    def test_removes_only_dates_matching_the_structured_start_and_end(self):
        start = datetime(2026, 7, 31)
        end = datetime(2026, 8, 1)
        self.assertEqual(
            normalize_event_title("Pizza Grillen, 31.07.2026", start=start),
            "Pizza Grillen",
        )
        self.assertEqual(
            normalize_event_title("Sommerfest – vom 31.07.26-01-08.26", start=start, end=end),
            "Sommerfest",
        )
        self.assertEqual(
            normalize_event_title("Historischer Rückblick, 30.07.2026", start=start),
            "Historischer Rückblick, 30.07.2026",
        )

    def test_all_caps_uses_german_small_words_and_preserves_short_acronyms(self):
        self.assertEqual(
            normalize_event_title("DIE WELT DER SCHOKOLADE MIT WDR", source="Choco Dealer"),
            "Die Welt der Schokolade mit WDR",
        )
        self.assertEqual(
            normalize_event_title("ERÖFFNUNG – HUMAN AI ART AWARD 2026", source="Kunstmuseum Bonn"),
            "Eröffnung – Human AI Art Award 2026",
        )

    def test_truncation_is_warning_only_and_avoids_short_stylistic_ellipsis(self):
        self.assertTrue(title_looks_truncated("Festival mit Auftritten von Calvin Kleinen u"))
        self.assertTrue(title_looks_truncated(
            "Ein sehr langer Titel aus dem Quellenteaser…",
            source="marktcom",
        ))
        self.assertFalse(title_looks_truncated("Ein sehr langer offizieller Werktitel…"))
        self.assertFalse(title_looks_truncated("Es war einmal…"))
