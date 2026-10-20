"""Cross-source duplicates still published after the 2026-10-19 fold release.

Every pair below is a record shape from the 2026-10-20 production import
(release 20261020T043217-8541dbcc). Each was live twice on the website.
"""

import unittest
from datetime import datetime

from nrw_events.civic_list_fallbacks import partition_city_list_fallbacks
from nrw_events.duplicate_identity import _series_tokens, events_are_duplicates
from nrw_events.validation import canonicalize_event

from tests.helpers import patch_window


def _event(**fields):
    event = {
        "description": "",
        "price": "",
        "score": 1.0,
        "status": "scheduled",
        "end_at": "",
        "all_day": False,
        "venue_id": "",
        "venue_address": "",
    }
    event.update(fields)
    event.setdefault("date", event["start_date"])
    event.setdefault("end_date", event["start_date"])
    return event


class CrossSourcePairTests(unittest.TestCase):
    def assertDuplicate(self, left, right):
        self.assertTrue(events_are_duplicates(left, right))
        self.assertTrue(events_are_duplicates(right, left))

    def assertSeparate(self, left, right):
        self.assertFalse(events_are_duplicates(left, right))
        self.assertFalse(events_are_duplicates(right, left))

    def test_day_month_stamp_is_not_an_episode_number(self):
        self.assertEqual(_series_tokens("11.11. Sessions-Start mit Cat Ballou"), ())
        self.assertEqual(_series_tokens("12.10.: Baby Massage"), ())
        # A full date or range keeps blocking (see test_open_issue_contracts).
        self.assertEqual(_series_tokens("20.08.2026 - 23.08.2026 Weinfest"), ("20", "08", "23", "08"))
        self.assertEqual(_series_tokens("Zum 100. Todestag"), ("100",))
        self.assertEqual(_series_tokens("Teil 2"), ("2",))

    def test_dated_ticket_title_folds_into_the_venue_listing(self):
        brueckenforum = {
            "venue": "Brückenforum Bonn", "venue_id": "brueckenforum-bonn",
            "venue_address": "Friedrich-Breuer-Straße 17, 53225 Bonn", "city": "Bonn",
            "category_key": "nightlife", "start_date": "2026-11-11",
        }
        self.assertDuplicate(
            _event(title="11.11. Sessions-Start mit Cat Ballou und den Höhnern", source="AfterJobParty Bonn",
                   source_id="afterjobparty-bonn", start_at="2026-11-11T15:00+01:00",
                   end_at="2026-11-11T23:00+01:00", link="https://afterjobparty.ticket.io/2mdeDxxn/", **brueckenforum),
            _event(title="AfterJobParty -Sessions-Start mit Cat Ballou und den Höhnern", source="Brückenforum Bonn",
                   source_id="brueckenforum-bonn", start_at="", all_day=True,
                   link="https://www.brueckenforum.de/events/afterjobparty-sessions-start-mit-cat-ballou-und-den-hoehnern/",
                   **brueckenforum),
        )

    def test_party_past_midnight_matches_the_single_day_listing(self):
        place = {
            "venue": "Brückenforum Bonn", "venue_id": "brueckenforum-bonn",
            "venue_address": "Friedrich-Breuer-Straße 17, 53225 Bonn", "city": "Bonn",
            "category_key": "nightlife",
        }
        ticket = _event(title="Zombie House // Brückenforum Bonn", source="RheinEvents", source_id="rheinevents",
                        start_date="2026-10-31", end_date="2026-11-01", start_at="2026-10-31T21:00+01:00",
                        end_at="2026-11-01T03:00+01:00",
                        link="https://tickets.rheinevents.de/event/zombie-house-bruckenforum-bonn-ygeeb0", **place)
        venue = _event(title="Zombie House Halloween Party", source="Brückenforum Bonn", source_id="brueckenforum-bonn",
                       start_date="2026-10-31", start_at="", all_day=True,
                       link="https://www.brueckenforum.de/events/zombie-house-halloween-party/", **place)
        self.assertDuplicate(ticket, venue)
        # The venue's afternoon kids' party that day is another event.
        kids = dict(venue, title="Pänz Dänz Halloween Party",
                    link="https://www.brueckenforum.de/events/paenz-daenz-halloween-party/")
        self.assertSeparate(ticket, kids)
        # A real two-day run at the venue is not a night out.
        weekend = dict(ticket, title="Weekender", start_at="2026-10-31T21:00+01:00", end_at="2026-11-01T23:00+01:00")
        self.assertSeparate(weekend, venue)

    def test_performer_and_programme_in_either_order(self):
        brotfabrik = {
            "venue": "Kulturzentrum Brotfabrik", "venue_id": "kulturzentrum-brotfabrik",
            "venue_address": "Kreuzstraße 16, 53225 Bonn", "city": "Bonn",
        }
        self.assertDuplicate(
            _event(title="Georg Brinkmann & Stefan Horz: Klezmerdialog", source="Brotfabrik Bonn",
                   source_id="brotfabrik-bonn", category_key="stage", start_date="2026-11-12",
                   start_at="2026-11-12T20:00+01:00", link="https://www.brotfabrik-theater.de/klezmerdialog/", **brotfabrik),
            _event(title="Klezmerdialog - Georg Brinkmann & Stefan Horz", source="Bonn.de Events",
                   source_id="bonn-de-events", category_key="concert", start_date="2026-11-12",
                   start_at="2026-11-12T20:00+01:00", end_at="2026-11-12T23:59+01:00",
                   link="https://www.bonn.de/veranstaltungskalender/klezmerdialog.php", **brotfabrik),
        )
        # The Brotfabrik feed cuts the title after "Rainer Maria".
        self.assertDuplicate(
            _event(title="Bernt Hahn / Michael Denhoff: Zum 100. Todestag Rainer Maria", source="Brotfabrik Bonn",
                   source_id="brotfabrik-bonn", category_key="stage", start_date="2026-11-08",
                   start_at="2026-11-08T11:30+01:00",
                   link="https://www.brotfabrik-theater.de/zum-100-todestag-rainer-maria-rilkes/", **brotfabrik),
            _event(title="Zum 100. Todestag Rainer Maria Rilkes - Bernt Hahn / Michael Denhoff",
                   source="Bonn.de Events", source_id="bonn-de-events", category_key="talk",
                   start_date="2026-11-08", start_at="2026-11-08T11:30+01:00", end_at="2026-11-08T23:59+01:00",
                   link="https://www.bonn.de/veranstaltungskalender/rilke.php", **brotfabrik),
        )

    def test_short_or_different_titles_at_one_venue_and_minute_stay_separate(self):
        # No registry id: an unregistered stage isolates the title rule from
        # the existing same-venue-same-minute-same-category rule.
        place = {"venue": "Theater im Hinterhof", "city": "Bonn",
                 "category_key": "stage", "start_date": "2026-11-12", "start_at": "2026-11-12T20:00+01:00"}
        self.assertSeparate(
            _event(title="Lesung: Anna Weber", source="Brotfabrik Bonn", **place),
            _event(title="Konzert: Anna Weber Quartett", source="Bonn.de Events", **place),
        )
        # Same words reordered at an unregistered venue still fold.
        self.assertDuplicate(
            _event(title="Anna Weber: Hinterhofsalon", source="Brotfabrik Bonn", **place),
            _event(title="Hinterhofsalon - Anna Weber", source="Bonn.de Events", **place),
        )
        self.assertSeparate(
            _event(title="Öffentliche Führung", source="Brotfabrik Bonn", **place),
            _event(title="Führung Öffentliche Probe Kinderchor", source="Bonn.de Events", **place),
        )


