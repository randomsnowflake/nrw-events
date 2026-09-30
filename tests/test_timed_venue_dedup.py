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


if __name__ == "__main__":
    unittest.main()
