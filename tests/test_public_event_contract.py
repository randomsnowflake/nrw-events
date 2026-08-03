"""The published snapshot is a cross-repository contract.

veranstaltungen-bonn.de builds permanent event detail pages from these fields.
Dropping one, or renaming a registry venue id, silently breaks pages that are
already public — so both are asserted here rather than discovered downstream.
"""

import unittest
from datetime import datetime

from nrw_events import config, runner
from nrw_events.identity import duplicate_event_ids
from nrw_events.normalization import VENUE_REGISTRY
from nrw_events.observability import configure_logging
from nrw_events.runtime import EventWindow, RunContext

#: Every field the website is allowed to rely on. Adding one is fine; removing
#: or renaming one is a breaking change for a published URL.
PUBLIC_EVENT_FIELDS = frozenset({
    "event_id",
    "title", "description", "ai_summary", "link",
    "date", "start_date", "end_date", "start_at", "end_at",
    "time", "time_note", "all_day", "ongoing", "timezone", "status",
    "venue", "venue_id", "venue_address", "venue_district", "venue_type",
    "venue_latitude", "venue_longitude",
    "city", "distance_km", "location_confidence", "location_source",
    "price", "admission", "category", "category_key", "category_label",
    "source", "source_id", "score",
    "ranking_features", "priority_bonus",
    "cancelled_at", "cancellation_source", "replacement_start_date",
    "first_seen_at", "content_hash",
    "series_id", "series_title", "run_id",
})

#: The venue identities the website crosswalk maps onto its own canonical
#: venues. Renaming an entry here requires the matching website change in the
#: same review; the list exists so that requirement cannot be missed.
PUBLIC_VENUE_IDS = frozenset({
    "annaplatz-bad-honnef",
    "arithmeum-bonn",
    "arkadenhof-universitaet-bonn",
    "arp-museum-bahnhof-rolandseck",
    "bikini-beach-bonn",
    "bonn-nachbarschaftszentrum-brueser-berg",
    "botanische-gaerten-bonn",
    "brueckenforum-bonn",
    "bundeskunsthalle",
    "contra-kreis-theater",
    "die-werke-bonn",
    "euro-theater-central",
    "gop-variete-bonn",
    "haus-der-geschichte-bonn",
    "haus-der-jugend-bonn",
    "haus-der-springmaus",
    "interim-zentralbibliothek-koeln",
    "internationaler-club-bonn",
    "junges-theater-bonn",
    "kleines-theater-bad-godesberg",
    "kult41",
    "kulturzentrum-brotfabrik",
    "kulturzentrum-hardtberg",
    "kulturzentrum-tapetenfabrik",
    "kunstmuseum-bonn",
    "lvr-landesmuseum-bonn",
    "malentes-theaterpalast",
    "museum-august-macke-haus",
    "museum-koenig-bonn",
    "oper-bonn",
    "pantheon-theater",
    "repair-cafe-mva-bonn",
    "rex-lichtspieltheater",
    "rhein-sieg-forum",
    "rheinaue-bonn",
    "rheinbuehne-kabarett",
    "schauspielhaus-bad-godesberg",
    "selbstwerk-bonn",
    "stadthalle-remagen",
    "stadtmuseum-siegburg",
    "theater-im-ballsaal",
    "theater-im-keller-bonn",
    "theater-marabu",
    "werkstattbuehne-bonn",
})


def snapshot_for(*raw_events):
    canonical = tuple(runner.validate_event(raw) for raw in raw_events)
    context = RunContext(
        config.RuntimeConfig(),
        EventWindow(datetime(2026, 6, 8), datetime(2026, 6, 14)),
        "contract",
        configure_logging("contract", "ERROR", "", ""),
        clock=lambda: datetime(2026, 6, 8, 12),
    )
    return runner.build_snapshot(runner.ImportResult(canonical, {}, len(canonical), "healthy"), context)


def raw_event(**overrides):
    candidate = {
        "title": "Konzert im Kulturzentrum",
        "source": "Test",
        "source_id": "test",
        "start_date": "2026-06-09",
        "end_date": "2026-06-09",
        "time": "20:00",
        "city": "Bonn",
        "venue": "Kulturzentrum Brotfabrik",
        "description": "Ein Konzert.",
        "price": "12 €",
        "link": "https://example.test/konzert",
        "score": 2.0,
        "distance_km": 3.0,
    }
    candidate.update(overrides)
    return candidate


class PublicEventContractTests(unittest.TestCase):
    def test_snapshot_events_carry_every_contract_field(self):
        event = snapshot_for(raw_event()).events[0]
        self.assertEqual(set(), PUBLIC_EVENT_FIELDS - set(event))

    def test_registry_venue_facts_reach_the_snapshot(self):
        event = snapshot_for(raw_event()).events[0]

        self.assertEqual("kulturzentrum-brotfabrik", event["venue_id"])
        self.assertEqual("Kreuzstraße 16, 53225 Bonn", event["venue_address"])
        self.assertEqual("Bonn-Beuel", event["venue_district"])
        self.assertEqual("cultural_center", event["venue_type"])
        self.assertIsNotNone(event["venue_latitude"])
        self.assertIsNotNone(event["venue_longitude"])
        self.assertEqual("exact", event["location_confidence"])

    def test_every_snapshot_event_has_a_unique_stable_id(self):
        snapshot = snapshot_for(
            raw_event(),
            raw_event(title="Lesung", link="https://example.test/lesung"),
            raw_event(start_date="2026-06-10", end_date="2026-06-10"),
        )

        identifiers = [event["event_id"] for event in snapshot.events]
        self.assertEqual({}, duplicate_event_ids(snapshot.events))
        self.assertTrue(all(identifiers))

    def test_snapshot_ids_do_not_follow_the_score_ranking(self):
        low = raw_event(title="Leiser Termin", link="https://example.test/leise", score=0.5)
        high = raw_event(title="Lauter Termin", link="https://example.test/laut", score=9.0)

        forward = {event["title"]: event["event_id"] for event in snapshot_for(low, high).events}
        backward = {event["title"]: event["event_id"] for event in snapshot_for(high, low).events}
        self.assertEqual(forward, backward)

    def test_published_venue_ids_match_the_documented_crosswalk_contract(self):
        self.assertEqual(PUBLIC_VENUE_IDS, {record.id for record in VENUE_REGISTRY})


if __name__ == "__main__":
    unittest.main()
