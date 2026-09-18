import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from nrw_events import ai_enrichment as ai
from tests.test_ai_enrichment import FACTS, SUMMARY, FakeClient, event


class AIContentRecoveryTests(unittest.TestCase):
    def test_numeric_dates_and_overlapping_exhibition_are_scoped_to_occurrence(self):
        payload = ai._input_payload(event(start_date="2026-10-15", end_date="2026-10-15"), "Ausstellung")
        facts = dict(FACTS, start_date="2026-10-10", end_date="2026-10-24",
                     program=["Vernissage am 07.09., 18:30 Uhr", "Malerei und Fotografie"],
                     admission={**FACTS["admission"], "note": "Vom 16.6.–10.7.2026 ermäßigter Eintritt"})
        cleaned = ai._sanitize_extracted_facts(facts, payload)
        self.assertEqual(cleaned["start_date"], "2026-10-15")
        self.assertEqual(cleaned["end_date"], "2026-10-15")
        self.assertEqual(cleaned["program"], ["Malerei und Fotografie"])
        self.assertIsNone(cleaned["admission"]["note"])

    def test_negative_registration_needs_source_evidence_and_cannot_hide_positive_claim(self):
        summary = "Im Quartier treffen sich Menschen bei Kaffee und Spielen. Eine Anmeldung ist nicht erforderlich."
        facts = dict(FACTS, registration=None)
        self.assertEqual(ai._summary_quality(summary, "Offener Treff. Ohne Anmeldung.", facts), "")
        self.assertEqual(ai._summary_quality(summary, "Offener Treff.", facts), "summary invents registration information")
        self.assertEqual(ai._summary_quality(summary + " Bitte reservieren.", "Ohne Anmeldung.", facts), "summary invents registration information")

    def test_structured_city_wins_over_biographical_city(self):
        summary = "Das Kunstmuseum Bonn zeigt grafische Arbeiten aus seiner Sammlung. Die Ausstellung würdigt eine Künstlerinnenvereinigung."
        self.assertEqual(ai._summary_quality(summary, "Die Vereinigung entstand in Hamburg. Die Ausstellung ist in Bonn.", dict(FACTS, city="Bonn")), "")

    def test_end_timestamp_is_valid_clock_evidence(self):
        summary = "Die Schifffahrt beginnt in Bonn und führt am Siebengebirge entlang. Sie endet um 13:50 Uhr."
        source = "Eine Fahrt auf dem Rhein."
        payload = ai._input_payload(event(start_date="2026-08-09T09:50:00", end_date="2026-08-09T13:50:00", time="09:50"), source)
        facts = ai._sanitize_extracted_facts(dict(FACTS), payload)
        self.assertEqual(facts["end_date"], "2026-08-09T13:50:00")
        self.assertEqual(ai._summary_quality(summary, source, facts), "")

    def test_bonn_district_does_not_erase_city_or_venue(self):
        payload = ai._input_payload(event(city="Bonn-Gronau", venue="Kunstmuseum Bonn"), "Ausstellung im Kunstmuseum Bonn.")
        cleaned = ai._sanitize_extracted_facts(dict(FACTS, city="Bonn", venue="Kunstmuseum Bonn"), payload)
        self.assertEqual(cleaned["city"], "Bonn")
        self.assertEqual(cleaned["venue"], "Kunstmuseum Bonn")

    def test_sparse_primary_sources_enter_ai_but_real_source_copy_is_preserved(self):
        for source in ("b-future-festival", "lupe-events"):
            with self.subTest(source=source):
                self.assertTrue(ai.is_target_event(event(source_id=source, description="", description_html="")))
                self.assertTrue(ai.is_target_event(event(source_id=source, description_source="generated")))
                self.assertFalse(ai.is_target_event(event(source_id=source, description_source="scraped")))

    def test_failed_cached_facts_are_cleaned_persisted_and_summary_is_reused(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = ai.AISettings(enabled=True, api_key="test", model="test-model",
                                    cache_db=Path(directory) / "ai.sqlite3", max_attempts=1)
            now = datetime(2026, 8, 3, tzinfo=timezone.utc)
            ai.enrich_event(event(), settings=settings, client=FakeClient([FACTS, SUMMARY]), now=now)
            with closing(sqlite3.connect(settings.cache_db)) as connection, connection:
                stored = json.loads(connection.execute("SELECT stage1_json FROM ai_event_enrichment").fetchone()[0])
                stored["program"].append("Vernissage am 07.09., 18:30 Uhr")
                connection.execute("UPDATE ai_event_enrichment SET stage1_json=?, stage2_json='', stage2_attempts=1, negative_until='2026-08-10T00:00:00+00:00', last_error='summary mentions a date outside the selected event'", (json.dumps(stored),))
            client = FakeClient([SUMMARY])
            result = ai.enrich_event(event(), settings=settings, client=client, now=now)
            self.assertEqual([call["stage"] for call in client.calls], ["summary"])
            self.assertEqual(result["ai_summary"], SUMMARY["ai_summary"])
            self.assertNotIn("Vernissage", json.dumps(client.calls[0]["payload"]["facts"]))
            with closing(sqlite3.connect(settings.cache_db)) as connection, connection:
                facts, summary = connection.execute("SELECT stage1_json, stage2_json FROM ai_event_enrichment").fetchone()
                self.assertNotIn("Vernissage", facts)
                self.assertEqual(json.loads(summary)["ai_summary"], SUMMARY["ai_summary"])
            cached = ai.enrich_event(event(), settings=settings, client=FakeClient([]), now=now)
            self.assertEqual(cached["ai_summary"], result["ai_summary"])
