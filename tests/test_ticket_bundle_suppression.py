import unittest
from datetime import datetime
from pathlib import Path

from nrw_events import deduplication
from nrw_events.ical import parse_ical
from nrw_events.sources import harmonie
from nrw_events.validation import validate_event

from tests.helpers import patch_window

FIXTURE = Path(__file__).parent / "fixtures" / "harmonie-bonn" / "rockpalast-2026.ics"
FEED = "https://www.harmonie-bonn.de/?post_type=tribe_events&ical=1"


def _night(title, start_date, *, end_date="", source_id="harmonie-bonn", venue_id="harmonie-bonn"):
    return {
        "title": title, "start_date": start_date, "end_date": end_date or start_date,
        "source": "Harmonie Bonn", "source_id": source_id, "venue": "Harmonie Bonn",
        "venue_id": venue_id, "city": "Bonn", "status": "scheduled",
    }


class RockpalastFixtureTests(unittest.TestCase):
    def setUp(self):
        patch_window(self, datetime(2026, 10, 1), datetime(2026, 10, 31))
        raw = FIXTURE.read_text(encoding="utf-8")
        parsed = harmonie._fill_calendar_venues(parse_ical(raw, FEED, "Harmonie Bonn", "Bonn", "concert", 1.0))
        self.events = [validate_event(event) for event in parsed]

    def test_upstream_pass_is_dropped_and_nights_publish_as_concerts(self):
        published = deduplication.suppress_redundant_series_umbrellas(self.events)
        self.assertEqual(
            [(event["title"], event["start_date"], event["time"], event["category_key"]) for event in published],
            [
                ("Henge + Kombynat Robotron – WDR Rockpalast-Festival – TV-Aufzeichnung", "2026-10-08", "19:15", "concert"),
                ("Wrest + Jerry Leger – WDR Rockpalast-Festival – TV-Aufzeichnung", "2026-10-09", "19:15", "concert"),
                ("Marlo Grosshardt + Crimson Bloom – WDR Rockpalast-Festival – TV-Aufzeichnung", "2026-10-10", "19:15", "concert"),
            ],
        )

    def test_pass_clock_is_the_upstream_value(self):
        # The feed itself says DTSTART 19:14 for the pass ("Beginn jeweils 19:15 Uhr" in its copy).
        bundle = next(event for event in self.events if "Tages-Karte" in event["title"])
        self.assertEqual(bundle["time"], "19:14–22:20")
        self.assertEqual(bundle["category_key"], "concert")


class TicketBundleSuppressionTests(unittest.TestCase):
    def test_keeps_a_pass_without_covered_single_nights(self):
        bundle = _night("Festivalpass Jazzfest", "2026-10-07", end_date="2026-10-10")
        other_source = _night("Band A", "2026-10-08", source_id="bonn-de-events")
        other_venue = _night("Band B", "2026-10-08", venue_id="pantheon-bonn")
        outside = _night("Band C", "2026-10-12")
        events = [bundle, other_source, other_venue, outside]
        self.assertEqual(deduplication.suppress_redundant_series_umbrellas(events), events)

    def test_keeps_single_day_or_non_bundle_runs(self):
        day_ticket = _night("2-Tages-Karte Probe", "2026-10-08")
        festival = _night("Rheinaue Festival", "2026-10-07", end_date="2026-10-10")
        night = _night("Band A", "2026-10-08")
        events = [day_ticket, festival, night]
        self.assertEqual(deduplication.suppress_redundant_series_umbrellas(events), events)

    def test_keeps_a_pass_when_the_covering_night_is_not_scheduled(self):
        for status in ("cancelled", "postponed"):
            with self.subTest(status=status):
                bundle = _night("Kombiticket Samstag + Sonntag", "2026-10-10", end_date="2026-10-11")
                night = _night("Band A", "2026-10-11")
                night["status"] = status
                events = [bundle, night]
                self.assertEqual(deduplication.suppress_redundant_series_umbrellas(events), events)

    def test_drops_kombiticket_covering_a_night(self):
        bundle = _night("Kombiticket Samstag + Sonntag", "2026-10-10", end_date="2026-10-11")
        night = _night("Band A", "2026-10-11")
        self.assertEqual(deduplication.suppress_redundant_series_umbrellas([bundle, night]), [night])


if __name__ == "__main__":
    unittest.main()
