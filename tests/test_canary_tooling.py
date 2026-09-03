import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.check_canary import canary_problems, report_markdown

ROOT = Path(__file__).resolve().parents[1]


class CanaryToolingTests(unittest.TestCase):
    def test_canary_reports_degraded_sources_and_baseline_anomalies(self):
        metadata = {
            "run_status": "degraded", "run_id": "canary-1",
            "generated_at": "2026-08-03T05:17:00", "event_count": 12,
            "source_results": {
                "Broken Calendar": {
                    "status": "parser_empty",
                    "anomalies": ["raw event count dropped from 20 to 0"],
                    "error": None,
                },
            },
        }

        problems = canary_problems(metadata)

        self.assertIn("run status is `degraded`", problems)
        self.assertTrue(any("Broken Calendar" in problem for problem in problems))
        self.assertIn("raw event count dropped from 20 to 0", report_markdown(metadata, problems))

    def test_canary_accepts_healthy_metadata(self):
        metadata = {"run_status": "healthy", "source_results": {}}
        self.assertEqual(canary_problems(metadata), [])

    def test_fixture_refresh_dry_run_uses_manifest_allowlist(self):
        result = subprocess.run(
            [sys.executable, "scripts/refresh_fixtures.py", "--source", "uni-bonn", "--dry-run"],
            cwd=ROOT, check=True, capture_output=True, text=True,
        )
        self.assertIn("tests/fixtures/uni-bonn/calendar.ics", result.stdout)
        self.assertIn("tests/fixtures/uni-bonn/choir-detail.html", result.stdout)

    def test_canary_cli_writes_report_and_fails_for_bad_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            metadata = root / "metadata.json"
            report = root / "report.md"
            metadata.write_text(json.dumps({"run_status": "failed"}), encoding="utf-8")

            result = subprocess.run(
                [sys.executable, "scripts/check_canary.py", str(metadata), "--report", str(report)],
                check=False, cwd=ROOT, capture_output=True, text=True,
            )

            self.assertEqual(result.returncode, 1)
            self.assertIn("run status is `failed`", report.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
