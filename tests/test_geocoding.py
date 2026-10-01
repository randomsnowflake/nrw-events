import json
import os
import sqlite3
import tempfile
import unittest
import urllib.error
from datetime import datetime
from pathlib import Path
from unittest import mock

from nrw_events import common, geocoding
from nrw_events.validation import validate_event

from tests.helpers import make_event, patch_window


def result(road, number=None, postcode="53340", category="place", kind="house", lat=50.63, lon=7.02):
    return {
        "category": category, "type": kind, "lat": str(lat), "lon": str(lon),
        "display_name": f"{road}, Meckenheim",
        "address": {"road": road, "house_number": number, "postcode": postcode, "town": "Meckenheim"},
    }


def event(venue="Merler Winkel", address="Merler Winkel, 53340 Meckenheim", **overrides):
    return validate_event(make_event(city="Meckenheim", venue=venue, venue_address=address, **overrides))


def place(name, category="amenity", kind="library", town="Meckenheim", lat=50.631, lon=7.021, **address):
    return {
        "category": category, "type": kind, "name": name, "lat": str(lat), "lon": str(lon),
        "display_name": f"{name}, {town}",
        "address": {"town": town, **address},
    }


LIBRARY = "Bücherei am Klosterhof"


class AcceptedPointTests(unittest.TestCase):
    def test_house_number_street_postcode_and_town_must_all_match(self):
        house = event("Bürgerhaus", "Merler Straße 12, 53340 Meckenheim")
        self.assertEqual(geocoding.accepted_point(house, [result("Merler Straße", "12")]), (50.63, 7.02))
        for rejected in (
            result("Merler Straße", "14"),
            result("Merler Straße", "12", postcode="53359"),
            result("Hauptstraße", "12"),
            result("Merler Straße", None, category="highway", kind="residential"),
            result("Meckenheim", None, category="boundary", kind="administrative"),
        ):
            with self.subTest(rejected=rejected):
                self.assertIsNone(geocoding.accepted_point(house, [rejected]))

    def test_street_without_house_number_only_pins_a_street_venue(self):
        street = result("Merler Winkel", category="highway", kind="residential")
        self.assertEqual(geocoding.accepted_point(event(), [street]), (50.63, 7.02))
        building = event("Kita Sonnenschein", "Merler Winkel, 53340 Meckenheim")
        self.assertIsNone(geocoding.accepted_point(building, [street]))
        unanchored = event("Merler Winkel", "Meckenheim")
        self.assertIsNone(geocoding.accepted_point(unanchored, [street]))

    def test_points_outside_the_radius_are_rejected(self):
        far = result("Merler Winkel", category="highway", kind="residential", lat=52.52, lon=13.4)
        self.assertIsNone(geocoding.accepted_point(event(), [far]))

    def test_town_is_added_only_without_postcode(self):
        self.assertEqual(
            geocoding.query_for(event("Bürgerhaus", "Merler Straße 12, 53340 Meckenheim")),
            "Merler Straße 12, 53340 Meckenheim, Deutschland",
        )
        self.assertEqual(
            geocoding.query_for(event("Bürgerhaus", "Merler Straße 12")),
            "Merler Straße 12, Meckenheim, Deutschland",
        )
        # The street-named venue keeps only postcode and town as its address.
        self.assertEqual(event().venue_address, "53340 Meckenheim")
        self.assertEqual(geocoding.query_for(event()), "Merler Winkel, 53340 Meckenheim, Deutschland")
        self.assertEqual(geocoding.query_for(event("Meckenheim", "53340 Meckenheim")), "")


