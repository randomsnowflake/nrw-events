"""Cross-source listings of one performance: same minute, same named venue.

Every pair below was published twice in the 2026-09-09 snapshot.
"""

import unittest

from nrw_events.duplicate_identity import duplicate_candidates, events_are_duplicates


def _event(title, venue, city, source, start="2026-09-18T19:30+02:00", **updates):
    event = {
        "title": title,
        "venue": venue,
        "city": city,
        "source": source,
        "date": start[:10],
        "start_date": start[:10],
        "end_date": start[:10],
        "start_at": start,
        "end_at": "",
        "time": start[11:16],
        "description": "",
        "price": "",
        "score": 1.0,
        "status": "scheduled",
    }
    event.update(updates)
    return event


class TimedVenueDedupTests(unittest.TestCase):
    def assertDuplicate(self, left, right):
        self.assertTrue(events_are_duplicates(left, right))
        self.assertTrue(events_are_duplicates(right, left))

    def test_district_spelling_of_the_same_venue(self):
        self.assertDuplicate(
            _event("This is America!", "Burg Adendorf", "Wachtberg", "Beethovenfest Bonn"),
            _event("This is America!", "Burg Adendorf, BUr", "Wachtberg-Adendorf", "Bonn.de Events"),
        )
        self.assertDuplicate(
            _event("Robert Quinney", "St. Martinus, Ollheim", "Swisttal", "Beethovenfest Bonn"),
            _event("Robert Quinney", "St. Martinus", "Swisttal-Ollheim", "Bonn.de Events"),
        )

    def test_wrong_aggregator_city_does_not_hide_the_venue(self):
        self.assertDuplicate(
            _event("Zhouhui Shen", "Burg Namedy, Burg", "Bonn", "Bonn.de Events"),
            _event("Zhouhui Shen", "Burg Namedy", "Andernach", "Beethovenfest Bonn"),
        )

    def test_venue_name_inside_a_longer_institution_name(self):
        self.assertDuplicate(
            _event("Offenes Denkmal mit Musik", "Adenauerhaus, Rhöndorf", "Bad Honnef", "Beethovenfest Bonn"),
            _event("Offenes Denkmal mit Musik", "Stiftung Bundeskanzler-Adenauer-Haus", "Bad Honnef", "Bad Honnef"),
        )

    def test_editorial_words_around_the_same_title(self):
        self.assertDuplicate(
            _event("Rachel Joyce »Sommerhaus«", "Rheinhotel Dreesen", "Bonn", "Bonn.de Events"),
            _event("Rachel Joyce liest aus »Sommerhaus«", "Rheinhotel Dreesen", "Bonn-Bad Godesberg", "Parkbuchhandlung"),
        )
        self.assertDuplicate(
            _event("Konzert mit Wave Of Joy", "Große Evangelische Kirche", "Bonn", "Bonn.de Events"),
            _event("Konzert Gospelchor Wave of Joy", "Große Evangelische Kirche Oberkassel", "Bonn-Oberkassel", "Beuel.net"),
        )

    def test_age_rating_is_not_an_episode_number(self):
        start = "2026-10-03T15:00+02:00"
        self.assertDuplicate(
            _event("Percy Jackson - The Lightning Thief (10+)", "Junges Theater Bonn", "Bonn", "Bonn.de Events", start),
            _event("Percy Jackson – The Lightning Thief", "Junges Theater Bonn", "Bonn", "Theater Bonn", start),
        )

    def test_conflicting_time_or_venue_stays_separate(self):
        self.assertFalse(events_are_duplicates(
            _event("Gerüchteküche: Onkel Wanja", "Kulturzentrum Brotfabrik", "Bonn", "Bonn.de Events",
                   "2026-10-04T18:00+02:00"),
            _event("Gerüchteküche: Onkel Wanja", "Kulturzentrum Brotfabrik", "Bonn", "Brotfabrik Bonn",
                   "2026-10-04T19:30+02:00"),
        ))
        self.assertFalse(events_are_duplicates(
            _event("Öffentliche Führung", "Kunstmuseum Bonn", "Bonn", "Bonn.de Events", "2026-09-20T14:00+02:00"),
            _event("Öffentliche Führung", "LVR-Landesmuseum", "Bonn", "LVR", "2026-09-20T14:00+02:00"),
        ))


