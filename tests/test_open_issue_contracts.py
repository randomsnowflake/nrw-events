import json
import unittest
from unittest import mock
from datetime import date, datetime
from pathlib import Path

from nrw_events import config, highlights, runner, series
from nrw_events.observability import configure_logging
from nrw_events.runtime import EventWindow, RunContext
from nrw_events.source_specs import AdapterType, SourceSpec, adapter_for, load_source_specs


def raw_event(title="Flohmarkt Rheinaue", day="2026-08-15", **overrides):
    event = {
        "title": title, "source": "Bonn.de Events", "source_id": "bonn-de-events",
        "start_date": day, "end_date": day, "time": "10:00", "venue": "Rheinaue",
        "venue_id": "rheinaue-bonn", "city": "Bonn", "description": "Flohmarkt",
        "link": "https://example.test/event", "distance_km": 0, "score": 1.5,
        "category": "Flohmarkt", "category_key": "market", "category_label": "Markets",
        "category_confidence": 1.0, "category_reason": "test", "status": "scheduled",
    }
    event.update(overrides)
    return event


class OpenIssueContractTests(unittest.TestCase):
    def test_snapshot_exports_editorial_features_without_changing_score(self):
        canonical = runner.validate_event(raw_event())
        context = RunContext(
            config.RuntimeConfig(), EventWindow(datetime(2026, 8, 1), datetime(2026, 8, 28)),
            "features", configure_logging("features", "ERROR", "", ""),
            clock=lambda: datetime(2026, 8, 1, 12),
        )
        snapshot = runner.build_snapshot(runner.ImportResult((canonical,), {}, 1, "healthy"), context)
        event = snapshot.events[0]
        self.assertEqual(event["score"], 1.5)
        self.assertEqual(event["ranking_features"], {"flea_market": 0.5, "bonn_local": 0.1})
        self.assertEqual(event["priority_bonus"], 0.6)

    def test_unresolved_locations_are_published_and_radius_drops_are_counted(self):
        settings = config.RuntimeConfig(score_floor=0, radius_km=10, series_ledger_json="")
        context = RunContext(
            settings, EventWindow(datetime(2026, 8, 1), datetime(2026, 8, 28)),
            "radius", configure_logging("radius", "ERROR", "", ""),
        )
        unresolved = raw_event("Unklarer Ort", distance_km=None, location_confidence="unresolved", venue="", venue_id="")
        outside = raw_event("Weit weg", city="Köln", venue="", venue_id="", distance_km=20)
        with mock.patch.object(runner.common, "MAX_RADIUS_KM", runner.common.MAX_RADIUS_KM), \
                mock.patch.object(config, "MAX_RADIUS_KM", config.MAX_RADIUS_KM):
            result = runner.run_import(context, {"Bonn.de Events": lambda: [unresolved, outside]})
        self.assertEqual([event.title for event in result.events], ["Unklarer Ort"])
        self.assertEqual(result.source_results["Bonn.de Events"].rejection_reasons["filter:radius"], 1)

    def test_authoritative_cancellation_keeps_occurrence_identity_and_score_floor(self):
        settings = config.RuntimeConfig(score_floor=0, series_ledger_json="")
        context = RunContext(
            settings, EventWindow(datetime(2026, 8, 1), datetime(2026, 8, 28)),
            "cancellation", configure_logging("cancellation", "ERROR", "", ""),
            clock=lambda: datetime(2026, 8, 1, 12),
        )
        scheduled = raw_event()
        cancelled = raw_event(status="cancelled", description="Die Veranstaltung ist abgesagt.")
        result = runner.run_import(
            context, {"Bonn.de Events": lambda: [scheduled, cancelled]},
        )
        snapshot = runner.build_snapshot(result, context)

        [event] = snapshot.events
        self.assertEqual(event["status"], "cancelled")
        self.assertEqual(event["score"], 0.0)
        self.assertEqual(event["cancellation_source"], "Bonn.de Events")
        self.assertEqual(event["cancelled_at"], "2026-08-01T12:00:00")
        self.assertEqual(
            result.source_results["Bonn.de Events"].cancelled_events[0]["status"],
            "cancelled",
        )

    def test_side_channel_cancellation_is_canonicalized_before_cross_run_fields(self):
        cancellation = raw_event(
            title="Deutschkurs für Männer", source="Stadt Bonn", source_id="stadt-bonn",
            city="Bonn", venue="Bürgerzentrum", venue_id="",
            link="https://example.test/course", status="cancelled", score=0.0,
            category="kurs", category_key="workshop", category_label="Workshops & Kurse",
            description="Der Termin wurde abgesagt.",
        )

        events = runner._attach_cross_run_fields(
            [cancellation], {}, "2026-08-01T12:00:00",
        )

        [event] = events
        self.assertEqual(event.status, "cancelled")
        self.assertEqual(event.cancelled_at, "2026-08-01T12:00:00")
        self.assertEqual(event.cancellation_source, "Stadt Bonn")
        self.assertEqual(event.admission, {
            "isFree": None, "amount": None, "currency": "EUR",
            "basis": "", "note": "", "donationSuggested": False,
        })

    def test_series_model_handles_seasonal_runs_and_keeps_estimates_separate(self):
        events = [raw_event(day=value) for value in ("2026-04-12", "2026-04-26", "2026-05-10")]
        ledger = {"schema_version": 1, "series": {}}
        rows, metadata, updated = series.enrich_events(
            events, ledger, today=date(2026, 4, 1), generated_at="2026-04-01T00:00:00",
        )
        self.assertTrue(all(row["series_id"] and row["run_id"] for row in rows))
        self.assertEqual(metadata[0]["series_state"], "active")
        stored = next(iter(updated["series"].values()))
        stored["occurrences"].update({
            "2025-a": "2025-04-13", "2025-b": "2025-06-08", "2025-c": "2025-10-12",
            "2026-c": "2026-10-11",
        })
        _, [winter], _ = series.enrich_events([], updated, today=date(2027, 1, 10), generated_at="2027-01-10T00:00:00")
        self.assertEqual(winter["series_state"], "dormant_seasonal")
        self.assertIsNone(winter["next_occurrence"])
        self.assertIsNotNone(winter["next_occurrence_estimated"])
        self.assertEqual(winter["season_start_month"], 4)
        self.assertEqual(winter["season_end_month"], 10)

    def test_series_realistic_groups_cadence_conclusion_and_announced_dates(self):
        events = [
            raw_event("Feierabendmarkt Bonn", value, venue="Marktplatz Bonn", venue_id="")
            for value in ("2026-05-03", "2026-06-07", "2026-07-05")
        ] + [
            raw_event("Cölln Antik&Design", value, venue="Rheinaue", venue_id="rheinaue-bonn")
            for value in ("2026-05-10", "2026-06-14")
        ]
        rows, metadata, _ = series.enrich_events(
            events, {"schema_version": 1, "series": {}},
            today=date(2026, 5, 1), generated_at="2026-05-01T00:00:00",
            announced_events=[
                raw_event("Feierabendmarkt Bonn", "2026-08-02", venue="Marktplatz Bonn", venue_id=""),
            ],
        )
        self.assertEqual(len({row["series_id"] for row in rows}), 2)
        market = next(item for item in metadata if item["title"] == "Feierabendmarkt Bonn")
        self.assertEqual(market["runs"][0]["cadence"], "monthly")
        self.assertEqual(market["runs"][0]["cadence_pattern"], "first_sunday")
        self.assertEqual(market["announced_dates"], ["2026-08-02"])
        self.assertNotIn("2026-08-02", [row["start_date"] for row in rows])

        old = {"schema_version": 1, "series": {"old-series": {
            "series_id": "old-series", "title": "Befristete Lesereihe", "venue": "Haus",
            "canonical_venue_id": "haus", "city": "Bonn", "category_key": "talk",
            "first_seen": "2024-01-01T00:00:00", "last_seen": "2024-02-01T00:00:00",
            "occurrences": {"a": "2024-01-07", "b": "2024-02-04"}, "announced_dates": [],
        }}}
        _, [concluded], _ = series.enrich_events(
            [], old, today=date(2026, 5, 1), generated_at="2026-05-01T00:00:00",
        )
        self.assertEqual(concluded["series_state"], "concluded")

    def test_announced_future_date_is_the_confirmed_next_occurrence(self):
        ledger = {"schema_version": 1, "series": {"market": {
            "series_id": "market", "title": "Wintermarkt", "venue": "Marktplatz",
            "canonical_venue_id": "marktplatz", "city": "Bonn", "category_key": "market",
            "first_seen": "2025-01-01T00:00:00", "last_seen": "2025-02-01T00:00:00",
            "occurrences": {"a": "2025-01-01", "b": "2025-02-01"},
            "announced_dates": ["2026-09-01"],
        }}}
        _, [market], _ = series.enrich_events(
            [], ledger, today=date(2026, 8, 2), generated_at="2026-08-02T00:00:00",
        )
        self.assertEqual(market["series_state"], "active")
        self.assertEqual(market["next_occurrence"], "2026-09-01")
        self.assertIsNone(market["next_occurrence_estimated"])

    def test_highlights_are_reproducible_and_apply_generic_diversity_caps(self):
        events = []
        for index in range(6):
            event = raw_event(title=f"Markt {index}", day=f"2026-08-{10 + index:02d}", score=2 - index / 10)
            event.update(event_id=f"event-{index}", ranking_features={}, priority_bonus=0)
            events.append(event)
        first = highlights.build_highlights(events, run_id="run", generated_at="now")
        second = highlights.build_highlights(reversed(events), run_id="run", generated_at="now")
        self.assertEqual(
            [row["eventId"] for row in first["categories"][0]["selected"]],
            [row["eventId"] for row in second["categories"][0]["selected"]],
        )
        self.assertLessEqual(len(first["categories"][0]["selected"]), highlights.MAX_PER_VENUE)

    def test_registry_fixture_is_schema_valid_region_aware_and_declarative(self):
        path = Path(__file__).parents[1] / "scripts/nrw_events/sources/registry.json"
        fixture = json.loads(path.read_text(encoding="utf-8"))
        specs = load_source_specs(path)
        self.assertEqual(fixture["schema_version"], 1)
        self.assertEqual(len(specs), 81)
        self.assertGreaterEqual(len(specs), 40)
        self.assertTrue(all(spec.region for spec in specs))
        self.assertTrue(all(isinstance(spec.adapter, AdapterType) for spec in specs))
        self.assertEqual(len({spec.id for spec in specs}), len(specs))

    def test_declarative_adapters_cover_multi_endpoint_html_and_cached_details(self):
        ical = SourceSpec(
            "multi", "Multi", ("https://example.test/a.ics", "https://example.test/b.ics"),
            AdapterType.ICAL, "Bonn",
        )
        with mock.patch("nrw_events.source_specs.common.fetch_ical", side_effect=[[{"title": "A"}], [{"title": "B"}]]) as fetch:
            self.assertEqual([row["title"] for row in adapter_for(ical)()], ["A", "B"])
        self.assertEqual(fetch.call_count, 2)

        html = SourceSpec(
            "cards", "Cards", ("https://example.test/events",), AdapterType.HTML,
            "Bonn", selectors=(
                ("item", r"(<article>.*?</article>)"),
                ("title", r"<h2>(.*?)</h2>"),
                ("date", r"<time>(.*?)</time>"),
            ),
        )
        with mock.patch("nrw_events.source_specs.common.fetch_url", return_value=(
            "<article><h2>Sommerfest</h2><time>15.08.2026</time></article>"
        )):
            [parsed] = adapter_for(html)()
        self.assertEqual(parsed["title"], "Sommerfest")

        detail = SourceSpec(
            "detail", "Detail", ("https://example.test/index",), AdapterType.JSON_LD,
            "Bonn", detail_urls=("https://example.test/detail",),
        )
        with mock.patch("nrw_events.source_specs.common.fetch_url", return_value="{}"), \
                mock.patch("nrw_events.source_specs.common.fetch_detail_url", return_value="{}") as cached, \
                mock.patch("nrw_events.source_specs.common.events_from_jsonld", return_value=[]):
            self.assertEqual(adapter_for(detail)(), [])
        cached.assert_called_once()


if __name__ == "__main__":
    unittest.main()
