"""A literal prerequisite skips impossible regex matches without changing policy."""

import re
import unittest
from dataclasses import replace
from unittest.mock import Mock

from nrw_events import category_taxonomy as taxonomy


class TaxonomyLiteralGuardTests(unittest.TestCase):
    def keyword(self, value="jazz", mode="word"):
        return taxonomy._keyword_from_spec({
            "value": value, "match_mode": mode, "scope": "all", "weight": 1, "comment": "fixture",
        })

    def test_missing_literal_does_not_run_the_regex(self):
        keyword = self.keyword()
        pattern = Mock(wraps=keyword.pattern)
        keyword = replace(keyword, pattern=pattern)
        self.assertFalse(taxonomy._matches("ein abend im museum", keyword, is_title=True))
        pattern.search.assert_not_called()

    def test_guard_preserves_all_match_modes_and_unicode_boundaries(self):
        for value in ("jazz", "fest", "töpfer", "a+b", "zwei worte", "i", "ß"):
            for mode in ("word", "word_prefix", "word_suffix", "compound_word"):
                keyword = self.keyword(value, mode)
                for text in (
                    value, f"vor{value}", f"{value}nach", f"vor{value}nach",
                    f"({value})", f"_{value}_", f"1{value}2", f"İ{value}ſ", "manifest", "", "ohne treffer",
                ):
                    normalized = taxonomy.comparison_text(text)
                    if keyword.compound_word:
                        expected = any(match.group(0) != keyword.normalized_value for match in keyword.pattern.finditer(normalized))
                    elif keyword.word_suffix and keyword.normalized_value == "fest":
                        expected = any(match.group(0) != "manifest" for match in keyword.pattern.finditer(normalized))
                    else:
                        expected = keyword.pattern.search(normalized) is not None
                    with self.subTest(value=value, mode=mode, text=text):
                        self.assertEqual(taxonomy._matches(normalized, keyword, is_title=True), expected)

    def test_custom_patterns_are_not_assumed_to_require_the_label(self):
        custom = taxonomy.Keyword("jazz", "jazz", re.compile("blues", re.I))
        self.assertTrue(taxonomy._matches("BLUES", custom, is_title=True))


if __name__ == "__main__":
    unittest.main()
