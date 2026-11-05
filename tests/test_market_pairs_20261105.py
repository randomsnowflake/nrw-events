"""Market duplicates from the 2026-10-20 production feed, reviewed 2026-11-05."""

import unittest

from nrw_events.duplicate_identity import events_are_duplicates


def _event(**fields):
    event = {
        "description": "", "price": "", "score": 1.0, "status": "scheduled", "category_key": "market",
        "start_at": "", "end_at": "", "all_day": False, "venue_id": "", "venue_address": "",
    }
    event.update(fields)
    event.setdefault("date", event["start_date"])
    event.setdefault("end_date", event["start_date"])
    return event


class MarketPairTests(unittest.TestCase):
    def assertDuplicate(self, left, right):
        self.assertTrue(events_are_duplicates(left, right))
        self.assertTrue(events_are_duplicates(right, left))

    def assertSeparate(self, left, right):
        self.assertFalse(events_are_duplicates(left, right))
        self.assertFalse(events_are_duplicates(right, left))

    def test_directory_venue_repeating_the_title_folds_into_the_venue_listing(self):
        directory = _event(title="Schallplattenbörse Bonn", venue="SCHALLPLATTENBÖRSE Bonn", city="Bonn",
                           source="marktcom", source_id="marktcom", start_date="2026-10-25",
                           link="https://www.marktcom.de/veranstaltung/schallplattenboerse-bonn-in-53225-bonn-beuel")
        venue = _event(title="Schallplattenbörse", venue="Brückenforum Bonn", venue_id="brueckenforum-bonn",
                       city="Bonn", source="Brückenforum Bonn", source_id="brueckenforum-bonn",
                       start_date="2026-10-25", start_at="2026-10-25T11:00+01:00", end_at="2026-10-25T16:00+01:00",
                       link="https://www.brueckenforum.de/events/schallplattenboerse/")
        self.assertDuplicate(directory, venue)
        # The echo only drops the venue: a different title on the same day stays separate.
        self.assertSeparate(directory, {**venue, "title": "Comicbörse"})

    def test_theaterplatz_is_the_bad_godesberg_town_centre(self):
        self.assertDuplicate(
            _event(title="Deutsch – Holländischer Stoff- und Tuchmarkt", venue="Theaterplatz",
                   city="Bonn-Bad Godesberg", source="Bad Godesberg Stadtmarketing",
                   source_id="bad-godesberg-stadtmarketing", start_date="2026-11-08",
                   link="https://bad-godesberg.info/veranstaltungen_st/stoff-und-tuchmarkt"),
            _event(title="Deutsch-Holländischer Stoffmarkt", venue="Bad Godesberger Innenstadt",
                   city="Bonn-Bad Godesberg", source="Bonn.de Events", source_id="bonn-de-events",
                   start_date="2026-11-08", start_at="2026-11-08T11:00+01:00", end_at="2026-11-08T17:00+01:00",
                   link="https://www.bonn.de/veranstaltungskalender/veranstaltungen/hauptkalender/extern/Deutsch-Hollaendische-Stoffmarkt-.php"),
        )

    def test_church_programme_during_the_market_stays_separate(self):
        # The church opens with its own exhibition during the town's Martinimarkt.
        run = {"city": "Bad Honnef", "source": "ionas4 regional", "source_id": "ionas4-bad-honnef",
               "start_date": "2026-10-21", "end_date": "2026-10-25"}
        self.assertSeparate(
            _event(title="Martinimarkt", venue="Fußgängerzone", link="https://meinbadhonnef.de/a", **run),
            _event(title="MartiniMarkt in Johann Baptist Bad Honnef", venue="Kirche Sankt Johann Baptist",
                   link="https://meinbadhonnef.de/b", **run),
        )


if __name__ == "__main__":
    unittest.main()
