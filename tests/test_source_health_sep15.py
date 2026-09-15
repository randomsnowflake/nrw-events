"""Regression shapes observed on first-party calendars on 2026-09-15."""
import json
import unittest
from datetime import datetime
from unittest.mock import patch
from urllib.error import HTTPError

from nrw_events import common, http, run_state
from nrw_events.health import SourceResult, SourceStatus
from nrw_events.sources import bonnlive, katharinenhof, regional_sitekit, regional_common
from tests.helpers import patch_window


BONNLIVE_EMPTY = '<div class="events_wrapper w-dyn-list"><div class="no_events w-dyn-empty"><div>Aktuell keine Events</div></div></div>'


def schema_event(name, start="2026-10-18T18:00+2:00"):
    return '<script type="application/ld+json">' + json.dumps({
        "@type": "Event", "name": name, "startDate": start,
        "location": [{"name": "Katharinenhof"}],
    }) + '</script>'


class SourceHealthRegressionTests(unittest.TestCase):
    def setUp(self):
        patch_window(self, datetime(2026, 9, 15), datetime(2026, 10, 12))

    def test_bonnlive_explicit_empty_is_healthy_but_broken_markup_is_not(self):
        for html, expected in [
            (BONNLIVE_EMPTY, False),
            ('<html>Aktuell keine Events</html>', True),
            ('<div class="events_wrapper w-dyn-list"></div>', True),
            (BONNLIVE_EMPTY + '<div role="listitem" class="collection-item w-dyn-item">broken date</div>', True),
        ]:
            with self.subTest(html=html), patch.object(common, "fetch_url", side_effect=[html, ""]), \
                 patch.object(common, "_record_endpoint") as record, \
                 patch.object(common, "log_source_error") as warning:
                self.assertEqual(bonnlive.fetch(), [])
                self.assertEqual(record.call_args.kwargs["parser_empty"], expected)
                self.assertEqual(warning.called, expected)

    def test_katharinenhof_valid_non_market_calendar_is_healthy(self):
        html = schema_event("Himmel un Ääd") + schema_event("Tach, Frau Walterscheidt", "2026-11-8T18:00+2:00")
        with patch.object(common, "fetch_url", return_value=html), \
             patch.object(common, "log_source_error") as warning:
            self.assertEqual(katharinenhof.fetch(), [])
            warning.assert_not_called()

    def test_katharinenhof_broken_contract_remains_actionable(self):
        for html in ["<html></html>", schema_event("Himmel un Ääd", "changed"),
                     schema_event("Flohmarkt", "changed"),
                     schema_event("Himmel un Ääd") + schema_event("Flohmarkt", "changed")]:
            with self.subTest(html=html), self.assertRaises(regional_common.ParserEmptyError):
                katharinenhof._events_from_page(html, strict=True)

    def test_katharinenhof_valid_out_of_window_market_remains_empty(self):
        rows = katharinenhof._events_from_page(schema_event("Flohmarkt", "2026-8-9T10:00+2:00"), strict=True)
        self.assertEqual(len(rows), 1)
        self.assertFalse(common.event_in_window(rows[0]))

    def test_regional_optional_failure_does_not_retain_sibling_calendars(self):
        from tests import test_source_outage as fixtures

        case = fixtures.SourceOutageTests()
        calendars = regional_sitekit._CALENDARS
        with fixtures.make_runner_env() as env:
            previous = [fixtures.event(source="SiteKit regional", title=f"Old concert {city}",
                                       source_id=source_id) for city, source_id, _url, _trust in calendars]
            sources = lambda fetch: {"SiteKit regional": fetch,
                                     "Healthy": lambda: [fixtures.event("Healthy", "Play")]}
            case.run_day(env, -1, sources(lambda: previous))

            def calendar():
                url = "https://www.bruehl.de/detail.php"
                row = fixtures.event(source="SiteKit regional", title="Fresh concert", city="Brühl",
                                     source_id="sitekit-bruehl", link=url,
                                     description_source="generated")

                def fail(*args, **kwargs):
                    http._record_endpoint(url, error_type="HTTPError", error="HTTP Error 500")
                    raise HTTPError(url, 500, "Internal Server Error", {}, None)

                with patch.object(common, "fetch_detail_url", side_effect=fail):
                    return regional_sitekit._enrich_details([row])

            result, payload = case.run_day(env, 0, sources(calendar))
            self.assertEqual(payload["retained_sources"], [])
            self.assertEqual(payload["retained_event_count"], 0)
            self.assertIn("Fresh concert", {event.title for event in result.events})
            self.assertFalse(any(event.title.startswith("Old concert") for event in result.events))

    def test_sitekit_optional_detail_does_not_mark_six_calendars_down(self):
        result = SourceResult("SiteKit regional", status=SourceStatus.DEGRADED)
        url = "https://www.bruehl.de/detail.php"
        event = {"title": "Concert", "date": "2026-09-20", "start_date": "2026-09-20",
                 "end_date": "2026-09-20", "city": "Brühl", "source": "SiteKit regional",
                 "source_id": "sitekit-bruehl", "link": url, "description": "Known listing facts",
                 "description_source": "generated", "venue": ""}

        def fail(*args, **kwargs):
            http._record_endpoint(url, status=500, error_type="HTTPError", error="HTTP Error 500: Internal Server Error")
            raise HTTPError(url, 500, "Internal Server Error", {}, None)

        with patch.object(run_state._SOURCE_CONTEXT, "result", result, create=True), \
             patch.object(common, "fetch_detail_url", side_effect=fail):
            self.assertEqual(regional_sitekit._enrich_details([event]), [event])
        self.assertIs(result.endpoints[url].get("optional_detail"), True)
        self.assertEqual(result.warnings[0]["error_type"], "OptionalDetailWarning")
        self.assertEqual(result.warnings[0]["source_id"], "sitekit-bruehl")
        self.assertFalse(result.has_outage_evidence())
        result.endpoint("https://www.wesseling.de/calendar", error_type="HTTPError", error="HTTP Error 500: Internal Server Error")
        self.assertTrue(result.has_outage_evidence())