class VenueNameTests(unittest.TestCase):
    def test_named_venue_in_its_town_is_pinned(self):
        library = event(LIBRARY, "")
        self.assertEqual(geocoding.venue_query_for(library), "Bücherei am Klosterhof, Meckenheim, Deutschland")
        self.assertEqual(geocoding.accepted_venue_point(library, [place(LIBRARY)]), (50.631, 7.021))

    def test_areas_other_towns_and_other_names_are_rejected(self):
        library = event(LIBRARY, "")
        for rejected in (
            place(LIBRARY, category="place", kind="suburb"),
            place(LIBRARY, category="place", kind="hamlet"),
            place(LIBRARY, category="boundary", kind="administrative"),
            place(LIBRARY, town="Rheinbach"),
            place("Stadtbibliothek"),
            place(LIBRARY, category="highway", kind="bus_stop"),
            place(LIBRARY, lat=52.52, lon=13.4),
        ):
            with self.subTest(rejected=rejected):
                self.assertIsNone(geocoding.accepted_venue_point(library, [rejected]))

    def test_a_shared_word_is_not_the_same_town(self):
        kurhaus = validate_event(make_event(city="Bad Honnef", venue="Kurhaus am Park", venue_address=""))
        elsewhere = place("Kurhaus am Park", town="Bad Münstereifel", lat=50.556, lon=6.763)
        self.assertIsNone(geocoding.accepted_venue_point(kurhaus, [elsewhere]))
        here = place("Kurhaus am Park", town="Bad Honnef", lat=50.64, lon=7.22)
        self.assertEqual(geocoding.accepted_venue_point(kurhaus, [here]), (50.64, 7.22))

    def test_district_labels_match_their_town(self):
        self.assertEqual(geocoding.municipality("Bonn-Beuel"), "Bonn")
        self.assertEqual(geocoding.municipality("Köln (Braunsfeld)"), "Köln")
        self.assertEqual(geocoding.municipality("Bad Neuenahr-Ahrweiler"), "Bad Neuenahr-Ahrweiler")
        beuel = validate_event(make_event(city="Bonn-Beuel", venue="Brückenhofmuseum Test", venue_address=""))
        self.assertTrue(geocoding.accepted_venue_point(beuel, [place("Brückenhofmuseum Test", town="Bonn", lat=50.74, lon=7.13)]))

    def test_source_address_must_not_conflict(self):
        library = event(LIBRARY, "Klosterweg 3, 53340 Meckenheim")
        self.assertEqual(
            geocoding.accepted_venue_point(library, [place(LIBRARY, road="Klosterweg", house_number="3", postcode="53340")]),
            (50.631, 7.021),
        )
        for conflict in ({"postcode": "53359"}, {"road": "Klosterweg", "house_number": "9"}, {"road": "Hauptstraße"}):
            with self.subTest(conflict=conflict):
                self.assertIsNone(geocoding.accepted_venue_point(library, [place(LIBRARY, **conflict)]))

    def test_generic_online_and_rejected_names_are_not_looked_up(self):
        for venue in ("Dorfplatz", "Online-Veranstaltung", "Treffpunkt", "Meckenheim", "12"):
            with self.subTest(venue=venue):
                self.assertEqual(geocoding.venue_query_for(event(venue, "")), "")
        self.assertEqual(geocoding.venue_query_for(event(LIBRARY, ""), {LIBRARY.upper(): "matched a school"}), "")
        self.assertTrue(geocoding.venue_query_for(event("Rathaus Meckenheim", "")))

    def test_address_goes_first_and_postcode_only_addresses_are_judged_as_venue(self):
        street = event(LIBRARY, "Klosterweg 3, 53340 Meckenheim")
        self.assertEqual(geocoding.lookups_for(street), [
            ("Klosterweg 3, 53340 Meckenheim, Deutschland", geocoding.LOCATION_SOURCE),
            ("Bücherei am Klosterhof, Meckenheim, Deutschland", geocoding.VENUE_LOCATION_SOURCE),
        ])
        postcode_only = event(LIBRARY, "53340 Meckenheim")
        self.assertEqual(geocoding.lookups_for(postcode_only), [
            ("Bücherei am Klosterhof, 53340 Meckenheim, Deutschland", geocoding.LOCATION_SOURCE),
            ("Bücherei am Klosterhof, 53340 Meckenheim, Deutschland", geocoding.VENUE_LOCATION_SOURCE),
            ("Bücherei am Klosterhof, Meckenheim, Deutschland", geocoding.VENUE_LOCATION_SOURCE),
        ])

    def test_budget_comes_from_the_environment(self):
        for value, expected in (("", 120.0), ("600", 600.0), ("-5", 0.0), ("viel", 120.0)):
            with self.subTest(value=value), mock.patch.dict(os.environ, {"NRW_EVENTS_GEOCODING_BUDGET_SECONDS": value}):
                self.assertEqual(geocoding.run_budget_seconds(), expected)


