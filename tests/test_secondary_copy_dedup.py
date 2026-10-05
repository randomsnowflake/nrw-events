import unittest

from nrw_events import report

_BONN = {"source": "Bonn.de Events", "source_id": "bonn-de-events", "status": "scheduled", "score": 1.0}


def _day(day, **fields):
    return {"date": day, "start_date": day, "end_date": day, **fields}


class SecondaryCopyTests(unittest.TestCase):
    # Reviewed 2026-10-05 against bad-godesberg.info, uni-bonn.de and troisdorf.de.
    organiser = {
        "title": "GoVinum – Bad Godesberger Weinfest", "city": "Bonn-Bad Godesberg",
        "date": "2026-10-09", "start_date": "2026-10-09", "end_date": "2026-10-11",
        "venue": "Theaterplatz", "category_key": "festival", "status": "scheduled", "score": 1.0,
        "source": "Bad Godesberg Stadtmarketing", "source_id": "bad-godesberg-stadtmarketing",
        "link": "https://bad-godesberg.info/veranstaltungen_st/govinum-das-weinfest-in-bad-godesberg",
    }
    university = {
        **_day("2026-10-06"), "title": "25. Bonner Dialog für Cybersicherheit (BDCS)",
        "city": "Bonn-Poppelsdorf", "start_at": "2026-10-06T17:30+02:00",
        "venue": "Hörsaalzentrum Poppelsdorf", "category_key": "talk", "status": "scheduled", "score": 1.0,
        "venue_address": "Hörsaal 2, Friedrich-Hirzebruch-Allee 5, 53115 Bonn",
        "source": "Universität Bonn", "source_id": "uni-bonn",
        "link": "https://www.uni-bonn.de/de/veranstaltungen/25-bonner-dialog-fuer-cybersicherheit-bdcs",
    }

    def _govinum_copy(self, day, **fields):
        return {
            **_BONN, **_day(day), "title": "GoVinum - Das Weinfest in Bad Godesberg",
            "city": "Bonn-Bad Godesberg", "start_at": f"{day}T12:00+02:00",
            "venue": "Bad Godesberger Innenstadt", "category_key": "festival",
            "link": f"https://www.bonn.de/veranstaltungskalender/govinum-{day}.php", **fields,
        }

    def _cyber_copy(self, **fields):
        return {
            **_BONN, **_day("2026-10-06"), "title": "25. Bonner Dialog für Cybersicherheit",
            "city": "Bonn-Poppelsdorf", "start_at": "2026-10-06T17:30+02:00",
            "end_at": "2026-10-06T20:30+02:00", "venue": "Universität Bonn Campus Poppelsdorf",
            "venue_address": "Hörsaal 2", "category_key": "talk",
            "link": "https://www.bonn.de/veranstaltungskalender/25.Bonner-Dialog-fuer-Cybersicherheit.php",
            **fields,
        }

    def test_organiser_run_absorbs_daily_city_copies(self):
        daily = [self._govinum_copy(day) for day in ("2026-10-09", "2026-10-10", "2026-10-11")]
        for rows in ([self.organiser, *daily], [*daily, self.organiser]):
            with self.subTest(first=rows[0]["source_id"]):
                [winner] = report.deduplicate(rows)
                self.assertEqual(winner["source_id"], "bad-godesberg-stadtmarketing")
                self.assertEqual((winner["start_date"], winner["end_date"]), ("2026-10-09", "2026-10-11"))
                self.assertFalse(winner.get("start_at"))

    def test_organiser_listing_absorbs_renamed_venue_copy(self):
        for rows in ([self.university, self._cyber_copy()], [self._cyber_copy(), self.university]):
            with self.subTest(first=rows[0]["source_id"]):
                [winner] = report.deduplicate(rows)
                self.assertEqual(winner["source_id"], "uni-bonn")
                self.assertEqual(winner["end_at"], "2026-10-06T20:30+02:00")

    def test_distinct_programme_points_stay_separate(self):
        for copy, primary in (
            (self._govinum_copy("2026-10-12"), self.organiser),
            (self._govinum_copy("2026-10-10", city="Bonn-Mehlem"), self.organiser),
            (self._govinum_copy("2026-10-10", title="GoVinum Weinfest Kuratorenführung"), self.organiser),
            (self._cyber_copy(start_at="2026-10-06T10:00+02:00"), self.university),
            (self._cyber_copy(title="Dialog für Cybersicherheit Bonn"), self.university),
            (self._cyber_copy(source="Anderer Kalender", source_id="other"), self.university),
        ):
            with self.subTest(copy=(copy["title"], copy["city"], copy.get("start_at"), copy["source"])):
                self.assertEqual(len(report.deduplicate([primary, copy])), 2)


    def test_market_directory_copy_with_city_suffix_merges(self):
        base = {**_day("2026-10-11"), "city": "Troisdorf", "category_key": "market", "status": "scheduled", "score": 1.0}
        organiser = {
            **base, "title": "Trödelmarkt-Spich TOOM-Parkplatz", "venue": "Toom Baumarkt Spich",
            "start_at": "2026-10-11T11:00+02:00", "source": "Troisdorf", "source_id": "troisdorf",
            "link": "https://www.troisdorf.de/de/kalender/2026-10-11-troedelmarkt-spich-toom-parkplatz/",
        }
        directory = {
            **base, "title": "Trödelmarkt-Spich TOOM-Parkplatz Troisdorf", "venue": "Trödelmarkt-Spich TOOM-Parkplatz",
            "venue_address": "Langeler Ring 53842 Troisdorf, Spich", "source": "marktcom", "source_id": "marktcom",
            "link": "https://www.marktcom.de/veranstaltung/troedelmarkt-spich-toom-parkplatz",
        }
        [winner] = report.deduplicate([directory, organiser])
        self.assertEqual(winner["source_id"], "troisdorf")
        other_market = {**directory, "title": "Trödelmarkt-Sieglar TOOM-Parkplatz Troisdorf"}
        self.assertEqual(len(report.deduplicate([organiser, other_market])), 2)

    def test_same_publisher_listing_in_two_calendars_merges(self):
        copy = (
            "Mia san in Troisdorf – und 2026 heißt es endlich: Troisdorfer Oktoberfest in der Stadthalle! "
            "Freut euch auf eine einzigartige Mischung aus bayrischer Wiesn-Gaudi und rheinischer Lebensfreude. "
            "Erlebt ausgelassene Partystimmung und Musik zum Mitsingen."
        )
        base = {
            "city": "Troisdorf", "venue": "Stadthalle Troisdorf", "category_key": "festival",
            "status": "scheduled", "score": 1.0, "source": "Troisdorf", "source_id": "troisdorf",
        }
        general = {
            **base, "date": "2026-10-24", "start_date": "2026-10-24", "end_date": "2026-10-25",
            "title": "Troisdorfer Oktoberfest in der Stadthalle", "description": copy,
            "start_at": "2026-10-24T16:30+02:00", "end_at": "2026-10-25T01:30+02:00",
            "link": "https://www.troisdorf.de/de/kalender/allgemeine-veranstaltungen/2026-10-24-troisdorfer-oktoberfest-in-der-stadthalle/",
        }
        hall = {
            **base, **_day("2026-10-24"), "title": "Troisdorfer Oktoberfest",
            "description": f"O’zapft is in der Stadthalle! {copy}", "price": "ab 34,90 €",
            "start_at": "2026-10-24T17:30+02:00", "end_at": "2026-10-24T23:59+02:00",
            "link": "https://www.troisdorf.de/de/kalender/stadthalle/2026-10-24-troisdorfer-oktoberfest/",
        }
        [winner] = report.deduplicate([general, hall])
        self.assertEqual(winner["price"], "ab 34,90 €")
        for updates in (
            {"start_at": "2026-10-24T20:00+02:00"},
            {"description": "Ein anderer Abend in der Stadthalle mit eigenem Programm. " * 5},
            {"source": "Anderer Kalender", "source_id": "other", "venue": "Stadthalle Siegburg"},
        ):
            with self.subTest(updates=updates):
                self.assertEqual(len(report.deduplicate([general, {**hall, **updates}])), 2)


if __name__ == "__main__":
    unittest.main()
