"""Normalization cache keys include the separator and preserve exact semantics."""

import os
import unittest
from unittest.mock import patch

from nrw_events import normalization


class NormalizationCacheTests(unittest.TestCase):
    def setUp(self):
        normalization._cached_comparison_text.cache_clear()
        self.addCleanup(normalization._cached_comparison_text.cache_clear)
        environment = patch.dict(os.environ, {"NRW_EVENTS_NORMALIZATION_CACHE": "1"})
        environment.start()
        self.addCleanup(environment.stop)

    def test_cached_values_match_uncached_unicode_and_separator_behavior(self):
        for value in ("", "Straße & Fußball", "KÖLN", "Cafe\u0301", "Rock’n’Roll", "中文 🎵", "  A---B  "):
            for separator in (" ", "-", "", "__"):
                with self.subTest(value=value, separator=separator):
                    with patch.dict(os.environ, {"NRW_EVENTS_NORMALIZATION_CACHE": "0"}):
                        expected = normalization.comparison_text(value, separator=separator)
                    self.assertEqual(normalization.comparison_text(value, separator=separator), expected)
                    self.assertEqual(normalization.comparison_text(value, separator=separator), expected)

    def test_repeated_values_reuse_work_and_separator_is_part_of_key(self):
        with patch.object(normalization, "_comparison_text_uncached", wraps=normalization._comparison_text_uncached) as uncached:
            self.assertEqual(normalization.comparison_text("A & B"), "a b")
            self.assertEqual(normalization.comparison_text("A & B"), "a b")
            self.assertEqual(normalization.comparison_text("A & B", separator="-"), "a-b")
        self.assertEqual(uncached.call_count, 2)

    def test_disable_switch_and_oversized_values_bypass_cache(self):
        with patch.dict(os.environ, {"NRW_EVENTS_NORMALIZATION_CACHE": "0"}):
            normalization.comparison_text("uncached")
        normalization.comparison_text("x" * 5000)
        self.assertEqual(normalization._cached_comparison_text.cache_info().currsize, 0)

    def test_null_compatibility_is_unchanged(self):
        self.assertEqual(normalization.comparison_text(None), "")

    def test_cache_size_is_bounded(self):
        for index in range(20_000):
            normalization.comparison_text(f"unique value {index}")
        self.assertEqual(normalization._cached_comparison_text.cache_info().currsize, normalization.COMPARISON_CACHE_SIZE)


if __name__ == "__main__":
    unittest.main()