@mock.patch.dict(os.environ, {"NRW_EVENTS_GEOCODING": "1"})
@mock.patch.object(geocoding.time, "sleep")
class GeocodeMissingTests(unittest.TestCase):
    def setUp(self):
        geocoding.cache_path().unlink(missing_ok=True)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.points = Path(directory.name) / "points.json"
        patcher = mock.patch.object(geocoding, "POINTS_PATH", self.points)
        patcher.start()
        self.addCleanup(patcher.stop)

    def store(self, query, results, fetched_at):
        geocoding.geocode_missing([])  # creates the table
        with sqlite3.connect(geocoding.cache_path()) as connection:
            connection.execute("INSERT OR REPLACE INTO nominatim VALUES (?, ?, ?)", (query, fetched_at, json.dumps(results)))

    def test_pinned_lookups_never_expire_but_misses_are_retried(self, _sleep):
        street = result("Merler Winkel", category="highway", kind="residential")
        query = geocoding.query_for(event())
        self.store(query, [street], "2020-01-01T00:00:00+00:00")
        with mock.patch.object(geocoding, "fetch") as fetch:
            self.assertEqual(geocoding.geocode_missing([event()]), {"geocoded": 1})
        fetch.assert_not_called()
        self.store(query, [], "2020-01-01T00:00:00+00:00")
        with mock.patch.object(geocoding, "fetch", return_value=[street]) as fetch:
            self.assertEqual(geocoding.geocode_missing([event()]), {"geocoded": 1})
        fetch.assert_called_once()

    def test_versioned_points_pin_without_lookup(self, _sleep):
        query = geocoding.query_for(event())
        self.points.write_text(json.dumps({"version": 1, "points": {query: {"latitude": 50.6, "longitude": 7.05}}}))
        events = [event()]
        with mock.patch.object(geocoding, "fetch") as fetch:
            self.assertEqual(geocoding.geocode_missing(events), {"geocoded": 1})
        fetch.assert_not_called()
        self.assertEqual((events[0].venue_latitude, events[0].venue_longitude), (50.6, 7.05))

    def test_backfill_stores_pins_from_seed_and_keeps_existing_ones(self, _sleep):
        kept = {"latitude": 50.7, "longitude": 7.1, "checkedAt": "2026-01-01"}
        self.points.write_text(json.dumps({"version": 1, "points": {"Alt, Deutschland": kept}}))
        feed = self.points.with_name("feed.json")
        feed.write_text(json.dumps({"events": [
            make_event(city="Meckenheim", venue="Merler Winkel", venue_address="Merler Winkel, 53340 Meckenheim"),
        ]}))
        seed = self.points.with_name("seed.json")
        query = geocoding.query_for(event())
        seed.write_text(json.dumps({"queries": {query: {
            "fetchedAt": "2026-09-28T00:00:00+00:00",
            "results": [result("Merler Winkel", category="highway", kind="residential")],
        }}}))
        with mock.patch.object(geocoding, "fetch") as fetch:
            outcomes = geocoding.backfill([feed], seed)
        fetch.assert_not_called()
        self.assertEqual(outcomes["geocoded"], 1)
        points = json.loads(self.points.read_text())["points"]
        self.assertEqual(points["Alt, Deutschland"], kept)
        self.assertEqual((points[query]["latitude"], points[query]["longitude"]), (50.63, 7.02))

    def test_pins_event_and_serves_repeat_addresses_from_cache(self, _sleep):
        street = result("Merler Winkel", category="highway", kind="residential")
        events = [event(), event()]
        with mock.patch.object(geocoding, "fetch", return_value=[street]) as fetch:
            outcomes = geocoding.geocode_missing(events)
            geocoding.geocode_missing([event()])
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(outcomes, {"geocoded": 2})
        self.assertEqual((events[0].venue_latitude, events[0].venue_longitude), (50.63, 7.02))
        self.assertEqual(events[0].location_confidence, "exact")
        self.assertEqual(events[0].location_source, "geocoded_address")

    def test_service_failure_stops_lookups_for_the_run(self, _sleep):
        events = [event(), event(address="Merler Straße 12, 53340 Meckenheim")]
        with mock.patch.object(geocoding, "fetch", side_effect=urllib.error.URLError("429")) as fetch:
            outcomes = geocoding.geocode_missing(events)
        self.assertEqual(fetch.call_count, geocoding.TRANSIENT_RETRY_ATTEMPTS)
        self.assertEqual(outcomes, {"failed": 1, "deferred": 1})
        self.assertIsNone(events[0].venue_latitude)

    def test_venue_name_pins_when_the_address_cannot(self, _sleep):
        library = event(LIBRARY, "Klosterweg 3, 53340 Meckenheim")
        responses = {
            "Klosterweg 3, 53340 Meckenheim, Deutschland": [],
            "Bücherei am Klosterhof, Meckenheim, Deutschland": [place(LIBRARY, postcode="53340")],
        }
        events = [library]
        matched: dict = {}
        with mock.patch.object(geocoding, "fetch", side_effect=responses.__getitem__) as fetch:
            self.assertEqual(geocoding.geocode_missing(events, matched=matched), {"geocoded_venue": 1})
        self.assertEqual(fetch.call_count, 2)
        self.assertEqual((events[0].venue_latitude, events[0].venue_longitude), (50.631, 7.021))
        self.assertEqual((events[0].location_confidence, events[0].location_source), ("exact", "geocoded_venue"))
        self.assertEqual(matched, {"Bücherei am Klosterhof, Meckenheim, Deutschland": {
            "latitude": 50.631, "longitude": 7.021, "method": "venue",
        }})

    def test_versioned_venue_pin_only_serves_the_venue_lookup(self, _sleep):
        query = "Bücherei am Klosterhof, Meckenheim, Deutschland"
        self.points.write_text(json.dumps({"version": 1, "points": {
            query: {"latitude": 50.6, "longitude": 7.05, "method": "venue"},
        }}))
        events = [event(LIBRARY, "")]
        with mock.patch.object(geocoding, "fetch") as fetch:
            self.assertEqual(geocoding.geocode_missing(events), {"geocoded_venue": 1})
        fetch.assert_not_called()
        self.points.write_text(json.dumps({"version": 1, "rejectedVenues": {LIBRARY: "wrong building"}, "points": {
            query: {"latitude": 50.6, "longitude": 7.05, "method": "venue"},
        }}))
        # A rejected name keeps only the street rule, which pins no library.
        with mock.patch.object(geocoding, "fetch", return_value=[place(LIBRARY)]):
            self.assertEqual(geocoding.geocode_missing([event(LIBRARY, "")]), {"unmatched": 1})

    def test_venue_pin_does_not_override_the_source_address(self, _sleep):
        library = event(LIBRARY, "Merler Straße 12, 53340 Meckenheim")
        self.points.write_text(json.dumps({"version": 1, "points": {
            "Bücherei am Klosterhof, Meckenheim, Deutschland": {"latitude": 50.6, "longitude": 7.05, "method": "venue"},
        }}))
        events = [library]
        with mock.patch.object(geocoding, "fetch", return_value=[result("Merler Straße", "12")]) as fetch:
            self.assertEqual(geocoding.geocode_missing(events), {"geocoded": 1})
        fetch.assert_called_once_with("Merler Straße 12, 53340 Meckenheim, Deutschland")
        self.assertEqual((events[0].venue_latitude, events[0].location_source), (50.63, "geocoded_address"))
        # When the address misses, the stored venue pin answers without another request.
        with mock.patch.object(geocoding, "fetch") as fetch:
            self.store("Merler Straße 12, 53340 Meckenheim, Deutschland", [], "2026-09-30T00:00:00+00:00")
            events = [event(LIBRARY, "Merler Straße 12, 53340 Meckenheim")]
            self.assertEqual(geocoding.geocode_missing(events), {"geocoded_venue": 1})
        fetch.assert_not_called()
        self.assertEqual(events[0].venue_latitude, 50.6)

    def test_earliest_events_are_looked_up_first(self, _sleep):
        late = event(address="Merler Straße 12, 53340 Meckenheim", date="2026-06-20", start_date="2026-06-20", end_date="2026-06-20")
        soon = event(date="2026-06-10", start_date="2026-06-10", end_date="2026-06-10")
        events = [late, soon]
        with mock.patch.object(geocoding, "fetch", side_effect=urllib.error.URLError("429")) as fetch:
            self.assertEqual(geocoding.geocode_missing(events), {"failed": 1, "deferred": 1})
        self.assertEqual(fetch.call_args.args[0], geocoding.query_for(soon))

    def test_backfill_keeps_rejected_venues_and_marks_venue_pins(self, _sleep):
        self.points.write_text(json.dumps({"version": 1, "rejectedVenues": {"Online": "no place"}, "points": {}}))
        feed = self.points.with_name("feed.json")
        feed.write_text(json.dumps({"events": [make_event(city="Meckenheim", venue=LIBRARY)]}))
        seed = self.points.with_name("seed.json")
        query = "Bücherei am Klosterhof, Meckenheim, Deutschland"
        seed.write_text(json.dumps({"queries": {query: {"fetchedAt": "2026-09-28T00:00:00+00:00", "results": [place(LIBRARY)]}}}))
        with mock.patch.object(geocoding, "fetch") as fetch:
            outcomes = geocoding.backfill([feed], seed)
        fetch.assert_not_called()
        self.assertEqual(outcomes["geocoded_venue"], 1)
        payload = json.loads(self.points.read_text())
        self.assertEqual(payload["rejectedVenues"], {"Online": "no place"})
        self.assertEqual(payload["points"][query]["method"], "venue")

    def test_disabled_by_environment(self, _sleep):
        with mock.patch.dict(os.environ, {"NRW_EVENTS_GEOCODING": "0"}), \
                mock.patch.object(geocoding, "fetch") as fetch:
            self.assertEqual(geocoding.geocode_missing([event()]), {})
        fetch.assert_not_called()


class SourceCoordinateTests(unittest.TestCase):
    def setUp(self):
        patch_window(self, datetime(2026, 10, 1), datetime(2026, 10, 31))

    def build(self, venue):
        raw = common.make_event(
            "Repair Café", datetime(2026, 10, 3, 14), None, venue, "Bonn", "Reparieren statt wegwerfen.",
            "https://example.test/repair", "Repair Cafés", "Workshop", coords=(50.7201, 7.1302),
        )
        return validate_event(raw)

    def test_source_point_is_published_without_a_registry_point(self):
        event = self.build("Unregistriertes Nachbarschaftszentrum")
        self.assertEqual((event.venue_latitude, event.venue_longitude), (50.7201, 7.1302))
        self.assertEqual((event.location_confidence, event.location_source), ("exact", "source_coordinates"))

    def test_registry_point_outranks_the_source_point(self):
        event = self.build("Brückenforum")
        self.assertIsNotNone(event.venue_latitude)
        self.assertNotEqual((event.venue_latitude, event.venue_longitude), (50.7201, 7.1302))
        self.assertEqual(event.location_source, "venue_registry")


if __name__ == "__main__":
    unittest.main()
