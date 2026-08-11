import json
import tempfile
import tomllib
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from nrw_events import config, radio_primary_resolution as resolution, report, runner
from nrw_events.identity import event_id
from nrw_events.models import MAX_DISCOVERY_PROVENANCE_SOURCES
from nrw_events.observability import configure_logging
from nrw_events.runtime import EventWindow, RunContext
from nrw_events.validation import canonicalize_event


RADIO_ID = "radio-bonn-rhein-sieg"


def lead(title, start_date, **overrides):
    return {
        "title": title,
        "source": "Radio Bonn/Rhein-Sieg",
        "source_id": RADIO_ID,
        "source_role": "discovery",
        "discovered_via": [RADIO_ID],
        "date": start_date,
        "start_date": start_date,
        "end_date": start_date,
        "score": 1.0,
        "city": "Bonn",
        "category": "Event",
        **overrides,
    }


def primary(title, start_date, source, source_id, link, **overrides):
    return canonicalize_event({
        "title": title,
        "source": source,
        "source_id": source_id,
        "date": start_date,
        "score": 1.0,
        "city": "Bonn",
        "link": link,
        **overrides,
    })


class RadioPrimaryManifestTests(unittest.TestCase):
    def test_checked_in_manifest_has_84_unique_audited_keys_and_expected_classes(self):
        manifest = resolution.load_manifest()

        self.assertEqual(len(manifest), 84)
        self.assertEqual(len({entry.key for entry in manifest}), 84)
        classes = [resolution.expected_resolution_class(entry) for entry in manifest]
        self.assertEqual(classes.count("promote"), 64)
        self.assertEqual(classes.count("match"), 14)
        self.assertEqual(classes.count("withhold"), 6)

    def test_manifest_rejects_duplicate_keys_unknown_corrections_and_unsafe_fallbacks(self):
        valid = {
            "title": "Example", "start_date": "2026-08-09",
            "primary_url": "https://official.example/event",
            "primary_source": "Official", "primary_source_id": "official",
            "evidence_status": "confirmed", "verified_at": "2026-08-09",
            "fallback_publication": True, "corrections": {},
        }
        invalid_entries = [
            [valid, dict(valid)],
            [{**valid, "corrections": {"description": "publisher copy"}}],
            [{**valid, "evidence_status": "unresolved"}],
            [{**valid, "primary_url": "", "fallback_publication": True}],
            [{**valid, "primary_url": "javascript:alert(1)"}],
            [{**valid, "evidence_status": "probable"}],
            [{**valid, "primary_facts": {"description_html": "<p>copy</p>"}}],
            [{**valid, "evidence_status": "probable", "fallback_publication": False,
              "withhold_reason": "insufficient_first_party_evidence",
              "primary_facts": {"description": "Unconfirmed copy"}}],
        ]
        for entries in invalid_entries:
            with self.subTest(entries=entries):
                with tempfile.TemporaryDirectory() as directory:
                    path = Path(directory) / "manifest.json"
                    path.write_text(json.dumps({"schema_version": 1, "entries": entries}))
                    with self.assertRaises(ValueError):
                        resolution.load_manifest(path)

    def test_manifest_is_included_in_installed_package_data(self):
        pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
        package_data = pyproject["tool"]["setuptools"]["package-data"]["nrw_events"]

        self.assertIn("sources/radio_primary_sources.json", package_data)


