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
        sources = (ROOT / "docs/sources.md").read_text(encoding="utf-8")
        inventory = (ROOT / "docs/modules.md").read_text(encoding="utf-8")
        self.assertTrue(all(row["display_name"] in sources for row in registry["sources"]))
        self.assertTrue(all(module in inventory for module in modules | source_modules))
        for document in ("README.md", "SKILL.md"):
            text = (ROOT / document).read_text(encoding="utf-8")
            self.assertIn("docs/sources.md", text)
            self.assertIn("docs/modules.md", text)
            self.assertNotIn("<!-- BEGIN GENERATED", text)



if __name__ == "__main__":
    unittest.main()
