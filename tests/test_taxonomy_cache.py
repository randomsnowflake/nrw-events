"""Reuse only pure keyword matching, never mutable classifications or policy."""

import os
import re
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from nrw_events import category_taxonomy as taxonomy


class TaxonomyCacheTests(unittest.TestCase):
    def setUp(self):
        taxonomy._cached_keyword_matches.cache_clear()
        self.addCleanup(taxonomy._cached_keyword_matches.cache_clear)
        self.environment = patch.dict(os.environ, {"NRW_EVENTS_TAXONOMY_CACHE": "1"})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def test_same_input_reuses_work_and_returns_detached_list(self):
        with patch.object(taxonomy, "_matches", wraps=taxonomy._matches) as matches:
            first = taxonomy._matched_keywords("jazz konzert", ("jazz", "markt"), is_title=True)
            first.clear()
            second = taxonomy._matched_keywords("jazz konzert", ("jazz", "markt"), is_title=True)
        self.assertEqual(second, ["jazz"])
        self.assertEqual(matches.call_count, 2)

    def test_scope_pattern_flags_and_keyword_policy_are_in_key(self):
        title_only = taxonomy.Keyword("jazz", "jazz", re.compile("jazz"), title_only=True)
        insensitive = taxonomy.Keyword("jazz", "jazz", re.compile("jazz", re.I))
        sensitive = taxonomy.Keyword("jazz", "jazz", re.compile("jazz"))
        self.assertEqual(taxonomy._matched_keywords("jazz", (title_only,), is_title=False), [])
        self.assertEqual(taxonomy._matched_keywords("jazz", (title_only,), is_title=True), [title_only])
        self.assertEqual(taxonomy._matched_keywords("JAZZ", (insensitive,), is_title=True), [insensitive])
        self.assertEqual(taxonomy._matched_keywords("JAZZ", (sensitive,), is_title=True), [])

    def test_disabled_cache_performs_all_matching(self):
        with patch.dict(os.environ, {"NRW_EVENTS_TAXONOMY_CACHE": "0"}), patch.object(
            taxonomy, "_matches", wraps=taxonomy._matches,
        ) as matches:
            for _ in range(2):
                self.assertEqual(taxonomy._matched_keywords("jazz", ("jazz",), is_title=True), ["jazz"])
        self.assertEqual(matches.call_count, 2)
        self.assertEqual(taxonomy._cached_keyword_matches.cache_info().currsize, 0)

    def test_oversized_text_is_not_retained(self):
        text = "jazz " * 2000
        self.assertEqual(taxonomy._matched_keywords(text, ("jazz",), is_title=True), ["jazz"])
        self.assertEqual(taxonomy._cached_keyword_matches.cache_info().currsize, 0)

    def test_size_is_bounded_and_concurrent_results_are_stable(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(
                lambda index: taxonomy._matched_keywords(f"jazz {index}", ("jazz",), is_title=True),
                range(10_000),
            ))
        self.assertTrue(all(result == ["jazz"] for result in results))
        self.assertEqual(taxonomy._cached_keyword_matches.cache_info().currsize, taxonomy.KEYWORD_CACHE_SIZE)

    def test_whole_classification_remains_fresh_for_all_inputs(self):
        inputs = [
            {"source_category": "Kultur", "title": "Offener Abend", "description": "Jazzkonzert", "venue": "Brotfabrik"},
            {"source_category": "Sport", "title": "Offener Abend", "description": "Fußballturnier", "venue": "Sportplatz"},
            {"source_category": "", "title": "Offener Abend", "source": "Alpha", "source_id": "alpha", "default_category_key": "concert", "category_locked": True},
            {"source_category": "", "title": "Offener Abend", "source": "Beta", "source_id": "beta", "default_category_key": "sports", "category_locked": True},
        ]
        for values in inputs:
            with self.subTest(values=values):
                with patch.dict(os.environ, {"NRW_EVENTS_TAXONOMY_CACHE": "0"}):
                    expected = taxonomy.categorize_event(**values)
                actual = taxonomy.categorize_event(**values)
                self.assertEqual(actual, expected)
                actual["key"] = "changed"
                self.assertEqual(taxonomy.categorize_event(**values), expected)

    def test_reviewed_fallback_changes_are_not_hidden_by_keyword_cache(self):
        title = "Quuxxyz Abcxyz"
        cache_key = taxonomy.category_cache_key("alpha", title)
        with patch.object(taxonomy, "_FALLBACK_CACHE", {}):
            self.assertEqual(taxonomy.categorize_event("", title, source_id="alpha")["key"], "other")
        reviewed = {cache_key: {"key": "talk", "label": "Vortrag", "confidence": 0.9, "reason": "reviewed"}}
        with patch.object(taxonomy, "_FALLBACK_CACHE", reviewed):
            self.assertEqual(taxonomy.categorize_event("", title, source_id="alpha")["key"], "talk")
            self.assertEqual(taxonomy.categorize_event("", title, source_id="beta")["key"], "other")


if __name__ == "__main__":
    unittest.main()
