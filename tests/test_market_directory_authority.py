"""Third-party market directories must never outrank the market organizer.

Directories relist organizer occurrences and keep serving dates the organizer has
already cancelled, so publishing the directory record would put stale or cancelled
markets on the site. They are also mutually redundant: krencky24,
meine-flohmarkt-termine and meine-kunsthandwerker-termine are one operator serving
one database, so a directory record must lose to a peer directory only by score,
never by pretending to be a direct publisher.
"""

import unittest
from datetime import datetime

from nrw_events import core, report
from nrw_events.health import SourceResult
from nrw_events.sources import search
from tests.helpers import patch_window


def _market(source, score, link, *, title="Trödelmarkt beim KAUFLAND",
            price="", time="11:00–17:00"):
    return {
        "title": title,
        "start_date": "2026-08-09", "end_date": "2026-08-09", "date": "2026-08-09",
        "city": "Siegburg", "venue": "Wilhelm-Ostwald-Straße 1",
        "score": score, "source": source, "description": "", "price": price,
        "link": link, "time": time, "start_at": "", "end_at": "",
    }


class MarketDirectoryAuthorityTests(unittest.TestCase):
    def test_directory_records_rank_below_direct_organizers(self):
        for directory in ("marktcom", "krencky24", "meine-flohmarkt-termine",
                          "meine-kunsthandwerker-termine", "flohmap"):
            with self.subTest(directory=directory):
                self.assertLess(
                    report.source_authority(directory),
                    report.source_authority("Grote & Hiller"),
                )

    def test_directory_records_still_rank_above_web_search(self):
        self.assertGreater(
            report.source_authority("marktcom"),
            report.source_authority("Exa Search"),
        )

    def test_organizer_wins_dedup_against_higher_scoring_directory(self):
        """A directory must not publish its own link over the organizer's."""
        events = [
            _market("marktcom", 1.0, "https://www.marktcom.de/veranstaltung/12345"),
            _market("Grote & Hiller", 0.5,
                    "https://www.grote-hiller.de/unsere-maerkte/siegburg-troedelmarkt/",
                    price="Eintritt frei"),
        ]

        deduped = report.deduplicate(events)

        self.assertEqual(len(deduped), 1)
        self.assertEqual(deduped[0]["source"], "Grote & Hiller")
        self.assertEqual(
            deduped[0]["link"],
            "https://www.grote-hiller.de/unsere-maerkte/siegburg-troedelmarkt/",
        )

    def test_directory_metadata_still_enriches_the_organizer_record(self):
        """Losing authority must not mean losing usable detail."""
        events = [
            _market("Grote & Hiller", 0.9, "https://www.grote-hiller.de/markt/", time=""),
            _market("marktcom", 0.4, "https://www.marktcom.de/veranstaltung/12345",
                    price="Eintritt frei", time="11:00–17:00"),
        ]

        deduped = report.deduplicate(events)

        self.assertEqual(len(deduped), 1)
        self.assertEqual(deduped[0]["source"], "Grote & Hiller")
        self.assertEqual(deduped[0]["price"], "Eintritt frei")
        self.assertEqual(deduped[0]["time"], "11:00–17:00")

    def test_organizer_cancellation_suppresses_stale_directory_copy(self):
        """Cancelled organizers stay unpublished but act as tombstones."""
        source_result = SourceResult("Grote & Hiller")
        core.set_source_context(source_result)
        try:
            cancelled = core.make_event(
                "-ABGESAGT- Trödelmarkt beim KAUFLAND",
                datetime(2026, 8, 9, 11),
                datetime(2026, 8, 9, 17),
                "Wilhelm-Ostwald-Straße 1",
                "Siegburg",
                "Der Termin wurde abgesagt.",
                "https://www.grote-hiller.de/markt/",
                "Grote & Hiller",
                "trödelmarkt markt",
            )
        finally:
            core.set_source_context(None)

        self.assertIsNone(cancelled)
        self.assertEqual(len(source_result.cancelled_events), 1)
        directory = _market(
            "marktcom",
            1.0,
            "https://www.marktcom.de/veranstaltung/12345",
        )
        self.assertEqual(
            report.deduplicate(
                [directory],
                cancellations=source_result.cancelled_events,
            ),
            [],
        )

    def test_directory_cancellation_cannot_suppress_direct_organizer(self):
        organizer = _market(
            "Grote & Hiller",
            0.5,
            "https://www.grote-hiller.de/markt/",
        )
        directory_cancellation = {
            **organizer,
            "source": "marktcom",
            "status": "cancelled",
        }

        self.assertEqual(
            report.deduplicate(
                [organizer],
                cancellations=[directory_cancellation],
            ),
            [{**organizer, "link_kind": "detail"}],
        )

    def test_sibling_directories_collapse_to_a_single_record(self):
        """One operator, one database: three frontends must not yield three markets."""
        events = [
            _market("krencky24", 0.9, "https://krencky24.de/a.html"),
            _market("meine-flohmarkt-termine", 0.9, "https://meine-flohmarkt-termine.de/b"),
            _market("meine-kunsthandwerker-termine", 0.9,
                    "https://meine-kunsthandwerker-termine.de/c"),
        ]

        self.assertEqual(len(report.deduplicate(events)), 1)


