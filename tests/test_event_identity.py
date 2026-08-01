"""The public event id must survive enrichment and ignore feed order."""

import json
import unittest
from pathlib import Path

from nrw_events.identity import (
    assign_event_ids,
    duplicate_event_ids,
    event_id,
    identity_tuple,
)

VECTORS_PATH = Path(__file__).parent / "data" / "event_id_vectors.json"


def occurrence(**overrides):
    event = {
        "title": "GA-Sommergarten – Albie Donnelly's Supercharge",
        "start_date": "2026-08-02",
        "date": "2026-08-02",
        "end_date": "2026-08-02",
        "time": "11:30–14:30",
        "start_at": "2026-08-02T11:30+02:00",
        "all_day": False,
        "venue": "Bundeskunsthalle",
        "venue_id": "bundeskunsthalle",
        "city": "Bonn",
        "source": "Bundeskunsthalle",
        "source_id": "bundeskunsthalle",
        "link": "https://www.bundeskunsthalle.de/programm/sommergarten.html",
        "description": "Rhythm 'n' Blues, Soul und Funk unter freiem Himmel.",
        "price": "Eintritt frei",
        "score": 4.2,
    }
    event.update(overrides)
    return event


class EventIdStabilityTests(unittest.TestCase):
    def test_id_is_readable_and_carries_title_and_date(self):
        identifier = event_id(occurrence())
        self.assertTrue(
            identifier.startswith("ga-sommergarten-albie-donnelly-s-supercharge-2026-08-02-"),
            identifier,
        )

    def test_enrichment_does_not_change_the_id(self):
        base = event_id(occurrence())
        for field, value in (
            ("description", "Ein deutlich längerer, angereicherter Beschreibungstext."),
            ("price", "12,00 €"),
            ("link", "https://example.org/anderer-link"),
            ("source", "Bonn.de"),
            ("source_id", "bonn-de"),
            ("score", 9.9),
            ("category_key", "concerts"),
            ("distance_km", 3.4),
            ("end_date", "2026-08-30"),
        ):
            with self.subTest(field=field):
                self.assertEqual(event_id(occurrence(**{field: value})), base)

    def test_only_the_start_time_defines_the_id(self):
        """An end time that appears or is reformatted must not move the URL.

        ``rc.time_text`` emits a range as soon as a listing names two clock
        times, so the same occurrence legitimately arrives as ``11:30`` in one
        import and ``11:30–14:30`` in the next.
        """
        base = event_id(occurrence(time="11:30"))
        for time_text in ("11:30–14:30", "11:30-14:30", "11:30 – 14:30", "11:30 bis 14:30"):
            with self.subTest(time=time_text):
                self.assertEqual(event_id(occurrence(time=time_text)), base)

    def test_series_dates_and_times_stay_distinct(self):
        first = event_id(occurrence())
        other_day = event_id(occurrence(start_date="2026-08-09", date="2026-08-09", start_at="2026-08-09T11:30+02:00"))
        other_time = event_id(occurrence(time="19:30", start_at="2026-08-02T19:30+02:00"))
        self.assertEqual(3, len({first, other_day, other_time}))

    def test_source_id_alone_is_not_an_identity(self):
        left = occurrence(title="Konzert A")
        right = occurrence(title="Konzert B")
        self.assertEqual(left["source_id"], right["source_id"])
        self.assertNotEqual(event_id(left), event_id(right))

    def test_all_day_events_share_one_time_key(self):
        left = occurrence(time="", start_at="", all_day=True)
        right = occurrence(time="", start_at="", all_day=True, price="frei")
        self.assertEqual(event_id(left), event_id(right))
        self.assertIn("all-day", identity_tuple(left))

    def test_registry_venue_id_beats_a_reworded_venue_label(self):
        labelled = occurrence(venue="Bundeskunsthalle, Helmut-Kohl-Allee 4")
        self.assertEqual(event_id(occurrence()), event_id(labelled))

    def test_published_venue_name_can_preserve_an_id_during_registry_enrichment(self):
        published = occurrence(venue="Bikini Beach", venue_id="")
        enriched = occurrence(
            venue="Bikini Beach",
            venue_id="bikini-beach-bonn",
            identity_venue="Bikini Beach",
        )
        self.assertEqual(event_id(published), event_id(enriched))

    def test_venue_label_identifies_events_without_a_registry_venue(self):
        left = occurrence(venue_id="", venue="Marktplatz")
        right = occurrence(venue_id="", venue="Stadthalle")
        self.assertNotEqual(event_id(left), event_id(right))

    def test_map_only_location_enrichment_preserves_lantershofen_url(self):
        event = occurrence(
            title="100-Jahre Löschgruppe Lantershofen",
            start_date="2026-08-01",
            date="2026-08-01",
            time="",
            start_at="",
            all_day=True,
            venue="Mehrzweckhalle Lantershofen",
            venue_id="",
            city="Grafschaft",
        )

        self.assertEqual(
            event_id(event),
            "100-jahre-loeschgruppe-lantershofen-2026-08-01-8e18306c73",
        )


class AssignEventIdsTests(unittest.TestCase):
    def test_reordering_the_feed_does_not_move_any_id(self):
        events = [occurrence(title=f"Veranstaltung {index}") for index in range(12)]
        forward = {event["title"]: event["event_id"] for event in assign_event_ids(events)}
        backward = {event["title"]: event["event_id"] for event in assign_event_ids(reversed(events))}
        self.assertEqual(forward, backward)

    def test_every_assigned_id_is_unique(self):
        events = [occurrence(title=f"Veranstaltung {index}") for index in range(50)]
        self.assertEqual({}, duplicate_event_ids(assign_event_ids(events)))

    def test_colliding_occurrences_are_disambiguated_deterministically(self):
        # Same title, venue, city, date and time: identical identity tuple.
        left = occurrence(link="https://example.org/a", description="A")
        right = occurrence(link="https://example.org/b", description="B")
        self.assertEqual(identity_tuple(left), identity_tuple(right))
        forward = assign_event_ids([left, right])
        backward = assign_event_ids([right, left])
        self.assertEqual({}, duplicate_event_ids(forward))
        self.assertEqual(
            {event["link"]: event["event_id"] for event in forward},
            {event["link"]: event["event_id"] for event in backward},
        )

    def test_assigning_ids_never_mutates_the_input_records(self):
        source = occurrence()
        assign_event_ids([source])
        self.assertNotIn("event_id", source)

    def test_internal_identity_venue_is_not_published(self):
        assigned = assign_event_ids([occurrence(identity_venue="Bundeskunsthalle")])
        self.assertNotIn("identity_venue", assigned[0])


class EventIdVectorTests(unittest.TestCase):
    """Golden vectors shared verbatim with the website implementation."""

    def test_committed_vectors_match_the_implementation(self):
        vectors = json.loads(VECTORS_PATH.read_text(encoding="utf-8"))
        for vector in vectors["vectors"]:
            with self.subTest(name=vector["name"]):
                self.assertEqual(vector["eventId"], event_id(vector["event"]))


if __name__ == "__main__":
    unittest.main()
