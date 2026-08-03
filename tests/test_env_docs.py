import tempfile
from pathlib import Path
import unittest

from scripts import check_env_docs


class EnvironmentDocumentationTests(unittest.TestCase):
    def test_ast_scan_recognizes_source_specific_environment_helpers(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            source = Path(tmpdir) / "source.py"
            source.write_text(
                '_env_number("NRW_EVENTS_BONN_DETAIL_DESCRIPTION_MAX_CHARS", 0)\n',
                encoding="utf-8",
            )

            variables = check_env_docs.environment_variables(source)

        self.assertEqual(
            variables,
            {"NRW_EVENTS_BONN_DETAIL_DESCRIPTION_MAX_CHARS"},
        )


if __name__ == "__main__":
    unittest.main()
