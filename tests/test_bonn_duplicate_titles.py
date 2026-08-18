import unittest

from nrw_events.sources import bonn


class BonnDuplicateTitleTests(unittest.TestCase):
    def test_clean_weinfest_occurrence_suppresses_same_day_dated_variant(self):
        clean = {
            "title": "Weinfest auf dem Bonner Münsterplatz",
            "city": "Bonn",
            "date": "2026-08-21",
            "link": "https://www.bonn.de/clean",
        }
        duplicate = {
            "title": (
                "20.08.2026 - 23.08.2026 Weinfest auf dem Bonner Münsterplatz "
                "- täglich ab Mittagszeit"
            ),
            "city": "Bonn",
            "date": "2026-08-21",
            "link": "https://www.bonn.de/dated",
        }

        self.assertEqual(bonn._drop_redundant_dated_title_variants([duplicate, clean]), [clean])

    def test_date_prefixed_record_is_kept_without_a_clean_twin(self):
        event = {
            "title": "20.08.2026 - 23.08.2026 Einmaliges Fest",
            "city": "Bonn",
            "date": "2026-08-20",
        }
        self.assertEqual(bonn._drop_redundant_dated_title_variants([event]), [event])


if __name__ == "__main__":
    unittest.main()
