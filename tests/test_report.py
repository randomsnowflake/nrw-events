import unittest

from scripts.nrw_events import report


class ReportTests(unittest.TestCase):
    def test_every_category_has_one_deterministic_report_section(self):
        from scripts.nrw_events.category_taxonomy import CATEGORIES
        self.assertEqual({item["key"] for item in CATEGORIES}, set(report.CATEGORY_SECTIONS))
        for category in CATEGORIES:
            self.assertEqual(report._bucket({"category_key": category["key"]}),
                             report.CATEGORY_SECTIONS[category["key"]])

    def test_ranking_features_are_named(self):
        features = report.ranking_features({"title": "Flohmarkt", "category": "market",
                                            "description": "", "city": "Bonn"})
        self.assertEqual(features, {"flea_market": 0.5, "bonn_local": 0.1})

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
                "category_confidence": 1,
                "category_reason": "forced:nightlife",
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
                "category_confidence": 0.8,
                "category_reason": "festival:title=bar",
            },
        ]

        deduped = report.deduplicate(events)

        self.assertEqual(len(deduped), 1)
        self.assertEqual(deduped[0]["title"], "Sundowner Bar auf dem Dach der Bundeskunsthalle")
        self.assertEqual(deduped[0]["price"], "kostenlos")

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
                "link": "https://rausgegangen.example/event",
                "distance_km": 0,
                "score": 0.8,
                "source": "Rausgegangen Party",
                "category": "Party",
            },
        ]

        deduped = report.deduplicate(events)

        self.assertEqual(len(deduped), 1)
        self.assertEqual(deduped[0]["source"], "Eventbrite Party")
        self.assertEqual(deduped[0]["time"], "17:00")


if __name__ == "__main__":
    unittest.main()