class ProduceMarketExclusionTests(unittest.TestCase):
    """The market section is for flea/antique/collector formats, not produce."""

    def setUp(self):
        patch_window(self, datetime(2026, 8, 1), datetime(2026, 8, 29))

    @staticmethod
    def _event(title):
        return {
            "title": title, "description": "", "venue": "Marktplatz",
            "city": "Bonn", "link": "https://example.test/event",
            "date": "2026-08-08", "start_date": "2026-08-08",
            "end_date": "2026-08-08", "category": "market", "source": "Test",
        }

    def test_produce_and_evening_markets_are_dropped(self):
        for title in ("Wochenmarkt am Münsterplatz", "Frischemarkt Beuel",
                      "Bauernmarkt Wachtberg", "Biomarkt Bonn",
                      "Abendmarkt auf dem Fischerplatz",
                      "Zwibbelsmaat Zwiebelmarkt Bad Breisig"):
            with self.subTest(title=title):
                self.assertTrue(core.is_junk_event(self._event(title)))

    def test_feierabendmarkt_stays_until_its_whitelist_entry_is_revisited(self):
        """Documents a real conflict rather than silently flipping it.

        ``abendmarkt`` matches ``feierabendmarkt`` as a substring, but
        ``_DESTINATION_MARKET_PATTERN`` names ``feierabendmarkt`` explicitly, so the
        destination override keeps it. Change the whitelist, not this set, to drop it.
        """
        self.assertFalse(core.is_junk_event(self._event("Feierabendmarkt Bad Neuenahr")))

    def test_wanted_second_hand_formats_survive(self):
        for title in ("Trödelmarkt beim KAUFLAND", "Antikmarkt Bonn",
                      "Antik- und Krammarkt Ahrweiler",
                      "Hofflohmarkt Bonn-Friesdorf", "Nachtflohmarkt Fabrik45",
                      "Flohmarkt Kölner Altstadt"):
            with self.subTest(title=title):
                self.assertFalse(core.is_junk_event(self._event(title)))


class SearchQueryFormatTests(unittest.TestCase):
    def test_market_search_query_targets_second_hand_formats_only(self):
        """Do not spend search budget asking for the formats we then discard."""
        queries = " ".join(search.search_queries()).casefold()

        self.assertIn("flohmarkt", queries)
        self.assertIn("trödelmarkt", queries)
        self.assertIn("antikmarkt", queries)
        self.assertIn("hofflohmarkt", queries)
        self.assertNotIn("wochenmarkt", queries)
        self.assertNotIn("bauernmarkt", queries)


if __name__ == "__main__":
    unittest.main()
