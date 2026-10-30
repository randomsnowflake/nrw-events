"""Regression evidence from the authenticated source audit of 30 October."""

import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from nrw_events import common, detail_enrichment, jsonld, report, retention_policy, source_execution
from nrw_events.health import SourceResult
from nrw_events.sources import bonn, bonn_districts, bonn_food, in_guten_kreisen, koelner_hallenflohmarkt
from nrw_events.validation import validate_event

from tests.helpers import patch_window

FIXTURES = Path(__file__).parent / "fixtures" / "source_audit_20261030"


class SourceAuditOctober30Tests(unittest.TestCase):
    def setUp(self):
        patch_window(self, datetime(2026, 10, 30), datetime(2026, 11, 26))

    def test_beuel_timing_span_keeps_dates_times_and_primary_links(self):
        events = bonn_districts.events_from_beuel_html((FIXTURES / "beuel.html").read_text())
        current = [event for event in events if common.event_in_window(event)]
        self.assertEqual(len(current), 10)
        [martin] = [e for e in current if e["title"].startswith("Großer Beueler")]
        self.assertEqual(martin["start_date"], "2026-11-05")
        self.assertEqual(martin["time"], "17:15")
        self.assertEqual(martin["link"], "https://www.sankt-martin-beuel.de/")
        self.assertTrue(all(e["description_source"] == "generated" for e in current))

    def test_hallen_cancellation_replaces_the_retained_sunday(self):
        html = (FIXTURES / "hallenflohmarkt.html").read_text()
        [cancelled] = koelner_hallenflohmarkt._events_from_page(html, strict=True)
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertEqual(cancelled["start_date"], "2026-11-01")
        old = validate_event({**cancelled, "status": "scheduled", "description": "Flohmarkt", "time": "10:00–16:00"})
        [event] = report.deduplicate([old], cancellations=[cancelled])
        self.assertEqual(event.status, "cancelled")
        with patch.object(common, "TODAY", datetime(2026, 11, 2)):
            self.assertEqual(koelner_hallenflohmarkt._events_from_page(html, strict=True), [])

    def test_ludwigs_current_slugs_use_the_bounded_yearless_date_policy(self):
        html = (FIXTURES / "ludwigs-list.html").read_text()
        events = bonn_food.events_from_ludwigs(html)
        self.assertEqual([e["start_date"] for e in events], ["2026-12-24", "2026-12-31"])
        self.assertFalse(any(common.event_in_window(e) for e in events))
        with patch.object(common, "END_DATE", datetime(2026, 12, 31)):
            events = bonn_food.events_from_ludwigs(
                html, lambda _url: (FIXTURES / "ludwigs-weihnachten.html").read_text(),
            )
        self.assertEqual(events[0]["time"], "19:00–23:00")

    def test_vhs_invalid_supplemental_jsonld_does_not_become_an_outage(self):
        result = SourceResult("VHS Bonn")
        with patch.object(common._SOURCE_CONTEXT, "result", result, create=True):
            context = detail_enrichment.extract_detail_context(
                (FIXTURES / "vhs-course.html").read_text(),
                {"title": "Vortrag", "start_date": "2026-11-26", "link": "https://www.vhs-bonn.de/kurs/N1126"},
            )
        self.assertIsInstance(context, dict)
        self.assertTrue(result.warnings)
        self.assertTrue(all(w["error_type"] == "OptionalDetailWarning" for w in result.warnings))
        result.finish([{}])
        self.assertFalse(result.has_outage_evidence())


    def test_closed_market_cancellation_never_rolls_into_a_future_edition(self):
        html = (FIXTURES / "hallenflohmarkt.html").read_text()
        with patch.object(common, "TODAY", datetime(2032, 10, 30)), \
             patch.object(common, "END_DATE", datetime(2032, 11, 26)):
            self.assertEqual(koelner_hallenflohmarkt._events_from_page(html, strict=True), [])
        self.assertFalse(koelner_hallenflohmarkt._programme_closed(html.replace("Industriestraße 131", "Andere Straße")))

    def test_healthy_ludwigs_listing_stops_retaining_the_removed_november_event(self):
        html = (FIXTURES / "ludwigs-list.html").read_text()
        with patch.object(common, "fetch_url", return_value=html), \
             patch.object(detail_enrichment, "enrich_events", side_effect=lambda events, **_kwargs: events):
            result, accepted = source_execution._run_source("Ludwig's Bonn", bonn_food.fetch_ludwigs)
        self.assertEqual(accepted, [])
        self.assertFalse(result.has_outage_evidence())
        previous = {"events": [{"title": "Herbst-Küchenparty", "source": "Ludwig's Bonn", "source_id": "ludwig-s-bonn"}]}
        labels, _fallbacks = retention_policy._retention_labels({"Ludwig's Bonn": result}, previous)
        self.assertEqual(labels, set())

    def test_primary_jsonld_errors_are_still_source_failures(self):
        result = SourceResult("Structured source")
        with patch.object(common._SOURCE_CONTEXT, "result", result, create=True):
            self.assertEqual(jsonld.jsonld_event_items('<script type="application/ld+json">{"@type": "Event", bad}</script>'), [])
        self.assertEqual(result.warnings[0]["error_type"], "JSONDecodeError")

    def test_empty_wine_calendar_is_explicitly_allowed(self):
        with patch.object(common, "fetch_ical", return_value=[]) as fetch, \
             patch.object(common, "fetch_detail_url", side_effect=TimeoutError):
            in_guten_kreisen.fetch()
        self.assertTrue(fetch.call_args.kwargs["empty_calendar_is_valid"])

    def test_bonn_geodata_is_optional_but_still_reports_the_failure(self):
        result = SourceResult("Bonn.de Events")
        with patch.object(bonn, "_venue_points_cache", None), \
             patch.object(common._SOURCE_CONTEXT, "result", result, create=True), \
             patch.object(common, "fetch_url", side_effect=TimeoutError("slow")):
            self.assertEqual(bonn._venue_points(), {})
        self.assertEqual(len(result.warnings), 1)
        self.assertEqual(result.warnings[0]["error_type"], "OptionalDetailWarning")


if __name__ == "__main__":
    unittest.main()
