import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import unicodedata
from unittest import mock

from scripts import audit_venue_locations, enrich_verified_venue_locations
from nrw_events.normalization import (
    VENUE_REGISTRY,
    VERIFIED_VENUE_LOCATIONS,
    resolve_venue,
)
from nrw_events.validation import canonicalize_event


def event(**overrides):
    candidate = {
        "title": "Testtermin",
        "source": "Test",
        "start_date": "2026-08-05",
        "end_date": "2026-08-05",
        "city": "Bonn",
        "venue": "",
        "description": "",
        "price": "",
        "link": "https://example.test/event",
        "score": 1.0,
        "distance_km": 0,
    }
    candidate.update(overrides)
    return candidate


class VenueRegistryTests(unittest.TestCase):
    def test_nfd_venue_name_resolves_like_its_nfc_equivalent(self):
        nfd_name = unicodedata.normalize("NFD", "Werkstattbühne")

        venue = resolve_venue(nfd_name, "Bonn")

        self.assertEqual(venue.venue, "Werkstattbühne")
        self.assertEqual(venue.venue_id, "werkstattbuehne-bonn")
        self.assertAlmostEqual(venue.venue_latitude or 0, 50.7363281468)

    def test_registry_rebuild_is_diff_free(self):
        root = Path(__file__).resolve().parents[1]
        completed = subprocess.run(
            [
                sys.executable,
                str(root / "scripts/build_verified_venue_locations.py"),
                str(root / "scripts/venue_geocoding_proposals.json"),
                "--registry", str(root / "scripts/nrw_events/verified_venue_locations.json"),
                "--decisions", str(root / "scripts/venue_geocoding_audit.json"),
                "--check",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_registry_check_does_not_trust_existing_checked_at(self):
        root = Path(__file__).resolve().parents[1]
        registry = json.loads(
            (root / "scripts/nrw_events/verified_venue_locations.json").read_text(encoding="utf-8")
        )
        registry["locations"][0]["checkedAt"] = "1999-01-01"
        with tempfile.TemporaryDirectory() as directory:
            candidate = Path(directory) / "registry.json"
            candidate.write_text(json.dumps(registry), encoding="utf-8")
            completed = subprocess.run(
                [
                    sys.executable,
                    str(root / "scripts/build_verified_venue_locations.py"),
                    str(root / "scripts/venue_geocoding_proposals.json"),
                    "--registry", str(candidate),
                    "--decisions", str(Path(directory) / "decisions.json"),
                    "--check",
                ],
                check=False,
                capture_output=True,
                text=True,
            )

        self.assertNotEqual(completed.returncode, 0)

    def test_audit_generator_marks_only_unresolved_venues_as_candidates(self):
        payload = audit_venue_locations.audit_payload([
            {"title": "Known", "city": "Bonn", "venue": "TiK Theater Im Keller", "link": "https://example.test/1"},
            {"title": "Unknown", "city": "Bonn", "venue": "Invented Test Hall", "link": "https://example.test/2"},
        ])
        classifications = {item["venue"]: item["classification"] for item in payload["candidates"]}
        self.assertEqual(classifications["TiK Theater Im Keller"], "verified")
        self.assertEqual(classifications["Invented Test Hall"], "candidate")

    def test_cityless_verified_lookup_requires_a_registrywide_unique_name(self):
        self.assertIsNone(resolve_venue("Katharinenhof", "").venue_latitude)
        self.assertIsNotNone(resolve_venue("Katharinenhof", "Bonn").venue_latitude)

    def test_snapshot_enrichment_reports_verified_source_and_reapplies_radius(self):
        event = {"venue": "Mehrzweckhalle Lantershofen", "city": "Grafschaft", "location_confidence": "unresolved"}
        with tempfile.TemporaryDirectory() as directory:
            feed = Path(directory) / "feed.json"
            feed.write_text(json.dumps({"events": [event]}), encoding="utf-8")
            with mock.patch.object(sys, "argv", ["enrich", str(feed)]):
                enrich_verified_venue_locations.main()
            enriched = json.loads(feed.read_text(encoding="utf-8"))["events"][0]
            self.assertEqual(enriched["location_source"], "verified_venue_locations")

            feed.write_text(json.dumps({"events": [event]}), encoding="utf-8")
            with mock.patch.object(enrich_verified_venue_locations.common, "MAX_RADIUS_KM", 0.01), \
                    mock.patch.object(sys, "argv", ["enrich", str(feed)]):
                enrich_verified_venue_locations.main()
            self.assertEqual(json.loads(feed.read_text(encoding="utf-8"))["events"], [])

    def test_registry_ids_and_aliases_are_unique(self):
        ids = [record.id for record in VENUE_REGISTRY]
        aliases = [
            alias.casefold()
            for record in VENUE_REGISTRY
            for alias in (record.display_name, *record.aliases)
        ]

        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(len(aliases), len(set(aliases)))

    def test_verified_map_locations_are_unique_per_city_and_name(self):
        keys = [
            (record.city.casefold(), record.venue.casefold())
            for record in VERIFIED_VENUE_LOCATIONS
        ]

        self.assertEqual(len(keys), len(set(keys)))

    def test_official_alias_replaces_address_blob_with_structured_fields(self):
        canonical = canonicalize_event(event(
            venue="kleines theater, Koblenzer Str. 78, Bonn, 53177, Deutschland",
        ))

        self.assertEqual(canonical.venue, "Kleines Theater Bad Godesberg")
        self.assertEqual(canonical.venue_id, "kleines-theater-bad-godesberg")
        self.assertEqual(canonical.venue_address, "Koblenzer Straße 78, 53177 Bonn")
        self.assertEqual(canonical.venue_district, "Bonn-Bad Godesberg")
        self.assertEqual(canonical.venue_type, "theater")
        self.assertAlmostEqual(canonical.venue_latitude or 0, 50.6808563311)
        self.assertEqual(canonical.location_source, "venue_registry")

    def test_unknown_address_is_separated_without_inventing_an_identity(self):
        canonical = canonicalize_event(event(
            city="Much",
            venue="Jugendzentrum Much, Klosterstraße 4a, 53804 Much",
        ))

        self.assertEqual(canonical.venue, "Jugendzentrum Much")
        self.assertEqual(canonical.venue_address, "Klosterstraße 4a, 53804 Much")
        self.assertEqual(canonical.venue_id, "")
        self.assertEqual(canonical.venue_type, "")

    def test_city_placeholder_is_removed_when_an_address_follows(self):
        resolved = resolve_venue(
            "Bad Honnef, Quellenstraße 2, 53604 Bad Honnef", "Bad Honnef")

        self.assertEqual(resolved.venue, "")
        self.assertEqual(
            resolved.venue_address, "Quellenstraße 2, 53604 Bad Honnef")

    def test_postcode_and_trailing_city_are_coalesced(self):
        resolved = resolve_venue(
            "Beethovenhalle Bonn, Wachsbleiche 16, Bonn, 53111", "Bonn")

        self.assertEqual(resolved.venue, "Beethovenhalle Bonn")
        self.assertEqual(resolved.venue_address, "Wachsbleiche 16, 53111 Bonn")

    def test_prestructured_unknown_address_survives_validation(self):
        canonical = canonicalize_event(event(
            venue="Jugendzentrum Much",
            city="Much",
            venue_address="Klosterstraße 4a, 53804 Much",
        ))

        self.assertEqual(canonical.venue, "Jugendzentrum Much")
        self.assertEqual(canonical.venue_address, "Klosterstraße 4a, 53804 Much")

    def test_city_placeholder_is_empty_but_registered_park_survives(self):
        self.assertEqual(resolve_venue("Brühl", "Brühl").venue, "")
        rheinaue = resolve_venue("Rheinaue", "Rheinaue")
        self.assertEqual(rheinaue.venue, "Rheinaue")
        self.assertEqual(rheinaue.venue_id, "rheinaue-bonn")

    def test_bikini_beach_has_primary_source_address_and_coordinates(self):
        venue = resolve_venue("Bikini Beach", "Bonn")

        self.assertEqual(venue.venue_id, "bikini-beach-bonn")
        self.assertEqual(venue.venue_address, "Karl-Duwe-Straße 1, 53227 Bonn")
        self.assertAlmostEqual(venue.venue_latitude or 0, 50.7155999)
        self.assertAlmostEqual(venue.venue_longitude or 0, 7.1566788)

    def test_verified_location_adds_map_point_without_public_venue_id(self):
        venue = resolve_venue("Mehrzweckhalle Lantershofen", "Grafschaft")

        self.assertEqual(venue.venue, "Mehrzweckhalle Lantershofen")
        self.assertEqual(venue.venue_id, "")
        self.assertEqual(
            venue.venue_address,
            "Graf-Blankard-Str. 25, 53501 Grafschaft-Lantershofen",
        )
        self.assertAlmostEqual(venue.venue_latitude or 0, 50.55506)
        self.assertAlmostEqual(venue.venue_longitude or 0, 7.1031)

    def test_verified_location_is_scoped_to_its_municipality(self):
        venue = resolve_venue("Mehrzweckhalle Lantershofen", "Köln")

        self.assertIsNone(venue.venue_latitude)

    def test_verified_location_can_complete_a_canonical_venue(self):
        venue = resolve_venue("Museum Koenig Bonn", "Bonn")

        self.assertEqual(venue.venue_id, "museum-koenig-bonn")
        self.assertAlmostEqual(venue.venue_latitude or 0, 50.7221437)
        self.assertAlmostEqual(venue.venue_longitude or 0, 7.1134825)

    def test_unknown_casing_is_preserved_instead_of_title_cased(self):
        resolution = resolve_venue("brauhaus im stiefel", "Bonn")

        self.assertEqual(resolution.venue, "brauhaus im stiefel")

    def test_explicit_registered_id_outranks_conflicting_source_text(self):
        resolution = resolve_venue(
            "temporary source label",
            "Bonn",
            explicit_id="bundeskunsthalle",
        )

        self.assertEqual(resolution.venue, "Bundeskunsthalle")
        self.assertEqual(resolution.venue_id, "bundeskunsthalle")

    def test_branch_addresses_remain_available_to_legacy_identity_rules(self):
        canonical = canonicalize_event(event(
            title="Flohmarkt am HIT-Markt",
            city="Troisdorf",
            venue="HIT-Markt, Spicher Straße 101, 53844 Troisdorf",
            category="Flohmarkt",
        ))

        self.assertEqual(canonical.venue, "HIT-Markt")
        self.assertEqual(canonical.venue_address, "Spicher Straße 101, 53844 Troisdorf")
        self.assertEqual(canonical.venue_id, "hit-markt-rotter-see")

    def test_generic_verified_venue_does_not_override_another_branch_address(self):
        canonical = canonicalize_event(event(
            title="Flohmarkt, HIT Roitzheimer Str. 117, 53879 Euskirchen",
            city="Euskirchen",
            venue="Flohmarkt",
            venue_address="HIT Roitzheimer Str. 117, 53879 Euskirchen",
            category="Flohmarkt",
        ))

        self.assertEqual(canonical.venue, "Flohmarkt")
        self.assertEqual(
            canonical.venue_address,
            "HIT Roitzheimer Str. 117, 53879 Euskirchen",
        )
        self.assertIsNone(canonical.venue_latitude)
        self.assertIsNone(canonical.venue_longitude)

    def test_generic_verified_venue_still_resolves_its_matching_branch_address(self):
        canonical = canonicalize_event(event(
            title="Flohmarkt, HIT Georgstr. 22, 53879 Euskirchen",
            city="Euskirchen",
            venue="Flohmarkt",
            venue_address="HIT Georgstr. 22, 53879 Euskirchen",
            category="Flohmarkt",
        ))

        self.assertEqual(
            canonical.venue_address,
            "HIT Georgstr. 22, 53879 Euskirchen",
        )
        self.assertAlmostEqual(canonical.venue_latitude or 0, 50.6539617)
        self.assertAlmostEqual(canonical.venue_longitude or 0, 6.7704394)


if __name__ == "__main__":
    unittest.main()
