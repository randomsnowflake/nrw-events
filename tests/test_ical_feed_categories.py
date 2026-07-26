"""A feed's per-event CATEGORIES must reach classification, not be overridden.

A ``SourceSpec`` category hint describes the whole feed; ``CATEGORIES`` describes the
single event. The ionas4 municipal calendars tag every entry precisely
("Markt,Trödelmarkt" vs "Volksfest,Fest" vs "Ausstellung,Kunst"), and the Troisdorf
spec's hint "troisdorf lokal kultur markt" used to replace that detail wholesale —
so every Troisdorf event arrived carrying the same generic text.
"""

import unittest
from datetime import datetime
from unittest import mock

from nrw_events import common


def _calendar(*, summary, categories="", ionas_category=""):
    lines = [
        "BEGIN:VCALENDAR",
        "BEGIN:VEVENT",
        f"SUMMARY:{summary}",
        "DTSTART:20260919T110000",
        "DTEND:20260919T170000",
        "LOCATION:Troisdorf",
    ]
    if ionas_category:
        lines.append(f"X-IONAS-CATEGORY:{ionas_category}")
    if categories:
        lines.append(f"CATEGORIES:{categories}")
    lines += ["END:VEVENT", "END:VCALENDAR"]
    return "\n".join(lines)


class IcalFeedCategoryTests(unittest.TestCase):
    def _fetch(self, payload, hint=""):
        with mock.patch.object(common, "TODAY", datetime(2026, 9, 14)), \
                mock.patch.object(common, "END_DATE", datetime(2026, 9, 25)), \
                mock.patch.object(common, "fetch_url", return_value=payload):
            return common.fetch_ical(
                "https://example.test/event.ics", "Troisdorf", "Troisdorf", hint)

    def test_event_categories_replace_the_static_feed_hint(self):
        payload = _calendar(summary="Spicher Dorftrödel",
                            categories="Markt,Trödelmarkt,Innenstadt",
                            ionas_category="Markt")

        events = self._fetch(payload, hint="troisdorf lokal kultur markt")

        self.assertEqual(len(events), 1)
        category_text = events[0]["category"]
        self.assertEqual(category_text, "Markt,Trödelmarkt,Innenstadt")
        self.assertNotIn("troisdorf lokal kultur markt", category_text)

    def test_flea_market_tag_classifies_as_market(self):
        payload = _calendar(summary="Spicher Dorftrödel",
                            categories="Markt,Trödelmarkt,Innenstadt")

        events = self._fetch(payload, hint="troisdorf lokal kultur markt")

        self.assertEqual(events[0]["category_key"], "market")

    def test_distinct_feed_tags_are_no_longer_flattened_by_the_hint(self):
        """The hint alone cannot tell an exhibition from a flea market."""
        market = self._fetch(
            _calendar(summary="Garagenflohmarkt Müllekoven",
                      categories="Markt,Trödelmarkt"),
            hint="troisdorf lokal kultur markt")
        exhibition = self._fetch(
            _calendar(summary="Bilderschau Burg Wissem", categories="Ausstellung,Kunst"),
            hint="troisdorf lokal kultur markt")

        self.assertIn("Trödelmarkt", market[0]["category"])
        self.assertIn("Ausstellung", exhibition[0]["category"])
        self.assertNotEqual(market[0]["category"], exhibition[0]["category"])

    def test_hint_only_feeds_are_unchanged(self):
        """Regression: a feed without CATEGORIES must still use the hint alone."""
        events = self._fetch(_calendar(summary="Konzert im Park"), hint="concert musik")

        self.assertEqual(events[0]["category"], "concert musik")

    def test_categories_only_feeds_are_unchanged(self):
        """Regression: Wachtberg registers no hint and must keep using CATEGORIES."""
        events = self._fetch(
            _calendar(summary="Dorfflohmarkt in Pech", categories="Markt,Flohmarkt"))

        self.assertEqual(events[0]["category"], "Markt,Flohmarkt")

    def test_event_categories_cannot_form_a_broad_bag_with_the_hint(self):
        events = self._fetch(
            _calendar(summary="Sommerabend", categories="Party,Festival"),
            hint="concert",
        )

        self.assertEqual(events[0]["category"], "Party,Festival")
        self.assertNotEqual(events[0]["category_key"], "other")

    def test_blank_hint_and_blank_categories_do_not_leak_whitespace(self):
        events = self._fetch(_calendar(summary="Treffen", categories=""), hint="   ")

        self.assertEqual(events[0]["category"], "")


if __name__ == "__main__":
    unittest.main()
