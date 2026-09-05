import json
import tempfile
import threading
import time
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest import mock

from nrw_events import common, config, core, highlights, report, runner, series
from nrw_events.health import SourceFetchResult, SourceStatus
from nrw_events.observability import configure_logging
from nrw_events.runtime import EventWindow, RunContext
from nrw_events.source_specs import AdapterType, SourceSpec, adapter_for, load_source_specs, typed_adapter_for


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
    def test_retained_event_counts_accept_serialized_snapshot_rows(self):
        retained = raw_event(source="Kunstmuseum Bonn", source_id="kunstmuseum-bonn")

        self.assertEqual(
            runner._retained_event_counts_by_source(
                [retained],
                {runner.event_id(retained)},
            ),
            {"kunstmuseum-bonn": 1},
        )

    def test_extract_dates_preserves_text_position_across_formats(self):
        dates = core.extract_dates("Am 15. August 2026, Stand 2026-08-06")

        self.assertEqual(
            [value.strftime("%Y-%m-%d") for value in dates],
            ["2026-08-15", "2026-08-06"],
        )

    def test_corrupt_series_ledger_is_preserved_as_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "series.json"
            path.write_text("{broken", encoding="utf-8")
            self.assertEqual(series.load_ledger(str(path))["series"], {})
            self.assertEqual(
                path.with_name("series.json.bak").read_text(encoding="utf-8"),
                "{broken",
            )

    def test_series_season_uses_smallest_arc_across_new_year(self):
        observed = [
            raw_event(day=value)
            for value in (
                "2024-11-25", "2025-01-06", "2025-11-24", "2026-01-05",
            )
        ]
        _rows, [metadata], _ledger = series.enrich_events(
            observed,
            {"schema_version": 1, "series": {}},
            today=date(2026, 2, 1),
            generated_at="2026-02-01T00:00:00",
        )
        self.assertEqual(metadata["season_start_month"], 11)
        self.assertEqual(metadata["season_end_month"], 1)

    def test_detached_executor_runs_tasks_on_daemon_threads(self):
        pool = runner._DetachedThreadPoolExecutor(max_workers=1)
        try:
            self.assertTrue(pool.submit(lambda: threading.current_thread().daemon).result(timeout=1))
        finally:
            pool.shutdown(wait=True)

    def test_completed_future_past_budget_is_rejected_at_watchdog_boundary(self):
        settings = config.RuntimeConfig(
            score_floor=0,
            source_timeout_seconds=0.001,
            source_processing_grace_seconds=0,
            series_ledger_json="",
        )
        context = RunContext(
            settings,
            EventWindow(datetime(2026, 8, 1), datetime(2026, 8, 28)),
            "watchdog-boundary",
            configure_logging("watchdog-boundary", "ERROR", "", ""),
        )
        wait_timeouts = []

        def late_source():
            time.sleep(0.01)
            return [raw_event(source="Boundary", source_id="boundary")]

        def omit_completed_future(pending, *, timeout, return_when):
            del return_when
            wait_timeouts.append(timeout)
            for future in pending:
                future.result(timeout=1)
            return set(), pending

        with mock.patch.object(runner, "_previous_snapshot", return_value={}), \
                mock.patch.object(runner, "wait", side_effect=omit_completed_future):
            result = runner.run_import(context, {"Boundary": late_source})

        self.assertEqual(result.source_results["Boundary"].status, SourceStatus.FAILED)
        self.assertEqual(result.events, ())
        self.assertTrue(wait_timeouts)
        self.assertGreaterEqual(min(wait_timeouts), 0.05)

    def test_cancellation_key_uses_the_canonical_title(self):
        raw_cancellation = raw_event(
            title="SOMMERFEST, 15.08.2026",
            status="cancelled",
            source="Official Calendar",
            source_id="official-calendar",
        )

        def fetch():
            common._SOURCE_CONTEXT.result.cancelled_events.append(dict(raw_cancellation))
            return [raw_cancellation]

        context = RunContext(config.RuntimeConfig(), EventWindow.from_days(3, datetime(2026, 8, 15)), 'cancellation', configure_logging('cancellation', 'ERROR', '', ''))
        token = common.configure_context(context)
        try:
            result, events = runner._run_source("Official Calendar", fetch)
        finally:
            common.reset_runtime(token)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].title, "Sommerfest")
        self.assertEqual(len(result.cancelled_events), 1)

    def test_retained_filter_hashes_each_event_once(self):
        fresh = [raw_event(title="Fresh"), raw_event(title="Shared")]
        retained = [
            raw_event(title="Shared"),
            raw_event(title="Retained", day="2026-08-16"),
        ]
        real_event_id = runner.event_id

        with mock.patch.object(runner, "event_id", wraps=real_event_id) as identity:
            result = runner._retained_events_without_fresh_duplicate(fresh, retained)

        self.assertEqual([event["title"] for event in result], ["Retained"])
        self.assertEqual(identity.call_count, len(fresh) + len(retained))

    def test_fresh_bonn_event_suppresses_retained_dated_title_variant(self):
        fresh = [raw_event(
            title="Weinfest auf dem Bonner Münsterplatz",
            source="Bonn.de Events",
            source_id="bonn-de-events",
            day="2026-08-21",
            city="Bonn",
        )]
        retained = [raw_event(
            title=(
                "20.08.2026 - 23.08.2026 Weinfest auf dem Bonner Münsterplatz "
                "- täglich ab Mittagszeit"
            ),
            source="Bonn.de Events",
            source_id="bonn-de-events",
            day="2026-08-21",
            city="Bonn",
        )]

        self.assertEqual(
            runner._retained_events_without_fresh_duplicate(fresh, retained),
            [],
        )

    def test_retained_dated_title_without_same_source_twin_is_kept(self):
        fresh = [raw_event(
            title="Weinfest auf dem Bonner Münsterplatz",
            source="Independent Calendar",
            source_id="independent-calendar",
            day="2026-08-21",
            city="Bonn",
        )]
        retained = [raw_event(
            title=(
                "20.08.2026 - 23.08.2026 Weinfest auf dem Bonner Münsterplatz "
                "- täglich ab Mittagszeit"
            ),
            source="Bonn.de Events",
            source_id="bonn-de-events",
            day="2026-08-21",
            city="Bonn",
        )]

        self.assertEqual(
            runner._retained_events_without_fresh_duplicate(fresh, retained),
            retained,
        )

    def test_targeted_bonn_refresh_keeps_retained_primary_record(self):
        municipal = raw_event(
            title="Atelier am Sonntag",
            source="Bonn.de Events",
            source_id="bonn-de-events",
            description="Nicht veröffentlichbarer Bonn-Kalendertext.",
            description_source="generated",
            ai_summary="Bonn-Zusammenfassung",
            link="https://www.bonn.de/veranstaltungskalender/atelier-am-sonntag.php",
        )
        primary = raw_event(
            title="Atelier am Sonntag",
            source="Kunstmuseum Bonn",
            source_id="kunstmuseum-bonn",
            description="Primärtext des Kunstmuseums.",
            description_source="scraped",
            ai_summary="",
            link="https://www.kunstmuseum-bonn.de/atelier-am-sonntag/",
        )

        fresh, remaining, promoted = runner._prefer_retained_primary_over_bonn_fallback(
            [municipal], [primary]
        )

        self.assertEqual(remaining, [])
        self.assertEqual(promoted, fresh)
        self.assertEqual(fresh[0]["source_id"], "kunstmuseum-bonn")
        self.assertEqual(fresh[0]["link"], primary["link"])
        self.assertEqual(fresh[0]["description"], "Primärtext des Kunstmuseums.")
        self.assertEqual(fresh[0]["ai_summary"], "")
        self.assertIn(municipal["link"], fresh[0]["source_links"])

    def test_targeted_bonn_refresh_does_not_promote_weak_venue_match(self):
        municipal = raw_event(
            title="Ausstellung: Zu den Sternen!",
            venue="Arp Museum Bahnhof Rolandseck",
            venue_id="arp-museum-rolandseck",
            category="Ausstellung",
            category_key="exhibition",
        )
        primary = raw_event(
            title="Öffentliche Führung durch das Arp Museum",
            source="Arp Museum",
            source_id="arp-museum",
            venue="Arp Museum Bahnhof Rolandseck",
            venue_id="arp-museum-rolandseck",
            category="Ausstellung",
            category_key="exhibition",
            description="Primärtext einer anderen Veranstaltung.",
            link="https://arpmuseum.org/veranstaltungen/fuehrung.html",
        )

        fresh, remaining, promoted = runner._prefer_retained_primary_over_bonn_fallback(
            [municipal], [primary]
        )

        self.assertEqual(fresh, [municipal])
        self.assertEqual(remaining, [primary])
        self.assertEqual(promoted, [])

    def test_targeted_bonn_refresh_uses_reviewed_primary_title_alias(self):
        municipal = raw_event(
            title="Repair-Café: Holz- und Drechselarbeiten",
            source="Bonn.de Events",
            source_id="bonn-de-events",
            venue="Repair Café MVA Bonn",
            venue_id="bonn-repair-cafe-mva",
        )
        primary = raw_event(
            title="Holzarbeiten und Drechseln im Repair Café MVA Bonn",
            source="Repair Cafés Bonn",
            source_id="repair-cafes-bonn",
            venue="Repair Café MVA Bonn",
            venue_id="bonn-repair-cafe-mva",
            description="Primärtext des Repair Cafés.",
            description_source="scraped",
        )

        fresh, remaining, promoted = runner._prefer_retained_primary_over_bonn_fallback(
            [municipal], [primary]
        )

        self.assertEqual(remaining, [])
        self.assertEqual(promoted, fresh)
        self.assertEqual(fresh[0]["source_id"], "repair-cafes-bonn")
        self.assertEqual(fresh[0]["description"], "Primärtext des Repair Cafés.")

    def test_highlight_rank_preserves_zero_distance(self):
        self.assertEqual(highlights._rank({"distance_km": 0})[1], 0)
        self.assertEqual(highlights._rank({"distance_km": None})[1], 999)

    def test_source_wall_clock_timeout_returns_without_waiting_for_stalled_parser(self):
        settings = config.RuntimeConfig(
            score_floor=0, source_timeout_seconds=0.2,
            source_processing_grace_seconds=0,
            series_ledger_json="",
        )
        context = RunContext(
            settings, EventWindow(datetime(2026, 8, 1), datetime(2026, 8, 28)),
            "timeout", configure_logging("timeout", "ERROR", "", ""),
        )

        started = time.monotonic()
        with mock.patch.object(runner, "_previous_snapshot", return_value={}), \
                mock.patch.object(runner.ai_enrichment, "enrich_events", side_effect=lambda events, **_: events):
            result = runner.run_import(context, {
                "Fast": lambda: [raw_event()],
                "Stalled": lambda: (time.sleep(1.0), [])[1],
            })
        elapsed = time.monotonic() - started

        self.assertLess(elapsed, 0.5)
        self.assertEqual([event.title for event in result.events], ["Flohmarkt Rheinaue"])
        self.assertEqual(result.source_results["Stalled"].status, runner.SourceStatus.FAILED)
        self.assertEqual(result.source_results["Stalled"].error["error_type"], "TimeoutError")

    def test_processing_grace_preserves_a_successful_large_source_result(self):
        settings = config.RuntimeConfig(
            score_floor=0,
            source_timeout_seconds=0.03,
            source_processing_grace_seconds=0.1,
            series_ledger_json="",
        )
        context = RunContext(
            settings,
            EventWindow(datetime(2026, 8, 1), datetime(2026, 8, 28)),
            "processing-grace",
            configure_logging("processing-grace", "ERROR", "", ""),
        )

        with mock.patch.object(runner, "_previous_snapshot", return_value={}), \
                mock.patch.object(runner.ai_enrichment, "enrich_events", side_effect=lambda events, **_: events):
            result = runner.run_import(context, {
                "Large": lambda: (time.sleep(0.08), [raw_event()])[1],
            })

        self.assertEqual(result.source_results["Large"].status, runner.SourceStatus.HEALTHY)
        self.assertEqual([event.title for event in result.events], ["Flohmarkt Rheinaue"])

    def test_queued_source_gets_its_own_budget_after_stalled_worker(self):
        settings = config.RuntimeConfig(
            score_floor=0, source_workers=1, source_timeout_seconds=0.05,
            source_processing_grace_seconds=0,
            series_ledger_json="",
        )
        context = RunContext(
            settings, EventWindow(datetime(2026, 8, 1), datetime(2026, 8, 28)),
            "queued-timeout", configure_logging("queued-timeout", "ERROR", "", ""),
        )

        with mock.patch.object(runner, "_previous_snapshot", return_value={}), \
                mock.patch.object(runner.ai_enrichment, "enrich_events", side_effect=lambda events, **_: events):
            result = runner.run_import(context, {
                "Stalled": lambda: (time.sleep(0.2), [])[1],
                "Fast": lambda: [raw_event()],
            })

        self.assertEqual([event.title for event in result.events], ["Flohmarkt Rheinaue"])
        self.assertEqual(result.source_results["Stalled"].status, runner.SourceStatus.FAILED)
        self.assertEqual(result.source_results["Fast"].status, runner.SourceStatus.HEALTHY)

    def test_restricted_source_does_not_borrow_publication_ai_budget(self):
        settings = config.RuntimeConfig(
            score_floor=0, source_timeout_seconds=0.03,
            source_processing_grace_seconds=0,
            series_ledger_json="",
        )
        context = RunContext(
            settings, EventWindow(datetime(2026, 8, 1), datetime(2026, 8, 28)),
            "ai-timeout-grace", configure_logging("ai-timeout-grace", "ERROR", "", ""),
        )

        with mock.patch.object(runner, "_previous_snapshot", return_value={}), \
                mock.patch.object(
                    runner.ai_enrichment, "settings_from_env",
                    return_value=mock.Mock(
                        enabled=True, api_key="test-key", batch_timeout_seconds=0.05,
                    ),
                ), \
                mock.patch.object(
                    runner.ai_enrichment, "enrich_events", side_effect=lambda events, **_: events,
                ):
            result = runner.run_import(context, {
                "Bonn.de Events": lambda: (time.sleep(0.06), [raw_event()])[1],
            })

        self.assertEqual(result.source_results["Bonn.de Events"].status, runner.SourceStatus.FAILED)
        self.assertEqual(result.events, ())

    def test_publication_ai_runs_after_source_worker_deadline_is_finished(self):
        settings = config.RuntimeConfig(
            score_floor=0,
            source_timeout_seconds=0.03,
            source_processing_grace_seconds=0,
            series_ledger_json="",
        )
        context = RunContext(
            settings,
            EventWindow(datetime(2026, 8, 1), datetime(2026, 8, 28)),
            "ai-batch-allowance",
            configure_logging("ai-batch-allowance", "ERROR", "", ""),
        )

        with mock.patch.object(runner, "_previous_snapshot", return_value={}), \
                mock.patch.object(
                    runner.ai_enrichment,
                    "settings_from_env",
                    return_value=mock.Mock(
                        enabled=True, api_key="test-key", batch_timeout_seconds=0.1,
                    ),
                ), \
                mock.patch.object(
                    runner.ai_enrichment, "enrich_events",
                    side_effect=lambda events, **_: (time.sleep(0.08), events)[1],
                ):
            result = runner.run_import(context, {
                "Bonn.de Events": lambda: [raw_event()],
            })

        self.assertEqual(result.source_results["Bonn.de Events"].status, runner.SourceStatus.HEALTHY)
        self.assertEqual([event.title for event in result.events], ["Flohmarkt Rheinaue"])

    def test_timed_out_source_cannot_start_fresh_proxy_fallbacks(self):
        settings = config.RuntimeConfig(
            score_floor=0, source_timeout_seconds=0.03,
            source_processing_grace_seconds=0, series_ledger_json="",
        )
        context = RunContext(
            settings, EventWindow(datetime(2026, 8, 1), datetime(2026, 8, 28)),
            "cancel-fallback", configure_logging("cancel-fallback", "ERROR", "", ""),
        )
        source_finished = mock.Mock()

        def stalled_source():
            time.sleep(0.06)
            try:
                common.fetch_url_with_brightdata_fallback(
                    "https://example.test/events",
                    allowed_hosts=("example.test",),
                    fallback_on_timeout=True,
                )
            finally:
                source_finished()
            return []

        with mock.patch.object(runner, "_previous_snapshot", return_value={}), \
                mock.patch.object(common, "fetch_url", side_effect=TimeoutError("direct timeout")), \
                mock.patch.object(common, "fetch_url_with_brightdata", return_value="proxy") as proxy, \
                mock.patch.dict("os.environ", {
                    "BRIGHT_DATA_API_KEY": "test-key", "BRIGHT_DATA_ZONE": "test-zone",
                }):
            result = runner.run_import(context, {"Stalled": stalled_source})
            for _ in range(50):
                if source_finished.called:
                    break
                time.sleep(0.01)

        self.assertEqual(result.source_results["Stalled"].status, runner.SourceStatus.FAILED)
        self.assertTrue(source_finished.called)
        proxy.assert_not_called()

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
        with mock.patch.object(runner, "_previous_snapshot", return_value={}), \
                mock.patch.object(runner.common, "MAX_RADIUS_KM", runner.common.MAX_RADIUS_KM), \
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
        with mock.patch.object(runner, "_previous_snapshot", return_value={}):
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

    def test_sundowner_title_variants_share_the_verified_bundeskunsthalle_series(self):
        events = [
            raw_event(
                title,
                day,
                venue="Bundeskunsthalle",
                venue_id="bundeskunsthalle",
                source=source,
                source_id=source_id,
            )
            for title, day, source, source_id in (
                ("Sundowner Bar", "2026-08-12", "Bundeskunsthalle", "bundeskunsthalle"),
                (
                    "Sundowner Bar auf dem Dach der Bundeskunsthalle",
                    "2026-08-19",
                    "Bonn.de Events",
                    "bonn-de-events",
                ),
                (
                    "Sundowner Bar – Terrassen-Edition",
                    "2026-08-28",
                    "Bundeskunsthalle",
                    "bundeskunsthalle",
                ),
            )
        ]
        unrelated = raw_event(
            "Sundowner Bar",
            "2026-08-21",
            venue="Hotelterrasse",
            venue_id="hotelterrasse",
        )

        rows, metadata, _updated = series.enrich_events(
            [*events, unrelated],
            {"schema_version": 1, "series": {}},
            today=date(2026, 8, 11),
            generated_at="2026-08-11T12:00:00",
        )

        sundowner = [row for row in rows if row["venue_id"] == "bundeskunsthalle"]
        self.assertEqual(len({row["series_id"] for row in sundowner}), 1)
        self.assertEqual({row["series_title"] for row in sundowner}, {"Sundowner Bar"})
        self.assertEqual(
            metadata[0]["occurrence_dates"],
            ["2026-08-12", "2026-08-19", "2026-08-28"],
        )
        self.assertNotIn("series_id", rows[-1])

    def test_kaldauen_rochus_title_variants_share_one_place_scoped_series(self):
        variants = [
            raw_event("Rochus-Kirmes", "2026-08-14", city="Siegburg", venue="Kaldauer Zentrum", venue_id=""),
            raw_event("Rochuskirmes Kaldauen", "2026-08-15", city="Siegburg", venue="Kaldauer Zentrum", venue_id=""),
            raw_event("Kaldauer Rochus Kirmes", "2026-08-16", city="Siegburg", venue="Kaldauer Zentrum", venue_id=""),
        ]
        unrelated = raw_event(
            "Rochus-Kirmes", "2026-08-16", city="Bonn", venue="Rochusplatz", venue_id="",
        )

        rows, _metadata, _updated = series.enrich_events(
            [*variants, unrelated], {"schema_version": 1, "series": {}},
            today=date(2026, 8, 13), generated_at="2026-08-13T12:00:00",
        )

        kaldauen = [row for row in rows if row["city"] == "Siegburg"]
        self.assertEqual(1, len({row["series_id"] for row in kaldauen}))
        self.assertEqual({"Kaldauer Rochuskirmes"}, {row["series_title"] for row in kaldauen})
        self.assertNotIn("series_id", rows[-1])

    def test_bonn_friedensplatz_antikmarkt_variants_share_one_verified_series(self):
        variants = [
            raw_event(
                "Antik-, Kunst- & Designmarkt Bonn", "2026-04-12",
                venue="Friedensplatz", venue_id="friedensplatz-bonn",
                source="Rhein Antik", source_id="rhein-antik",
            ),
            raw_event(
                "Antik Markt Bonn Friedensplatz", "2026-06-21",
                venue="Antik Markt Bonn", venue_id="",
                source="Bonn.de Events", source_id="bonn-de-events",
            ),
            raw_event(
                "Antikmarkt Bonn", "2026-08-16",
                venue="Friedensplatz", venue_id="",
                source="Cölln Konzept", source_id="c-lln-konzept",
            ),
        ]
        unrelated = raw_event(
            "Antikmarkt Bonn", "2026-08-23",
            venue="Rheinaue", venue_id="rheinaue-bonn",
        )

        rows, metadata, _updated = series.enrich_events(
            [*variants, unrelated], {"schema_version": 1, "series": {}},
            today=date(2026, 4, 1), generated_at="2026-04-01T12:00:00",
        )

        friedensplatz = rows[:3]
        self.assertEqual(1, len({row["series_id"] for row in friedensplatz}))
        self.assertEqual(
            {"Antikmarkt Bonn am Friedensplatz"},
            {row["series_title"] for row in friedensplatz},
        )
        self.assertEqual(
            ["2026-04-12", "2026-06-21", "2026-08-16"],
            next(
                item["occurrence_dates"]
                for item in metadata
                if item["title"] == "Antikmarkt Bonn am Friedensplatz"
            ),
        )
        self.assertNotIn("series_id", rows[-1])

    def test_bonn_friedensplatz_antikmarkt_ledger_variants_migrate_together(self):
        ledger = {"schema_version": 1, "series": {}}
        for index, (title, venue, venue_id, day) in enumerate((
            ("Antik-, Kunst- & Designmarkt Bonn", "Friedensplatz", "", "2026-04-12"),
            ("Antik Markt Bonn Friedensplatz", "Antik Markt Bonn", "", "2026-06-21"),
            ("Antikmarkt Bonn", "Friedensplatz", "friedensplatz-bonn", "2026-08-16"),
        )):
            ledger["series"][f"legacy-{index}"] = {
                "series_id": f"legacy-{index}", "title": title, "venue": venue,
                "canonical_venue_id": venue_id, "city": "Bonn", "category_key": "market",
                "first_seen": f"{day}T00:00:00", "last_seen": f"{day}T00:00:00",
                "occurrences": {str(index): day}, "announced_dates": [],
            }

        _rows, metadata, updated = series.enrich_events(
            [], ledger, today=date(2026, 8, 17), generated_at="2026-08-17T12:00:00",
        )

        self.assertEqual(1, len(updated["series"]))
        self.assertEqual("Antikmarkt Bonn am Friedensplatz", metadata[0]["title"])
        self.assertEqual(
            ["2026-04-12", "2026-06-21", "2026-08-16"],
            metadata[0]["occurrence_dates"],
        )

    def test_beuel_rathausplatz_flohmarkt_variants_share_one_verified_series(self):
        variants = [
            raw_event(
                "Außer Haus: Floh- und Trödelmarkt auf dem Rathausplatz",
                "2026-08-30", city="Bonn", venue="Brückenforum Bonn", venue_id="",
                source="Brückenforum Bonn", source_id="brueckenforum-bonn",
            ),
            raw_event(
                "Floh- und Trödelmarkt Beueler Rathausplatz",
                "2026-09-27", city="Bonn-Beuel",
                venue="Beueler Rathausplatz (Möhneplatz)", venue_id="",
                source="Beuel hat's", source_id="beuel-net",
            ),
            raw_event(
                "Floh- und Trödelmarkt Beueler Rathausplatz",
                "2026-10-25", city="Bonn", venue="Beueler Rathausplatz",
                venue_id="beueler-rathausplatz",
            ),
        ]
        unrelated = raw_event(
            "Floh- und Trödelmarkt Beueler Rathausplatz", "2026-11-01",
            city="Köln", venue="Rathausplatz", venue_id="",
        )

        rows, metadata, _updated = series.enrich_events(
            [*variants, unrelated], {"schema_version": 1, "series": {}},
            today=date(2026, 8, 20), generated_at="2026-08-20T12:00:00",
        )

        beuel = rows[:3]
        self.assertEqual(1, len({row["series_id"] for row in beuel}))
        self.assertEqual(
            {"Floh- und Trödelmarkt Beueler Rathausplatz"},
            {row["series_title"] for row in beuel},
        )
        self.assertEqual(
            ["2026-08-30", "2026-09-27", "2026-10-25"],
            next(
                item["occurrence_dates"]
                for item in metadata
                if item["title"] == "Floh- und Trödelmarkt Beueler Rathausplatz"
            ),
        )
        self.assertNotIn("series_id", rows[-1])

    def test_beuel_rathausplatz_flohmarkt_ledger_variants_migrate_together(self):
        ledger = {"schema_version": 1, "series": {}}
        for index, (title, venue, venue_id, city, day) in enumerate((
            (
                "Außer Haus: Floh- und Trödelmarkt auf dem Rathausplatz",
                "Brückenforum Bonn", "brueckenforum-bonn", "Bonn", "2026-08-30",
            ),
            (
                "Floh- und Trödelmarkt Beueler Rathausplatz",
                "Beueler Rathausplatz (Möhneplatz)", "", "Bonn-Beuel", "2026-09-27",
            ),
        )):
            ledger["series"][f"legacy-beuel-{index}"] = {
                "series_id": f"legacy-beuel-{index}", "title": title, "venue": venue,
                "canonical_venue_id": venue_id, "city": city, "category_key": "market",
                "first_seen": f"{day}T00:00:00", "last_seen": f"{day}T00:00:00",
                "occurrences": {str(index): day}, "announced_dates": [],
            }

        _rows, metadata, updated = series.enrich_events(
            [], ledger, today=date(2026, 8, 20), generated_at="2026-08-20T12:00:00",
        )

        self.assertEqual(1, len(updated["series"]))
        self.assertEqual("Floh- und Trödelmarkt Beueler Rathausplatz", metadata[0]["title"])
        self.assertEqual(
            ["2026-08-30", "2026-09-27"], metadata[0]["occurrence_dates"],
        )

    def test_primary_program_suppresses_only_covered_lower_authority_umbrellas(self):
        series_title = "Internationale Stummfilmtage – 42. Bonner Sommerkino"
        primary = [
            raw_event(
                title,
                day,
                source="Internationale Stummfilmtage",
                source_id="internationale-stummfilmtage",
                series_title=series_title,
                venue="Arkadenhof Universität Bonn",
                venue_id="arkadenhof-universitaet-bonn",
            )
            for title, day in (
                ("Should Men Walk Home?", "2026-08-17"),
                ("Schatten der Weltstadt", "2026-08-17"),
            )
        ]
        covered_umbrella = raw_event(
            series_title,
            "2026-08-17",
            source="Bonn.de Events",
            source_id="bonn-de-events",
            venue="Arkadenhof Universität Bonn",
            venue_id="arkadenhof-universitaet-bonn",
        )
        fallback_umbrella = raw_event(
            series_title,
            "2026-08-18",
            source="Bonn.de Events",
            source_id="bonn-de-events",
            venue="Arkadenhof Universität Bonn",
            venue_id="arkadenhof-universitaet-bonn",
        )
        cancelled_primary = raw_event(
            "Cancelled programme",
            "2026-08-18",
            status="cancelled",
            source="Internationale Stummfilmtage",
            source_id="internationale-stummfilmtage",
            series_title=series_title,
            venue="Arkadenhof Universität Bonn",
            venue_id="arkadenhof-universitaet-bonn",
        )
        postponed_primary = raw_event(
            "Postponed programme",
            "2026-08-19",
            status="postponed",
            source="Internationale Stummfilmtage",
            source_id="internationale-stummfilmtage",
            series_title=series_title,
            venue="Arkadenhof Universität Bonn",
            venue_id="arkadenhof-universitaet-bonn",
        )
        postponed_fallback = raw_event(
            series_title,
            "2026-08-19",
            source="Bonn.de Events",
            source_id="bonn-de-events",
            venue="Arkadenhof Universität Bonn",
            venue_id="arkadenhof-universitaet-bonn",
        )
        different_venue = raw_event(
            series_title,
            "2026-08-17",
            source="Bonn.de Events",
            source_id="bonn-de-events",
            venue="Brotfabrik Bonn",
            venue_id="brotfabrik-bonn",
        )
        peer_umbrella = raw_event(
            series_title,
            "2026-08-17",
            source="Festival venue",
            source_id="festival-venue",
            venue="Arkadenhof Universität Bonn",
            venue_id="arkadenhof-universitaet-bonn",
        )
        multiday_fallback = raw_event(
            series_title,
            "2026-08-17",
            end_date="2026-08-18",
            source="Bonn.de Events",
            source_id="bonn-de-events",
            venue="Arkadenhof Universität Bonn",
            venue_id="arkadenhof-universitaet-bonn",
        )
        aggregator_program = raw_event(
            "Aggregator programme item",
            "2026-08-17",
            source="Eventbrite",
            source_id="eventbrite",
            series_title=series_title,
            venue="Arkadenhof Universität Bonn",
            venue_id="arkadenhof-universitaet-bonn",
        )
        lower_authority_umbrella = raw_event(
            series_title,
            "2026-08-17",
            source="Tourismus NRW",
            source_id="tourismus-nrw",
            venue="Arkadenhof Universität Bonn",
            venue_id="arkadenhof-universitaet-bonn",
        )

        result = report.suppress_redundant_series_umbrellas([
            *primary, cancelled_primary, postponed_primary,
            covered_umbrella, fallback_umbrella, postponed_fallback, different_venue,
            peer_umbrella, multiday_fallback, aggregator_program,
            lower_authority_umbrella,
        ])

        self.assertEqual(
            [(event["title"], event["start_date"], event["venue_id"]) for event in result],
            [
                ("Should Men Walk Home?", "2026-08-17", "arkadenhof-universitaet-bonn"),
                ("Schatten der Weltstadt", "2026-08-17", "arkadenhof-universitaet-bonn"),
                ("Cancelled programme", "2026-08-18", "arkadenhof-universitaet-bonn"),
                ("Postponed programme", "2026-08-19", "arkadenhof-universitaet-bonn"),
                (series_title, "2026-08-18", "arkadenhof-universitaet-bonn"),
                (series_title, "2026-08-19", "arkadenhof-universitaet-bonn"),
                (series_title, "2026-08-17", "brotfabrik-bonn"),
                (series_title, "2026-08-17", "arkadenhof-universitaet-bonn"),
                (series_title, "2026-08-17", "arkadenhof-universitaet-bonn"),
                ("Aggregator programme item", "2026-08-17", "arkadenhof-universitaet-bonn"),
                (series_title, "2026-08-17", "arkadenhof-universitaet-bonn"),
            ],
        )

    def test_runner_publishes_primary_program_as_series_and_keeps_uncovered_fallback(self):
        series_title = "Internationale Stummfilmtage – 42. Bonner Sommerkino"
        primary = [
            raw_event(
                title,
                "2026-08-17",
                source="Internationale Stummfilmtage",
                source_id="internationale-stummfilmtage",
                series_title=series_title,
                venue="Arkadenhof Universität Bonn",
                venue_id="arkadenhof-universitaet-bonn",
            )
            for title in ("Should Men Walk Home?", "Schatten der Weltstadt")
        ]
        civic = [
            raw_event(
                series_title,
                day,
                source="Bonn.de Events",
                source_id="bonn-de-events",
                venue="Arkadenhof Universität Bonn",
                venue_id="arkadenhof-universitaet-bonn",
            )
            for day in ("2026-08-17", "2026-08-18")
        ]
        settings = config.RuntimeConfig(
            score_floor=0,
            radius_km=1000,
            series_ledger_json="",
        )
        context = RunContext(
            settings,
            EventWindow(datetime(2026, 8, 17), datetime(2026, 8, 18)),
            "primary-series",
            configure_logging("primary-series", "ERROR", "", ""),
            clock=lambda: datetime(2026, 8, 16, 12),
        )

        with mock.patch.object(runner, "_previous_snapshot", return_value={}):
            result = runner.run_import(context, {
                "Internationale Stummfilmtage": lambda: primary,
                "Bonn.de Events": lambda: civic,
            })

        # Sources run concurrently, so completion order is deliberately not a
        # publication contract. Verify the exact surviving occurrences without
        # coupling this test to thread scheduling.
        self.assertCountEqual(
            [(event.title, event.start_date, event.source_id) for event in result.events],
            [
                ("Should Men Walk Home?", "2026-08-17", "internationale-stummfilmtage"),
                ("Schatten der Weltstadt", "2026-08-17", "internationale-stummfilmtage"),
                (series_title, "2026-08-18", "bonn-de-events"),
            ],
        )
        self.assertEqual(len({event.series_id for event in result.events}), 1)
        self.assertEqual({event.series_title for event in result.events}, {series_title})

    def test_sundowner_ledger_title_variants_migrate_into_one_series(self):
        ledger = {"schema_version": 1, "series": {
            "short-title": {
                "series_id": "short-title",
                "title": "Sundowner Bar",
                "venue": "Bundeskunsthalle",
                "canonical_venue_id": "bundeskunsthalle",
                "city": "Bonn",
                "category_key": "nightlife",
                "first_seen": "2026-08-05T12:00:00",
                "last_seen": "2026-08-12T12:00:00",
                "occurrences": {"first": "2026-08-05", "second": "2026-08-12"},
                "announced_dates": [],
            },
            "roof-title": {
                "series_id": "roof-title",
                "title": "Sundowner Bar auf dem Dach der Bundeskunsthalle",
                "venue": "Bundeskunsthalle",
                "canonical_venue_id": "bundeskunsthalle",
                "city": "Bonn",
                "category_key": "nightlife",
                "first_seen": "2026-08-12T12:00:00",
                "last_seen": "2026-08-19T12:00:00",
                "occurrences": {"duplicate": "2026-08-12", "third": "2026-08-19"},
                "announced_dates": [],
            },
        }}

        _rows, metadata, updated = series.enrich_events(
            [],
            ledger,
            today=date(2026, 8, 11),
            generated_at="2026-08-11T12:00:00",
        )

        self.assertEqual(len(updated["series"]), 1)
        self.assertEqual(metadata[0]["title"], "Sundowner Bar")
        self.assertEqual(
            metadata[0]["occurrence_dates"],
            ["2026-08-05", "2026-08-12", "2026-08-19"],
        )

    def test_series_season_estimate_clamps_leap_day_in_both_candidate_years(self):
        ledger = {"schema_version": 1, "series": {"leap-day": {
            "series_id": "leap-day", "title": "Schalttag-Reihe", "venue": "Haus",
            "canonical_venue_id": "haus", "city": "Bonn", "category_key": "talk",
            "first_seen": "2016-02-29T00:00:00", "last_seen": "2025-01-01T00:00:00",
            "occurrences": {"a": "2016-02-29", "b": "2020-02-29"},
            "announced_dates": [],
        }}}

        _, [non_leap_current], _ = series.enrich_events(
            [], ledger, today=date(2026, 1, 1), generated_at="2026-01-01T00:00:00",
        )
        _, [non_leap_next], _ = series.enrich_events(
            [], ledger, today=date(2024, 8, 1), generated_at="2024-08-01T00:00:00",
        )

        self.assertEqual(non_leap_current["next_occurrence_estimated"], "2026-02-28")
        self.assertEqual(non_leap_next["next_occurrence_estimated"], "2025-02-28")

    def test_series_enrichment_failure_does_not_abort_import(self):
        settings = config.RuntimeConfig(score_floor=0, series_ledger_json="")
        context = RunContext(
            settings, EventWindow(datetime(2026, 8, 1), datetime(2026, 8, 28)),
            "series-failure", configure_logging("series-failure", "ERROR", "", ""),
        )

        with mock.patch.object(runner, "_previous_snapshot", return_value={}), \
                mock.patch.object(runner.series_entities, "enrich_events", side_effect=ValueError("bad season")):
            result = runner.run_import(context, {"Bonn.de Events": lambda: [raw_event()]})

        self.assertEqual([event.title for event in result.events], ["Flohmarkt Rheinaue"])
        self.assertEqual(result.series, ())
        self.assertEqual(result.run_status, "degraded")
        snapshot = runner.build_snapshot(result, context)
        self.assertIn(
            {
                "source": "series",
                "error_type": "ValueError",
                "error": "series enrichment failed: bad season",
            },
            snapshot.metadata["source_warnings"],
        )

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
            "first_seen": "2024-01-01T00:00:00", "last_seen": "2025-06-01T00:00:00",
            "occurrences": {"a": "2024-01-07", "b": "2024-02-04"}, "announced_dates": [],
        }}}
        _, [concluded], _ = series.enrich_events(
            [], old, today=date(2026, 5, 1), generated_at="2026-05-01T00:00:00",
        )
        self.assertEqual(concluded["series_state"], "concluded")

    def test_series_ledger_prunes_stale_rows_and_ignores_category_in_identity(self):
        stale = raw_event("Monatsmarkt", "2024-01-01", venue="Marktplatz", venue_id="market")
        old_key = series._identifier(("market", series._stem("Monatsmarkt")))
        ledger = {"schema_version": 1, "series": {old_key: {
            "series_id": old_key, "title": "Monatsmarkt", "venue": "Marktplatz",
            "canonical_venue_id": "market", "city": "Bonn", "category_key": "market",
            "first_seen": "2024-01-01T00:00:00", "last_seen": "2024-01-01T00:00:00",
            "occurrences": {"old": stale["start_date"]}, "announced_dates": [],
        }}}

        current = [
            raw_event("Lesereihe", "2026-08-02", venue="Haus", venue_id="haus", category_key="talk"),
            raw_event("Lesereihe", "2026-08-09", venue="Haus", venue_id="haus", category_key="other"),
        ]
        rows, _metadata, updated = series.enrich_events(
            current, ledger, today=date(2026, 8, 2), generated_at="2026-08-02T00:00:00",
        )

        self.assertEqual(len({row["series_id"] for row in rows}), 1)
        self.assertNotIn(old_key, updated["series"])

    def test_category_dependent_legacy_ids_are_migrated_and_merged(self):
        title = "Lesereihe"
        venue = "haus"
        legacy = {}
        for category, day in (("talk", "2026-07-01"), ("other", "2026-07-08")):
            legacy_id = series._identifier((venue, series._stem(title))) + "-" + category
            legacy[legacy_id] = {
                "series_id": legacy_id, "title": title, "venue": "Haus",
                "canonical_venue_id": venue, "city": "Bonn", "category_key": category,
                "first_seen": f"{day}T00:00:00", "last_seen": f"{day}T00:00:00",
                "occurrences": {category: day}, "announced_dates": [],
            }

        _, metadata, updated = series.enrich_events(
            [], {"schema_version": 1, "series": legacy},
            today=date(2026, 8, 2), generated_at="2026-08-02T00:00:00",
        )

        self.assertEqual(len(updated["series"]), 1)
        self.assertEqual(len(metadata), 1)
        self.assertEqual(metadata[0]["occurrence_dates"], ["2026-07-01", "2026-07-08"])

    def test_series_fallback_venue_identity_is_scoped_to_city(self):
        events = [
            raw_event("Sommerfest", "2026-08-02", venue="Marktplatz", venue_id="", city="Bonn"),
            raw_event("Sommerfest", "2026-08-09", venue="Marktplatz", venue_id="", city="Bonn"),
            raw_event("Sommerfest", "2026-08-03", venue="Marktplatz", venue_id="", city="Siegburg"),
            raw_event("Sommerfest", "2026-08-10", venue="Marktplatz", venue_id="", city="Siegburg"),
        ]

        rows, _metadata, _updated = series.enrich_events(
            events, {"schema_version": 1, "series": {}},
            today=date(2026, 8, 2), generated_at="2026-08-02T00:00:00",
        )

        self.assertEqual(len({row["series_id"] for row in rows}), 2)

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

    def test_cancelled_announced_date_is_not_a_future_occurrence(self):
        historical = [
            raw_event("Wintermarkt", day, venue="Marktplatz", venue_id="marktplatz")
            for day in ("2025-01-01", "2025-02-01")
        ]
        _, _, ledger = series.enrich_events(
            historical, {"schema_version": 1, "series": {}},
            today=date(2025, 1, 1), generated_at="2025-02-01T00:00:00",
            announced_events=[
                raw_event("Wintermarkt", "2026-09-01", venue="Marktplatz", venue_id="marktplatz"),
            ],
        )
        _, metadata, updated = series.enrich_events(
            [], ledger, today=date(2026, 8, 2), generated_at="2026-08-02T00:00:00",
            announced_events=[
                raw_event(
                    "Wintermarkt", "2026-09-01", venue="Marktplatz", venue_id="marktplatz",
                    status="cancelled",
                ),
            ],
        )
        self.assertEqual(metadata, [])
        self.assertEqual(updated["series"], {})

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
        self.assertEqual(
            first["categories"][0]["selectedEventIds"],
            [row["eventId"] for row in first["categories"][0]["selected"]],
        )

    def test_registry_fixture_is_schema_valid_region_aware_and_declarative(self):
        path = Path(__file__).parents[1] / "scripts/nrw_events/sources/registry.json"
        fixture = json.loads(path.read_text(encoding="utf-8"))
        specs = load_source_specs(path)
        self.assertEqual(fixture["schema_version"], 1)
        self.assertEqual(len(specs), 103)
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

        cards = SourceSpec(
            "cards-rich", "Cards Rich", ("https://example.test/events/",), AdapterType.HTML,
            "Bonn", category_hint="concert", selectors=(
                ("item", r"(<article>.*?</article>)"),
                ("title", r"<h2>(.*?)</h2>"),
                ("date", r"<time>(.*?)</time>"),
                ("time", r"<span class='time'>(.*?)</span>"),
                ("link", r"href='(.*?)'"),
                ("venue", r"<address>(.*?)</address>"),
            ),
        )
        with mock.patch("nrw_events.source_specs.common.fetch_url", return_value=(
            "<article><h2>Abendkonzert</h2><time>15.08.2026</time>"
            "<span class='time'>19:30</span><a href='detail'>Mehr</a>"
            "<address>Rheinaue</address><p>Live-Musik.</p></article>"
        )):
            [rich] = adapter_for(cards)()
        self.assertEqual(rich["time"], "19:30")
        self.assertTrue(rich["start_at"].startswith("2026-08-15T19:30"))
        self.assertEqual(rich["venue"], "Rheinaue")
        self.assertEqual(rich["link"], "https://example.test/events/detail")

        with mock.patch("nrw_events.source_specs.common.fetch_url", return_value="<main>changed</main>"):
            result, parsed = runner._run_source("Cards Rich", adapter_for(cards))
        self.assertEqual(parsed, [])
        self.assertEqual(result.status, runner.SourceStatus.PARSER_EMPTY)

        detail = SourceSpec(
            "detail", "Detail", ("https://example.test/index",), AdapterType.JSON_LD,
            "Bonn", detail_urls=("https://example.test/detail",),
        )
        with mock.patch("nrw_events.source_specs.common.fetch_url", return_value="{}"), \
                mock.patch("nrw_events.source_specs.common.fetch_detail_url", return_value="{}") as cached, \
                mock.patch("nrw_events.source_specs.common.events_from_jsonld", return_value=[]):
            self.assertEqual(adapter_for(detail)(), [])
        cached.assert_called_once()

    def test_typed_adapter_lifts_legacy_lists_and_passes_specs_to_new_fetchers(self):
        legacy = SourceSpec(
            "legacy", "Legacy", adapter=AdapterType.PYTHON, region="test",
            callable="nrw_events.sources.harmonie:fetch",
        )
        with mock.patch("nrw_events.source_specs.adapter_for", return_value=lambda: [{"title": "A"}]):
            result = typed_adapter_for(legacy)()
        self.assertIsInstance(result, SourceFetchResult)
        self.assertEqual(result.status, SourceStatus.HEALTHY)
        self.assertEqual(result.events, ({"title": "A"},))

        received = []
        with mock.patch(
            "nrw_events.source_specs.adapter_for",
            return_value=lambda spec: received.append(spec) or [],
        ):
            typed_adapter_for(legacy)()
        self.assertEqual(received, [legacy])


if __name__ == "__main__":
    unittest.main()
