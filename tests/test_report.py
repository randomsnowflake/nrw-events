import unittest
from unittest import mock
from unittest.mock import patch

from nrw_events import common, report
from nrw_events.identity import event_id


class ReportTests(unittest.TestCase):
    def test_malformed_one_character_venue_does_not_split_a_cross_source_occurrence(self):
        base = {
            "title": "Digital Independence Day", "date": "2026-08-02",
            "start_date": "2026-08-02", "end_date": "2026-08-02",
            "start_at": "2026-08-02T14:00+02:00", "end_at": "2026-08-02T14:00+02:00",
            "city": "Bonn", "category_key": "talk", "description": "", "price": "",
            "time": "14:00", "score": 1.0,
        }
        deduped = report.deduplicate([
            {
                **base, "venue": "Kulturzentrum Brotfabrik Bonn", "source": "Bonn.jetzt",
                "venue_id": "kulturzentrum-brotfabrik",
                "venue_address": "Kreuzstraße 16, 53225 Bonn",
                "venue_district": "Bonn-Beuel",
                "venue_type": "cultural_center",
                "venue_latitude": 50.74091,
                "venue_longitude": 7.12369,
                "distance_km": 2.1,
                "location_confidence": "exact",
                "location_source": "venue_registry",
                "link": "https://bonn.jetzt/event/digital-independence-day-1",
            },
            {
                **base, "venue": "g", "source": "Brotfabrik Bonn", "score": 1.1,
                "venue_id": "",
                "venue_address": "",
                "venue_district": "",
                "venue_type": "",
                "venue_latitude": None,
                "venue_longitude": None,
                "distance_km": 0,
                "location_confidence": "known_city",
                "location_source": "configured_city",
                "link": "https://klimaviertel-beuel.de/termine/",
            },
        ])

        self.assertEqual(len(deduped), 1)
        self.assertEqual(deduped[0]["source"], "Brotfabrik Bonn")
        self.assertEqual(deduped[0]["venue"], "Kulturzentrum Brotfabrik Bonn")
        self.assertEqual(deduped[0]["venue_id"], "kulturzentrum-brotfabrik")
        self.assertEqual(deduped[0]["venue_address"], "Kreuzstraße 16, 53225 Bonn")
        self.assertEqual(deduped[0]["venue_district"], "Bonn-Beuel")
        self.assertEqual(deduped[0]["venue_type"], "cultural_center")
        self.assertEqual(deduped[0]["venue_latitude"], 50.74091)
        self.assertEqual(deduped[0]["venue_longitude"], 7.12369)
        self.assertEqual(deduped[0]["distance_km"], 2.1)
        self.assertEqual(deduped[0]["location_confidence"], "exact")
        self.assertEqual(deduped[0]["location_source"], "venue_registry")

    def test_citywide_street_food_festival_collapses_broad_venue_aliases(self):
        base = {
            "title": "Street Food Festival", "date": "2026-08-28",
            "start_date": "2026-08-28", "end_date": "2026-08-30", "start_at": "", "end_at": "",
            "city": "Bonn-Bad Godesberg", "category_key": "food", "description": "",
            "price": "", "time": "", "score": 1.0,
        }
        deduped = report.deduplicate([
            {
                **base, "venue": "Theaterplatz", "source": "Bonn district festivals",
                "link": "https://www.bonn.de/pressemitteilungen/dezember/abwechslungsreiches-veranstaltungsjahr-2026-in-bonn.php",
            },
            {
                **base, "venue": "Bad Godesberger Innenstadt", "source": "Bad Godesberg Stadtmarketing",
                "link": "https://bad-godesberg.info/veranstaltungen_st/street-food-festival",
            },
            {
                **base, "date": "2026-08-29", "start_date": "2026-08-29", "end_date": "2026-08-29",
                "start_at": "2026-08-29T12:00+02:00", "end_at": "2026-08-29T20:00+02:00",
                "venue": "Bad Godesberger Innenstadt", "source": "Bonn.de Events",
                "link": "https://www.bonn.de/veranstaltungskalender/veranstaltungen/hauptkalender/extern/Street-Food-Festival.php",
            },
        ])

        self.assertEqual(len(deduped), 1)
        self.assertEqual(deduped[0]["start_date"], "2026-08-28")
        self.assertEqual(deduped[0]["source"], "Bad Godesberg Stadtmarketing")

    def test_same_detail_url_collapses_cross_source_location_disagreement(self):
        base = {
            "title": "Qi Gong Ost-West im Park in den Sommerferien",
            "date": "2026-08-19", "start_date": "2026-08-19", "end_date": "2026-08-19",
            "time": "18:30", "start_at": "2026-08-19T18:30+02:00",
            "end_at": "2026-08-19T19:30+02:00", "city": "Bonn-Bad Godesberg",
            "category_key": "sports", "description": "", "price": "", "score": 1.0,
            "link": "https://www.bonn.de/veranstaltungskalender/veranstaltungen/hauptkalender/extern/Qi-Gong-Ost-West-im-Park-in-den-Sommerferien.php",
        }

        deduped = report.deduplicate([
            {**base, "venue": "Park", "source": "Bonn.de Events"},
            {**base, "venue": "Haus Carstanjen", "source": "Bonn.de Sports"},
        ])

        self.assertEqual(len(deduped), 1)

    def test_reviewed_rathaustreppe_venue_aliases_collapse(self):
        base = {
            "date": "2026-08-06", "start_date": "2026-08-06", "end_date": "2026-08-06",
            "time": "18:00", "start_at": "2026-08-06T18:00+02:00",
            "end_at": "2026-08-06T20:00+02:00", "city": "Bonn-Beuel",
            "category_key": "concert", "description": "", "price": "", "score": 1.0,
        }

        deduped = report.deduplicate([
            {
                **base, "title": "Musik auf der Rathaustreppe: B-Five Bluesband (Blues)",
                "venue": "Möhneplatz Bonn-Beuel", "source": "Beuel.net",
                "link": "https://beuelhats.de/",
            },
            {
                **base, "title": "Musik auf der Rathaustreppe B-Five Bluesband",
                "venue": "Beueler Rathaus", "source": "Bonn.de Events",
                "link": "https://www.bonn.de/example/b-five.php",
            },
        ])

        self.assertEqual(len(deduped), 1)

    def test_reviewed_sieglar_venue_aliases_collapse_radio_tip_with_primary_event(self):
        base = {
            "title": "Sommer findet Stadt x Weinfest", "date": "2026-08-07",
            "start_date": "2026-08-07", "time": "17:00",
            "start_at": "2026-08-07T17:00+02:00", "city": "Troisdorf",
            "category_key": "festival", "description": "", "price": "", "score": 1.0,
        }

        deduped = report.deduplicate([
            {
                **base, "end_date": "2026-08-09", "end_at": "2026-08-09T19:00+02:00",
                "venue": "Sieglarer Marktplatz", "source": "Radio Bonn/Rhein-Sieg",
                "link": "https://www.radiobonn.de/artikel/was-geht-unsere-veranstaltungstipps-2674962",
            },
            {
                **base, "end_date": "2026-08-07", "end_at": "2026-08-07T23:00+02:00",
                "venue": "Troisdorf-Sieglar", "source": "Troisdorf", "score": 1.2,
                "link": "https://www.instagram.com/sommerfindetstadt/",
            },
        ])

        self.assertEqual(len(deduped), 1)
        self.assertEqual(deduped[0]["source"], "Troisdorf")

    def test_citywide_title_does_not_override_distinct_concrete_venues(self):
        base = {
            "title": "Street Food Festival", "date": "2026-08-28",
            "start_date": "2026-08-28", "end_date": "2026-08-30",
            "city": "Bonn", "category_key": "food", "description": "",
            "price": "", "time": "", "score": 1.0,
        }

        deduped = report.deduplicate([
            {
                **base, "venue": "Rheinaue", "source": "Veranstalter Nord",
                "link": "https://north.test/street-food",
            },
            {
                **base, "venue": "Telekom Dome", "source": "Veranstalter Süd",
                "link": "https://south.test/street-food",
            },
        ])

        self.assertEqual(len(deduped), 2)

    def test_deduplicate_blocks_unrelated_events_before_fuzzy_comparison(self):
        events = [
            {
                "title": f"Konzert {index:04d} mit eigenem Programm",
                "start_date": "2026-08-15", "end_date": "2026-08-15",
                "date": "2026-08-15", "city": "Bonn", "venue": "Rheinaue",
                "source": "Veranstalter", "score": 1.0, "description": "",
                "price": "", "time": "", "start_at": "", "end_at": "",
                "link": f"https://example.test/{index}",
            }
            for index in range(400)
        ]

        original = report.events_are_duplicates
        with patch.object(report, "events_are_duplicates", wraps=original) as duplicate_check:
            deduped = report.deduplicate(events)

        self.assertEqual(len(deduped), 400)
        self.assertLess(duplicate_check.call_count, 2_000)

    def test_candidate_index_keeps_embedded_title_duplicates_reachable(self):
        base = {
            "start_date": "2026-08-15", "end_date": "2026-08-15",
            "date": "2026-08-15", "city": "Bonn", "venue": "Pantheon",
            "score": 1.0, "description": "", "price": "", "time": "",
            "start_at": "", "end_at": "",
        }

        deduped = report.deduplicate([
            {
                **base, "title": "Beethoven Orchester Bonn",
                "source": "Orchester", "link": "https://orchester.test/event",
            },
            {
                **base, "title": "Live: Beethoven Orchester Bonn im Pantheon",
                "source": "Stadtkalender", "link": "https://city.test/event",
            },
        ])

        self.assertEqual(len(deduped), 1)

    def test_pathological_date_range_has_bounded_blocking_keys(self):
        event = {
            "title": "Langzeitausstellung", "start_date": "0001-01-01",
            "end_date": "9999-12-31", "date": "0001-01-01–9999-12-31",
            "city": "Bonn", "venue": "Museum", "category_key": "exhibition",
            "start_at": "", "source": "Museum",
        }

        keys = report._dedup_blocking_keys(event)

        self.assertLess(len(keys), 2_000)
        self.assertIn("century:20", report._occurrence_date_keys(event))

    def test_metadata_merge_preserves_winner_time_and_venue_identity(self):
        winner = {
            "title": "Sommerkonzert", "start_date": "2026-08-15", "end_date": "2026-08-15",
            "date": "2026-08-15", "city": "Bonn", "venue": "", "time": "",
            "start_at": "", "end_at": "", "source": "Veranstalter", "score": 1.0,
            "description": "", "price": "", "link": "https://direct.test/event",
        }
        duplicate = {
            **winner, "venue": "Rheinaue", "time": "20:00", "start_at": "2026-08-15T20:00:00+02:00",
            "source": "Radio Bonn/Rhein-Sieg", "score": 0.8,
            "link": "https://radio.test/event",
        }

        [merged] = report.deduplicate([winner, duplicate])

        self.assertEqual(merged["venue"], "Rheinaue")
        self.assertEqual(merged["time"], "20:00")
        self.assertEqual(event_id(merged), event_id(winner))

    def test_metadata_merge_fills_ai_summary_without_changing_winner_identity(self):
        winner = {
            "title": "Rheinaue parkrun", "start_date": "2026-08-15", "end_date": "2026-08-15",
            "date": "2026-08-15", "city": "Bonn", "venue": "Rheinaue", "time": "09:00",
            "start_at": "2026-08-15T09:00+02:00", "end_at": "2026-08-15T10:00+02:00",
            "source": "Bonn.de Sports", "score": 1.2, "description": "", "price": "",
            "link": "https://www.bonn.de/sport/rheinaue-parkrun", "ai_summary": "",
        }
        duplicate = {
            **winner,
            "source": "Bonn.de Events", "score": 0.9,
            "link": "https://www.bonn.de/events/rheinaue-parkrun",
            "ai_summary": "Am 15. August findet in der Rheinaue ein parkrun statt.",
        }

        [merged] = report.deduplicate([winner, duplicate])

        self.assertEqual(merged["source"], winner["source"])
        self.assertEqual(merged["link"], winner["link"])
        self.assertEqual(merged["title"], winner["title"])
        self.assertEqual(merged["time"], winner["time"])
        self.assertEqual(merged["ai_summary"], duplicate["ai_summary"])
        self.assertEqual(event_id(merged), event_id(winner))

        winner_with_summary = {**winner, "ai_summary": "Bestehende Zusammenfassung."}
        [preserved] = report.deduplicate([winner_with_summary, duplicate])
        self.assertEqual(preserved["ai_summary"], "Bestehende Zusammenfassung.")

    def test_civic_market_absorbs_directory_title_variant_at_same_venue(self):
        base = {
            "start_date": "2026-08-23", "end_date": "2026-08-23",
            "date": "2026-08-23", "city": "Unkel", "description": "",
            "price": "", "time": "", "start_at": "", "end_at": "",
        }
        events = [
            {
                **base,
                "title": "Floh- und Trödelmarkt am Vorteil Center",
                "venue": "Vorteil Center Unkel",
                "source": "VG Unkel",
                "score": 1.02,
                "link": "https://rhein.info/unkel/",
            },
            {
                **base,
                "title": "Flohmarkt Unkel, Vorteil Center",
                "venue": "Flohmarkt Unkel, Vorteil Center",
                "source": "marktcom",
                "score": 0.71,
                "link": "https://www.marktcom.de/veranstaltung/flohmarkt-unkel",
            },
            {
                **base,
                "title": "Hof- und Garagenflohmarkt in Unkel-Heister",
                "venue": "in den Straßen von Unkel-Heister",
                "source": "VG Unkel",
                "score": 1.02,
                "link": "https://rhein.info/unkel-heister/",
            },
        ]

        deduped = report.deduplicate(events)

        self.assertEqual(len(deduped), 2)
        self.assertEqual(deduped[0]["source"], "VG Unkel")
        self.assertEqual(
            {event["title"] for event in deduped},
            {
                "Floh- und Trödelmarkt am Vorteil Center",
                "Hof- und Garagenflohmarkt in Unkel-Heister",
            },
        )

    def test_repeated_overview_link_loses_to_more_specific_duplicate_link(self):
        base = {
            "date": "2026-08-23", "start_date": "2026-08-23", "end_date": "2026-08-23",
            "city": "Unkel", "venue": "Vorteil Center Unkel", "category_key": "market",
            "description": "", "price": "", "time": "", "start_at": "", "end_at": "",
        }
        events = [
            {
                **base, "title": "Floh- und Trödelmarkt am Vorteil Center",
                "source": "VG Unkel", "score": 1.02, "link": "https://rhein.info/unkel/",
            },
            {
                **base, "title": "Flohmarkt Unkel, Vorteil Center", "source": "marktcom",
                "score": 0.71, "link": "https://www.marktcom.de/veranstaltung/flohmarkt-unkel",
            },
        ]
        for index in range(4):
            event_date = f"2026-08-{24 + index}"
            events.append({
                **base, "title": f"Unkel Veranstaltung {index}", "venue": f"Ort {index}",
                "date": event_date, "start_date": event_date, "end_date": event_date,
                "category_key": "festival", "source": "VG Unkel", "score": 1.0,
                # HTTP/HTTPS and a trailing slash identify the same overview page for counting.
                "link": "http://rhein.info/unkel" if index == 0 else "https://rhein.info/unkel/",
            })

        deduped = report.deduplicate(events)

        market = next(event for event in deduped if event["start_date"] == "2026-08-23")
        self.assertEqual(market["source"], "VG Unkel")
        self.assertEqual(market["link"], "https://www.marktcom.de/veranstaltung/flohmarkt-unkel")
        self.assertEqual(market["link_kind"], "detail")
        overview = next(event for event in deduped if event["title"] == "Unkel Veranstaltung 0")
        self.assertEqual(overview["link_kind"], "overview")

    def test_less_frequent_link_does_not_win_without_more_specific_route(self):
        base = {
            "date": "2026-08-23", "start_date": "2026-08-23", "end_date": "2026-08-23",
            "city": "Unkel", "venue": "Rathaus", "category_key": "talk",
            "description": "", "price": "", "time": "", "start_at": "", "end_at": "",
        }
        events = [{
            **base, "title": "Lesung", "source": "VG Unkel", "score": 1.0,
            "link": "https://rhein.info/calendar/",
        }, {
            **base, "title": "Lesung", "source": "Eventbrite", "score": 0.5,
            "link": "https://events.test/schedule/",
        }]
        for index in range(4):
            event_date = f"2026-08-{24 + index}"
            events.append({
                **base, "title": f"Termin {index}", "venue": f"Ort {index}",
                "date": event_date, "start_date": event_date, "end_date": event_date,
                "source": "VG Unkel", "score": 1.0, "link": "https://rhein.info/calendar/",
            })

        deduped = report.deduplicate(events)

        lesung = next(event for event in deduped if event["title"] == "Lesung")
        self.assertEqual(lesung["source"], "VG Unkel")
        self.assertEqual(lesung["link"], "https://rhein.info/calendar/")

    def test_recurring_series_link_is_not_misclassified_as_an_overview(self):
        recurring_link = "https://theater.test/programm/der-sturm"
        events = []
        for index in range(6):
            event_date = f"2026-09-{10 + index}"
            events.append({
                "title": "Der Sturm", "venue": "Stadttheater", "date": event_date,
                "start_date": event_date, "end_date": event_date, "city": "Bonn",
                "category_key": "stage", "description": "", "price": "", "time": "20:00",
                "start_at": "", "end_at": "", "source": "Stadttheater", "score": 1.0,
                "link": recurring_link,
            })
        events.append({
            **events[0], "source": "Eventbrite", "score": 0.5,
            "link": "https://eventbrite.test/events/der-sturm/10-september",
        })

        deduped = report.deduplicate(events)

        first = next(event for event in deduped if event["start_date"] == "2026-09-10")
        self.assertEqual(first["source"], "Stadttheater")
        self.assertEqual(first["link"], recurring_link)
        self.assertEqual(first["link_kind"], "detail")

    def test_syndicated_copies_do_not_make_a_link_an_overview(self):
        publisher_link = "https://museum.test/programm/nacht-der-museen"
        base = {
            "title": "Nacht der Museen", "venue": "Stadtmuseum", "date": "2026-09-19",
            "start_date": "2026-09-19", "end_date": "2026-09-19", "city": "Bonn",
            "category_key": "exhibition", "description": "", "price": "", "time": "18:00",
            "start_at": "", "end_at": "", "score": 1.0, "link": publisher_link,
        }
        events = [
            {**base, "source": source}
            for source in ("Stadtmuseum", "Partner A", "Partner B", "Partner C", "Partner D")
        ]
        events.append({
            **base, "source": "Eventbrite", "score": 0.5,
            "link": "https://eventbrite.test/events/nacht-der-museen/bonn/2026",
        })

        deduped = report.deduplicate(events)

        self.assertEqual(len(deduped), 1)
        self.assertEqual(deduped[0]["source"], "Stadtmuseum")
        self.assertEqual(deduped[0]["link"], publisher_link)

    def test_fragment_event_routes_are_counted_as_distinct_links(self):
        events = []
        for index in range(5):
            event_date = f"2026-10-{10 + index}"
            events.append({
                "title": f"Konzert {index}", "venue": "Oper Bonn", "date": event_date,
                "start_date": event_date, "end_date": event_date, "city": "Bonn",
                "category_key": "concert", "description": "", "price": "", "time": "19:00",
                "start_at": "", "end_at": "", "source": "Oper Bonn", "score": 1.0,
                "link": f"https://tickets.bonn.de/#/event/{1000 + index}",
            })
        events.append({
            **events[0], "source": "Eventbrite", "score": 0.5,
            "link": "https://eventbrite.test/events/konzert-0/bonn/2026",
        })

        deduped = report.deduplicate(events)

        first = next(event for event in deduped if event["title"] == "Konzert 0")
        self.assertEqual(first["source"], "Oper Bonn")
        self.assertEqual(first["link"], "https://tickets.bonn.de/#/event/1000")

    def test_katharinenhof_primary_record_absorbs_radio_title_variant(self):
        base = {
            "start_date": "2026-08-09", "end_date": "2026-08-09",
            "date": "2026-08-09", "city": "Bonn", "venue": "Katharinenhof",
            "venue_id": "katharinenhof-bonn", "category_key": "market",
            "description": "", "price": "", "time": "", "start_at": "", "end_at": "",
        }
        events = [
            {
                **base, "title": "Mädelskram und Scheunentrödel", "score": 0.94,
                "source": "Radio Bonn/Rhein-Sieg",
                "link": "https://www.radiobonn.de/artikel/was-geht-unsere-veranstaltungstipps-2674962",
            },
            {
                **base, "title": "Flohmarkt im Katharinenhof", "score": 0.9,
                "source": "Katharinenhof", "price": "3 €", "time": "10:00",
                "link": "https://beikircher.de/events/flohmarkt/",
            },
        ]

        deduped = report.deduplicate(events)

        self.assertEqual(len(deduped), 1)
        self.assertEqual(deduped[0]["source"], "Katharinenhof")
        self.assertEqual(deduped[0]["price"], "3 €")
        self.assertEqual(deduped[0]["link"], "https://beikircher.de/events/flohmarkt/")

    def test_antique_market_dedup_does_not_suppress_unmatched_dates(self):
        def market(date, source, score, link):
            return {
                "title": (
                    "Antik-, Kunst- & Designmarkt Bonn"
                    if source == "Bonn district festivals"
                    else "Antikmarkt Bonn"
                ),
                "start_date": date, "end_date": date, "date": date,
                "city": "Bonn", "venue": "Friedensplatz", "score": score,
                "venue_id": "friedensplatz-bonn", "category_key": "market",
                "source": source, "description": "", "price": "", "link": link,
                "time": "11:00–17:00", "start_at": "", "end_at": "",
            }

        events = [
            market("2026-08-16", "Cölln Konzept", 0.9, "https://coelln.test/antik"),
            market("2026-08-16", "Bonn district festivals", 1.0, "https://bonn.test/press"),
            market("2026-10-11", "Bonn district festivals", 1.0, "https://bonn.test/press"),
            market("2026-10-18", "Cölln Konzept", 0.9, "https://coelln.test/antik"),
        ]

        deduped = report.deduplicate(events)

        self.assertEqual(
            [(event["start_date"], event["source"]) for event in deduped],
            [
                ("2026-08-16", "Cölln Konzept"),
                ("2026-10-11", "Bonn district festivals"),
                ("2026-10-18", "Cölln Konzept"),
            ],
        )

    def test_every_category_has_one_deterministic_report_section(self):
        from nrw_events.category_taxonomy import CATEGORIES
        self.assertEqual({item["key"] for item in CATEGORIES}, set(report.CATEGORY_SECTIONS))
        for category in CATEGORIES:
            self.assertEqual(report._bucket({"category_key": category["key"]}),
                             report.CATEGORY_SECTIONS[category["key"]])

    def test_ranking_features_are_named(self):
        features = report.ranking_features({"title": "Flohmarkt", "category": "market",
                                            "description": "", "city": "Bonn"})
        self.assertEqual(features, {"flea_market": 0.5, "bonn_local": 0.1})

    def test_format_report_uses_stored_priority_bonus(self):
        event = {
            "title": "Stored ranking", "source": "Test", "score": 1.0,
            "priority_bonus": 0.7, "ranking_features": {"test": 0.7},
            "category_key": "other", "city": "Bonn",
            "description": "", "category": "", "distance_km": 0,
        }

        with mock.patch.object(
            report, "ranking_features", side_effect=AssertionError("ranking recomputed"),
        ):
            rendered = report.format_report([event])

        self.assertIn("Stored ranking", rendered)

    def test_default_model_bonus_is_recomputed_before_snapshot(self):
        event = {
            "title": "Flohmarkt", "category": "market", "description": "",
            "city": "Bonn", "priority_bonus": 0.0, "ranking_features": None,
        }

        self.assertEqual(report._priority_bonus(event), 0.6)

    def test_source_authority_handles_source_family_variants(self):
        self.assertEqual(report.source_authority("Bundeskunsthalle"), 3)
        self.assertEqual(report.source_authority("Bonn.de Events"), 2)
        self.assertEqual(report.source_authority("Eventbrite NRW"), 1)
        self.assertEqual(report.source_authority("Radio Bonn/Rhein-Sieg"), 1)
        self.assertEqual(report.source_authority("EXA SEARCH fallback"), 0)

    def test_deduplicate_treats_free_entry_prefix_as_same_title(self):
        events = [
            {
                "title": "Sundowner Bar auf dem Dach der Bundeskunsthalle",
                "date": "2026-07-08",
                "time": "18:00",
                "venue": "Bundeskunsthalle",
                "city": "Bonn",
                "description": "",
                "price": "",
                "link": "https://www.bundeskunsthalle.de/sundowner",
                "distance_km": 0,
                "score": 1.0,
                "source": "Bundeskunsthalle",
                "category": "nightlife",
                "category_key": "nightlife",
                "category_label": "Nachtleben & Party",
                "category_confidence": 0.5,
                "category_reason": "nightlife:title=bar",
            },
            {
                "title": "kostenloser Eintritt: Sundowner Bar auf dem Dach der Bundeskunsthalle",
                "date": "2026-07-08",
                "time": "",
                "venue": "",
                "city": "Bonn",
                "description": "",
                "price": "kostenlos",
                "link": "https://www.bonn.de/sundowner.php",
                "distance_km": 0,
                "score": 0.86,
                "source": "Bonn.de Events",
                "category": "Ausstellung | Fest/Festival",
                "category_key": "festival",
                "category_label": "Feste & Stadtleben",
                "category_confidence": 0.83,
                "category_reason": "exhibition:source_category=ausstellung",
            },
        ]

        deduped = report.deduplicate(events)

        self.assertEqual(len(deduped), 1)
        self.assertEqual(deduped[0]["title"], "Sundowner Bar auf dem Dach der Bundeskunsthalle")
        self.assertEqual(deduped[0]["price"], "kostenlos")
        self.assertEqual(deduped[0]["category_key"], "nightlife")

    def test_direct_source_owns_same_occurrence_without_dropping_later_date(self):
        events = [
            {
                "title": "Sundowner Bar auf dem Dach der Bundeskunsthalle",
                "start_date": "2026-07-15", "end_date": "2026-07-15",
                "date": "2026-07-15", "city": "Bonn-Gronau",
                "venue": "Bundeskunsthalle", "score": 1.2,
                "source": "Bonn.de Events", "description": "Jeden Mittwoch auf dem Dach.",
                "price": "kostenlos", "link": "https://www.bonn.de/sundowner",
                "time": "", "start_at": "", "end_at": "",
            },
            {
                "title": "Sundowner Bar auf dem Dach der Bundeskunsthalle",
                "start_date": "2026-07-22", "end_date": "2026-07-22",
                "date": "2026-07-22", "city": "Bonn-Gronau",
                "venue": "Bundeskunsthalle", "score": 1.2,
                "source": "Bonn.de Events", "description": "Jeden Mittwoch auf dem Dach.",
                "price": "kostenlos", "link": "https://www.bonn.de/sundowner",
                "time": "", "start_at": "", "end_at": "",
            },
            {
                "title": "Sundowner Bar", "start_date": "2026-07-15",
                "end_date": "2026-07-15", "date": "2026-07-15", "city": "Bonn",
                "venue": "Bundeskunsthalle", "score": 1.0,
                "source": "Bundeskunsthalle", "description": "Elektronische Musik und Drinks.",
                "price": "", "link": "https://www.bundeskunsthalle.de/veranstaltungen/detail/10136",
                "time": "18:00–22:00", "start_at": "", "end_at": "",
            },
        ]

        deduped = report.deduplicate(events)

        self.assertEqual(len(deduped), 2)
        self.assertEqual(deduped[0]["source"], "Bundeskunsthalle")
        self.assertEqual(deduped[0]["link"], "https://www.bundeskunsthalle.de/veranstaltungen/detail/10136")
        self.assertEqual(deduped[0]["price"], "kostenlos")
        self.assertEqual(deduped[1]["start_date"], "2026-07-22")

    def test_deduplicate_preserves_free_price_and_category_from_lower_scored_duplicate(self):
        events = [
            {
                "title": "SSF Bonn Play Stations Spiel und Spaß im Sportpark Nord",
                "date": "2026-07-11",
                "time": "12:00",
                "venue": "",
                "city": "Bonn",
                "description": "",
                "price": "",
                "link": "https://www.bonn.de/sports.php",
                "distance_km": 0,
                "score": 0.64,
                "source": "Bonn.de Sports",
                "category": "Sport",
                "category_key": "outdoor",
                "category_label": "Führungen & Outdoor",
                "category_confidence": 0.5,
                "category_reason": "outdoor:title=park",
            },
            {
                "title": "SSF Bonn Play Stations Spiel und Spaß im Sportpark Nord",
                "date": "2026-07-11",
                "time": "12:00",
                "venue": "Sportpark Nord",
                "city": "Bonn",
                "description": "",
                "price": "kostenlos",
                "link": "https://www.bonn.de/json.php",
                "distance_km": 0,
                "score": 0.2,
                "source": "Bonn.de Events",
                "category": "Sport",
                "category_key": "sports",
                "category_label": "Sport & Bewegung",
                "category_confidence": 0.86,
                "category_reason": "bonn-free-tag:Sport",
            },
        ]

        deduped = report.deduplicate(events)

        self.assertEqual(len(deduped), 1)
        self.assertEqual(deduped[0]["source"], "Bonn.de Sports")
        self.assertEqual(deduped[0]["price"], "kostenlos")
        self.assertEqual(deduped[0]["venue"], "Sportpark Nord")
        self.assertEqual(deduped[0]["category_key"], "sports")
        self.assertEqual(deduped[0]["category_label"], "Sport & Bewegung")

    def test_deduplicate_collapses_near_identical_titles_from_different_sources(self):
        events = [
            {
                "title": "Dominik Eulberg & Jonathan Kaspar - strandliebe Open Air Bikini Beach Bonn",
                "date": "2026-07-10",
                "start_date": "2026-07-10",
                "time": "",
                "venue": "Bikini Beach Bonn",
                "city": "Bonn",
                "description": "",
                "price": "",
                "link": "https://eventbrite.example/event",
                "distance_km": 0,
                "score": 1.0,
                "source": "Eventbrite Party",
                "category": "Party",
            },
            {
                "title": "DOMINIK EULBERG & JONATHAN KASPAR - strandliebe Open Air I Bikini Beach Bonn",
                "date": "2026-07-10",
                "start_date": "2026-07-10",
                "time": "17:00",
                "venue": "Bikini Beach",
                "city": "Bonn",
                "description": "",
                "price": "",
                "link": "https://meetup.example/event",
                "distance_km": 0,
                "score": 0.8,
                "source": "Meetup Bonn",
                "category": "Party",
            },
        ]

        deduped = report.deduplicate(events)

        self.assertEqual(len(deduped), 1)
        self.assertEqual(deduped[0]["source"], "Eventbrite Party")
        self.assertEqual(deduped[0]["time"], "17:00")

    def test_deduplicate_collapses_short_aggregator_title_into_authoritative_listing(self):
        events = [
            {
                "title": "Critical Mass Bonn", "start_date": "2026-07-31",
                "end_date": "2026-07-31", "date": "2026-07-31",
                "city": "Bonn", "venue": "Bonn, Hofgarten", "score": 1.3,
                "source": "Bonn.jetzt", "description": "Demonstration Sport Stadt Bonn",
                "price": "", "link": "https://bonn.jetzt/event/critical-mass-bonn-24",
                "time": "18:00–20:00", "start_at": "2026-07-31T18:00+02:00",
                "end_at": "2026-07-31T20:00+02:00",
            },
            {
                "title": "Critical Mass - Radeln in großer Runde durch Bonn",
                "start_date": "2026-07-31", "end_date": "2026-07-31",
                "date": "2026-07-31", "city": "Bonn",
                "venue": "Hofgartenwiese vor dem akademischen Kunstmuseum",
                "score": 1.12, "source": "Bonn.de Events",
                "description": "Ausführliche Informationen zur gemeinsamen Fahrradtour.",
                "price": "", "link": "https://www.bonn.de/critical-mass.php",
                "time": "18:00", "start_at": "2026-07-31T18:00+02:00",
                "end_at": "2026-07-31T18:00+02:00",
            },
        ]

        deduped = report.deduplicate(events)

        self.assertEqual(len(deduped), 1)
        self.assertEqual(deduped[0]["source"], "Bonn.de Events")
        self.assertEqual(deduped[0]["link"], "https://www.bonn.de/critical-mass.php")
        self.assertIn("Ausführliche Informationen", deduped[0]["description"])
        self.assertEqual(deduped[0]["time"], "18:00–20:00")
        self.assertEqual(deduped[0]["end_at"], "2026-07-31T20:00+02:00")

    def test_directory_title_with_venue_suffix_merges_into_authoritative_fair(self):
        authoritative = {
            "title": "Kaldauer Rochus Kirmes", "start_date": "2026-08-16",
            "end_date": "2026-08-16", "date": "2026-08-16",
            "city": "Siegburg", "venue": "Kaldauer Zentrum", "score": 1.32,
            "source": "Siegburg", "source_id": "siegburg",
            "description": "Sonntag: 11 Uhr Kinder-Trödelmarkt und Familienfest.",
            "price": "", "link": "https://events.siegburg.de/rochus-kirmes",
            "time": "", "start_at": "", "end_at": "",
            "category_key": "festival", "run_id": "rochus-kirmes-2026",
        }
        directory_clone = {
            **authoritative,
            "title": "Kaldauen Rochus Kirmes, Kaldauer Zentrum",
            "score": 0.9, "source": "Kinderflohmarkt.com",
            "source_id": "kinderflohmarkt-com",
            "description": "Kinderflohmarkt mit Ständen an der Kirmes.",
            "link": "https://kinderflohmarkt.example/kaldauer-zentrum/#t21365",
            "time": "11:00–14:00", "start_at": "2026-08-16T11:00+02:00",
            "end_at": "2026-08-16T14:00+02:00", "run_id": "",
        }

        [merged] = report.deduplicate([directory_clone, authoritative])

        self.assertEqual(merged["source"], "Siegburg")
        self.assertEqual(merged["time"], "")
        self.assertEqual(merged["start_at"], "")
        self.assertEqual(merged["end_at"], "")
        self.assertIn(event_id(directory_clone), merged["previous_event_ids"])

    def test_directory_venue_suffix_does_not_absorb_a_named_fair_subevent(self):
        base = {
            "start_date": "2026-08-16", "end_date": "2026-08-16",
            "date": "2026-08-16", "city": "Siegburg",
            "venue": "Kaldauer Zentrum", "description": "", "price": "",
            "time": "11:00", "start_at": "2026-08-16T11:00+02:00",
            "end_at": "", "category_key": "festival",
        }
        events = [
            {
                **base, "title": "Kaldauer Rochus Kirmes", "score": 1.32,
                "source": "Siegburg", "link": "https://siegburg.example/kirmes",
            },
            {
                **base,
                "title": "Kinderflohmarkt an der Rochus-Kirmes, Kaldauer Zentrum",
                "score": 0.9, "source": "Kinderflohmarkt.com",
                "link": "https://kinderflohmarkt.example/termin",
            },
        ]

        self.assertEqual(len(report.deduplicate(events)), 2)

    def test_relaxed_aggregator_title_match_requires_same_start_time(self):
        shared = {
            "start_date": "2026-07-31", "end_date": "2026-07-31",
            "date": "2026-07-31", "city": "Bonn", "venue": "Hofgarten",
            "description": "", "price": "", "end_at": "",
        }
        events = [
            {
                **shared, "title": "Critical Mass Bonn", "score": 1.3,
                "source": "Bonn.jetzt", "link": "https://bonn.jetzt/early",
                "time": "16:00", "start_at": "2026-07-31T16:00+02:00",
            },
            {
                **shared,
                "title": "Critical Mass - Radeln in großer Runde durch Bonn",
                "score": 1.12, "source": "Bonn.de Events",
                "link": "https://www.bonn.de/evening", "time": "18:00",
                "start_at": "2026-07-31T18:00+02:00",
            },
        ]

        self.assertEqual(len(report.deduplicate(events)), 2)

    def test_deduplicate_prefers_primary_source_and_keeps_richer_description(self):
        events = [
            {
                "title": "Sommerkonzert am Rhein", "start_date": "2026-07-18",
                "date": "2026-07-18", "city": "Bonn", "venue": "Rheinaue",
                "score": 1.4, "source": "Eventbrite Party",
                "description": "Ausführliche Informationen zum Programm und zum Einlass.",
                "price": "12 Euro", "link": "https://eventbrite.example/sommerkonzert",
                "time": "19:00", "start_at": "", "end_at": "",
            },
            {
                "title": "Sommerkonzert am Rhein", "start_date": "2026-07-18",
                "date": "2026-07-18", "city": "Bonn", "venue": "Rheinaue",
                "score": 0.7, "source": "Bonn.de Events", "description": "Konzert.",
                "price": "", "link": "https://www.bonn.de/sommerkonzert",
                "time": "19:00", "start_at": "", "end_at": "",
            },
        ]

        deduped = report.deduplicate(events)

        self.assertEqual(deduped[0]["source"], "Bonn.de Events")
        self.assertEqual(deduped[0]["link"], "https://www.bonn.de/sommerkonzert")
        self.assertEqual(deduped[0]["price"], "12 Euro")
        self.assertIn("Ausführliche Informationen", deduped[0]["description"])

    def test_deduplicate_prefers_primary_source_over_radio_aggregation(self):
        events = [
            {
                "title": "Pride Bonn", "start_date": "2026-07-18",
                "date": "2026-07-18", "city": "Bonn", "venue": "Hofgarten",
                "score": 1.4, "source": "Radio Bonn/Rhein-Sieg",
                "description": "Ausführliche Informationen zur Demonstration.",
                "price": "", "link": "https://www.radiobonn.de/artikel/was-geht-unsere-veranstaltungstipps-2674962",
                "time": "11:00", "start_at": "", "end_at": "",
            },
            {
                "title": "Pride Bonn", "start_date": "2026-07-18",
                "date": "2026-07-18", "city": "Bonn", "venue": "Hofgarten",
                "score": 0.7, "source": "Pride Bonn", "description": "Demo.",
                "price": "", "link": "https://pridebonn.org/",
                "time": "11:00", "start_at": "", "end_at": "",
            },
        ]

        deduped = report.deduplicate(events)

        self.assertEqual(deduped[0]["source"], "Pride Bonn")
        self.assertEqual(deduped[0]["link"], "https://pridebonn.org/")
        self.assertIn("Ausführliche Informationen", deduped[0]["description"])

    def test_choco_dealer_title_absorbs_generic_radio_listing(self):
        base = {
            "start_date": "2026-07-31", "end_date": "2026-07-31",
            "date": "2026-07-31", "city": "Bonn-Bad Godesberg",
            "description": "", "price": "", "time": "19:00",
            "start_at": "2026-07-31T19:00:00+02:00",
            "end_at": "2026-07-31T20:30:00+02:00",
        }
        events = [
            {
                **base,
                "title": "Schokoladentasting",
                "venue": "Bonn",
                "score": 0.8,
                "source": "Radio Bonn/Rhein-Sieg",
                "link": "https://www.radiobonn.de/artikel/was-geht-unsere-veranstaltungstipps-2674962",
            },
            {
                **base,
                "title": (
                    "Schokoladen Tasting: "
                    "DIE WELT DER SCHOKOLADE ENTDECKEN - EINSTEIGER"
                ),
                "venue": "CHOCO DEALER SHOP, Elsässer Str. 8, 53175 Bonn",
                "score": 0.97,
                "source": "Choco Dealer",
                "link": "https://choco-dealer.com/SCHOKOLADEN-TASTING-FUER-EINSTEIGER/EVENT-BBA",
            },
        ]

        deduped = report.deduplicate(events)

        self.assertEqual(len(deduped), 1)
        self.assertEqual(deduped[0]["source"], "Choco Dealer")
        self.assertTrue(deduped[0]["link"].startswith("https://choco-dealer.com/"))

    def test_deduplicate_replaces_only_radio_fallback_link_from_search_record(self):
        events = [
            {
                "title": "Pride Bonn", "start_date": "2026-07-18",
                "date": "2026-07-18", "city": "Bonn", "venue": "Hofgarten",
                "score": 1.4, "source": "Radio Bonn/Rhein-Sieg", "description": "Details.",
                "price": "", "link": "https://www.radiobonn.de/artikel/was-geht-unsere-veranstaltungstipps-2674962",
                "time": "11:00", "start_at": "", "end_at": "",
            },
            {
                "title": "Pride Bonn", "start_date": "2026-07-18",
                "date": "2026-07-18", "city": "Bonn", "venue": "Hofgarten",
                "score": 0.7, "source": "Exa Search", "description": "",
                "price": "", "link": "https://pridebonn.org/",
                "time": "", "start_at": "", "end_at": "",
            },
        ]

        deduped = report.deduplicate(events)

        self.assertEqual(deduped[0]["source"], "Radio Bonn/Rhein-Sieg")
        self.assertEqual(deduped[0]["link"], "https://pridebonn.org/")

    def test_deduplicate_normalizes_city_district_aliases(self):
        events = [
            {
                "title": "Eitorf Live: Steeldriver", "start_date": "2026-07-17",
                "date": "2026-07-17", "city": "Eitorf", "venue": "Marktplatz",
                "score": 1.0, "source": "Eitorf", "description": "", "price": "",
                "link": "https://example.test/eitorf", "time": "", "start_at": "", "end_at": "",
            },
            {
                "title": "Eitorf live mit STEELDRIVER", "start_date": "2026-07-17",
                "date": "2026-07-17", "city": "Eitorf (Zentrum)", "venue": "Eitorfer Marktplatz",
                "score": 0.8, "source": "Radio", "description": "Details", "price": "",
                "link": "https://example.test/radio", "time": "19:00", "start_at": "", "end_at": "",
            },
        ]

        deduped = report.deduplicate(events)

        self.assertEqual(len(deduped), 1)
        self.assertEqual(deduped[0]["time"], "19:00")

    def test_deduplicate_allows_missing_location_for_distinctive_title(self):
        events = [
            {
                "title": "Ferienprogramm: Schatzsuche in Heisterbach", "start_date": "2026-07-21",
                "date": "2026-07-21", "city": "Königswinter", "venue": "",
                "score": 1.0, "source": "VVS", "description": "", "price": "",
                "link": "https://example.test/vvs", "time": "", "start_at": "", "end_at": "",
            },
            {
                "title": "Kinderferienprogramm: Schatzsuche in Heisterbach", "start_date": "2026-07-21",
                "date": "2026-07-21", "city": "Bonn", "venue": "",
                "score": 0.8, "source": "Bonn", "description": "", "price": "",
                "link": "https://example.test/bonn", "time": "", "start_at": "", "end_at": "",
            },
        ]

        self.assertEqual(len(report.deduplicate(events)), 1)

    def test_deduplicate_collapses_overlapping_versions_of_a_multi_day_event(self):
        events = [
            {
                "title": "Feuerwehrfest in Winterscheid", "start_date": "2026-07-10",
                "end_date": "2026-07-12", "date": "ongoing until 2026-07-12",
                "city": "Ruppichteroth", "venue": "", "score": 0.75,
                "source": "Bröltal / Ruppichteroth", "description": "10.07. – 12.07.2026",
                "price": "", "link": "https://example.test/feuerwehrfest",
                "time": "", "start_at": "", "end_at": "",
            },
            {
                "title": "Feuerwehrfest in Winterscheid", "start_date": "2026-07-11",
                "end_date": "2026-07-12", "date": "2026-07-11–2026-07-12",
                "city": "Ruppichteroth", "venue": "", "score": 0.75,
                "source": "Bröltal / Ruppichteroth", "description": "11.07. – 12.07.2026",
                "price": "", "link": "https://example.test/feuerwehrfest",
                "time": "", "start_at": "", "end_at": "",
            },
            {
                "title": "Feuerwehrfest in Winterscheid", "start_date": "2026-07-12",
                "end_date": "2026-07-12", "date": "2026-07-12",
                "city": "Ruppichteroth", "venue": "", "score": 0.75,
                "source": "Bröltal / Ruppichteroth", "description": "12.07.2026",
                "price": "", "link": "https://example.test/feuerwehrfest",
                "time": "", "start_at": "", "end_at": "",
            },
        ]

        deduped = report.deduplicate(events)

        self.assertEqual(len(deduped), 1)
        self.assertEqual(deduped[0]["start_date"], "2026-07-10")
        self.assertEqual(deduped[0]["end_date"], "2026-07-12")

    def test_deduplicate_collapses_cross_source_festival_with_one_day_end_disagreement(self):
        events = [
            {
                "title": "Kirmes in Lengsdorf", "start_date": "2026-07-31",
                "end_date": "2026-08-03", "date": "2026-07-31–2026-08-03",
                "city": "Bonn-Hardtberg", "venue": "Dorfplatz", "score": 1.0,
                "source": "Radio Bonn/Rhein-Sieg",
                "description": "Von Freitag bis Montag auf dem Dorfplatz in Lengsdorf.",
                "price": "",
                "link": "https://www.radiobonn.de/artikel/was-geht-unsere-veranstaltungstipps-2674962",
                "time": "", "start_at": "", "end_at": "", "category_key": "festival",
            },
            {
                "title": "Kirmes Lengsdorf", "start_date": "2026-07-31",
                "end_date": "2026-08-02", "date": "2026-07-31–2026-08-02",
                "city": "Bonn-Hardtberg", "venue": "Dorfplatz/Uhlgasse", "score": 0.9,
                "source": "Bonn district festivals",
                "description": "Kirmes Lengsdorf, Dorfplatz/Uhlgasse.",
                "price": "",
                "link": "https://www.bonn.de/pressemitteilungen/dezember/abwechslungsreiches-veranstaltungsjahr-2026-in-bonn.php",
                "time": "", "start_at": "", "end_at": "", "category_key": "festival",
            },
        ]

        deduped = report.deduplicate(events)

        self.assertEqual(len(deduped), 1)
        self.assertEqual(deduped[0]["source"], "Bonn district festivals")
        self.assertEqual(deduped[0]["end_date"], "2026-08-02")

    def test_deduplicate_keeps_cross_source_events_with_materially_different_end_dates(self):
        base = {
            "title": "Sommerfestival", "start_date": "2026-08-01",
            "date": "2026-08-01", "city": "Bonn", "venue": "Dorfplatz",
            "score": 1.0, "description": "", "price": "", "time": "",
            "start_at": "", "end_at": "", "category_key": "festival",
        }
        events = [
            {
                **base, "end_date": "2026-08-02", "source": "Veranstalter",
                "link": "https://veranstalter.test/sommerfestival",
            },
            {
                **base, "end_date": "2026-08-09", "source": "Stadtkalender",
                "link": "https://stadt.test/sommerfestival",
            },
        ]

        self.assertEqual(len(report.deduplicate(events)), 2)

    def test_deduplicate_rechecks_prior_results_after_metadata_merge(self):
        base = {
            "start_date": "2026-07-28", "end_date": "2026-07-28",
            "date": "2026-07-28", "city": "Bonn", "score": 1.0,
            "description": "", "price": "", "time": "", "start_at": "",
            "end_at": "", "category_key": "sports",
        }
        events = [
            {
                **base, "title": "Sportangebot im Reuterpark",
                "venue": "Reuterpark", "source": "Stadtsportbund",
                "link": "https://sport.test/reuterpark",
            },
            {
                **base, "title": "Draußen Aktiv Reuterpark",
                "venue": "Reuterpark", "source": "Bonn.de Events",
                "link": "https://bonn.test/reuterpark",
            },
            {
                **base,
                "title": "Draußen Aktiv Reuterpark – Sportangebot im Reuterpark",
                "venue": "", "source": "Veranstalter", "score": 1.1,
                "link": "https://veranstalter.test/reuterpark",
            },
        ]

        deduped = report.deduplicate(events)

        self.assertEqual(len(deduped), 1)
        self.assertEqual(deduped[0]["source"], "Veranstalter")

    def test_deduplicate_keeps_same_link_on_distinct_dates(self):
        events = [
            {
                "title": "Offene Fahrradwerkstatt", "start_date": "2026-07-24",
                "end_date": "2026-07-24", "date": "2026-07-24", "city": "Bonn-Beuel",
                "venue": "Nachbarschaftshaus", "score": 1.0, "source": "Lokalkalender",
                "description": "Wöchentlicher Termin.", "price": "",
                "link": "https://example.test/werkstatt/?occurrence=2",
                "time": "16:00", "start_at": "", "end_at": "",
            },
            {
                "title": "Offene Fahrradwerkstatt", "start_date": "2026-07-17",
                "end_date": "2026-07-17", "date": "2026-07-17", "city": "Bonn-Beuel",
                "venue": "Nachbarschaftshaus", "score": 1.0, "source": "Lokalkalender",
                "description": "Wöchentlicher Termin.", "price": "",
                "link": "https://example.test/werkstatt/?occurrence=1",
                "time": "16:00", "start_at": "", "end_at": "",
            },
        ]

        deduped = report.deduplicate(events)

        self.assertEqual(len(deduped), 2)
        self.assertEqual(
            {event["start_date"] for event in deduped},
            {"2026-07-17", "2026-07-24"},
        )

    def test_deduplicate_keeps_same_source_title_and_venue_on_distinct_dates(self):
        events = [
            {
                "title": "Sommermusik 2026", "start_date": "2026-07-19",
                "end_date": "2026-07-19", "date": "2026-07-19", "city": "Bonn-Duisdorf",
                "venue": "Kulturzentrum", "score": 1.0, "source": "Stadtkalender",
                "description": "Erstes Konzert.", "price": "",
                "link": "https://example.test/sommermusik/erstes-konzert",
                "time": "11:00", "start_at": "", "end_at": "",
            },
            {
                "title": "Sommermusik 2026", "start_date": "2026-07-26",
                "end_date": "2026-07-26", "date": "2026-07-26", "city": "Bonn-Duisdorf",
                "venue": "Kulturzentrum", "score": 1.0, "source": "Stadtkalender",
                "description": "Zweites Konzert.", "price": "",
                "link": "https://example.test/sommermusik/zweites-konzert",
                "time": "11:00", "start_at": "", "end_at": "",
            },
        ]

        self.assertEqual(len(report.deduplicate(events)), 2)

    def test_deduplicate_keeps_same_title_at_different_venues(self):
        base = {
            "title": "Offene Sprechstunde", "start_date": "2026-07-17",
            "end_date": "2026-07-17", "date": "2026-07-17", "city": "Bonn",
            "score": 1.0, "source": "Stadtkalender", "description": "", "price": "",
            "time": "16:00", "start_at": "", "end_at": "",
        }
        events = [
            {**base, "venue": "Haus Nord", "link": "https://example.test/nord"},
            {**base, "start_date": "2026-07-24", "end_date": "2026-07-24",
             "date": "2026-07-24", "venue": "Haus Süd", "link": "https://example.test/sued"},
        ]

        self.assertEqual(len(report.deduplicate(events)), 2)

    def test_deduplicate_keeps_same_title_same_day_at_distinct_venues(self):
        base = {
            "title": "Tag des offenen Denkmals", "start_date": "2026-09-13",
            "end_date": "2026-09-13", "date": "2026-09-13", "city": "Bonn",
            "score": 1.0, "description": "", "price": "", "time": "11:00",
            "start_at": "2026-09-13T11:00+02:00", "end_at": "",
        }
        events = [
            {
                **base, "venue": "Holzlarer Mühle", "source": "BV Holzlar",
                "link": "https://bv-holzlar.test/veranstaltungen",
            },
            {
                **base, "venue": "Burg Lede", "source": "Beuel.net",
                "link": "https://burglede.test/veranstaltungen",
            },
        ]

        self.assertEqual(len(report.deduplicate(events)), 2)

    def test_deduplicate_folds_transliterated_titles_cities_and_venues(self):
        base = {
            "start_date": "2026-08-08", "end_date": "2026-08-08",
            "date": "2026-08-08", "description": "", "price": "", "time": "",
            "start_at": "", "end_at": "", "score": 1.0,
        }
        events = [
            {
                **base, "title": "Kölner Sommerbühne", "city": "Köln",
                "venue": "Bürgerzentrum Köln", "source": "Köln Kultur",
                "link": "https://koeln.test/sommerbuehne",
            },
            {
                **base, "title": "Koelner Sommerbuehne", "city": "Koeln",
                "venue": "Buergerzentrum Koeln", "source": "Regionaler Kalender",
                "link": "https://regional.test/sommerbuehne",
            },
        ]

        self.assertEqual(len(report.deduplicate(events)), 1)

    def test_deduplicate_keeps_numbered_series_parts_separate(self):
        base = {
            "start_date": "2026-08-08", "end_date": "2026-08-08",
            "date": "2026-08-08", "city": "Bonn", "venue": "Stadtmuseum",
            "description": "", "price": "", "time": "", "start_at": "", "end_at": "",
            "score": 1.0, "category_key": "talk", "venue_id": "stadtmuseum-bonn",
        }
        events = [
            {**base, "title": "Römer am Rhein – Teil 1", "source": "Museum",
             "link": "https://museum.test/1"},
            {**base, "title": "Römer am Rhein – Teil 2", "source": "Stadtkalender",
             "link": "https://stadt.test/2"},
        ]

        self.assertEqual(len(report.deduplicate(events)), 2)

    def test_deduplicate_does_not_absorb_single_date_into_cross_source_run(self):
        base = {
            "city": "Bonn", "venue": "Stadtmuseum", "description": "", "price": "",
            "time": "", "start_at": "", "end_at": "", "score": 1.0,
        }
        events = [
            {
                **base, "title": "Römer am Rhein", "start_date": "2026-08-01",
                "end_date": "2026-08-31", "date": "2026-08-01–2026-08-31",
                "source": "Museum", "link": "https://museum.test/ausstellung",
            },
            {
                **base, "title": "Römer am Rhein – Kuratorenführung",
                "start_date": "2026-08-08", "end_date": "2026-08-08", "date": "2026-08-08",
                "source": "Stadtkalender", "link": "https://stadt.test/fuehrung",
            },
        ]

        self.assertEqual(len(report.deduplicate(events)), 2)

    def test_deduplicate_matches_cross_source_by_venue_date_and_category(self):
        base = {
            "start_date": "2026-08-08", "end_date": "2026-08-08",
            "date": "2026-08-08", "city": "Bonn", "venue": "Haus der Geschichte",
            "venue_id": "haus-der-geschichte-bonn", "category_key": "exhibition",
            "description": "", "price": "", "time": "", "start_at": "", "end_at": "",
            "score": 1.0,
        }
        events = [
            {**base, "title": "Zeitzeugengespräch zur Nachkriegszeit", "source": "Museum",
             "link": "https://museum.test/zeitzeugen"},
            {**base, "title": "Gespräch mit Zeitzeugen: Deutschlands Nachkriegszeit",
             "source": "Stadtkalender", "link": "https://stadt.test/zeitzeugen"},
        ]

        self.assertEqual(len(report.deduplicate(events)), 1)


