"""September 2026 venue evidence: site points, not guessed city centroids."""
import json
import unittest
from pathlib import Path

from nrw_events.normalization import resolve_venue
from nrw_events.validation import canonicalize_event

# Exact production labels/address spellings exercise the canonical join, not
# merely a convenient alias. Coordinates independently checked against Places/OSM.
CASES = (
    ("Muffendorfer Hauptstraße", "Bonn", "", 50.671572, 7.1603755),
    # KiöR Bonn's Europa-Säule map point on Europaplatz, not Rochusplatz park.
    ("Europaplatz an der Rochusstraße", "Bonn-Duisdorf", "", 50.71627008, 7.05092157),
    ("Markt Bonn", "Bonn", "Markt 53111 Bonn", 50.7353141, 7.1018176),
    ("Park am Kettelerplatz", "Dransdorf", "", 50.7379347, 7.0487661),
    ("Treffpunkt: Bonn-Information", "Bonn", "Windeckstraße 1, 53111 Bonn", 50.7345333, 7.0980444),
    ("Kirchplatz Hersel", "Bornheim", "Rheinstraße 204, 53332 Bornheim", 50.7742356, 7.0447758),
    ("Dorfplatz Buschdorf", "Bonn", "", 50.7578225, 7.0529762),
    ("Flohmarkt Roller Euskirchen", "Euskirchen", "Gottfried-Schenker-Str. 8", 50.6481055, 6.8027497),
    ("Ziepchensplatz, Bad Honnef-Rhöndorf", "Bad Honnef", "Löwenburgstr. 21, 53604 Bad Honnef-Rhöndorf", 50.6593944, 7.2129747),
    ("Tierheim Bonn", "Bonn", "Lambareneweg 2, 53119 Bonn", 50.7389298, 7.0720115),
    ("Brückenforum GmbH", "Bonn", "", 50.7387911, 7.1158409),
    ("Rheinufer Beuel, China-Schiff bis Bahnhöfchen", "Bonn-Beuel", "", 50.7376622, 7.1133741),
)


class SeptemberVenueLocationTests(unittest.TestCase):
    def test_production_labels_canonicalize_to_exact_site_points(self):
        for venue, city, address, latitude, longitude in CASES:
            with self.subTest(venue=venue):
                result = canonicalize_event({
                    "title": "Testtermin", "source": "Test", "start_date": "2026-09-06",
                    "end_date": "2026-09-06", "city": city, "venue": venue,
                    "venue_address": address, "link": "https://example.test/event",
                    "score": 1.0, "distance_km": 0,
                })
                self.assertAlmostEqual(result["venue_latitude"] or 0, latitude)
                self.assertAlmostEqual(result["venue_longitude"] or 0, longitude)
                self.assertEqual(result["location_confidence"], "exact")

    def test_brueckenforum_company_alias_has_canonical_identity(self):
        self.assertEqual(resolve_venue("Brückenforum GmbH", "Bonn").venue_id, "brueckenforum-bonn")
        self.assertNotEqual(resolve_venue("Brückenforum GmbH", "Bonn").venue_longitude, 7.11902)

    def test_city_scoping_and_multi_site_labels_do_not_acquire_false_points(self):
        for venue, city in (
            ("Beuel-Zentrum", "Beuel"),
            ("Rösrath-Mitte", "Rösrath"),

            ("Markt Bonn", "Siegburg"),
            ("Dorfplatz Buschdorf", "Bornheim"),
            ("Europaplatz an der Rochusstraße", "Bad Münstereifel"),
            ("Tierheim Bonn", "Köln"),
        ):

            with self.subTest(venue=venue, city=city):
                self.assertIsNone(resolve_venue(venue, city).venue_latitude)

    def test_dransdorf_park_supports_canonical_bonn_locality_too(self):
        self.assertAlmostEqual(resolve_venue("Park am Kettelerplatz", "Bonn-Dransdorf").venue_latitude or 0, 50.7379347)

    def test_named_multi_site_components_resolve_without_broad_city_aliases(self):
        for venue, city, latitude, longitude in (
            ("Möhneplatz", "Bonn-Beuel", 50.7393914, 7.1198711),
            ("Schützenplatz", "Rösrath", 50.8943563, 7.1828993),
        ):
            with self.subTest(venue=venue):
                point = resolve_venue(venue, city)
                self.assertAlmostEqual(point.venue_latitude or 0, latitude)
                self.assertAlmostEqual(point.venue_longitude or 0, longitude)
        self.assertIsNone(resolve_venue("Schützenplatz", "Bonn").venue_latitude)

    def test_cityless_schuetzenplatz_stays_unresolved(self):
        event = canonicalize_event({
            "title": "Testtermin", "source": "Test", "start_date": "2026-09-06",
            "end_date": "2026-09-06", "city": "", "venue": "Schützenplatz",
            "link": "https://example.test/event", "score": 1.0, "distance_km": 0,
        })
        self.assertIsNone(event["venue_latitude"])
        self.assertIsNone(event["venue_longitude"])
        self.assertNotEqual(event["location_confidence"], "exact")

    def test_other_or_unspecified_beuel_river_sections_stay_unresolved(self):
        for venue in ("Rheinufer Beuel, südlich Südbrücke", "Rheinufer Beuel"):
            with self.subTest(venue=venue):
                point = resolve_venue(venue, "Bonn")
                self.assertIsNone(point.venue_latitude)
                self.assertIsNone(point.venue_longitude)

    def test_reviewed_proposals_keep_current_per_entry_check_date(self):
        root = Path(__file__).resolve().parents[1]
        locations = json.loads((root / "scripts/nrw_events/verified_venue_locations.json").read_text())["locations"]
        for venue in ("Tierheim Bonn", "Markt Bonn", "Ziepchensplatz, Bad Honnef-Rhöndorf"):
            item = next(item for item in locations if item["venue"] == venue)
            self.assertEqual(item["checkedAt"], "2026-09-05")
            self.assertTrue(item["evidence"]["eventUrl"])
            self.assertTrue(item["evidence"]["mapUrl"])


if __name__ == "__main__":
    unittest.main()
