"""Early quality pruning preserves complete records, diagnostics, and ledgers."""

import os
import unittest
from dataclasses import replace
from datetime import datetime
from unittest.mock import patch

from nrw_events import common, core, performance, runner
from nrw_events.benchmark import replay_differences
from nrw_events.models import EventDraft
from nrw_events.runtime import EventWindow

from .helpers import make_runner_env


def calendar(summary="Blutspende", dates="DTSTART:20260102T180000\nDTEND:20260102T190000", extra=""):
    return (
        "BEGIN:VCALENDAR\nVERSION:2.0\nBEGIN:VEVENT\nUID:fixture\n"
        f"SUMMARY:{summary}\n{dates}\nLOCATION:Brotfabrik Bonn\n"
        "DESCRIPTION:Ein ausführlich beschriebener öffentlicher Termin. Der Eintritt ist kostenlos.\n"
        "URL:https://example.test/event\n"
        f"{extra}\nEND:VEVENT\nEND:VCALENDAR\n"
    )


class ICalPruningTests(unittest.TestCase):
    def setUp(self):
        old_window = (common.DAYS_AHEAD, common.TODAY, common.END_DATE)

        def restore():
            common.DAYS_AHEAD, common.TODAY, common.END_DATE = old_window
            common._configure_date_reference(old_window[1])

        self.addCleanup(restore)

    def test_excluded_candidate_skips_full_canonicalization(self):
        raw = calendar()
        with patch.dict(os.environ, {"NRW_EVENTS_ICAL_PRUNE": "0"}), patch.object(core, "build_event", wraps=core.build_event) as full:
            baseline = core.parse_ical(raw, "https://example.test/feed", "Fixture", "Bonn")
        self.assertEqual(full.call_count, 1)
        with patch.dict(os.environ, {"NRW_EVENTS_ICAL_PRUNE": "1"}), patch.object(core, "build_event", wraps=core.build_event) as full:
            candidate = core.parse_ical(raw, "https://example.test/feed", "Fixture", "Bonn")
        self.assertEqual(candidate, baseline)
        self.assertEqual(full.call_count, 0)

    def test_calendar_matrix_preserves_snapshot_and_durable_artifacts(self):
        cases = [
            calendar(),
            calendar("Blutspende", "DTSTART:20260905T180000\nDTEND:20260905T190000"),
            calendar("Jazzkonzert"),
            calendar("Jazzkonzert", "DTSTART:20260905T180000\nDTEND:20260905T200000"),
            calendar("Jazzkonzert", "DTSTART:20270905T180000\nDTEND:20270905T200000"),
            calendar("Kunstausstellung", "DTSTART;VALUE=DATE:20260801\nDTEND;VALUE=DATE:20261001"),
            calendar("Jazzkonzert", "DTSTART:20260102T180000", "RRULE:FREQ=WEEKLY;INTERVAL=2;COUNT=40\nEXDATE:20260911T180000\nRDATE:20260912T180000"),
            calendar("Jazzkonzert", "DTSTART:20260901T180000", "RRULE:FREQ=DAILY;UNTIL=20260910T180000"),
            calendar("Jazzkonzert", "DTSTART:20260101T180000", "RRULE:FREQ=MONTHLY;COUNT=12"),
            calendar("Jazzkonzert", "DTSTART;TZID=Europe/Berlin:20261025T023000\nDTEND;TZID=Europe/Berlin:20261025T040000"),
            calendar("Abgesagt: Jazzkonzert"),
            calendar("Jazzkonzert", extra="STATUS:CANCELLED\nRECURRENCE-ID:20260102T180000"),
            calendar("Verschoben: Jazzkonzert"),
            calendar("Jazzkonzert", extra="RRULE:FREQ=UNSUPPORTED"),
            "BEGIN:VCALENDAR\nVERSION:2.0\nEND:VCALENDAR",
            "malformed calendar",
        ]
        for raw in cases:
            with self.subTest(raw=raw), make_runner_env() as environment:
                context = replace(environment.context(clock=lambda: datetime(2026, 9, 3, 12)),
                                  window=EventWindow(datetime(2026, 9, 3), datetime(2026, 12, 1)))
                outputs = []
                counts = []
                for enabled in ("0", "1"):
                    collector = performance.Collector()
                    fetched = []

                    def fetch(raw=raw, fetched=fetched):
                        events = core.parse_ical(raw, "https://example.test/feed", "Fixture", "Bonn")
                        fetched.append(events)
                        return events

                    with patch.dict(os.environ, {"NRW_EVENTS_ICAL_PRUNE": enabled, "NRW_EVENTS_AI_ENRICHMENT": "0"}), performance.collect(collector):
                        result = runner.run_import(context, {"Fixture": fetch})
                        snapshot = runner.build_snapshot(result, context)
                    outputs.append({"snapshot": {**snapshot.metadata, "events": snapshot.events}, "artifacts": {"series_ledger": snapshot.series_ledger, "highlights": snapshot.highlights, "raw_events": fetched[0]}})
                    counts.append({key: value for key, value in collector.snapshot()["counts"].items() if key.startswith("parser_")})
                self.assertEqual(replay_differences(*outputs), [])
                self.assertEqual(*counts)

    def test_bad_category_configuration_is_not_hidden_by_a_drop(self):
        with patch.dict(os.environ, {"NRW_EVENTS_ICAL_PRUNE": "1"}), self.assertRaises(ValueError):
            core.parse_ical(calendar(), "https://example.test/feed", "Fixture", "Bonn", default_category_key="invalid")

    def test_quality_decisions_are_reused_only_within_one_parse(self):
        block = calendar("Jazzkonzert").split("BEGIN:VEVENT", 1)[1].split("END:VEVENT", 1)[0]
        raw = "BEGIN:VCALENDAR\n" + ("BEGIN:VEVENT" + block + "END:VEVENT\n") * 3 + "END:VCALENDAR"
        with patch.dict(os.environ, {"NRW_EVENTS_ICAL_PRUNE": "1"}), patch.object(
            core, "evaluate_event_quality", wraps=core.evaluate_event_quality,
        ) as evaluate:
            first = core.parse_ical(raw, "https://example.test/feed", "Fixture", "Bonn")
            self.assertEqual(evaluate.call_count, 1)
            second = core.parse_ical(raw, "https://example.test/feed", "Fixture", "Bonn")
            self.assertEqual(evaluate.call_count, 2)
        self.assertEqual(first, second)
        self.assertEqual(len(first), 3)

    def test_quality_cache_is_bounded_and_separates_policy_inputs(self):
        draft = EventDraft(
            title="Jazzkonzert", start=datetime(2026, 9, 5, 18), end=None,
            venue="Brotfabrik", city="Bonn", description="Ein Konzert mit Jazzmusik.",
            link="https://example.test/event", source="Fixture", category="Kultur",
        )
        cache = {}
        with patch.object(core, "_ICAL_QUALITY_CACHE_SIZE", 3), patch.object(
            core, "evaluate_event_quality", wraps=core.evaluate_event_quality,
        ) as evaluate:
            core._prepare_ical_quality(draft, cache)
            core._prepare_ical_quality(draft, cache)
            self.assertEqual(evaluate.call_count, 1)
            for field, value in (
                ("source", "Other"), ("source_id", "other"), ("category", "Sport"),
                ("description", "Blutspende"), ("venue", "Oper Bonn"),
                ("title", "Blutspende"), ("link", "https://example.test/other"),
            ):
                with self.subTest(field=field):
                    before = evaluate.call_count
                    core._prepare_ical_quality(replace(draft, **{field: value}), cache)
                    self.assertEqual(evaluate.call_count, before + 1)
                    self.assertLessEqual(len(cache), 3)


if __name__ == "__main__":
    unittest.main()