class RadioPrimaryResolutionTests(unittest.TestCase):
    def setUp(self):
        # A promoted fallback now reads its audited primary URL, so the
        # end-to-end cases in this class would otherwise fetch real pages.
        # Resolution behaviour is what they assert; enrichment has its own
        # tests in RadioFallbackDetailEnrichmentTests.
        patcher = mock.patch.object(
            runner.detail_enrichment, "enrich_events",
            side_effect=lambda drafts, **_kwargs: drafts,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_existing_first_party_event_wins_and_is_only_annotated(self):
        entry = resolution.entry_for_key(("Platz & Prost im Rhein Sieg Forum", "2026-08-08"))
        official = primary(
            "Platz & Prost – Summer Edition 2026", "2026-08-08",
            "RHEIN SIEG FORUM", "rhein-sieg-forum", entry.primary_url,
            city="Siegburg", venue="RHEIN SIEG FORUM",
        )

        outcome = resolution.resolve_radio_leads(
            [lead(entry.title, entry.start_date)], [official], manifest=(entry,),
        )

        self.assertEqual(len(outcome.events), 1)
        self.assertEqual(outcome.events[0].source_id, "rhein-sieg-forum")
        self.assertEqual(outcome.events[0].discovered_via, [RADIO_ID])
        self.assertEqual(outcome.dispositions[entry.key], "matched_existing_primary")
        self.assertEqual(outcome.research_leads, ())
        self.assertEqual(outcome.promoted_fallback_event_ids, frozenset())

    def test_exact_overview_url_does_not_match_an_unrelated_event(self):
        entry = resolution.entry_for_key((
            "Supernatural plays Santana beim SWB-Sommerfestival", "2026-08-07",
        ))
        unrelated = primary(
            "Completely unrelated event", entry.start_date,
            entry.primary_source, entry.primary_source_id, entry.primary_url,
        )

        outcome = resolution.resolve_radio_leads(
            [lead(entry.title, entry.start_date)], [unrelated], manifest=(entry,),
        )

        self.assertEqual(len(outcome.events), 2)
        self.assertEqual(outcome.dispositions[entry.key], "promoted_fallback")
        self.assertEqual(outcome.events[0].discovered_via, [])
        self.assertEqual(outcome.events[1].title, entry.title)

    def test_filtered_primary_candidate_does_not_consume_a_publishable_fallback(self):
        entry = resolution.entry_for_key(("Familientag im Aggua Troisdorf", "2026-08-07"))
        discovery = lead(
            entry.title, entry.start_date, score=1.0,
            venue="AGGUA Troisdorf", city="Troisdorf",
        )
        low_score_primary = {
            "title": entry.title, "source": entry.primary_source,
            "source_id": entry.primary_source_id, "date": entry.start_date,
            "score": 0.1, "city": "Troisdorf", "venue": "AGGUA Troisdorf",
            "link": entry.primary_url,
        }
        context = RunContext(
            config.RuntimeConfig(score_floor=1.0, radius_km=1000, series_ledger_json=""),
            EventWindow(datetime(2026, 8, 6), datetime(2026, 8, 27)),
            "radio-filtered-primary",
            configure_logging("radio-filtered-primary", "ERROR", "", ""),
            clock=lambda: datetime(2026, 8, 9, 12),
        )
        with mock.patch.object(runner, "_previous_snapshot", return_value={}), \
                mock.patch.object(resolution, "load_manifest", return_value=(entry,)):
            result = runner.run_import(context, {
                "Radio Bonn/Rhein-Sieg": lambda: [discovery],
                entry.primary_source: lambda: [low_score_primary],
            })

        [event] = result.events
        self.assertEqual(event.source_id, entry.primary_source_id)
        self.assertEqual(event.score, 1.0)
        self.assertEqual(event.discovered_via, [RADIO_ID])
        self.assertEqual(
            result.source_results["Radio Bonn/Rhein-Sieg"].research_lead_count, 0,
        )
        self.assertEqual(
            result.source_results["Radio Bonn/Rhein-Sieg"].accepted_event_count, 1,
        )

    def test_filtered_fallback_remains_a_research_lead(self):
        entry = resolution.entry_for_key(("Familientag im Aggua Troisdorf", "2026-08-07"))
        discovery = lead(
            entry.title, entry.start_date, score=0.1,
            venue="AGGUA Troisdorf", city="Troisdorf",
        )
        context = RunContext(
            config.RuntimeConfig(score_floor=1.0, radius_km=1000, series_ledger_json=""),
            EventWindow(datetime(2026, 8, 6), datetime(2026, 8, 27)),
            "radio-filtered-fallback",
            configure_logging("radio-filtered-fallback", "ERROR", "", ""),
            clock=lambda: datetime(2026, 8, 9, 12),
        )
        with mock.patch.object(runner, "_previous_snapshot", return_value={}), \
                mock.patch.object(resolution, "load_manifest", return_value=(entry,)):
            result = runner.run_import(
                context, {"Radio Bonn/Rhein-Sieg": lambda: [discovery]},
            )

        self.assertEqual(result.events, ())
        radio_result = result.source_results["Radio Bonn/Rhein-Sieg"]
        self.assertEqual(radio_result.accepted_event_count, 0)
        self.assertEqual(radio_result.research_lead_count, 1)
        self.assertEqual(
            radio_result.research_lead_reasons,
            {"primary_fallback_filtered:score_floor": 1},
        )

    def test_discovery_provenance_survives_a_later_duplicate_winner(self):
        discovered = primary(
            "Official festival", "2026-08-09", "Official", "official",
            "https://official.example/festival", discovered_via=[RADIO_ID], score=1.0,
        )
        stronger = primary(
            "Official festival", "2026-08-09", "Official", "official",
            "https://official.example/festival", score=2.0,
        )

        [winner] = report.deduplicate([discovered, stronger])

        self.assertEqual(winner.score, 2.0)
        self.assertEqual(winner.discovered_via, [RADIO_ID])

    def test_duplicate_provenance_union_stays_within_canonical_limit(self):
        existing = [
            f"discovery-{index}"
            for index in range(MAX_DISCOVERY_PROVENANCE_SOURCES)
        ]
        winner = primary(
            "Official festival", "2026-08-09", "Official", "official",
            "https://official.example/festival", discovered_via=existing, score=2.0,
        )
        duplicate = primary(
            "Official festival", "2026-08-09", "Other", "other",
            "https://other.example/festival", discovered_via=["another-discovery"],
        )

        [merged] = report.deduplicate([winner, duplicate])

        self.assertEqual(len(merged.discovered_via), MAX_DISCOVERY_PROVENANCE_SOURCES)
        self.assertEqual(canonicalize_event(merged.to_dict()), merged)

    def test_radio_annotation_remains_valid_at_provenance_limit(self):
        entry = resolution.entry_for_key((
            "Platz & Prost im Rhein Sieg Forum", "2026-08-08",
        ))
        existing = [
            f"discovery-{index}"
            for index in range(MAX_DISCOVERY_PROVENANCE_SOURCES)
        ]
        official = primary(
            entry.title, entry.start_date, entry.primary_source,
            entry.primary_source_id, entry.primary_url,
            discovered_via=existing, city="Siegburg",
        )

        outcome = resolution.resolve_radio_leads(
            [lead(entry.title, entry.start_date)], [official], manifest=(entry,),
        )
        [annotated] = outcome.events

        self.assertEqual(len(annotated.discovered_via), MAX_DISCOVERY_PROVENANCE_SOURCES)
        self.assertIn(RADIO_ID, annotated.discovered_via)
        self.assertEqual(canonicalize_event(annotated.to_dict()), annotated)

    def test_fallback_promotion_uses_only_safe_master_data_and_verified_corrections(self):
        entry = resolution.entry_for_key(("Familientag im Aggua Troisdorf", "2026-08-07"))
        dirty = lead(
            entry.title, entry.start_date,
            description="RADIO COPY MUST NOT SURVIVE",
            description_html="<p>RADIO HTML MUST NOT SURVIVE</p>",
            ai_summary="RADIO AI MUST NOT SURVIVE",
            link="https://www.radiobonn.de/article/tips",
            time="11:00",
            venue="AGGUA Troisdorf",
            city="Troisdorf",
        )

        outcome = resolution.resolve_radio_leads([dirty], [], manifest=(entry,))
        [event] = outcome.events
        serialized = json.dumps(event.to_dict(), ensure_ascii=False)

        self.assertEqual(outcome.dispositions[entry.key], "promoted_fallback")
        self.assertEqual(event.source, "AGGUA Troisdorf")
        self.assertEqual(event.source_id, "aggua")
        self.assertEqual(event.source_role, "primary")
        self.assertEqual(event.link, entry.primary_url)
        self.assertEqual(event.time, "10:00–18:00")
        self.assertTrue(event.start_at.endswith("T10:00:00+02:00"))
        self.assertTrue(event.end_at.endswith("T18:00:00+02:00"))
        self.assertEqual(event.description_source, "generated")
        self.assertIn("Familientag im Aggua Troisdorf", event.description)
        self.assertNotIn("radiobonn", serialized.casefold())
        self.assertNotIn("RADIO COPY", serialized)
        self.assertNotIn("RADIO HTML", serialized)
        self.assertNotIn("RADIO AI", serialized)

    def test_promotions_apply_only_manifest_verified_primary_facts(self):
        expected = {
            ("Willi Bellinghausens´s Dancing Sound", "2026-08-11"): {
                "description": "Deutsche Schlager. Eintritt frei.",
                "price": "kostenlos",
                "category_key": "concert",
            },
            ("Benefiz-Konzert in Bad Honnef", "2026-08-20"): {
                "description": (
                    "Auf Einladung der Bürgerstiftung kommt wieder die mitreißende Bonner Band "
                    "„Plenty Fourty“ mit Soul and Funk im modern Style von Joe Cocker, "
                    "Aretha Franklin, van Morrisson, Tina Turner, Tower of Power und anderen, "
                    "die keinen auf den Sitzen hält."
                ),
                "price": "",
                "category_key": "concert",
            },
        }
        for key, facts in expected.items():
            with self.subTest(key=key):
                entry = resolution.entry_for_key(key)
                dirty = lead(
                    entry.title, entry.start_date,
                    description="RADIO COPY MUST NOT SURVIVE",
                    price="99 Euro",
                    category_key="sports",
                    category_label="Sport",
                )

                [event] = resolution.resolve_radio_leads(
                    [dirty], [], manifest=(entry,),
                ).events

                self.assertEqual(event.description, facts["description"])
                self.assertEqual(event.price, facts["price"])
                self.assertEqual(event.category_key, facts["category_key"])
                self.assertEqual(event.category_label, "Konzert")
                self.assertEqual(event.description_source, "scraped")
                self.assertEqual(event.discovered_via, [RADIO_ID])
                self.assertEqual(event.link, entry.primary_url)
                self.assertNotIn("RADIO COPY", json.dumps(event.to_dict(), ensure_ascii=False))

    def test_city_correction_recomputes_stale_radio_location_fields(self):
        entry = resolution.entry_for_key(("Platz & Prost im Rhein Sieg Forum", "2026-08-08"))
        dirty = lead(
            entry.title, entry.start_date, city="Bonn", venue="RHEIN SIEG FORUM",
            distance_km=0.0, location_confidence="known_city",
        )

        [event] = resolution.resolve_radio_leads([dirty], [], manifest=(entry,)).events

        self.assertEqual(event.city, "Siegburg")
        self.assertEqual(event.location_confidence, "exact")
        self.assertGreater(event.distance_km, 0)
        self.assertIsNotNone(event.venue_latitude)

    def test_all_manifest_keys_have_deterministic_promote_match_or_withhold_disposition(self):
        manifest = resolution.load_manifest()
        leads = [lead(entry.title, entry.start_date) for entry in manifest]

        outcome = resolution.resolve_radio_leads(leads, [], manifest=manifest)

        self.assertEqual(set(outcome.dispositions), {entry.key for entry in manifest})
        self.assertEqual(
            {value: list(outcome.dispositions.values()).count(value) for value in set(outcome.dispositions.values())},
            {"promoted_fallback": 64, "awaiting_existing_primary": 14, "withheld": 6},
        )
        self.assertEqual(len(outcome.events), 70)
        self.assertEqual(len(outcome.research_leads), 20)

    def test_new_radio_leads_publish_only_audited_primary_facts(self):
        swb = resolution.entry_for_key(("SWB Sommerfestival: Höösch", "2026-08-12"))
        zauber = resolution.entry_for_key(("Zaubershow im Weinberg", "2026-08-22"))

        outcome = resolution.resolve_radio_leads([
            lead(swb.title, swb.start_date, description="RADIO COPY", price="99 Euro"),
            lead(zauber.title, zauber.start_date, description="RADIO COPY"),
        ], [], manifest=(swb, zauber))

        [event] = outcome.events
        self.assertEqual(event.source, "Parkrestaurant Rheinaue")
        self.assertEqual(event.link, swb.primary_url)
        self.assertEqual(event.price, "kostenlos")
        self.assertNotIn("RADIO COPY", event.description)
        self.assertEqual(outcome.dispositions[zauber.key], "withheld")
        self.assertEqual(outcome.research_leads[0]["reason"], "no_dated_primary_confirmation")

    def test_new_existing_primary_match_replaces_radio_without_duplicate(self):
        entry = resolution.entry_for_key(("Laurentius Kirmes", "2026-08-14"))
        official = primary(
            "Laurentius-Kirmes", entry.start_date,
            entry.primary_source, entry.primary_source_id, entry.primary_url,
            end_date="2026-08-16", venue="Dorfplatz Lessenich",
        )

        outcome = resolution.resolve_radio_leads(
            [lead(entry.title, entry.start_date)], [official], manifest=(entry,),
        )

        self.assertEqual(len(outcome.events), 1)
        self.assertEqual(outcome.events[0].source_id, "bonn-district-festivals")
        self.assertEqual(outcome.events[0].discovered_via, [RADIO_ID])
        self.assertEqual(outcome.dispositions[entry.key], "matched_existing_primary")

    def test_burg_satzvey_lead_expands_to_confirmed_primary_occurrences(self):
        entry = resolution.entry_for_key((
            "Sommertheater: Chaos im Land der Trolle", "2026-08-15",
        ))

        outcome = resolution.resolve_radio_leads(
            [lead(entry.title, entry.start_date)], [], manifest=(entry,),
        )

        self.assertEqual(
            [(event.start_date, event.source_id) for event in outcome.events],
            [("2026-08-15", "burg-satzvey"), ("2026-08-16", "burg-satzvey")],
        )
        self.assertTrue(all(event.link == entry.primary_url for event in outcome.events))

    def test_generic_stadtgarten_series_matches_each_official_act_without_generic_fallback(self):
        entry = resolution.entry_for_key(("Stadtgartenkonzerte", "2026-08-14"))
        official = [
            primary(
                f"Stadtgartenkonzerte: {act}", entry.start_date,
                entry.primary_source, entry.primary_source_id, entry.primary_url,
                venue="Stadtgarten",
            )
            for act in ("Act One", "Act Two")
        ]

        outcome = resolution.resolve_radio_leads(
            [lead(entry.title, entry.start_date)], official, manifest=(entry,),
        )

        self.assertEqual(len(outcome.events), 2)
        self.assertEqual({event.title for event in outcome.events}, {
            "Stadtgartenkonzerte: Act One", "Stadtgartenkonzerte: Act Two",
        })
        self.assertTrue(all(event.discovered_via == [RADIO_ID] for event in outcome.events))
        self.assertEqual(outcome.dispositions[entry.key], "matched_existing_primary")

    def test_stadtgarten_overview_url_does_not_annotate_unrelated_same_day_event(self):
        entry = resolution.entry_for_key(("Stadtgartenkonzerte", "2026-08-14"))
        official = primary(
            "Stadtgartenkonzerte: The Act", entry.start_date,
            entry.primary_source, entry.primary_source_id, entry.primary_url,
        )
        unrelated = primary(
            "Unrelated council event", entry.start_date,
            entry.primary_source, entry.primary_source_id, entry.primary_url,
        )

        outcome = resolution.resolve_radio_leads(
            [lead(entry.title, entry.start_date)], [official, unrelated], manifest=(entry,),
        )

        self.assertEqual(outcome.events[0].discovered_via, [RADIO_ID])
        self.assertEqual(outcome.events[1].discovered_via, [])

    def test_multiday_series_matches_each_official_occurrence_without_umbrella_fallback(self):
        entry = resolution.entry_for_key(("Sommer findet Stadt x Weinfest", "2026-08-07"))
        official = [
            primary(
                "Sommer findet Stadt x Weinfest", f"2026-08-{day:02d}",
                entry.primary_source, entry.primary_source_id,
                f"https://www.troisdorf.de/events/2026-08-{day:02d}-weinfest/",
                city="Troisdorf", venue="Sieglarer Marktplatz",
            )
            for day in (7, 8, 9)
        ]

        outcome = resolution.resolve_radio_leads(
            [lead(entry.title, entry.start_date)], official, manifest=(entry,),
        )

        self.assertEqual(len(outcome.events), 3)
        self.assertTrue(all(event.discovered_via == [RADIO_ID] for event in outcome.events))
        self.assertEqual(outcome.dispositions[entry.key], "matched_existing_primary")

    def test_single_existing_match_receives_audited_time_and_venue_corrections(self):
        entry = resolution.entry_for_key(("Siegburger Sommer Live: Bounce", "2026-08-12"))
        official = primary(
            entry.title, entry.start_date, entry.primary_source,
            entry.primary_source_id, entry.primary_url, city="Siegburg",
        )

        outcome = resolution.resolve_radio_leads(
            [lead(entry.title, entry.start_date)], [official], manifest=(entry,),
        )

        [event] = outcome.events
        self.assertEqual(event.time, "18:00")
        self.assertEqual(event.venue, "Siegburger Marktplatz")
        self.assertTrue(event.start_at.endswith("T18:00:00+02:00"))

    def test_verified_correction_matrix_is_applied(self):
        expected = {
            ("Familientag im Aggua Troisdorf", "2026-08-07"): {"time": "10:00–18:00"},
            ("Supernatural plays Santana beim SWB-Sommerfestival", "2026-08-07"): {
                "time": "19:30–22:00",
            },
            ("Wein & Schokolade in Bonn-Bad Godesberg", "2026-08-07"): {
                "time": "19:00–22:00",
            },
            ("Ferien-Aktion im LVR-Landesmueseum Bonn", "2026-08-07"): {
                "title": "Aktionstag am 7. August", "time": "11:00–17:00",
            },
            ("SWB Sommerfestival: Reckless", "2026-08-08"): {
                "title": "SWB-Sommerfestival: Reckless plays Bryan Adams",
                "time": "19:30–22:00",
            },
            ("Platz & Prost im Rhein Sieg Forum", "2026-08-08"): {"city": "Siegburg"},
            ("SWB Sommerfestival: Sou Brasil", "2026-08-09"): {"time": "14:00–17:00"},
            ("WM Philippinischer Stockkampf", "2026-08-11"): {"end_date": "2026-08-16"},
            ("Feierabendtour Niederkassel", "2026-08-07"): {"time": "16:00–18:00"},
            ("ADFC-Feierabendtour \"Über Berg und Tal von Dorf zu Dorf\"", "2026-08-13"): {"time": "16:00–18:30"},
            ("ADFC-Radtour", "2026-08-15"): {"time": "08:00–15:00"},
            ("Internationale Stummfilmtage", "2026-08-13"): {"end_date": "2026-08-22"},
            ("Eine Zeitreise ins Jahr 1976 im LVR-Museum", "2026-08-15"): {"city": "Mechernich"},
            ("Sommerfest im Haus Schlesien", "2026-08-16"): {"city": "Königswinter"},
            ("Kunstausstellung in Linz am Rhein", "2026-08-08"): {
                "title": "11 bis 8 – Franca Perschen & Helmut Reinelt",
                "end_date": "2026-08-30",
            },
            ("Benefiz-Konzert in Bad Honnef", "2026-08-20"): {
                "title": "Benefizkonzert: Plenty Fourty auf dem Annaplatz",
            },
            ("Musik auf der Rathaustreppe", "2026-08-20"): {
                "title": "Musik auf der Rathaustreppe: First Lane",
            },
        }
        for key, fields in expected.items():
            with self.subTest(key=key):
                entry = resolution.entry_for_key(key)
                if resolution.expected_resolution_class(entry) == "match":
                    for field, value in fields.items():
                        self.assertEqual(entry.corrections[field], value)
                    continue
                outcome = resolution.resolve_radio_leads(
                    [lead(entry.title, entry.start_date)], [], manifest=(entry,),
                )
                [event] = outcome.events
                for field, value in fields.items():
                    self.assertEqual(getattr(event, field), value)

    def test_sommerkino_expands_to_six_distinct_audited_film_occurrences(self):
        entry = resolution.entry_for_key(("Sommerkino in Rheinbach", "2026-08-22"))

        outcome = resolution.resolve_radio_leads(
            [lead(entry.title, entry.start_date)], [], manifest=(entry,),
        )

        self.assertEqual(len(outcome.events), 6)
        self.assertEqual(
            [event.start_date for event in outcome.events],
            [f"2026-08-{day:02d}" for day in range(22, 28)],
        )
        self.assertEqual(len({event.title for event in outcome.events}), 6)
        self.assertTrue(all(event.start_date == event.end_date for event in outcome.events))
        self.assertTrue(all(event.time == "19:30" for event in outcome.events))
        self.assertEqual(outcome.events[-1].title, "Sommerkino Rheinbach: Ein fast perfekter Antrag")

    def test_richer_retained_primary_replaces_generated_radio_fallback(self):
        entry = resolution.entry_for_key((
            "Supernatural plays Santana beim SWB-Sommerfestival", "2026-08-07",
        ))
        [fallback] = resolution.resolve_radio_leads(
            [lead(entry.title, entry.start_date)], [], manifest=(entry,),
        ).events
        retained = primary(
            entry.title, entry.start_date, entry.primary_source,
            entry.primary_source_id, entry.primary_url,
            description="Rich retained official programme copy",
            description_source="scraped",
        )

        fresh, remaining = runner._prefer_retained_primary_over_radio_fallback(
            [fallback], [retained], frozenset({runner.event_id(fallback)}),
        )

        self.assertEqual(remaining, [])
        self.assertEqual(fresh[0].description, "Rich retained official programme copy")
        self.assertEqual(fresh[0].description_source, "scraped")
        self.assertEqual(fresh[0].discovered_via, [RADIO_ID])

    def test_generated_retained_record_does_not_replace_fresh_radio_fallback(self):
        entry = resolution.entry_for_key((
            "Supernatural plays Santana beim SWB-Sommerfestival", "2026-08-07",
        ))
        [fallback] = resolution.resolve_radio_leads(
            [lead(entry.title, entry.start_date)], [], manifest=(entry,),
        ).events
        retained = primary(
            entry.title, entry.start_date, entry.primary_source,
            entry.primary_source_id, entry.primary_url,
            time="18:00", venue="Stale venue",
            description="Old generated copy", description_source="generated",
        )

        fresh, remaining = runner._prefer_retained_primary_over_radio_fallback(
            [fallback], [retained], frozenset({runner.event_id(fallback)}),
        )

        self.assertEqual(fresh, [fallback])
        self.assertEqual(remaining, [retained])

    def test_retained_replacement_caps_discovery_provenance(self):
        entry = resolution.entry_for_key((
            "Supernatural plays Santana beim SWB-Sommerfestival", "2026-08-07",
        ))
        [fallback] = resolution.resolve_radio_leads(
            [lead(entry.title, entry.start_date)], [], manifest=(entry,),
        ).events
        retained = primary(
            entry.title, entry.start_date, entry.primary_source,
            entry.primary_source_id, entry.primary_url,
            description="Rich retained official programme copy",
            description_source="scraped",
            discovered_via=[f"source-{index}" for index in range(MAX_DISCOVERY_PROVENANCE_SOURCES)],
        )

        fresh, remaining = runner._prefer_retained_primary_over_radio_fallback(
            [fallback], [retained], frozenset({runner.event_id(fallback)}),
        )

        self.assertEqual(remaining, [])
        self.assertEqual(len(fresh[0].discovered_via), MAX_DISCOVERY_PROVENANCE_SOURCES)
        self.assertEqual(fresh[0].discovered_via[-1], RADIO_ID)

    def test_current_matched_primary_is_not_replaced_by_retained_record(self):
        entry = resolution.entry_for_key(("Platz & Prost im Rhein Sieg Forum", "2026-08-08"))
        fresh_primary = primary(
            entry.title, entry.start_date, entry.primary_source,
            entry.primary_source_id, entry.primary_url,
            time="20:00", venue="New venue",
            description="Current generated official copy",
            description_source="generated",
            discovered_via=[RADIO_ID],
        )
        retained = primary(
            entry.title, entry.start_date, entry.primary_source,
            entry.primary_source_id, entry.primary_url,
            time="18:00", venue="Old venue",
            description="Old retained copy",
            description_source="scraped",
        )

        fresh, remaining = runner._prefer_retained_primary_over_radio_fallback(
            [fresh_primary], [retained], frozenset(),
        )
        retained_only = runner._retained_events_without_fresh_duplicate(fresh, remaining)

        self.assertEqual(fresh[0].time, "20:00")
        self.assertEqual(fresh[0].venue, "New venue")
        self.assertEqual(retained_only, [])

    def test_run_updates_only_public_lead_aggregates_and_never_serializes_lead_objects(self):
        manifest = resolution.load_manifest()
        discovery_leads = [lead(entry.title, entry.start_date) for entry in manifest]
        context = RunContext(
            config.RuntimeConfig(score_floor=0, radius_km=1000, series_ledger_json=""),
            EventWindow(datetime(2026, 8, 6), datetime(2026, 8, 27)),
            "radio-all", configure_logging("radio-all", "ERROR", "", ""),
            clock=lambda: datetime(2026, 8, 9, 12),
        )
        with mock.patch.object(runner, "_previous_snapshot", return_value={}):
            result = runner.run_import(
                context, {"Radio Bonn/Rhein-Sieg": lambda: discovery_leads},
            )
        snapshot = runner.build_snapshot(result, context)
        radio_result = result.source_results["Radio Bonn/Rhein-Sieg"]
        serialized = json.loads(json.dumps(snapshot.metadata))

        self.assertEqual(radio_result.raw_event_count, 84)
        self.assertEqual(radio_result.accepted_event_count, 68)
        self.assertEqual(radio_result.research_lead_count, 22)
        self.assertEqual(radio_result.research_lead_reasons, {
            "needs_existing_primary_match": 14,
            "no_reliable_primary_source": 2,
            "insufficient_first_party_evidence": 3,
            "no_dated_primary_confirmation": 1,
            "primary_fallback_filtered:window": 2,
        })
        self.assertEqual(snapshot.metadata["research_lead_count"], 22)
        self.assertEqual(
            snapshot.metadata["research_lead_reasons"],
            radio_result.research_lead_reasons,
        )
        self.assertNotIn("research_leads", serialized)
        self.assertNotIn(
            "research_leads",
            serialized["source_results"]["Radio Bonn/Rhein-Sieg"],
        )

    def test_cancelled_katharinenhof_override_suppresses_stale_scheduled_copy(self):
        manifest_entry = resolution.entry_for_key(("Mädelskram und Scheunentrödel", "2026-08-09"))
        discovery = lead(
            manifest_entry.title, manifest_entry.start_date,
            venue="Katharinenhof, Venner Straße 51",
        )
        scheduled = {
            "title": "Flohmarkt im Katharinenhof", "source": "Old official snapshot",
            "source_id": "old-katharinenhof", "date": "2026-08-09",
            "score": 1.0, "city": "Bonn",
            "venue": "Katharinenhof, Venner Straße 51",
            "link": "https://stale.example/flohmarkt",
        }
        context = RunContext(
            config.RuntimeConfig(score_floor=0, series_ledger_json=""),
            EventWindow(datetime(2026, 8, 6), datetime(2026, 8, 27)),
            "radio-cancel", configure_logging("radio-cancel", "ERROR", "", ""),
            clock=lambda: datetime(2026, 8, 9, 12),
        )
        with mock.patch.object(runner, "_previous_snapshot", return_value={}), \
                mock.patch.object(resolution, "load_manifest", return_value=(manifest_entry,)):
            result = runner.run_import(context, {
                "Radio Bonn/Rhein-Sieg": lambda: [discovery],
                "Old official snapshot": lambda: [scheduled],
            })

        [event] = result.events
        self.assertEqual(event.status, "cancelled")
        # Cancellation handling deliberately preserves the scheduled record's
        # occurrence identity while applying the audited first-party tombstone.
        self.assertEqual(event.source_id, "old-katharinenhof")
        self.assertEqual(event.cancellation_source, "Katharinenhof")
        self.assertEqual(event.score, 0.0)
        self.assertEqual(
            result.source_results["Radio Bonn/Rhein-Sieg"].research_lead_count, 0,
        )
        self.assertEqual(
            result.source_results["Radio Bonn/Rhein-Sieg"].cancelled_events[0]["status"],
            "cancelled",
        )

    def test_cancelled_override_still_wins_when_scheduled_primary_matches_exactly(self):
        entry = resolution.entry_for_key(("Mädelskram und Scheunentrödel", "2026-08-09"))
        discovery = lead(
            entry.title, entry.start_date,
            venue="Katharinenhof, Venner Straße 51",
        )
        scheduled = {
            "title": "Flohmarkt im Katharinenhof", "source": entry.primary_source,
            "source_id": entry.primary_source_id, "date": entry.start_date,
            "score": 1.0, "city": "Bonn",
            "venue": "Katharinenhof, Venner Straße 51", "link": entry.primary_url,
        }
        context = RunContext(
            config.RuntimeConfig(score_floor=0, series_ledger_json=""),
            EventWindow(datetime(2026, 8, 6), datetime(2026, 8, 27)),
            "radio-exact-cancel", configure_logging("radio-exact-cancel", "ERROR", "", ""),
            clock=lambda: datetime(2026, 8, 9, 12),
        )
        with mock.patch.object(runner, "_previous_snapshot", return_value={}), \
                mock.patch.object(resolution, "load_manifest", return_value=(entry,)):
            result = runner.run_import(context, {
                "Radio Bonn/Rhein-Sieg": lambda: [discovery],
                entry.primary_source: lambda: [scheduled],
            })

        [event] = result.events
        self.assertEqual(event.status, "cancelled")
        self.assertEqual(event.cancellation_source, entry.primary_source)
        self.assertEqual(
            result.source_results["Radio Bonn/Rhein-Sieg"].cancelled_events[0]["status"],
            "cancelled",
        )


class RadioFallbackDetailEnrichmentTests(unittest.TestCase):
    """The promoted fallback must read the primary page the manifest audited."""

    def _promoted_outcome(self):
        entry = resolution.entry_for_key(("Warther Kirmes", "2026-08-07"))
        outcome = resolution.resolve_radio_leads(
            [lead(entry.title, entry.start_date)], [], manifest=(entry,),
        )
        return entry, outcome

    def test_promoted_fallback_is_enriched_from_its_audited_primary_url(self):
        entry, outcome = self._promoted_outcome()
        [before] = outcome.events
        # The promotion itself only carries master data; this is the defect.
        self.assertEqual(before.venue, "")
        self.assertEqual(before.description_source, "generated")

        def fake_enrichment(drafts, *, cache_namespace):
            self.assertEqual(cache_namespace, "radio-primary-fallback-v1")
            [draft] = drafts
            self.assertEqual(draft["link"], entry.primary_url)
            return [{
                **draft,
                "venue": "Warther Festplatz",
                "description": "Vier Tage Kirmes in der Warth mit Programm. " * 4,
                "description_source": "scraped",
            }]

        with mock.patch.object(runner.detail_enrichment, "enrich_events", fake_enrichment):
            [after] = runner._enrich_promoted_fallbacks(
                outcome.events, outcome.promoted_fallback_event_ids,
            )

        self.assertEqual(after.venue, "Warther Festplatz")
        self.assertEqual(after.description_source, "scraped")
        self.assertIn("Vier Tage Kirmes", after.description)

    def test_enrichment_never_moves_the_already_public_url(self):
        _entry, outcome = self._promoted_outcome()
        published_id = event_id(outcome.events[0])

        with mock.patch.object(
            runner.detail_enrichment, "enrich_events",
            # Filling a blank venue changes the identity tuple, so the id would
            # move unless the pre-enrichment occurrence id is pinned.
            lambda drafts, *, cache_namespace: [
                {**draft, "venue": "Warther Festplatz"} for draft in drafts
            ],
        ):
            [after] = runner._enrich_promoted_fallbacks(
                outcome.events, outcome.promoted_fallback_event_ids,
            )

        self.assertEqual(event_id(after), published_id)

    def test_shared_primary_url_enriches_every_promoted_occurrence(self):
        entry = resolution.entry_for_key(("Sommerkino in Rheinbach", "2026-08-22"))
        outcome = resolution.resolve_radio_leads(
            [lead(entry.title, entry.start_date)], [], manifest=(entry,),
        )
        calls: list[str] = []

        def fake_enrichment(drafts, *, cache_namespace):
            self.assertEqual(cache_namespace, "radio-primary-fallback-v1")
            [draft] = drafts
            calls.append(draft["title"])
            return [{
                **draft,
                "description": f"Programminformation zu {draft['title']}. " * 8,
                "description_source": "scraped",
            }]

        with mock.patch.object(runner.detail_enrichment, "enrich_events", fake_enrichment):
            enriched = runner._enrich_promoted_fallbacks(
                outcome.events, outcome.promoted_fallback_event_ids,
            )

        self.assertEqual(len(calls), 6)
        self.assertEqual(calls, [event.title for event in outcome.events])
        self.assertTrue(all(event.description_source == "scraped" for event in enriched))

    def test_late_fallback_enrichment_cache_is_flushed(self):
        entry, _outcome = self._promoted_outcome()
        context = RunContext(
            config.RuntimeConfig(score_floor=0, series_ledger_json=""),
            EventWindow(datetime(2026, 8, 6), datetime(2026, 8, 27)),
            "radio-detail-cache", configure_logging("radio-detail-cache", "ERROR", "", ""),
            clock=lambda: datetime(2026, 8, 10, 12),
        )

        with mock.patch.object(runner, "_previous_snapshot", return_value={}), \
                mock.patch.object(resolution, "load_manifest", return_value=(entry,)), \
                mock.patch.object(
                    runner.detail_enrichment, "enrich_events",
                    side_effect=lambda drafts, **_kwargs: drafts,
                ), \
                mock.patch.object(runner.common, "flush_detail_page_caches", return_value=[]) as flush:
            runner.run_import(context, {
                "Radio Bonn/Rhein-Sieg": lambda: [lead(entry.title, entry.start_date)],
            })

        self.assertEqual(
            flush.call_args_list,
            [mock.call(), mock.call("radio-primary-fallback-v1")],
        )

    def test_unusable_detail_page_leaves_the_audited_fallback_untouched(self):
        _entry, outcome = self._promoted_outcome()
        [before] = outcome.events

        with mock.patch.object(
            runner.detail_enrichment, "enrich_events",
            lambda drafts, *, cache_namespace: [
                {**draft, "start_date": "", "date": ""} for draft in drafts
            ],
        ):
            [after] = runner._enrich_promoted_fallbacks(
                outcome.events, outcome.promoted_fallback_event_ids,
            )

        self.assertEqual(after, before)

    def test_events_that_were_not_promoted_are_never_fetched(self):
        entry = resolution.entry_for_key(("Warther Kirmes", "2026-08-07"))
        official = primary(
            "Warther Kirmes", entry.start_date,
            entry.primary_source, entry.primary_source_id, entry.primary_url,
            venue="Warther Festplatz",
        )
        outcome = resolution.resolve_radio_leads(
            [lead(entry.title, entry.start_date)], [official], manifest=(entry,),
        )
        self.assertEqual(outcome.promoted_fallback_event_ids, frozenset())

        def fail(*_args, **_kwargs):
            raise AssertionError("no detail fetch without a promoted fallback")

        with mock.patch.object(runner.detail_enrichment, "enrich_events", fail):
            resolved = runner._enrich_promoted_fallbacks(
                outcome.events, outcome.promoted_fallback_event_ids,
            )

        self.assertEqual(list(resolved), list(outcome.events))


if __name__ == "__main__":
    unittest.main()