if __name__ == "__main__":
    unittest.main()


class UnmatchedCancellationWindowTests(unittest.TestCase):
    """A tombstone with no scheduled counterpart is only published in-window.

    ``cancelled_events`` is filled inside ``make_event``, before the report
    window is applied. A source that leaves a months-old "verschoben" entry in
    its calendar would otherwise have that past occurrence appended as a
    standalone event — listed everywhere, but with no detail page, because the
    website builds pages for current events only.
    """

    @staticmethod
    def _cancellation(day):
        return {
            "title": "Lesung mit Autor Marco Hasenkopf", "start_date": day,
            "end_date": day, "date": day, "city": "Troisdorf",
            "venue": "Stadtbibliothek City-Center Troisdorf", "score": 0.0,
            "source": "Troisdorf", "status": "postponed", "price": "",
            "description": "Verschoben wegen der Hitze auf den 4. Juli!",
            "link": "https://www.troisdorf.de/de/kalender/startseite/",
            "time": "19:00–21:00", "start_at": f"{day}T19:00+02:00",
            "end_at": f"{day}T21:00+02:00",
        }

    def test_past_unmatched_cancellation_is_not_published(self):
        past = self._cancellation("2026-06-04")

        self.assertEqual(report.deduplicate([], cancellations=[past]), [])

    def test_in_window_unmatched_cancellation_is_still_published(self):
        current = self._cancellation(common.TODAY.strftime("%Y-%m-%d"))

        [published] = report.deduplicate([], cancellations=[current])

        self.assertEqual(published["status"], "postponed")
        self.assertEqual(published["cancellation_source"], "Troisdorf")