class DistrictProcessionTests(unittest.TestCase):
    def setUp(self):
        patch_window(self, datetime(2026, 10, 20), datetime(2026, 11, 30))

    def _row(self, **fields):
        return canonicalize_event({
            "source": "Bonn.de Martinszüge", "source_id": "bonn-martinszuege", "score": 1.0,
            "category_key": "kids", "link": "https://www.bonn.de/bonn-erleben/ausgehen-und-erleben/martinszuege.php",
            "end_date": fields["start_date"], **fields,
        })

    def _club(self, **fields):
        return canonicalize_event({"score": 5.0, "category_key": "kids", "end_date": fields["start_date"], **fields})

    def test_club_record_named_only_after_the_procession_replaces_the_district_row(self):
        cases = [
            (
                self._row(title="Martinszug Brüser Berg", start_date="2026-11-06", city="Bonn-Brüser Berg",
                          venue="Edisonallee", start_at="2026-11-06T18:00+01:00"),
                self._club(title="Martinszug", source="Veranstaltungen Brüser Berg",
                           source_id="veranstaltungen-brueser-berg", start_date="2026-11-06",
                           city="Bonn-Brüser Berg", venue="Brüser Berg", start_at="2026-11-06T17:30+01:00",
                           end_at="2026-11-06T20:00+01:00", link="https://brueser-berg.de"),
            ),
            (
                self._row(title="Martinszug Bürgerverein Vilich-Müldorf e.V.", start_date="2026-11-07",
                          city="Bonn-Vilich-Müldorf", venue="Wilfried-Hatzfeld-Straße",
                          start_at="2026-11-07T17:30+01:00"),
                self._club(title="St.Martinszug", source="Bürgerverein Vilich-Müldorf",
                           source_id="b-rgerverein-vilich-m-ldorf", start_date="2026-11-07",
                           city="Bonn-Vilich-Müldorf", venue="Vilich-Müldorf", venue_address="53229 Bonn",
                           start_at="2026-11-07T17:15+01:00", end_at="2026-11-07T21:00+01:00",
                           link="https://www.bv-vilich-mueldorf.de/event/st-martinszug-7/"),
            ),
        ]
        for row, club in cases:
            for events in ([row, club], [club, row]):
                kept, replaced = partition_city_list_fallbacks(events)
                self.assertEqual([event.source_id for event in kept], [club.source_id])
                self.assertEqual(replaced, [row])
                self.assertTrue(kept[0].previous_event_ids)

    def test_named_institution_row_does_not_fold_into_a_generic_district_record(self):
        row = self._row(title="Martinszug KiTa Taubenschlag", start_date="2026-11-06", city="Bonn-Brüser Berg",
                        venue="Edisonallee", start_at="2026-11-06T17:00+01:00")
        club = self._club(title="Martinszug", source="Veranstaltungen Brüser Berg",
                          source_id="veranstaltungen-brueser-berg", start_date="2026-11-06", city="Bonn-Brüser Berg",
                          venue="Brüser Berg", start_at="2026-11-06T17:30+01:00", link="https://brueser-berg.de")
        kept, replaced = partition_city_list_fallbacks([row, club])
        self.assertEqual(replaced, [])
        self.assertEqual(len(kept), 2)


if __name__ == "__main__":
    unittest.main()
