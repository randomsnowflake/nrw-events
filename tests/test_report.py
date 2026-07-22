import unittest

from nrw_events import report


class ReportTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
