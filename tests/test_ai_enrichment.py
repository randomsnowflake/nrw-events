import fcntl
import json
import sqlite3
import tempfile
import threading
import time
import unittest
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from nrw_events import ai_enrichment, common
from nrw_events.identity import event_id

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


def in_window_event(**overrides):
    """An ``event()`` dated inside the live import window.

    The shared fixture pins a literal date so the recorded FACTS/SUMMARY prose
    stays assertable. Batch behaviour depends on the window filter, so these
    cases date the record relative to today instead.
    """
    day = (common.TODAY + timedelta(days=2)).strftime("%Y-%m-%d")
    return event(start_date=day, end_date=day, date=day, **overrides)


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

    def test_pilot_cap_prioritizes_the_nearest_high_value_event(self):
        far_day = (common.TODAY + timedelta(days=20)).strftime("%Y-%m-%d")
        near_day = (common.TODAY + timedelta(days=1)).strftime("%Y-%m-%d")
        far = event(title="Später Termin", date=far_day, start_date=far_day, end_date=far_day, score=0.5)
        near = event(title="Morgen in Bonn", date=near_day, start_date=near_day, end_date=near_day, score=2.0)
        calls = []

        def enrich(value, **_kwargs):
            calls.append(value["title"])
            return {**value, "ai_summary": f"Zusammenfassung für {value['title']}"}

        with mock.patch.object(ai_enrichment, "enrich_event", side_effect=enrich), mock.patch.object(
            ai_enrichment, "_reuse_cached_success", side_effect=lambda value, _settings: value
        ):
            result = ai_enrichment.enrich_events(
                [far, near], settings=replace(self.settings, max_events=1),
            )

        self.assertEqual(["Morgen in Bonn"], calls)
        self.assertEqual(["Später Termin", "Morgen in Bonn"], [item["title"] for item in result])
        self.assertFalse(result[0].get("ai_summary"))
        self.assertIn("ai_summary", result[1])

    def test_pilot_cap_prioritizes_events_without_an_accepted_cached_summary(self):
        far_day = (common.TODAY + timedelta(days=2)).strftime("%Y-%m-%d")
        near_day = (common.TODAY + timedelta(days=1)).strftime("%Y-%m-%d")
        missing = event(
            title="Unbeschriebener später Termin",
            date=far_day,
            start_date=far_day,
            end_date=far_day,
        )
        cached = event(
            title="Bereits beschriebener Termin",
            date=near_day,
            start_date=near_day,
            end_date=near_day,
        )
        calls = []
        source_material = []

        def enrich(value, **_kwargs):
            calls.append(value["title"])
            source_material.append(value["description"])
            return {**value, "ai_summary": f"Zusammenfassung für {value['title']}"}

        def reuse(value, _settings):
            value["description"] = "Generated cache fallback."
            if value["title"] == cached["title"]:
                return {**value, "ai_summary": "Bereits akzeptierter Cache-Text"}
            return value

        with mock.patch.object(ai_enrichment, "enrich_event", side_effect=enrich), mock.patch.object(
            ai_enrichment, "_reuse_cached_success", side_effect=reuse,
        ):
            result = ai_enrichment.enrich_events(
                [cached, missing], settings=replace(self.settings, max_events=1),
            )

        self.assertEqual([missing["title"]], calls)
        self.assertTrue(source_material[0])
        self.assertEqual(missing["description"], source_material[0])
        self.assertEqual("Bereits akzeptierter Cache-Text", result[0]["ai_summary"])
        self.assertIn("Zusammenfassung", result[1]["ai_summary"])

    def test_pilot_cap_treats_malformed_ranking_values_as_zero(self):
        day = (common.TODAY + timedelta(days=1)).strftime("%Y-%m-%d")
        malformed = event(
            title="Malformed ranking",
            date=day,
            start_date=day,
            end_date=day,
            score="unknown",
            priority_bonus={},
        )
        overflowing = event(
            title="Overflowing ranking",
            date=day,
            start_date=day,
            end_date=day,
            score=10**1000,
        )
        valid = event(
            title="Valid ranking",
            date=day,
            start_date=day,
            end_date=day,
            score=2.0,
        )
        calls = []

        def enrich(value, **_kwargs):
            calls.append(value["title"])
            return {**value, "ai_summary": f"Zusammenfassung für {value['title']}"}

        with mock.patch.object(ai_enrichment, "enrich_event", side_effect=enrich), mock.patch.object(
            ai_enrichment, "_reuse_cached_success", side_effect=lambda value, _settings: value
        ):
            result = ai_enrichment.enrich_events(
                [malformed, overflowing, valid],
                settings=replace(self.settings, max_events=1),
            )

        self.assertEqual(["Valid ranking"], calls)
        self.assertFalse(result[0].get("ai_summary"))
        self.assertFalse(result[1].get("ai_summary"))
        self.assertIn("ai_summary", result[2])

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
        self.assertEqual(600, settings.batch_timeout_seconds)
        self.assertEqual(8, settings.workers)
        self.assertEqual(150, settings.max_new_cache_rows_per_day)

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
        ) as read_response, self.assertRaisesRegex(ai_enrichment.AIEnrichmentError, "TimeoutError"):
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

    def test_incremental_deadline_is_enforced_from_worker_thread(self):
        class SlowTrickleResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, _size):
                time.sleep(0.03)
                return b"x"

        request = urllib.request.Request("https://example.test/slow", method="POST")
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(
                ai_enrichment._read_http_response,
                request,
                0.05,
                lambda *_args, **_kwargs: SlowTrickleResponse(),
            )
            with self.assertRaisesRegex(TimeoutError, "wall-clock deadline"):
                future.result(timeout=1)

    def test_incremental_reader_rejects_oversized_responses(self):
        class OversizedResponse:
            def read(self, size):
                return b"x" * size

        with self.assertRaisesRegex(OSError, "5242880-byte limit"):
            ai_enrichment._read_bounded_response(OversizedResponse(), 30)

    def test_batch_budget_stops_enrichment_without_discarding_remaining_events(self):
        settings = replace(self.settings, batch_timeout_seconds=120, workers=1)
        values = [event(title="First"), event(title="Second")]
        seen_timeouts = []

        def enrich_one(value, *, settings, configured_timeout_seconds, outcome=None):
            seen_timeouts.append(settings.timeout_seconds)
            return {**value, "ai_summary": "done"}

        with mock.patch.object(
            ai_enrichment.time, "monotonic", side_effect=[100, 100, 221]
        ), mock.patch.object(
            ai_enrichment.common, "event_in_window", return_value=True
        ), mock.patch.object(ai_enrichment, "enrich_event", side_effect=enrich_one):
            result = ai_enrichment.enrich_events(values, settings=settings)

        self.assertEqual([30], seen_timeouts)
        self.assertEqual("done", result[0]["ai_summary"])
        self.assertIn("Second", result[1]["description"])
        self.assertTrue(result[1]["description_html"])

    def test_independent_events_are_enriched_concurrently_in_input_order(self):
        values = [event(title="First"), event(title="Second")]
        barrier = threading.Barrier(2, timeout=1)

        def enrich_one(value, *, settings, configured_timeout_seconds, outcome=None):
            barrier.wait()
            return {**value, "ai_summary": f"done: {value['title']}"}

        with mock.patch.object(
            ai_enrichment.common, "event_in_window", return_value=True
        ), mock.patch.object(ai_enrichment, "enrich_event", side_effect=enrich_one):
            result = ai_enrichment.enrich_events(
                values,
                settings=replace(self.settings, batch_timeout_seconds=120, workers=2),
            )

        self.assertEqual(
            ["done: First", "done: Second"],
            [value["ai_summary"] for value in result],
        )

    def test_different_events_do_not_hold_cache_lock_during_api_requests(self):
        barrier = threading.Barrier(2, timeout=1)
        cache_lock = self.settings.cache_db.with_suffix(
            self.settings.cache_db.suffix + ".lock"
        )

        class ConcurrentClient(FakeClient):
            def structured(self, **kwargs):
                if kwargs["stage"] == "facts":
                    with cache_lock.open("a+", encoding="utf-8") as lock:
                        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
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

    def test_database_schema_and_journal_mode_are_initialized_once_per_process(self):
        path = Path(self.temporary.name) / "schema-once.sqlite3"
        ai_enrichment._INITIALIZED_DATABASES.discard(path.resolve())

        with mock.patch.object(
            ai_enrichment.fcntl, "flock", wraps=ai_enrichment.fcntl.flock
        ) as flock:
            with ai_enrichment._locked_database(path, cache_key="first"):
                pass
            with ai_enrichment._locked_database(path, cache_key="second"):
                pass

        # One exclusive acquire and one release; the second connection skips
        # database-wide schema work entirely.
        self.assertEqual(2, flock.call_count)

    def test_same_event_cache_key_has_a_single_enrichment_owner(self):
        started = threading.Event()
        release = threading.Event()
        calls: list[str] = []
        calls_lock = threading.Lock()

        class BlockingClient:
            def structured(self, *, stage, **_kwargs):
                with calls_lock:
                    calls.append(stage)
                    first_call = len(calls) == 1
                if first_call:
                    started.set()
                    if not release.wait(timeout=2):
                        raise AssertionError("concurrent cache owner did not get released")
                return (FACTS if stage == "facts" else SUMMARY), ai_enrichment.Usage()

        client = BlockingClient()
        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(
                ai_enrichment.enrich_event,
                event(), settings=self.settings, client=client, now=self.now,
            )
            self.assertTrue(started.wait(timeout=1))
            second = executor.submit(
                ai_enrichment.enrich_event,
                event(), settings=self.settings, client=client, now=self.now,
            )
            time.sleep(0.05)
            release.set()
            results = [first.result(timeout=2), second.result(timeout=2)]

        self.assertEqual(["facts", "summary"], calls)
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
        source_description = source["description"]
        settings = ai_enrichment.AISettings(
            enabled=False, api_key="", model="gpt-5.6-luna",
            cache_db=Path(self.temporary.name) / "unused.sqlite3",
        )
        result = ai_enrichment.enrich_event(source, settings=settings, client=FakeClient([]))
        self.assertIn("Klangraum", result["description"])
        self.assertIn("09.08.2026", result["description"])
        self.assertNotIn(source_description, result["description"])
        self.assertEqual("generated", result["description_source"])
        self.assertTrue(result["description_html"])
        self.assertEqual("", result["ai_summary"])

    def test_disabled_provider_rejects_location_identity_without_cached_evidence(self):
        original = event(venue="", time="19:30")
        first = ai_enrichment.enrich_event(
            original,
            settings=self.settings,
            client=FakeClient([FACTS, SUMMARY]),
            now=self.now,
        )
        changed = event(
            venue="Altes Rathaus",
            time="19:30",
            description="Aktualisierte bestätigte Programminformation.",
        )
        self.assertNotEqual(event_id(original), event_id(changed))

        result = ai_enrichment.enrich_event(
            changed,
            settings=replace(self.settings, enabled=False, api_key=""),
            client=FakeClient([]),
            now=self.now + timedelta(days=1),
        )

        self.assertTrue(first["ai_summary"])
        self.assertEqual("", result["ai_summary"])
        self.assertEqual("Altes Rathaus", result["venue"])
        self.assertTrue(result["description"])
        self.assertEqual("generated", result["description_source"])

    def test_disabled_provider_reuses_exact_occurrence_across_bonn_source_aliases(self):
        sports = event(source="Bonn.de Sports", source_id="bonn-de-sports", time="19:30")
        first = ai_enrichment.enrich_event(
            sports,
            settings=self.settings,
            client=FakeClient([FACTS, SUMMARY]),
            now=self.now,
        )
        dedup_winner = event(
            source="Bonn.de Events",
            source_id="bonn-de-events",
            time="",
            start_at="2026-08-09T19:30+02:00",
            preserved_event_id=event_id(sports),
        )
        self.assertEqual(event_id(sports), event_id(dedup_winner))

        result = ai_enrichment.enrich_event(
            dedup_winner,
            settings=replace(self.settings, enabled=False, api_key=""),
            client=FakeClient([]),
            now=self.now + timedelta(days=1),
        )

        self.assertEqual(first["ai_summary"], result["ai_summary"])
        self.assertEqual("", result["venue"])

    def test_bonn_source_alias_cache_rejects_a_different_occurrence_time(self):
        ai_enrichment.enrich_event(
            event(source="Bonn.de Sports", source_id="bonn-de-sports", time="19:30"),
            settings=self.settings,
            client=FakeClient([FACTS, SUMMARY]),
            now=self.now,
        )

        result = ai_enrichment.enrich_event(
            event(source="Bonn.de Events", source_id="bonn-de-events", time="21:00"),
            settings=replace(self.settings, enabled=False, api_key=""),
            client=FakeClient([]),
            now=self.now + timedelta(days=1),
        )

        self.assertEqual("", result["ai_summary"])

    def test_cached_occurrence_rejects_missing_location_facts_for_known_venue(self):
        current = event(venue="Oper Bonn", time="19:30")
        cached_facts = dict(FACTS, venue="")

        self.assertFalse(ai_enrichment._cached_occurrence_matches(current, cached_facts))

    def test_cross_identity_fallback_reuses_start_time_when_source_adds_end_time(self):
        first = ai_enrichment.enrich_event(
            event(venue="", time="19:30"),
            settings=self.settings,
            client=FakeClient([FACTS, SUMMARY]),
            now=self.now,
        )
        with closing(sqlite3.connect(self.settings.cache_db)) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute("SELECT * FROM ai_event_enrichment").fetchone()
            columns = row.keys()
            values = dict(row)
            values["event_key"] = f"legacy-event-{row['event_key'][-10:]}"
            connection.execute(
                f"INSERT INTO ai_event_enrichment ({', '.join(columns)}) "
                f"VALUES ({', '.join('?' for _ in columns)})",
                [values[column] for column in columns],
            )
            connection.commit()
        changed = event(venue="", time="19:30–21:00")

        result = ai_enrichment.enrich_event(
            changed,
            settings=replace(self.settings, enabled=False, api_key=""),
            client=FakeClient([]),
            now=self.now + timedelta(days=1),
        )

        self.assertEqual(first["ai_summary"], result["ai_summary"])
        self.assertEqual("19:30–21:00", result["time"])

    def test_cross_identity_fallback_rejects_a_different_same_day_time(self):
        ai_enrichment.enrich_event(
            event(venue="", time="19:30"),
            settings=self.settings,
            client=FakeClient([FACTS, SUMMARY]),
            now=self.now,
        )
        different_occurrence = event(venue="Altes Rathaus", time="21:00–22:30")

        result = ai_enrichment.enrich_event(
            different_occurrence,
            settings=replace(self.settings, enabled=False, api_key=""),
            client=FakeClient([]),
            now=self.now + timedelta(days=1),
        )

        self.assertEqual("", result["ai_summary"])

    def test_batch_deadline_reuses_cached_summary_for_changed_source_material(self):
        # The batch path filters on the live import window, so this case dates
        # both records relative to today instead of using the pinned fixture.
        source = in_window_event(description=(
            "Klangraum bietet Kammermusik und ein Publikumsgespräch. Beginn ist um 19:30 Uhr "
            "im Alten Rathaus in Bonn. Der Eintritt ist frei und eine Anmeldung ist nötig."
        ))
        occurrence = {
            "start_date": source["start_date"],
            "end_date": source["end_date"],
        }
        ai_enrichment.enrich_event(
            source,
            settings=self.settings,
            client=FakeClient([{**FACTS, **occurrence}, SUMMARY]),
            now=self.now,
        )
        changed = in_window_event(description=(
            "Aktualisierte Programminformation: Klangraum beginnt um 19:30 Uhr im Alten Rathaus "
            "in Bonn. Der Eintritt ist frei und eine Anmeldung ist nötig."
        ))
        published_id = event_id(changed)

        stats: dict[str, int] = {}
        [result] = ai_enrichment.enrich_events([
            changed,
        ], settings=replace(self.settings, batch_timeout_seconds=-1), stats=stats)

        self.assertEqual(SUMMARY["ai_summary"], result["ai_summary"])
        self.assertEqual("", result["description"])
        self.assertEqual("19:30", result["time"])
        self.assertEqual("Altes Rathaus", result["venue"])
        self.assertEqual(published_id, event_id(result))
        self.assertEqual(1, stats["ai_deadline_skipped_event_count"])
        self.assertEqual(0, stats["ai_deadline_skipped_without_summary_event_count"])

    def test_batch_reports_deadline_skips_while_publishing_master_data_fallbacks(self):
        stats: dict[str, int] = {}

        results = ai_enrichment.enrich_events(
            [
                in_window_event(title="Erster Termin"),
                in_window_event(title="Zweiter Termin"),
            ],
            settings=replace(self.settings, batch_timeout_seconds=-1),
            stats=stats,
        )

        self.assertEqual(2, stats["ai_deadline_skipped_event_count"])
        self.assertEqual(0, stats["ai_cap_skipped_event_count"])
        self.assertEqual(2, stats["ai_deadline_skipped_without_summary_event_count"])
        self.assertTrue(all(result["description"] for result in results))
        self.assertTrue(all(result["description_source"] == "generated" for result in results))

    def test_near_deadline_skips_without_creating_a_negative_cache_row(self):
        stats: dict[str, int] = {}

        ai_enrichment.enrich_events(
            [in_window_event()],
            settings=replace(self.settings, batch_timeout_seconds=60),
            stats=stats,
        )

        self.assertEqual(1, stats["ai_deadline_skipped_event_count"])
        self.assertFalse(self.settings.cache_db.exists())

    def test_batch_reports_cap_skips_separately_from_deadline_skips(self):
        stats: dict[str, int] = {}

        ai_enrichment.enrich_events(
            [
                in_window_event(title="Erster Termin"),
                in_window_event(title="Zweiter Termin"),
            ],
            settings=replace(self.settings, enabled=False, api_key="", max_events=1),
            stats=stats,
        )

        self.assertEqual(1, stats["ai_cap_skipped_event_count"])
        self.assertEqual(0, stats["ai_deadline_skipped_event_count"])
        self.assertEqual(1, stats["ai_cap_skipped_without_summary_event_count"])

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

    def test_poisoned_cached_category_type_is_skipped_without_aborting(self):
        ai_enrichment.enrich_event(
            event(),
            settings=self.settings,
            client=FakeClient([FACTS, SUMMARY]),
            now=self.now,
        )
        poisoned = {**SUMMARY, "category_key": ["concert"]}
        with sqlite3.connect(self.settings.cache_db) as connection:
            connection.execute(
                "UPDATE ai_event_enrichment SET stage2_json = ?",
                (json.dumps(poisoned),),
            )

        result = ai_enrichment._reuse_cached_success(event(), self.settings)

        self.assertEqual(SUMMARY["ai_summary"], result["ai_summary"])
        self.assertEqual("other", result["category_key"])

    def test_client_boundary_rejects_schema_type_violations(self):
        with self.assertRaisesRegex(ai_enrichment.AIEnrichmentError, "output.category_key"):
            ai_enrichment._validate_types(
                ai_enrichment._SUMMARY_SCHEMA,
                {**SUMMARY, "category_key": ["concert"]},
            )

    def test_summary_pipeline_change_reuses_compatible_cached_facts(self):
        ai_enrichment.enrich_event(
            event(),
            settings=self.settings,
            client=FakeClient([FACTS, SUMMARY]),
            now=self.now,
        )
        summary_only = FakeClient([SUMMARY])

        with mock.patch.object(
            ai_enrichment,
            "cache_pipeline_version",
            return_value="event-summary-next:gpt-5.6-luna",
        ):
            result = ai_enrichment.enrich_event(
                event(),
                settings=self.settings,
                client=summary_only,
                now=self.now + timedelta(days=1),
            )

        self.assertEqual(["summary"], [call["stage"] for call in summary_only.calls])
        self.assertEqual(SUMMARY["ai_summary"], result["ai_summary"])

    def test_summary_pipeline_change_migrates_unlabelled_v15_facts(self):
        settings = replace(
            self.settings,
            provider="openrouter",
            model="deepseek/deepseek-v4-flash-0731",
            summary_reasoning_effort="low",
        )
        ai_enrichment.enrich_event(
            event(), settings=settings, client=FakeClient([FACTS, SUMMARY]), now=self.now,
        )
        with closing(sqlite3.connect(settings.cache_db)) as connection:
            connection.execute(
                "UPDATE ai_event_enrichment SET facts_pipeline_version = ''"
            )
            connection.commit()
        summary_only = FakeClient([SUMMARY])

        with mock.patch.object(
            ai_enrichment,
            "cache_pipeline_version",
            return_value="event-facts-summary-v16:openrouter:deepseek/deepseek-v4-flash-0731:"
            "facts-none:summary-low",
        ):
            ai_enrichment.enrich_event(
                event(), settings=settings, client=summary_only,
                now=self.now + timedelta(days=1),
            )

        self.assertEqual(["summary"], [call["stage"] for call in summary_only.calls])

    def test_facts_pipeline_change_does_not_reuse_incompatible_cached_facts(self):
        ai_enrichment.enrich_event(
            event(),
            settings=self.settings,
            client=FakeClient([FACTS, SUMMARY]),
            now=self.now,
        )
        refreshed = FakeClient([FACTS, SUMMARY])

        with mock.patch.object(
            ai_enrichment,
            "cache_pipeline_version",
            return_value="event-summary-next:gpt-5.6-luna",
        ), mock.patch.object(
            ai_enrichment,
            "facts_cache_version",
            return_value="event-facts-next:gpt-5.6-luna",
        ):
            ai_enrichment.enrich_event(
                event(),
                settings=self.settings,
                client=refreshed,
                now=self.now + timedelta(days=1),
            )

        self.assertEqual(["facts", "summary"], [call["stage"] for call in refreshed.calls])

    def test_daily_new_cache_row_budget_blocks_an_accidental_full_reprocess(self):
        settings = replace(self.settings, max_new_cache_rows_per_day=1)
        ai_enrichment.enrich_event(
            event(title="First"),
            settings=settings,
            client=FakeClient([FACTS, SUMMARY]),
            now=self.now,
        )

        with self.assertRaises(ai_enrichment.AICacheMissBudgetExceeded):
            ai_enrichment.enrich_event(
                event(title="Second"),
                settings=settings,
                client=FakeClient([FACTS, SUMMARY]),
                now=self.now,
            )

    def test_batch_reports_daily_cache_budget_skips(self):
        settings = replace(self.settings, max_new_cache_rows_per_day=1)
        ai_enrichment.enrich_event(
            in_window_event(title="First"),
            settings=settings,
            client=FakeClient([FACTS, SUMMARY]),
            now=datetime.now(timezone.utc),
        )
        stats: dict[str, int] = {}

        [result] = ai_enrichment.enrich_events(
            [in_window_event(title="Second")], settings=settings, stats=stats,
        )

        self.assertEqual(1, stats["ai_cache_budget_skipped_event_count"])
        self.assertEqual(
            1, stats["ai_cache_budget_skipped_without_summary_event_count"]
        )
        self.assertEqual("", result["ai_summary"])
        self.assertTrue(result["description"])

    def test_zero_daily_new_cache_row_budget_explicitly_allows_full_reprocess(self):
        settings = replace(self.settings, max_new_cache_rows_per_day=0)
        for title in ("First", "Second"):
            ai_enrichment.enrich_event(
                event(title=title),
                settings=settings,
                client=FakeClient([FACTS, SUMMARY]),
                now=self.now,
            )

        with closing(sqlite3.connect(settings.cache_db)) as connection:
            self.assertEqual(
                2,
                connection.execute("SELECT COUNT(*) FROM ai_event_enrichment").fetchone()[0],
            )

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
        self.assertIn("Klangraum", result["description"])
        self.assertEqual("generated", result["description_source"])
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

    def test_transport_failure_uses_bounded_negative_cache(self):
        settings = replace(self.settings, max_attempts=1, negative_cache_hours=0)
        client = FakeClient([
            ai_enrichment.AIEnrichmentError(
                "OpenAI request failed: URLError",
                transient=True,
            )
        ])

        ai_enrichment.enrich_event(event(), settings=settings, client=client, now=self.now)

        with closing(sqlite3.connect(settings.cache_db)) as connection:
            negative_until = datetime.fromisoformat(
                connection.execute(
                    "SELECT negative_until FROM ai_event_enrichment"
                ).fetchone()[0]
            )
        self.assertGreater(negative_until, self.now)
        self.assertLessEqual(negative_until, self.now + timedelta(hours=24))

    def test_transient_failure_backs_off_before_retrying(self):
        client = FakeClient([
            ai_enrichment.AIEnrichmentError("OpenAI HTTP 429", transient=True),
            FACTS,
            SUMMARY,
        ])

        with mock.patch.object(ai_enrichment.time, "sleep") as sleep:
            result = ai_enrichment.enrich_event(
                event(), settings=self.settings, client=client, now=self.now,
            )

        self.assertEqual(SUMMARY["ai_summary"], result["ai_summary"])
        sleep.assert_called_once_with(1.0)

    def test_content_refusal_keeps_permanent_negative_cache(self):
        settings = replace(self.settings, max_attempts=1, negative_cache_hours=0)
        client = FakeClient([
            ai_enrichment.AIEnrichmentError("OpenAI refused the enrichment request")
        ])

        ai_enrichment.enrich_event(event(), settings=settings, client=client, now=self.now)

        with closing(sqlite3.connect(settings.cache_db)) as connection:
            negative_until = connection.execute(
                "SELECT negative_until FROM ai_event_enrichment"
            ).fetchone()[0]
        self.assertEqual("9999-12-31T23:59:59+00:00", negative_until)

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

    def test_conditional_museum_admission_is_not_free_on_an_ordinary_day(self):
        source = event(
            title="Aki Inomata: Mit-werden",
            start_date="2026-08-25",
            end_date="2026-08-25",
            price="",
            description=(
                "Freier Eintritt für alle an jedem ersten Sonntag im Monat. "
                "Kinder und Jugendliche bis einschließlich 18 Jahre haben immer freien Eintritt."
            ),
        )
        payload = ai_enrichment._input_payload(source, source["description"])
        extracted = {
            **FACTS,
            "admission": {
                "is_free": True,
                "amount": 0,
                "currency": "EUR",
                "note": "Eintritt frei",
                "donation_suggested": False,
            },
        }

        cleaned = ai_enrichment._sanitize_extracted_facts(extracted, payload)

        self.assertIsNone(cleaned["admission"]["is_free"])
        self.assertIsNone(cleaned["admission"]["amount"])
        self.assertIsNone(cleaned["admission"]["note"])

    def test_first_sunday_museum_occurrence_can_keep_conditional_free_admission(self):
        source = event(
            title="Aki Inomata: Mit-werden",
            start_date="2026-09-06",
            end_date="2026-09-06",
            price="",
            description="Freier Eintritt für alle an jedem ersten Sonntag im Monat.",
        )
        payload = ai_enrichment._input_payload(source, source["description"])
        extracted = {
            **FACTS,
            "admission": {
                "is_free": True,
                "amount": 0,
                "currency": "EUR",
                "note": "Freier Eintritt am ersten Sonntag im Monat",
                "donation_suggested": False,
            },
        }

        cleaned = ai_enrichment._sanitize_extracted_facts(extracted, payload)

        self.assertIs(cleaned["admission"]["is_free"], True)

    def test_source_supported_registration_is_promoted_from_neutral_facts(self):
        source = event(
            description=(
                "Gemeinsam werden im Museum Rätsel gelöst. Anmeldung: Begrenzte Teilnehmerzahl; "
                "wir bitten um rechtzeitige Anmeldung."
            ),
        )
        payload = ai_enrichment._input_payload(source, source["description"])
        extracted = {
            **FACTS,
            "registration": None,
            "neutral_facts": ["Anmeldung erforderlich."],
        }

        cleaned = ai_enrichment._sanitize_extracted_facts(extracted, payload)

        self.assertEqual("Anmeldung erforderlich.", cleaned["registration"])
        self.assertNotIn("Anmeldung erforderlich.", cleaned["neutral_facts"])

    def test_conflicting_related_event_identity_is_removed_before_summary(self):
        source = event(
            title="Call for Ideas: Impact Pitch Night",
            start_date="2026-08-11",
            end_date="2026-08-11",
            city="",
            description=(
                "Gesucht werden frühphasige Gründungsideen aus Bonn und dem Rhein-Sieg-Kreis. "
                "Die zweite Bonner Impact Pitch Night findet am 3. November 2026 im DIGITALHUB statt. "
                "Die besten Ideen erhalten Preisgelder und ein Pitch-Coaching."
            ),
        )
        payload = ai_enrichment._input_payload(source, source["description"])
        related_event = {
            **FACTS,
            "title": "Bonner Impact Pitch Night",
            "start_date": "2026-11-03",
            "end_date": "2026-11-03",
            "time": "18:00",
            "time_note": "Einlass ab 17:30 Uhr",
            "venue": "DIGITALHUB",
            "venue_address": "Rheinwerkallee 6, 53227 Bonn",
            "city": "Sankt Augustin",
        }

        for start_date, end_date in (
            ("2026-11-03", "2026-11-03"),
            ("2026-11-03", None),
            (None, "2026-11-03"),
        ):
            with self.subTest(start_date=start_date, end_date=end_date):
                cleaned = ai_enrichment._sanitize_extracted_facts(
                    {**related_event, "start_date": start_date, "end_date": end_date},
                    payload,
                )

                self.assertEqual("Call for Ideas: Impact Pitch Night", cleaned["title"])
                self.assertEqual("2026-08-11", cleaned["start_date"])
                self.assertEqual("2026-08-11", cleaned["end_date"])
                self.assertIsNone(cleaned["time"])
                self.assertIsNone(cleaned["time_note"])
                self.assertIsNone(cleaned["venue"])
                self.assertIsNone(cleaned["venue_address"])
                self.assertIsNone(cleaned["city"])

    def test_extracted_location_must_be_supported_by_source_material(self):
        source = event(
            city="",
            venue="",
            description=(
                "Das Weinfest findet auf dem Sieglarer Marktplatz in Troisdorf statt. "
                "Am Freitag beginnt die Afterworkparty um 17 Uhr."
            ),
        )
        payload = ai_enrichment._input_payload(source, source["description"])
        hallucinated = {
            **FACTS,
            "city": "Bonn",
            "venue": "Bonner Marktplatz",
            "venue_address": "Markt 2, 53111 Bonn",
        }

        cleaned = ai_enrichment._sanitize_extracted_facts(hallucinated, payload)

        self.assertIsNone(cleaned["city"])
        self.assertIsNone(cleaned["venue"])
        self.assertIsNone(cleaned["venue_address"])
        self.assertEqual(
            ai_enrichment._summary_quality(
                "Das Weinfest findet auf dem Sieglarer Marktplatz in Bonn statt und beginnt am Freitag um 17 Uhr.",
                source["description"],
                {**cleaned, "_publication_start": "2026-08-09", "_publication_end": "2026-08-09"},
            ),
            "summary contradicts the source location",
        )

    def test_summary_quality_rejects_contact_data_non_german_and_unsupported_time(self):
        facts = {**FACTS, "time": "19:30"}
        cases = (
            (
                "Das Konzert findet im Rathaus statt und Details stehen auf https://example.test.",
                "summary contains contact or outbound-link data",
            ),
            (
                "This concert presents chamber music followed by a discussion with the ensemble.",
                "summary is not recognizably German",
            ),
            (
                "Das Konzert beginnt um 20:15 Uhr und danach gibt es ein Gespräch mit dem Ensemble.",
                "summary contains a clock time absent from the facts",
            ),
        )

        for summary, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(
                    ai_enrichment._summary_quality(summary, event()["description"], facts),
                    expected,
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

    def test_age_information_retry_avoids_invented_target_group(self):
        facts = {
            **FACTS,
            "target_group": [],
            "age_information": "Ab 8 Jahren",
        }
        invented_target_group = {
            **SUMMARY,
            "ai_summary": (
                "Das Escape Game richtet sich an Kinder ab 8 Jahren. "
                "Im Museum werden mit Taschenlampen mehrere Rätsel gelöst."
            ),
        }
        safe_summary = {
            **SUMMARY,
            "ai_summary": (
                "Beim Escape Game werden im Museum mit Taschenlampen mehrere Rätsel gelöst. "
                "Die Teilnahme ist ab 8 Jahren möglich."
            ),
        }
        client = FakeClient([facts, invented_target_group, safe_summary])

        result = ai_enrichment.enrich_event(
            event(title="Escape Game im LVR Museum"),
            settings=self.settings,
            client=client,
            now=self.now,
        )

        self.assertEqual(safe_summary["ai_summary"], result["ai_summary"])
        self.assertIn(
            "Formuliere die Altersangabe neutral",
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

    def test_ai_filled_identity_fields_do_not_move_the_public_event_id(self):
        source = event(
            time="", start_at="", venue="", city="",
            description=(
                "Erleben Sie ein Konzert mit Kammermusik. Im Anschluss sprechen die Mitwirkenden "
                "mit dem Publikum. Der Eintritt ist frei und eine Anmeldung ist nötig. "
                "Veranstalter: Kulturamt Bonn. Beginn ist um 19:30 Uhr im Alten Rathaus in Bonn."
            ),
        )
        published_id = event_id(source)

        result = ai_enrichment.enrich_event(
            source,
            settings=self.settings,
            client=FakeClient([FACTS, SUMMARY]),
            now=self.now,
        )

        self.assertEqual(result["time"], "19:30")
        self.assertEqual(result["venue"], "Altes Rathaus")
        self.assertEqual(result["city"], "Bonn")
        self.assertEqual(event_id(result), published_id)

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
