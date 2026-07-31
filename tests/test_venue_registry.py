import unittest

from nrw_events.normalization import VENUE_REGISTRY, resolve_venue
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
    def test_registry_ids_and_aliases_are_unique(self):
        ids = [record.id for record in VENUE_REGISTRY]
        aliases = [
            alias.casefold()
            for record in VENUE_REGISTRY
            for alias in (record.display_name, *record.aliases)
        ]

        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(len(aliases), len(set(aliases)))

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


if __name__ == "__main__":
    unittest.main()