class AdoptedDescriptionTests(unittest.TestCase):
    """A duplicate's copy is adopted as text *and* markup, or not at all.

    ``description_html`` renders one particular description. Taking the longer
    text from a duplicate while keeping the winner's markup published a
    generated "findet am … statt" line in place of the real write-up.
    """

    @staticmethod
    def _event(title, description, html, source, score):
        return {
            "title": title, "start_date": "2026-08-06", "end_date": "2026-08-06",
            "date": "2026-08-06", "city": "Bonn", "venue": "Rathaustreppe",
            "score": score, "source": source, "description": description,
            "description_html": html, "description_source": "scraped", "price": "",
            "link": "https://example.test/a", "time": "19:00",
            "start_at": "2026-08-06T19:00+02:00", "end_at": "2026-08-06T21:00+02:00",
        }

    def test_longer_duplicate_copy_brings_its_own_markup(self):
        winner = self._event(
            "Musik auf der Rathaustreppe", "Kurz.",
            "<p>„Musik auf der Rathaustreppe“ findet am 06.08.2026 statt.</p>",
            "Bonn.de Events", 1.0)
        duplicate = self._event(
            "Musik auf der Rathaustreppe",
            "Die B-Five Bluesband spielt auf der Rathaustreppe. Eintritt frei.",
            "<p>Die B-Five Bluesband spielt auf der Rathaustreppe.</p><p>Eintritt frei.</p>",
            "Beuel.net", 0.9)

        [merged] = report.deduplicate([winner, duplicate])

        self.assertIn("B-Five Bluesband", merged["description"])
        self.assertIn("B-Five Bluesband", merged["description_html"])
        self.assertNotIn("findet am", merged["description_html"])
