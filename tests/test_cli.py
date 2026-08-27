import contextlib
import io
import json
import os
import tempfile
import unittest
from datetime import datetime
from unittest import mock

from nrw_events import config, runner
from nrw_events.models import CanonicalEvent


def event(title, date, *, time="", category="other", distance=0, free=False):
    return CanonicalEvent(
        title=title,
        source="Test",
        start_date=date,
        end_date=date,
        score=1.0,
        time=time,
        category_key=category,
        distance_km=distance,
        admission={"isFree": free},
    )


class CliTests(unittest.TestCase):
    def test_positional_days_and_explicit_days_are_backward_compatible(self):
        self.assertEqual(runner._parse_cli(["nrw-events", "7"])[0], 7)
        self.assertEqual(runner._parse_cli(["nrw-events", "--days", "7"])[0], 7)
        with self.assertRaisesRegex(ValueError, "either positionally"):
            runner._parse_cli(["nrw-events", "7", "--days", "8"])

    def test_verbs_choose_expected_import_windows(self):
        monday = datetime(2026, 8, 3, 10)
        self.assertEqual(runner._parse_cli(["nrw-events", "heute"], monday)[0], 1)
        self.assertEqual(runner._parse_cli(["nrw-events", "heute-abend"], monday)[0], 1)
        self.assertEqual(runner._parse_cli(["nrw-events", "wochenende"], monday)[0], 7)

    def test_verbs_reject_a_conflicting_explicit_day_count(self):
        monday = datetime(2026, 8, 3, 10)
        for argv in (
            ["nrw-events", "heute", "--days", "7"],
            ["nrw-events", "wochenende", "--days", "14"],
        ):
            with self.subTest(argv=argv), self.assertRaisesRegex(ValueError, "own window"):
                runner._parse_cli(argv, monday)

    def test_filters_apply_category_free_radius_and_evening_semantics(self):
        today = datetime(2026, 7, 31)
        result = runner.ImportResult((
            event("Free market", "2026-07-31", time="18:00", category="market", distance=5, free=True),
            event("Paid market", "2026-07-31", time="19:00", category="market", distance=5),
            event("Far concert", "2026-07-31", time="20:00", category="concert", distance=30, free=True),
            event("Morning market", "2026-07-31", time="10:00", category="market", distance=5, free=True),
            event("Day market", "2026-07-31", time="10:00–18:00", category="market", distance=5, free=True),
        ), {}, 5, "healthy")
        settings = config.RuntimeConfig(
            radius_km=15,
            categories=("market",),
            free_only=True,
        )

        filtered = runner.filter_import_result(result, settings, runner.CliQuery("heute-abend"), today)

        self.assertEqual([candidate.title for candidate in filtered.events], ["Free market"])

    def test_json_writes_only_events_to_stdout_and_skips_state_files(self):
        def fake_import(context, sources):
            date = context.window.start.strftime("%Y-%m-%d")
            return runner.ImportResult((event("Machine readable", date),), {}, 1, "healthy")

        stdout = io.StringIO()
        with tempfile.TemporaryDirectory() as tmpdir, mock.patch.dict(os.environ, {
            "NRW_EVENTS_ENV_FILE": os.path.join(tmpdir, "missing.env"),
            "NRW_EVENTS_JSON_OUT": os.path.join(tmpdir, "events.json"),
            "NRW_EVENTS_META_JSON_OUT": os.path.join(tmpdir, "meta.json"),
            "NRW_EVENTS_LOG_LEVEL": "CRITICAL",
        }, clear=True), mock.patch.object(runner, "run_import", side_effect=fake_import), \
                mock.patch.object(runner, "publish_snapshot") as publish, contextlib.redirect_stdout(stdout):
            exit_code = runner.cli(["nrw-events", "heute", "--json"])

            self.assertFalse(os.path.exists(os.path.join(tmpdir, "events.json")))
            self.assertFalse(os.path.exists(os.path.join(tmpdir, "meta.json")))

        self.assertEqual(exit_code, runner.EXIT_SUCCESS)
        self.assertEqual(json.loads(stdout.getvalue())[0]["title"], "Machine readable")
        publish.assert_not_called()

    def test_filtered_cli_publishes_unfiltered_canonical_snapshot(self):
        def fake_import(context, sources):
            self.assertEqual(context.settings.radius_km, 75)
            date = context.window.start.strftime("%Y-%m-%d")
            return runner.ImportResult((
                event("Visible market", date, category="market"),
                event("Canonical concert", date, category="concert", distance=30),
            ), {}, 2, "healthy")

        stdout = io.StringIO()
        with tempfile.TemporaryDirectory() as tmpdir, mock.patch.dict(os.environ, {
            "NRW_EVENTS_ENV_FILE": os.path.join(tmpdir, "missing.env"),
            "NRW_EVENTS_JSON_OUT": os.path.join(tmpdir, "events.json"),
            "NRW_EVENTS_META_JSON_OUT": os.path.join(tmpdir, "meta.json"),
            "NRW_EVENTS_HIGHLIGHTS_JSON_OUT": os.path.join(tmpdir, "highlights.json"),
            "NRW_EVENTS_SERIES_LEDGER_JSON": os.path.join(tmpdir, "series.json"),
        }, clear=True), mock.patch.object(runner, "run_import", side_effect=fake_import), \
                mock.patch.object(runner, "publish_snapshot") as publish, contextlib.redirect_stdout(stdout):
            exit_code = runner.cli([
                "nrw-events", "heute", "--kategorie", "markt", "--umkreis", "15km",
            ])

        self.assertEqual(exit_code, runner.EXIT_SUCCESS)
        published = publish.call_args.args[0]
        self.assertEqual([row["title"] for row in published.events], ["Visible market", "Canonical concert"])
        self.assertEqual(published.metadata["event_count"], 2)
        self.assertIn("Visible market", stdout.getvalue())
        self.assertNotIn("Canonical concert", stdout.getvalue())

    def test_cli_flags_override_environment(self):
        with mock.patch.dict(os.environ, {
            "NRW_EVENTS_RADIUS_KM": "75",
            "NRW_EVENTS_CATEGORIES": "concert",
            "NRW_EVENTS_FREE_ONLY": "0",
        }, clear=True):
            _, _, overrides = runner._parse_cli([
                "nrw-events", "--umkreis", "15km", "--kategorie", "markt,festival", "--kostenlos",
                "--max-per-section", "4", "--max-chars", "12000",
            ])

        self.assertEqual(overrides["radius_km"], 15)
        self.assertEqual(overrides["categories"], ("market", "festival"))
        self.assertTrue(overrides["free_only"])
        self.assertEqual(overrides["max_per_section"], 4)
        self.assertEqual(overrides["report_max_chars"], 12000)

    def test_report_limit_flags_are_validated(self):
        with self.assertRaisesRegex(ValueError, "max-per-section"):
            runner._parse_cli(["nrw-events", "--max-per-section", "-1"])
        with self.assertRaisesRegex(ValueError, "max-chars"):
            runner._parse_cli(["nrw-events", "--max-chars", "199"])

    def test_source_filter_is_repeatable_and_validated(self):
        _, query, _ = runner._parse_cli([
            "nrw-events", "7", "--source", "bonn-de-events", "--source", "uni-bonn,bonn-de-events",
        ])

        self.assertEqual(query.source_ids, ("bonn-de-events", "uni-bonn"))
        with self.assertRaisesRegex(ValueError, "unknown source"):
            runner._parse_cli(["nrw-events", "7", "--source", "does-not-exist"])

    def test_targeted_sources_fetch_selected_and_schedule_skip_all_others(self):
        selected = mock.Mock(return_value=[])
        other = mock.Mock(return_value=[])
        with mock.patch.object(runner, "SOURCES", {
            "Bonn.de Events": selected,
            "Universität Bonn": other,
        }), mock.patch.object(runner, "SOURCE_IDS", {
            "Bonn.de Events": "bonn-de-events",
            "Universität Bonn": "uni-bonn",
        }):
            sources = runner._targeted_sources(("bonn-de-events",))

        self.assertIs(sources["Bonn.de Events"], selected)
        skipped = sources["Universität Bonn"]()
        self.assertEqual(skipped.status, "scheduled_skip")
        self.assertIn("previous snapshot", skipped.disabled_reason)
        other.assert_not_called()

    def test_targeted_refresh_requires_a_readable_previous_snapshot(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = config.RuntimeConfig(
                meta_json_out=os.path.join(tmpdir, "missing-meta.json"),
            )

            with self.assertRaisesRegex(ValueError, "readable previous snapshot"):
                runner._validate_targeted_refresh_snapshot(
                    settings,
                    ("bonn-de-events",),
                )

    def test_targeted_refresh_accepts_a_valid_empty_previous_snapshot(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            metadata_path = os.path.join(tmpdir, "meta.json")
            with open(metadata_path, "w", encoding="utf-8") as handle:
                json.dump({"events": []}, handle)
            settings = config.RuntimeConfig(meta_json_out=metadata_path)

            runner._validate_targeted_refresh_snapshot(
                settings,
                ("bonn-de-events",),
            )

    def test_targeted_refresh_rejects_an_unreadable_external_event_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            metadata_path = os.path.join(tmpdir, "meta.json")
            with open(metadata_path, "w", encoding="utf-8") as handle:
                json.dump({"events_path": "missing-events.json"}, handle)
            settings = config.RuntimeConfig(meta_json_out=metadata_path)

            with self.assertRaisesRegex(ValueError, "readable previous snapshot"):
                runner._validate_targeted_refresh_snapshot(
                    settings,
                    ("bonn-de-events",),
                )

    def test_targeted_refresh_rejects_a_non_array_external_event_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            metadata_path = os.path.join(tmpdir, "meta.json")
            events_path = os.path.join(tmpdir, "events.json")
            with open(metadata_path, "w", encoding="utf-8") as handle:
                json.dump({"events_path": events_path}, handle)
            with open(events_path, "w", encoding="utf-8") as handle:
                json.dump({"events": []}, handle)
            settings = config.RuntimeConfig(meta_json_out=metadata_path)

            with self.assertRaisesRegex(ValueError, "readable previous snapshot"):
                runner._validate_targeted_refresh_snapshot(
                    settings,
                    ("bonn-de-events",),
                )

    def test_unreadable_external_event_file_preserves_source_metadata(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            metadata_path = os.path.join(tmpdir, "meta.json")
            source_results = {"Bonn.de Events": {"accepted_event_count": 10}}
            with open(metadata_path, "w", encoding="utf-8") as handle:
                json.dump({
                    "events_path": "missing-events.json",
                    "source_results": source_results,
                }, handle)

            previous = runner._previous_snapshot(metadata_path)

            self.assertNotIn("events", previous)
            self.assertEqual(previous["source_results"], source_results)


if __name__ == "__main__":
    unittest.main()
