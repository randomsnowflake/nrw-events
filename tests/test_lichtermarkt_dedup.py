import unittest

from nrw_events.duplicate_identity import events_are_duplicates


class KaiserplatzLichtermarktDedupTests(unittest.TestCase):
    # Reviewed 2026-11-23: https://kirchenpavillon.ekir.de/inhalt/lichtermarkt-auf-dem-xtra-platz/
    # runs one Lichtermarkt (3.–19.12., Thu–Sat). Bonn.de lists 3.–18.12., the
    # approved submission only its first weekend.
    city_calendar = {
        "title": "Lichtermarkt 2026", "city": "Bonn", "date": "2026-12-03",
        "start_date": "2026-12-03", "end_date": "2026-12-18",
        "venue": "Evangelischer Kirchenpavillon Bonn", "venue_address": "Kaiserplatz 1a, 53113 Bonn",
        "source": "Bonn.de Events", "source_id": "bonn-de-events",
        "link": "https://www.bonn.de/veranstaltungskalender/veranstaltungen/hauptkalender/extern/Lichtermarkt-2026.php",
    }
    submission = {
        "title": "Lichtermarkt am Kaiserplatz", "city": "Bonn", "date": "2026-12-03",
        "start_date": "2026-12-03", "end_date": "2026-12-05", "start_at": "2026-12-03T16:00+01:00",
        "venue": "Kaiserplatz / Xtra-Platz vor der Kreuzkirche", "venue_address": "",
        "source": "Veranstaltungen Bonn Einreichung", "source_id": "veranstaltungen-bonn-submission",
        "link": "https://kirchenpavillon.ekir.de/inhalt/lichtermarkt-auf-dem-xtra-platz/",
    }

    def test_reviewed_alias_folds_overlapping_runs_with_different_ends(self):
        self.assertTrue(events_are_duplicates(self.city_calendar, self.submission))
        self.assertTrue(events_are_duplicates(self.submission, self.city_calendar))

    def test_reviewed_alias_keeps_runs_that_do_not_overlap(self):
        next_year = {**self.submission, "date": "2027-12-02", "start_date": "2027-12-02", "end_date": "2027-12-04", "start_at": ""}
        self.assertFalse(events_are_duplicates(self.city_calendar, next_year))


if __name__ == "__main__":
    unittest.main()
