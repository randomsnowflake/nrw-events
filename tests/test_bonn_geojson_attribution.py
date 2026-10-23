"""Bonn venue-coordinate outages stay separate from its fresh event calendar."""
import json
import unittest
from email.message import Message
from unittest.mock import Mock, patch

from nrw_events import http, run_state
from nrw_events.health import SourceResult
from nrw_events.sources import bonn

from tests import test_source_outage as fixtures

GEOJSON_SOURCE_ID = "bonn-venue-geojson"
ITEM = {
    "title": "Fresh concert",
    "description": "Live-Musik im Theater.",
    "category": ["Musik/Konzert"],
    "startDate": "2026-06-25 19:00:00",
    "endDate": "2026-06-25 21:00:00",
    "locationName": "Testtheater",
    "locationAddress": "Teststraße 1, 53111 Bonn",
    "link": "https://www.bonn.de/fresh-concert.php",
    "hasStartTime": True,
    "hasEndTime": True,
}
GEOJSON = {"features": [{
    "properties": {"name": "Testtheater"},
    "geometry": {"coordinates": [7.1, 50.73]},
}]}


class BonnGeojsonAttributionTests(unittest.TestCase):
    def urlopen(self, request, **kwargs):
        url = request.full_url
        self.assertIn(url, bonn._VENUE_GEOJSON_URLS)
        if url in self.failed_urls:
            raise TimeoutError("The read operation timed out")
        response = Mock(status=200)
        response.headers = Message()
        response.headers["Content-Type"] = "application/geo+json"
        response.read.return_value = json.dumps(GEOJSON).encode()
        response.geturl.return_value = url
        return response

    def setUp(self):
        self.failed_urls = set(bonn._VENUE_GEOJSON_URLS)
        for patcher in (
            patch.object(http.urllib.request, "urlopen", side_effect=self.urlopen),
            patch.object(http, "_retry_delay", return_value=60),
            patch.object(http, "_throttle_before_request"),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def calendar(self, *, base_failure=False):
        if base_failure:
            http._record_endpoint(bonn._EVENTS_JSON_URL, error_type="TimeoutError",
                                  error="The read operation timed out")
            run_state.log_source_error("Bonn.de Events", TimeoutError("calendar unavailable"))
        with patch.object(bonn, "_venue_points_cache", None):
            return bonn.fetch_events_json(include_fallbacks=False, enrich_details=False, items=[ITEM])

    def import_days(self, *, base_failure=False, days=(0,)):
        case = fixtures.SourceOutageTests()
        with fixtures.make_runner_env() as env:
            sources = lambda fetch: {
                "Bonn.de Events": fetch,
                "Healthy": lambda: [fixtures.event("Healthy", "Independent play")],
            }
            case.run_day(env, -1, sources(lambda: [fixtures.event("Bonn.de Events", "Old concert")]))
            for day in days:
                result, payload = case.run_day(
                    env, day, sources(lambda: self.calendar(base_failure=base_failure)))
            return result, payload

    def test_retry_budget_failure_tracks_only_geojson_and_keeps_fresh_events(self):
        result, payload = self.import_days(days=(0, 1))
        self.assertEqual([row["source_id"] for row in payload["retained_sources"]], [GEOJSON_SOURCE_ID])
        self.assertEqual(payload["retained_sources"][0]["consecutive_failures"], 2)
        self.assertEqual(payload["retained_event_count"], 0)
        self.assertIn("Fresh concert", {row.title for row in result.events})
        self.assertNotIn("Old concert", {row.title for row in result.events})
        source = payload["source_results"]["Bonn.de Events"]
        self.assertEqual(source["status"], "degraded")
        self.assertEqual({warning.get("source_id") for warning in source["warnings"]}, {GEOJSON_SOURCE_ID})
        self.assertIn("before retry", source["warnings"][0]["error"])
        for url in bonn._VENUE_GEOJSON_URLS:
            endpoint = source["endpoints"][url]
            self.assertEqual(endpoint.get("source_id"), GEOJSON_SOURCE_ID)
            self.assertEqual(endpoint["error"], "The read operation timed out")
            self.assertEqual(endpoint["attempts"], 1)

    def test_independent_calendar_failure_still_retains_calendar_events(self):
        result, payload = self.import_days(base_failure=True)
        self.assertEqual({row["source_id"] for row in payload["retained_sources"]},
                         {"bonn-de-events", GEOJSON_SOURCE_ID})
        self.assertIn("Old concert", {row.title for row in result.events})
        base = payload["source_results"]["Bonn.de Events"]["endpoints"][bonn._EVENTS_JSON_URL]
        self.assertNotEqual(base.get("source_id"), GEOJSON_SOURCE_ID)

    def test_previous_false_parent_outage_clears_and_geojson_recovery_resets_clock(self):
        case = fixtures.SourceOutageTests()
        sources = {
            "Bonn.de Events": self.calendar,
            "Healthy": lambda: [fixtures.event("Healthy", "Independent play")],
        }
        with fixtures.make_runner_env() as env:
            _, previous = case.run_day(env, 0, sources)
            previous["retained_sources"].append({
                **previous["retained_sources"][0],
                "source": "Bonn.de Events", "source_id": "bonn-de-events",
            })
            env.previous_path.write_text(json.dumps(previous))
            _, payload = case.run_day(env, 1, sources)
            self.assertEqual([row["source_id"] for row in payload["retained_sources"]], [GEOJSON_SOURCE_ID])
            self.assertEqual(payload["retained_sources"][0]["consecutive_failures"], 2)
            self.failed_urls = set()
            _, recovered = case.run_day(env, 2, sources)
            self.assertEqual(recovered["retained_sources"], [])
            self.failed_urls = set(bonn._VENUE_GEOJSON_URLS)
            _, outage = case.run_day(env, 3, sources)
            self.assertEqual(outage["retained_sources"][0]["consecutive_failures"], 1)
            self.assertNotEqual(outage["retained_sources"][0]["first_failure_at"],
                                previous["retained_sources"][0]["first_failure_at"])

    def test_one_healthy_layer_still_supplies_coordinates(self):
        self.failed_urls = {bonn._VENUE_GEOJSON_URLS[0]}
        result = SourceResult("Bonn.de Events")
        with patch.object(run_state._SOURCE_CONTEXT, "result", result, create=True), \
             patch.object(bonn, "_venue_points_cache", None):
            self.assertEqual(bonn._venue_points(), {"testtheater": (50.73, 7.1)})
        self.assertEqual(len(result.warnings), 1)
        for endpoint in result.endpoints.values():
            self.assertEqual(endpoint.get("source_id"), GEOJSON_SOURCE_ID)
            self.assertEqual(endpoint["attempts"], 1)

    def test_healthy_layers_clear_outage_and_preserve_coordinate_cache(self):
        self.failed_urls = set()
        result = SourceResult("Bonn.de Events")
        with patch.object(run_state._SOURCE_CONTEXT, "result", result, create=True), \
             patch.object(bonn, "_venue_points_cache", None):
            first = bonn._venue_points()
            self.assertIs(bonn._venue_points(), first)
        self.assertEqual(result.warnings, [])
        self.assertEqual(len(result.endpoints), 2)
        for endpoint in result.endpoints.values():
            self.assertEqual(endpoint.get("source_id"), GEOJSON_SOURCE_ID)
            self.assertEqual(endpoint["status"], 200)
            self.assertEqual(endpoint["attempts"], 1)
        _, payload = self.import_days()
        self.assertEqual(payload["retained_sources"], [])
