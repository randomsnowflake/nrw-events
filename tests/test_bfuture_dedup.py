import unittest

from nrw_events import report
from nrw_events.identity import event_id


class BfutureDedupTests(unittest.TestCase):
    def test_bfuture_calendar_detail_replaces_year_overview(self):
        overview, detail = self._bfuture_duplicate_rows()
        self.assertEqual(event_id(overview), "bfuture-journalismusfestival-710b2744aa")
        self.assertEqual(event_id(detail), "b-future-festival-8568a9a78d")
        for rows in ([overview, detail], [detail, overview]):
            with self.subTest(first_source=rows[0]["source_id"]):
                [winner] = report.deduplicate(rows)
                self.assertEqual(winner["title"], detail["title"])
                self.assertEqual(winner["link"], detail["link"])
                self.assertEqual(winner["source_id"], "bonn-de-events")
                self.assertEqual(winner["venue_address"], "Münsterplatz 1, 53111 Bonn")
                self.assertEqual(winner["start_date"], "2026-10-02")
                self.assertEqual(winner["end_date"], "2026-10-02")
                self.assertEqual(winner["description"], detail["description"])
                self.assertEqual(winner["admission"], detail["admission"])
                self.assertIn(event_id(overview), winner["previous_event_ids"])
                self.assertNotIn(overview["link"], winner["source_links"])

    def test_bfuture_overview_alias_covers_last_day_but_keeps_daily_slots(self):
        overview, friday = self._bfuture_duplicate_rows()
        saturday = {**friday, "date": "2026-10-03", "start_date": "2026-10-03", "end_date": "2026-10-03"}
        [last_day] = report.deduplicate([overview, saturday])
        self.assertEqual(last_day["start_date"], "2026-10-03")
        self.assertIn(event_id(overview), last_day["previous_event_ids"])
        self.assertEqual(len(report.deduplicate([overview, friday, saturday])), 2)

    def test_reviewed_overview_can_fill_a_missing_detail_description(self):
        overview, detail = self._bfuture_duplicate_rows()
        [winner] = report.deduplicate([overview, {**detail, "description": ""}])
        self.assertEqual(winner["description"], overview["description"])

    def test_reviewed_overview_preserves_separate_visitor_charge_evidence(self):
        overview, detail = self._bfuture_duplicate_rows()
        paid_overview = {**overview, "description": "Der Museumseintritt ist zusätzlich zu zahlen."}
        [winner] = report.deduplicate([paid_overview, detail])
        self.assertEqual(winner["description"], paid_overview["description"])
        self.assertIsNone(winner["admission"]["isFree"])

    def _bfuture_duplicate_rows(self):
        # Reviewed 2026-10-02: the year overview names the whole festival;
        # Bonn's detail calendar publishes the public programme's daily slot.
        # https://www.b-future.org/ confirms the festival on October 1–3 and
        # the public city programme on October 2–3. Programme items are distinct.
        base = {
            "city": "Bonn", "time": "", "start_at": "", "end_at": "",
            "category_key": "festival", "price": "", "status": "scheduled",
        }
        overview = {
            **base, "title": "b’future-Journalismusfestival",
            "date": "2026-10-01", "start_date": "2026-10-01",
            "end_date": "2026-10-03", "venue": "", "score": 1.45,
            "source": "Bonn district festivals", "source_id": "bonn-district-festivals",
            "link": "https://www.bonn.de/pressemitteilungen/dezember/abwechslungsreiches-veranstaltungsjahr-2026-in-bonn.php",
            "link_kind": "overview",
            "description": "b’future-Journalismusfestival, 1. bis 3. Oktober 2026, Bonn Institute gGmbH",
        }
        detail = {
            **base, "title": "b° future festival",
            "date": "2026-10-02", "start_date": "2026-10-02",
            "end_date": "2026-10-02", "venue": "b° future festival", "score": 1.0,
            "venue_address": "Münsterplatz 1, 53111 Bonn",
            "source": "Bonn.de Events", "source_id": "bonn-de-events",
            "link": "https://www.bonn.de/veranstaltungskalender/veranstaltungen/hauptkalender/extern/b-future-festival.php",
            "link_kind": "detail", "description": "Öffentliches Stadtprogramm des b° future festivals.",
            "admission": {"isFree": True, "amount": 0, "currency": "EUR"},
        }
        return overview, detail

    def test_bfuture_alias_preserves_other_occurrences_and_programme(self):
        overview, detail = self._bfuture_duplicate_rows()
        for updates in (
            {"date": "2026-10-04", "start_date": "2026-10-04", "end_date": "2026-10-04"},
            {"date": "2027-10-02", "start_date": "2027-10-02", "end_date": "2027-10-02"},
            {"city": "Köln"},
            {"title": "Triff Insa Backe und Die Maus auf dem b° future festival!"},
            {"source": "Anderer Kalender", "source_id": "other-calendar"},
            {"venue_address": "Münsterplatz 100, 53111 Bonn"},
        ):
            with self.subTest(updates=updates):
                other = {**detail, **updates}
                left = overview
                if "venue_address" in updates:
                    left = {**overview, "venue_address": detail["venue_address"]}
                self.assertEqual(len(report.deduplicate([left, other])), 2)
        timed_overview = {**overview, "start_at": "2026-10-02T10:00+02:00"}
        timed_detail = {**detail, "start_at": "2026-10-02T11:00+02:00"}
        self.assertEqual(len(report.deduplicate([timed_overview, timed_detail])), 2)