class PreProgrammeTests(unittest.TestCase):
    """The venue lists its pre-concert talk; the festival only the concert."""

    def _pair(self, **venue_updates):
        venue = _event(
            "live arts: Michael Barenboim & Nasmé Ensemble", "Bundeskunsthalle", "Bonn-Gronau", "Bundeskunsthalle",
            "2026-09-30T18:45+02:00", end_at="2026-09-30T21:30+02:00", venue_id="bundeskunsthalle",
            category_key="concert",
        )
        venue.update(venue_updates)
        festival = _event(
            "Michael Barenboim & Nasmé Ensemble", "Bundeskunsthalle", "Bonn", "Beethovenfest Bonn",
            "2026-09-30T19:30+02:00", venue_id="bundeskunsthalle", category_key="concert",
        )
        return venue, festival

    def test_performance_inside_the_venue_listing_is_one_occurrence(self):
        venue, festival = self._pair()
        self.assertTrue(events_are_duplicates(venue, festival))
        self.assertTrue(events_are_duplicates(festival, venue))

    def test_distinct_or_distant_listings_stay_separate(self):
        for updates in (
            {"start_at": "2026-09-30T18:00+02:00", "time": "18:00"},  # 90 minutes earlier
            {"end_at": "2026-09-30T19:15+02:00"},  # ends before the concert
            {"end_at": "2026-09-30T23:30+02:00"},  # long all-evening interval
            {"category_key": "talk"},
            {"venue_id": "kunstmuseum-bonn"},
            {"title": "live arts: Kinan Azmeh Quartett"},
            {"source": "Beethovenfest Bonn"},
        ):
            with self.subTest(updates=updates):
                self.assertFalse(events_are_duplicates(*self._pair(**updates)))


class DuplicateCandidateTests(unittest.TestCase):
    def test_conflicting_listings_are_queued_for_review(self):
        brotfabrik = _event("Gerüchteküche: Onkel Wanja", "Kulturzentrum Brotfabrik", "Bonn", "Brotfabrik Bonn",
                            "2026-10-04T19:30+02:00", event_id="a")
        aggregator = _event("Gerüchteküche: Onkel Wanja", "Kulturzentrum Brotfabrik", "Bonn", "Bonn.de Events",
                            "2026-10-04T18:00+02:00", event_id="b")
        elsewhere = _event("Gerüchteküche: Onkel Wanja", "Stadthalle", "Siegburg", "Siegburg",
                           "2026-10-04T18:00+02:00", event_id="c")

        [candidate] = duplicate_candidates([brotfabrik, aggregator, elsewhere])

        self.assertEqual((candidate["conflict"], candidate["event_ids"]), ("time", ["a", "b"]))


class AbbreviatedStreetAndUndatedTitleTests(unittest.TestCase):
    """Pairs published twice in the 2026-10-01 production feed."""

    def test_abbreviated_street_matches_the_venue_calendar_address(self):
        title = "MACBETH – William Shakespeare – Bearbeitung und Übersetzung von John von Düffel"
        aggregator = _event(
            title, "Kleines Theater Nachfolger 2019 e.V.", "Bonn", "Bonn.de Events",
            start="2026-10-05T19:30+02:00", venue_address="Koblenzer Str. 78, 53177 Bonn",
        )
        venue = _event(
            title.replace("–", "-"), "Kleines Theater Bad Godesberg", "Bonn-Bad Godesberg",
            "Kleines Theater Bad Godesberg", start="2026-10-05T19:30+02:00",
            venue_address="Koblenzer Straße 78, 53177 Bonn",
        )
        self.assertTrue(events_are_duplicates(aggregator, venue))
        self.assertTrue(events_are_duplicates(venue, aggregator))
        # The Sunday matinee conflict (19:30 vs 15:30) stays separate for review.
        venue_matinee = {**venue, "start_at": "2026-10-18T15:30+02:00", "date": "2026-10-18",
                         "start_date": "2026-10-18", "end_date": "2026-10-18"}
        aggregator_evening = {**aggregator, "start_at": "2026-10-18T19:30+02:00", "date": "2026-10-18",
                              "start_date": "2026-10-18", "end_date": "2026-10-18"}
        self.assertFalse(events_are_duplicates(aggregator_evening, venue_matinee))

    def test_one_sided_year_is_not_a_different_edition(self):
        dated = _event("Tag der Feuerwehr 2026", "Münsterplatz", "Bonn", "Bonn.de Events",
                       start="2026-10-17T10:00+02:00")
        undated = _event("Tag der Feuerwehr", "", "Bonn", "Bonn district festivals",
                         start="2026-10-17", start_at="", time="", all_day=True)
        self.assertTrue(events_are_duplicates(dated, undated))
        self.assertFalse(events_are_duplicates(
            _event("Stadtfest 2025", "Markt", "Bonn", "Bonn.de Events"),
            _event("Stadtfest 2026", "Markt", "Bonn", "Bonn district festivals"),
        ))


if __name__ == "__main__":
    unittest.main()
