import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest import mock
import urllib.request

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
        "dauert etwa 90 Minuten. Der Zugang ist stufenlos möglich. "
        "Der Eintritt ist frei, eine Anmeldung wird bis zum "
        "8. August erbeten. Der Termin gehört zur Reihe Bonner Klangräume. Inhaltlich verbindet "
        "der Abend das Konzertprogramm mit einem direkten Austausch."
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
            "Mitwirkenden mit dem Publikum. Der Eintritt ist frei und eine Anmeldung ist nötig. "
            "Veranstalter: Kulturamt Bonn."
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


class FakeHTTPResponse:
    def __init__(self, document):
        self.payload = json.dumps(document).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self.payload


class RecordingOpener:
    def __init__(self, document):
        self.document = document
        self.request = None
        self.timeout = None

    def __call__(self, request, *, timeout):
        self.request = request
        self.timeout = timeout
        return FakeHTTPResponse(self.document)


def slow_isolated_worker(sender, _request_spec, _socket_timeout):
    try:
        time.sleep(0.25)
        sender.send(("ok", b"{}"))
    finally:
        sender.close()


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

    def test_openrouter_settings_use_their_own_key_and_default_model(self):
        with mock.patch.dict(
            "os.environ",
            {
                "NRW_EVENTS_AI_PROVIDER": "openrouter",
                "OPENROUTER_API_KEY": "router-test-key",
            },
            clear=True,
        ):
            settings = ai_enrichment.settings_from_env()

        self.assertEqual("openrouter", settings.provider)
        self.assertEqual("router-test-key", settings.api_key)
        self.assertEqual("deepseek/deepseek-v4-flash-0731", settings.model)

    def test_openrouter_client_enforces_structured_zdr_non_reasoning_requests(self):
        opener = RecordingOpener({
            "choices": [{
                "finish_reason": "stop",
                "message": {"content": json.dumps(FACTS)},
            }],
            "usage": {
                "prompt_tokens": 120,
                "completion_tokens": 80,
                "cost": 0.000025,
                "prompt_tokens_details": {"cached_tokens": 20},
            },
        })
        settings = replace(
            self.settings,
            provider="openrouter",
            model="deepseek/deepseek-v4-flash-0731",
        )
        parsed, usage = ai_enrichment.OpenRouterClient(settings, opener=opener).structured(
            stage="facts",
            system="Extract facts.",
            payload={"source_material": "Private source copy."},
            schema=ai_enrichment._FACT_SCHEMA,
            attempt=1,
        )
        body = json.loads(opener.request.data)

        self.assertEqual(FACTS, parsed)
        self.assertEqual(5_000, body["max_tokens"])
        self.assertEqual({"effort": "none", "exclude": True}, body["reasoning"])
        self.assertEqual(
            {
                "require_parameters": True,
                "data_collection": "deny",
                "zdr": True,
                "sort": "throughput",
            },
            body["provider"],
        )
        self.assertTrue(body["response_format"]["json_schema"]["strict"])
        self.assertEqual(ai_enrichment.Usage(120, 20, 80, 0.000025), usage)

    def test_openrouter_can_allow_provider_data_collection_without_zdr(self):
        opener = RecordingOpener({
            "choices": [{
                "finish_reason": "stop",
                "message": {"content": json.dumps(FACTS)},
            }],
        })
        settings = replace(
            self.settings,
            provider="openrouter",
            model="deepseek/deepseek-v4-flash-0731",
            allow_data_collection=True,
        )

        ai_enrichment.OpenRouterClient(settings, opener=opener).structured(
            stage="facts",
            system="Extract facts.",
            payload={"source_material": "Private source copy."},
            schema=ai_enrichment._FACT_SCHEMA,
            attempt=1,
        )
        body = json.loads(opener.request.data)

        self.assertEqual(
            {
                "require_parameters": True,
                "data_collection": "allow",
                "sort": "throughput",
            },
            body["provider"],
        )
        self.assertNotIn("zdr", body["provider"])

    def test_openrouter_summary_keeps_reasoning_and_uses_larger_output_budget(self):
        opener = RecordingOpener({
            "choices": [{
                "finish_reason": "stop",
                "message": {"content": json.dumps(SUMMARY)},
            }],
            "usage": {"prompt_tokens": 120, "completion_tokens": 80},
        })
        settings = replace(
            self.settings,
            provider="openrouter",
            model="deepseek/deepseek-v4-flash-0731",
            summary_reasoning_effort="low",
        )

        parsed, _usage = ai_enrichment.OpenRouterClient(settings, opener=opener).structured(
            stage="summary",
            system="Write a summary.",
            payload={"facts": FACTS},
            schema=ai_enrichment._SUMMARY_SCHEMA,
            attempt=1,
        )
        body = json.loads(opener.request.data)

        self.assertEqual(SUMMARY, parsed)
        self.assertEqual(10_000, body["max_tokens"])
        self.assertEqual({"effort": "low", "exclude": True}, body["reasoning"])

    def test_default_transport_uses_killable_request_worker(self):
        settings = replace(self.settings, provider="openrouter", timeout_seconds=0.05)

        with mock.patch.object(
            ai_enrichment,
            "_read_response_isolated",
            side_effect=TimeoutError("AI request exceeded its wall-clock deadline"),
        ) as read_response:
            with self.assertRaisesRegex(ai_enrichment.AIEnrichmentError, "TimeoutError"):
                ai_enrichment.OpenRouterClient(settings).structured(
                    stage="facts",
                    system="Extract facts.",
                    payload={"source_material": "Private source copy."},
                    schema=ai_enrichment._FACT_SCHEMA,
                    attempt=1,
                )

        read_response.assert_called_once()

    def test_process_deadline_is_enforced_from_worker_thread(self):
        request = urllib.request.Request("https://example.test/slow", method="POST")
        started = time.monotonic()

        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(
                ai_enrichment._read_response_isolated,
                request,
                0.05,
                slow_isolated_worker,
            )
            with self.assertRaisesRegex(TimeoutError, "wall-clock deadline"):
                future.result(timeout=1)

        self.assertLess(time.monotonic() - started, 0.2)

    def test_batch_budget_stops_enrichment_without_discarding_remaining_events(self):
        settings = replace(self.settings, batch_timeout_seconds=30)
        values = [event(title="First"), event(title="Second")]
        seen_timeouts = []

        def enrich_one(value, *, settings):
            seen_timeouts.append(settings.timeout_seconds)
            return {**value, "ai_summary": "done"}

        with mock.patch.object(
            ai_enrichment.time, "monotonic", side_effect=[100, 100, 131]
        ), mock.patch.object(
            ai_enrichment.common, "event_in_window", return_value=True
        ), mock.patch.object(ai_enrichment, "enrich_event", side_effect=enrich_one):
            result = ai_enrichment.enrich_events(values, settings=settings)

        self.assertEqual([7.5], seen_timeouts)
        self.assertEqual("done", result[0]["ai_summary"])
        self.assertEqual("", result[1]["description"])
        self.assertEqual("", result[1]["description_html"])

    def test_different_events_do_not_hold_cache_lock_during_api_requests(self):
        barrier = threading.Barrier(2, timeout=1)

        class ConcurrentClient(FakeClient):
            def structured(self, **kwargs):
                if kwargs["stage"] == "facts":
                    barrier.wait()
                return super().structured(**kwargs)

        def enrich(title):
            return ai_enrichment.enrich_event(
                event(title=title),
                settings=self.settings,
                client=ConcurrentClient([FACTS, SUMMARY]),
                now=self.now,
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(enrich, ("First", "Second")))

        self.assertEqual([SUMMARY["ai_summary"], SUMMARY["ai_summary"]], [
            result["ai_summary"] for result in results
        ])

    def test_openrouter_billed_incomplete_response_is_recorded(self):
        opener = RecordingOpener({
            "choices": [{
                "finish_reason": "length",
                "message": {"content": "{}"},
            }],
            "usage": {
                "prompt_tokens": 120,
                "completion_tokens": 80,
                "cost": 0.00125,
            },
        })
        settings = replace(
            self.settings,
            provider="openrouter",
            model="deepseek/deepseek-v4-flash-0731",
            max_attempts=1,
            negative_cache_hours=0,
        )

        result = ai_enrichment.enrich_event(
            event(),
            settings=settings,
            client=ai_enrichment.OpenRouterClient(settings, opener=opener),
            now=self.now,
        )

        self.assertEqual("", result["ai_summary"])
        with closing(sqlite3.connect(settings.cache_db)) as connection:
            cost = connection.execute(
                "SELECT cost_usd FROM ai_event_enrichment"
            ).fetchone()[0]
        self.assertEqual(0.00125, cost)

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

    def test_structured_facts_are_source_material_when_prose_is_missing(self):
        material = ai_enrichment._source_material(event(
            description="",
            description_html="",
            time="19:30",
            venue="Altes Rathaus",
            venue_address="Markt 2, 53111 Bonn",
            price="Eintritt frei",
            link="https://example.test/private-transport-url",
        ))

        self.assertIn("Titel: Klangraum", material)
        self.assertIn("Datum: 2026-08-09", material)
        self.assertIn("Uhrzeit: 19:30", material)
        self.assertIn("Ort: Altes Rathaus", material)
        self.assertIn("Eintritt: Eintritt frei", material)
        self.assertNotIn("example.test", material)
        self.assertNotIn("Bonn.de Events", material)

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

    def test_cache_is_separate_per_provider(self):
        ai_enrichment.enrich_event(
            event(), settings=self.settings, client=FakeClient([FACTS, SUMMARY]), now=self.now,
        )
        router_client = FakeClient([FACTS, SUMMARY])
        router_settings = replace(self.settings, provider="openrouter")
        result = ai_enrichment.enrich_event(
            event(), settings=router_settings, client=router_client, now=self.now,
        )

        self.assertEqual(2, len(router_client.calls))
        self.assertEqual(SUMMARY["ai_summary"], result["ai_summary"])
        with closing(sqlite3.connect(self.settings.cache_db)) as connection:
            versions = {
                row[0] for row in connection.execute(
                    "SELECT pipeline_version FROM ai_event_enrichment"
                )
            }
        self.assertEqual({
            f"{ai_enrichment.PIPELINE_VERSION}:gpt-5.6-luna",
            (
                f"{ai_enrichment.OPENROUTER_PIPELINE_VERSION}:openrouter:gpt-5.6-luna:"
                "facts-none:summary-none"
            ),
        }, versions)

    def test_changed_source_content_gets_a_new_cache_version(self):
        first = FakeClient([FACTS, SUMMARY])
        ai_enrichment.enrich_event(event(), settings=self.settings, client=first, now=self.now)
        changed = event(description="Neue bestätigte Programminformation mit anderem Ablauf. Der Eintritt ist frei.")
        second = FakeClient([FACTS, SUMMARY])
        ai_enrichment.enrich_event(changed, settings=self.settings, client=second, now=self.now)
        self.assertEqual(2, len(second.calls))

        with closing(sqlite3.connect(self.settings.cache_db)) as connection:
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

    def test_default_terminal_failure_is_reused_forever(self):
        settings = replace(self.settings, negative_cache_hours=0)
        first = FakeClient([
            FACTS,
            ai_enrichment.AIEnrichmentError("temporary"),
            ai_enrichment.AIEnrichmentError("temporary"),
        ])
        result = ai_enrichment.enrich_event(
            event(), settings=settings, client=first, now=self.now,
        )

        blocked = FakeClient([])
        cached = ai_enrichment.enrich_event(
            event(), settings=settings, client=blocked,
            now=self.now + timedelta(days=3_650),
        )

        self.assertEqual("", result["ai_summary"])
        self.assertEqual("", cached["ai_summary"])
        self.assertEqual([], blocked.calls)

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

    def test_recommendation_language_is_rejected_with_feedback_on_retry(self):
        promotional = {
            **SUMMARY,
            "ai_summary": (
                "Wer Musik mag, sollte sich diesen stimmungsvollen Abend unbedingt vormerken. "
                "Das Konzert bietet eine gute Gelegenheit für ein entspanntes Erlebnis. " * 3
            ),
        }
        client = FakeClient([FACTS, promotional, SUMMARY])
        result = ai_enrichment.enrich_event(
            event(), settings=self.settings, client=client, now=self.now,
        )

        self.assertEqual(SUMMARY["ai_summary"], result["ai_summary"])
        self.assertIn("retry_instruction", client.calls[2]["payload"])

    def test_incomplete_summary_is_retried(self):
        incomplete = {
            **SUMMARY,
            "ai_summary": "Das Konzert beginnt im Alten Rathaus unter dem Titel „Klangraum",
        }
        client = FakeClient([FACTS, incomplete, SUMMARY])

        result = ai_enrichment.enrich_event(
            event(), settings=self.settings, client=client, now=self.now,
        )

        self.assertEqual(SUMMARY["ai_summary"], result["ai_summary"])
        self.assertIn("summary ends mid-sentence", client.calls[2]["payload"]["retry_instruction"])

    def test_seller_information_is_removed_from_summary(self):
        seller_copy = {
            **SUMMARY,
            "ai_summary": (
                "Der Flohmarkt findet am Sonntag in Bonn statt. "
                "Die Standgebühr beträgt 12 Euro pro laufendem Meter. "
                "Besucherinnen und Besucher zahlen keinen Eintritt."
            ),
        }
        client = FakeClient([FACTS, seller_copy])

        result = ai_enrichment.enrich_event(
            event(), settings=self.settings, client=client, now=self.now,
        )

        self.assertNotIn("Standgebühr", result["ai_summary"])
        self.assertIn("keinen Eintritt", result["ai_summary"])

    def test_extracted_facts_are_reduced_to_visitor_facts_for_selected_date(self):
        raw_facts = {
            **FACTS,
            "admission": {
                "is_free": True,
                "amount": None,
                "currency": "EUR",
                "note": "Standgebühr 12 Euro pro laufendem Meter",
                "donation_suggested": False,
            },
            "availability": "SoldOut",
            "registration": "Standplatzreservierung erforderlich",
            "program": [
                "Kammermusik des 20. Jahrhunderts",
                "Workshop am 31. Mai 2026 in Köln",
                "Standplätze ab drei Metern",
            ],
            "participants": ["Ensemble Beispiel", "Unterstützt durch Beispielstiftung"],
            "neutral_facts": ["Die Übungen lösen Blockaden in den Meridianen."],
        }
        source = event(
            price="",
            description="Standgebühr 12 Euro pro laufendem Meter. Die Kartenlage wird nicht erwähnt.",
        )
        payload = ai_enrichment._input_payload(source, source["description"])

        cleaned = ai_enrichment._sanitize_extracted_facts(raw_facts, payload)

        self.assertEqual(["Kammermusik des 20. Jahrhunderts"], cleaned["program"])
        self.assertEqual(["Ensemble Beispiel"], cleaned["participants"])
        self.assertEqual([], cleaned["neutral_facts"])
        self.assertIsNone(cleaned["registration"])
        self.assertIsNone(cleaned["availability"])
        self.assertEqual(
            {
                "is_free": None,
                "amount": None,
                "currency": None,
                "note": None,
                "donation_suggested": None,
            },
            cleaned["admission"],
        )

    def test_other_series_date_is_retried(self):
        unrelated_date = {
            **SUMMARY,
            "ai_summary": (
                "Klangraum beginnt am 9. August 2026 im Alten Rathaus in Bonn. "
                "Ein weiterer Workshop findet am 31. Mai 2026 in Köln statt."
            ),
        }
        client = FakeClient([FACTS, unrelated_date, SUMMARY])

        result = ai_enrichment.enrich_event(
            event(), settings=self.settings, client=client, now=self.now,
        )

        self.assertEqual(SUMMARY["ai_summary"], result["ai_summary"])
        self.assertIn(
            "summary mentions a date outside the selected event",
            client.calls[2]["payload"]["retry_instruction"],
        )

    def test_varied_missing_detail_sentences_are_removed_before_quality_check(self):
        padded = {
            **SUMMARY,
            "ai_summary": (
                "Das Training beginnt um 19:30 Uhr und dauert 90 Minuten. "
                "Nähere Details zum Treffpunkt sind nicht bekannt. "
                "Die übrigen Informationen werden direkt vom Verein kommuniziert."
            ),
        }
        client = FakeClient([FACTS, padded])
        result = ai_enrichment.enrich_event(
            event(), settings=self.settings, client=client, now=self.now,
        )

        self.assertEqual("Das Training beginnt um 19:30 Uhr und dauert 90 Minuten.", result["ai_summary"])
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
            event(
                source="marktcom",
                source_id="marktcom",
                description="Das Geschäft ist jeden Montag von 10 bis 18 Uhr geöffnet.",
            ),
            settings=self.settings,
            client=client,
            now=self.now,
        )
        self.assertEqual(["facts"], [call["stage"] for call in client.calls])
        self.assertEqual("", result["ai_summary"])

    def test_explicit_non_marktcom_calendar_occurrence_overrides_false_non_event(self):
        facts = {**FACTS, "is_concrete_event": False, "event_evidence": None}
        client = FakeClient([facts, SUMMARY])
        result = ai_enrichment.enrich_event(
            event(), settings=self.settings, client=client, now=self.now,
        )

        self.assertEqual(["facts", "summary"], [call["stage"] for call in client.calls])
        self.assertNotIn("is_concrete_event", client.calls[1]["payload"]["facts"])
        self.assertNotIn("event_evidence", client.calls[1]["payload"]["facts"])
        self.assertEqual(SUMMARY["ai_summary"], result["ai_summary"])


if __name__ == "__main__":
    unittest.main()
