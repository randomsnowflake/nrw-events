import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
import tempfile
import unittest

from nrw_events import ai_enrichment


FACTS = {
    "title": "Klangraum",
    "is_concrete_event": True,
    "event_evidence": "Ein angekündigtes Konzert mit festem Datum und Beginn.",
    "start_date": "2026-08-09",
    "end_date": "2026-08-09",
    "time": "19:30",
    "time_note": None,
    "venue": "Altes Rathaus",
    "venue_address": "Markt 2, 53111 Bonn",
    "city": "Bonn",
    "organizer": "Kulturamt Bonn",
    "admission": {
        "is_free": True,
        "amount": 0,
        "currency": "EUR",
        "note": "Eintritt frei",
        "donation_suggested": False,
    },
    "availability": "InStock",
    "series_title": "Bonner Klangräume",
    "program": ["Kammermusik des 20. Jahrhunderts", "Gespräch mit den Mitwirkenden"],
    "participants": ["Ensemble Beispiel"],
    "target_group": ["Erwachsene"],
    "age_information": None,
    "registration": "Anmeldung bis 8. August",
    "language": ["Deutsch"],
    "accessibility": ["stufenloser Zugang"],
    "duration": "90 Minuten",
    "requirements": [],
    "special_features": ["Publikumsgespräch"],
    "neutral_facts": ["Das Programm verbindet Musik und ein moderiertes Gespräch."],
}

SUMMARY = {
    "ai_summary": (
        "Bei Klangraum steht Kammermusik des 20. Jahrhunderts auf dem Programm. "
        "Das Ensemble Beispiel spielt im Alten Rathaus in Bonn; anschließend ist ein moderiertes "
        "Gespräch mit den Mitwirkenden vorgesehen. Die Veranstaltung beginnt um 19:30 Uhr und "
        "dauert nach den vorliegenden Angaben etwa 90 Minuten. Veranstaltungssprache ist Deutsch. "
        "Der Zugang ist stufenlos möglich. Der Eintritt ist frei, eine Anmeldung wird bis zum "
        "8. August erbeten. Der Termin gehört zur Reihe Bonner Klangräume. Inhaltlich verbindet "
        "der Abend das Konzertprogramm mit einem direkten Austausch. Als Veranstalter ist das "
        "Kulturamt Bonn genannt; die Angaben richten sich an ein erwachsenes Publikum."
    ),
    "time": "19:30",
    "time_note": None,
    "venue": "Altes Rathaus",
    "venue_address": "Markt 2, 53111 Bonn",
    "city": "Bonn",
    "organizer": "Kulturamt Bonn",
    "price": "Eintritt frei",
    "availability": "InStock",
    "category_key": "concert",
    "series_title": "Bonner Klangräume",
}


def event(**overrides):
    value = {
        "title": "Klangraum",
        "source": "Bonn.de Events",
        "source_id": "bonn-de-events",
        "start_date": "2026-08-09",
        "end_date": "2026-08-09",
        "city": "Bonn",
        "venue": "",
        "description": (
            "Erleben Sie ein einzigartiges Konzert mit Kammermusik. Im Anschluss sprechen die "
            "Mitwirkenden mit dem Publikum. Der Eintritt ist frei und eine Anmeldung ist nötig."
        ),
        "description_html": "<p>Erleben Sie ein einzigartiges Konzert mit Kammermusik.</p>",
        "score": 1.0,
        "category_key": "other",
        "category_confidence": 0.2,
    }
    value.update(overrides)
    return value


class FakeClient:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def structured(self, **kwargs):
        self.calls.append(kwargs)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply, ai_enrichment.Usage(input_tokens=100, cached_input_tokens=10, output_tokens=50)


class AIEnrichmentTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="nrw-ai-test-")
        self.addCleanup(self.temporary.cleanup)
        self.settings = ai_enrichment.AISettings(
            enabled=True,
            api_key="test-key",
            model="gpt-5.6-luna",
            cache_db=Path(self.temporary.name) / "cache.sqlite3",
            max_attempts=2,
            negative_cache_hours=24,
            timeout_seconds=30,
        )
        self.now = datetime(2026, 8, 3, 12, tzinfo=timezone.utc)

    def test_non_target_event_is_untouched(self):
        source = event(source_id="trusted-source")
        result = ai_enrichment.enrich_event(source, settings=self.settings, client=FakeClient([]))
        self.assertEqual(source, result)

    def test_disabled_or_missing_key_never_falls_back_to_source_copy(self):
        source = event()
        settings = ai_enrichment.AISettings(
            enabled=False, api_key="", model="gpt-5.6-luna",
            cache_db=Path(self.temporary.name) / "unused.sqlite3",
        )
        result = ai_enrichment.enrich_event(source, settings=settings, client=FakeClient([]))
        self.assertEqual("", result["description"])
        self.assertEqual("", result["description_html"])
        self.assertEqual("", result["ai_summary"])

    def test_two_stages_are_separate_and_success_is_reused_forever(self):
        client = FakeClient([FACTS, SUMMARY])
        first = ai_enrichment.enrich_event(
            event(), settings=self.settings, client=client, now=self.now,
        )
        second = ai_enrichment.enrich_event(
            event(), settings=self.settings, client=FakeClient([]),
            now=self.now + timedelta(days=400),
        )

        self.assertEqual(2, len(client.calls))
        self.assertIn("source_material", client.calls[0]["payload"])
        self.assertNotIn("source_material", json.dumps(client.calls[1]["payload"], ensure_ascii=False))
        self.assertEqual(SUMMARY["ai_summary"], first["ai_summary"])
        self.assertEqual(first["ai_summary"], second["ai_summary"])
        self.assertEqual("", first["description"])
        self.assertEqual("19:30", first["time"])
        self.assertEqual("concert", first["category_key"])
        self.assertEqual("Bonner Klangräume", first["series_title"])

    def test_changed_source_content_gets_a_new_cache_version(self):
        first = FakeClient([FACTS, SUMMARY])
        ai_enrichment.enrich_event(event(), settings=self.settings, client=first, now=self.now)
        changed = event(description="Neue bestätigte Programminformation mit einem anderen Ablauf.")
        second = FakeClient([FACTS, SUMMARY])
        ai_enrichment.enrich_event(changed, settings=self.settings, client=second, now=self.now)
        self.assertEqual(2, len(second.calls))

        with sqlite3.connect(self.settings.cache_db) as connection:
            self.assertEqual(2, connection.execute("SELECT COUNT(*) FROM ai_event_enrichment").fetchone()[0])

    def test_successful_extraction_is_kept_when_summary_stage_fails(self):
        first = FakeClient([
            FACTS,
            ai_enrichment.AIEnrichmentError("temporary"),
            ai_enrichment.AIEnrichmentError("temporary"),
        ])
        result = ai_enrichment.enrich_event(event(), settings=self.settings, client=first, now=self.now)
        self.assertEqual("", result["ai_summary"])
        self.assertEqual(["facts", "summary", "summary"], [call["stage"] for call in first.calls])

        blocked = FakeClient([])
        ai_enrichment.enrich_event(
            event(), settings=self.settings, client=blocked, now=self.now + timedelta(hours=1),
        )
        self.assertEqual([], blocked.calls)

        retry = FakeClient([SUMMARY])
        recovered = ai_enrichment.enrich_event(
            event(), settings=self.settings, client=retry, now=self.now + timedelta(hours=25),
        )
        self.assertEqual(["summary"], [call["stage"] for call in retry.calls])
        self.assertEqual(SUMMARY["ai_summary"], recovered["ai_summary"])

    def test_marketing_or_copied_summary_is_rejected_and_negatively_cached(self):
        promotional = {**SUMMARY, "ai_summary": "Erleben Sie ein einzigartiges Konzert, das man nicht verpassen darf. " * 8}
        copied = {
            **SUMMARY,
            "ai_summary": (
                "Erleben Sie ein einzigartiges Konzert mit Kammermusik. Im Anschluss sprechen die "
                "Mitwirkenden mit dem Publikum. Der Eintritt ist frei und eine Anmeldung ist nötig. " * 3
            ),
        }
        client = FakeClient([FACTS, promotional, copied])
        result = ai_enrichment.enrich_event(event(), settings=self.settings, client=client, now=self.now)
        self.assertEqual("", result["ai_summary"])
        self.assertEqual(3, len(client.calls))

    def test_missing_information_padding_is_removed_without_another_ai_call(self):
        padded = {
            **SUMMARY,
            "ai_summary": (
                "Die Veranstaltung findet in Bonn statt und beginnt um 19:30 Uhr. "
                "Weitere Angaben zu Programm, Ablauf und Beteiligten liegen nicht vor. " * 3
            ),
        }
        client = FakeClient([FACTS, padded])
        result = ai_enrichment.enrich_event(event(), settings=self.settings, client=client, now=self.now)
        self.assertNotIn("Weitere Angaben", result["ai_summary"])
        self.assertEqual(2, len(client.calls))

    def test_explicit_and_locked_fields_are_not_overwritten(self):
        source = event(
            time="20:00", identity_time_locked=True,
            venue="Festsaal", identity_venue_locked=True,
            price="15 €", admission_basis="explicit",
            category_key="stage", category_confidence=0.95,
            series_title="Bestehende Reihe",
        )
        result = ai_enrichment.enrich_event(
            source, settings=self.settings, client=FakeClient([FACTS, SUMMARY]), now=self.now,
        )
        self.assertEqual("20:00", result["time"])
        self.assertEqual("Festsaal", result["venue"])
        self.assertEqual("15 €", result["price"])
        self.assertEqual("stage", result["category_key"])
        self.assertEqual("Bestehende Reihe", result["series_title"])
        self.assertNotIn("Eintritt ist frei", result["ai_summary"])

    def test_routine_shop_opening_is_cached_without_a_second_ai_call(self):
        facts = {**FACTS, "is_concrete_event": False, "event_evidence": None}
        client = FakeClient([facts])
        result = ai_enrichment.enrich_event(
            event(description="Das Geschäft ist jeden Montag von 10 bis 18 Uhr geöffnet."),
            settings=self.settings,
            client=client,
            now=self.now,
        )
        self.assertEqual(["facts"], [call["stage"] for call in client.calls])
        self.assertEqual("", result["ai_summary"])


if __name__ == "__main__":
    unittest.main()
