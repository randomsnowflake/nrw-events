"""Optional detail attribution is endpoint evidence, not matching error prose."""
import time
import unittest
from collections import Counter
from unittest.mock import patch

from nrw_events import detail_enrichment, http
from nrw_events.health import EndpointOutcome, SourceFetchResult, SourceResult
from tests import test_source_outage as fixtures


class OptionalDetailAttributionTests(unittest.TestCase):
    def import_optional_failures(self, *, base_failure=False):
        case = fixtures.SourceOutageTests()
        with fixtures.make_runner_env() as env:
            case.run_day(env, -1, case.sources(lambda: [fixtures.event()]))

            def calendar():
                rows = [fixtures.event(title=f"Fresh concert {i}",
                                       link=f"https://calendar.bonn.de/event/{i}") for i in (1, 2)]

                def fail(url, *args, **kwargs):
                    http._record_endpoint(url, error_type="TimeoutError", error="upstream unavailable")
                    raise TimeoutError("upstream unavailable")

                with patch.object(fixtures.runner.common, "fetch_detail_url", side_effect=fail):
                    enriched = detail_enrichment._enrich_batch(
                        rows, "offline-attribution", time.monotonic() + 60,
                        {id(row) for row in rows}, Counter(row["link"] for row in rows))
                if base_failure:
                    return SourceFetchResult.partial(enriched, endpoints=(EndpointOutcome(
                        "https://calendar.bonn.de/feed", error_type="TimeoutError",
                        error="upstream unavailable"),))
                return enriched

            return case.run_day(env, 0, case.sources(calendar))

    def test_identical_optional_timeouts_have_exact_endpoint_evidence(self):
        result, payload = self.import_optional_failures()
        endpoints = payload["source_results"]["Calendar"]["endpoints"]
        for url in ("https://calendar.bonn.de/event/1", "https://calendar.bonn.de/event/2"):
            endpoint = endpoints[url]
            self.assertIs(endpoint.get("optional_detail"), True)
            self.assertEqual(endpoint["error_type"], "TimeoutError")
            self.assertEqual(endpoint["attempts"], 1)
        self.assertEqual(payload["retained_sources"], [])
        self.assertNotIn("Concert", {event.title for event in result.events})

    def test_same_message_base_failure_remains_an_outage(self):
        result, payload = self.import_optional_failures(base_failure=True)
        self.assertIn("Concert", {event.title for event in result.events})
        self.assertEqual([row["source_id"] for row in payload["retained_sources"]], ["calendar"])
        base = payload["source_results"]["Calendar"]["endpoints"]["https://calendar.bonn.de/feed"]
        self.assertIsNot(base.get("optional_detail"), True)

    def test_new_observation_clears_previous_optional_role(self):
        for observation in ({"status": 200}, {"error": "new base failure", "error_type": "TimeoutError"}):
            with self.subTest(observation=observation):
                result = SourceResult("Calendar")
                result.endpoint("https://example.test/shared", error="old detail failure", error_type="TimeoutError")
                result.endpoints["https://example.test/shared"]["optional_detail"] = True
                result.endpoint("https://example.test/shared", **observation)
                self.assertNotIn("optional_detail", result.endpoints["https://example.test/shared"])
