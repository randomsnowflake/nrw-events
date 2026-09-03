import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class GeneratedDocumentationTests(unittest.TestCase):
    def test_generated_blocks_are_current_and_complete(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts/generate_docs.py"), "--check"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        registry = json.loads(
            (ROOT / "scripts/nrw_events/sources/registry.json").read_text(encoding="utf-8")
        )
        modules = {path.name for path in (ROOT / "scripts/nrw_events").glob("*.py")}
        source_modules = {
            path.name for path in (ROOT / "scripts/nrw_events/sources").glob("*.py")
        }
        for document in ("README.md", "SKILL.md"):
            text = (ROOT / document).read_text(encoding="utf-8")
            with self.subTest(document=document):
                self.assertTrue(all(row["display_name"] in text for row in registry["sources"]))
                self.assertTrue(all(module in text for module in modules))
                self.assertTrue(all(module in text for module in source_modules))


if __name__ == "__main__":
    unittest.main()
