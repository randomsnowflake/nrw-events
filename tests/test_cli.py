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

    def test_filters_apply_category_free_radius_and_evening_semantics(self):
        today = datetime(2026, 7, 31)
        result = runner.ImportResult((
            event("Free market", "2026-07-31", time="18:00", category="market", distance=5, free=True),
            event("Paid market", "2026-07-31", time="19:00", category="market", distance=5),
            event("Far concert", "2026-07-31", time="20:00", category="concert", distance=30, free=True),
            event("Morning market", "2026-07-31", time="10:00", category="market", distance=5, free=True),
        ), {}, 4, "healthy")
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
        }, clear=True), mock.patch.object(runner, "run_import", side_effect=fake_import), \
                mock.patch.object(runner, "publish_snapshot") as publish, contextlib.redirect_stdout(stdout):
            exit_code = runner.cli(["nrw-events", "heute", "--json"])

            self.assertFalse(os.path.exists(os.path.join(tmpdir, "events.json")))
            self.assertFalse(os.path.exists(os.path.join(tmpdir, "meta.json")))

        self.assertEqual(exit_code, runner.EXIT_SUCCESS)
        self.assertEqual(json.loads(stdout.getvalue())[0]["title"], "Machine readable")
        publish.assert_not_called()

    def test_cli_flags_override_environment(self):
        with mock.patch.dict(os.environ, {
            "NRW_EVENTS_RADIUS_KM": "75",
            "NRW_EVENTS_CATEGORIES": "concert",
            "NRW_EVENTS_FREE_ONLY": "0",
        }, clear=True):
            _, _, overrides = runner._parse_cli([
                "nrw-events", "--umkreis", "15km", "--kategorie", "markt,festival", "--kostenlos",
            ])

        self.assertEqual(overrides["radius_km"], 15)
        self.assertEqual(overrides["categories"], ("market", "festival"))
        self.assertTrue(overrides["free_only"])


if __name__ == "__main__":
    unittest.main()
