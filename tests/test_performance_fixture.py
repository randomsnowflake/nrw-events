"""Synthetic scale fixtures must be reproducible and cannot overwrite inputs."""

import json
import tempfile
import unittest
from pathlib import Path

from generate_performance_fixture import generate


class PerformanceFixtureTests(unittest.TestCase):
    def test_generated_record_count_calendar_hosts_and_repeatability(self):
        with tempfile.TemporaryDirectory() as temporary:
            first = Path(temporary) / "first"
            second = Path(temporary) / "second"
            manifest = generate(first, 128)
            generate(second, 128)
            self.assertEqual(len(json.loads(manifest.read_text())["sources"]), 2)
            calendars = [path.read_text() for path in sorted(first.glob("*.ics"))]
            self.assertEqual(sum(text.count("BEGIN:VEVENT") for text in calendars), 128)
            for token in ("RRULE:", "RDATE:", "EXDATE:", "STATUS:CANCELLED"):
                self.assertIn(token, "".join(calendars))
            for path in first.iterdir():
                self.assertEqual(path.read_bytes(), (second / path.name).read_bytes())
            with self.assertRaisesRegex(ValueError, "already exist"):
                generate(first, 256)

    def test_empty_fixture_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary, self.assertRaises(ValueError):
            generate(Path(temporary), 0)


if __name__ == "__main__":
    unittest.main()
