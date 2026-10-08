import unittest
from datetime import datetime

from nrw_events.identity import event_id
from nrw_events.identity_reconciliation import _reconcile_published_ids
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

    def test_softens_shouted_runs_inside_mixed_case_titles(self):
        cases = {
            "MACBETH - William Shakespeare - Bearbeitung und Übersetzung von John von Düffel":
                "Macbeth - William Shakespeare - Bearbeitung und Übersetzung von John von Düffel",
            "NightWash Live - COMEDY AT ITS BEST - Live am lustigsten!":
                "NightWash Live - Comedy At Its Best - Live am lustigsten!",
            "Maybebop - 25 Jahre MAYBEBOP - Vier Typen. Vier Mikrofone. Sonst nichts.":
                "Maybebop - 25 Jahre Maybebop - Vier Typen. Vier Mikrofone. Sonst nichts.",
            "HALLOWEEN IN DER HARMONIE – „mit DJ H2O-LEE“": "Halloween in der Harmonie – „mit DJ H2O-LEE“",
            "KING KING – „Autmn-Tour 2026\"": "King King – „Autmn-Tour 2026\"",
            "Hagen Rether: LIEBE – AKTUELLE FASSUNG": "Hagen Rether: Liebe – Aktuelle Fassung",
            "LUDWIG II – Das Musical": "Ludwig II – Das Musical",
        }
        for title, expected in cases.items():
            with self.subTest(title=title):
                self.assertEqual(normalize_event_title(title, source="Fixture"), expected)

    def test_keeps_short_and_known_acronyms_next_to_ordinary_words(self):
        for title in (
            "ADFC Fahrrad Repaircafe", "ZWAR Basisgruppentreffen", "Trödelmarkt beim überdachten REWE Center",
            "MINT-Workshop am Sonntag", "Schloss Augustusburg: FLINTA*-Workshop: Rokoko fürs Smartphone",
            "WDR Rockpalast", "UNESCO-Welterbetag", "Schnuppervortrag zum Elternkurs: ADHS verstehen",
        ):
            with self.subTest(title=title):
                self.assertEqual(normalize_event_title(title, source="Fixture"), title)

    def test_lowercased_titles_only_gain_a_capital_start(self):
        self.assertEqual(
            normalize_event_title("gemeinsam singen - gemeinsam statt einsam", source="ionas4 regional"),
            "Gemeinsam singen - gemeinsam statt einsam",
        )
        for title in ("zeitguised", "dein Mädelsflohmarkt Köln Kalk", "novoflot: Die fünfte Jahreszeit"):
            with self.subTest(title=title):
                self.assertEqual(normalize_event_title(title, source="Fixture"), title)

    def test_unifies_trailing_quoted_festival_subtitles(self):
        expected = "Henge + Kombynat Robotron – WDR Rockpalast-Festival – TV-Aufzeichnung"
        for title in (
            "HENGE + KOMBYNAT ROBOTRON «WDR Rockpalast-Festival - TV-Aufzeichnung»",
            "HENGE + KOMBYNAT ROBOTRON - «WDR Rockpalast-Festival - TV-Aufzeichnung»",
            "HENGE + KOMBYNAT ROBOTRON - >>WDR Rockpalast-Festival - TV-Aufzeichnung",
        ):
            with self.subTest(title=title):
                self.assertEqual(normalize_event_title(title, source="Harmonie Bonn"), expected)
        # German »…« work titles and a quote-only title stay as written.
        self.assertEqual(normalize_event_title("Miku Sophie Kühmel »Hannah«"), "Miku Sophie Kühmel »Hannah«")
        self.assertEqual(normalize_event_title("«Hannah»"), "«Hannah»")

    def test_strips_place_repeating_structured_venue_or_city(self):
        self.assertEqual(
            normalize_event_title("Kindersachenbasar der FeG Bonn, FeG-Bonn", venue="FeG-Bonn", city="Bonn"),
            "Kindersachenbasar der FeG Bonn",
        )
        self.assertEqual(
            normalize_event_title(
                "Herbstbasar der Wiesenzwerge, Pfarrsaal St. Maria Magdalena Endenich",
                venue="Pfarrsaal St. Maria Magdalena Endenich", city="Endenich",
            ),
            "Herbstbasar der Wiesenzwerge",
        )
        self.assertEqual(
            normalize_event_title("Bonn, Mädelsflohmarkt im Telekom Dome", venue="Telekom Dome", city="Bonn-Hardtberg"),
            "Mädelsflohmarkt im Telekom Dome",
        )
        unchanged = (
            ("Kinderflohmarkt, Kita Purzelbaum", "Kita Purzelbaum", "Alfter"),
            ("Bartleby, der Schreiber", "Schauspielhaus Foyer", "Bonn"),
            ("Köln-Mülheim, Trödelmarkt beim Kaufland", "Kaufland", "Köln"),
            ("Bonn, ich liebe dich", "Pantheon", "Bonn"),
            ("Repair Café, Haus Müllestumpe", "Haus Vielinbusch", "Bonn"),
        )
        for title, venue, city in unchanged:
            with self.subTest(title=title):
                self.assertEqual(normalize_event_title(title, venue=venue, city=city), title)

    def test_place_cleanup_keeps_the_published_event_id(self):
        prior = {
            "title": "Bonn, Mädelsflohmarkt im Telekom Dome", "start_date": "2026-10-18",
            "end_date": "2026-10-18", "time": "11:00", "source": "Grote & Hiller",
            "source_id": "grote-hiller", "venue": "Telekom Dome", "city": "Bonn-Hardtberg",
        }
        prior["event_id"] = event_id(prior)
        current = {**prior, "title": normalize_event_title(prior["title"], venue=prior["venue"], city=prior["city"])}
        del current["event_id"]
        self.assertNotEqual(event_id(current), prior["event_id"])
        reconciled = _reconcile_published_ids([current], {"events": [prior]})[0]
        self.assertEqual(event_id(reconciled), prior["event_id"])

    def test_casing_cleanup_does_not_move_event_ids(self):
        event = {"title": "MACBETH - William Shakespeare", "start_date": "2026-10-10", "time": "20:00",
                 "venue": "Kleines Theater", "city": "Bonn"}
        cleaned = {**event, "title": normalize_event_title(event["title"])}
        self.assertEqual(event_id(cleaned), event_id(event))

    def test_truncation_is_warning_only_and_avoids_short_stylistic_ellipsis(self):
        self.assertTrue(title_looks_truncated("Festival mit Auftritten von Calvin Kleinen u"))
        self.assertTrue(title_looks_truncated(
            "Ein sehr langer Titel aus dem Quellenteaser…",
            source="marktcom",
        ))
        self.assertFalse(title_looks_truncated("Ein sehr langer offizieller Werktitel…"))
        self.assertFalse(title_looks_truncated("Es war einmal…"))
