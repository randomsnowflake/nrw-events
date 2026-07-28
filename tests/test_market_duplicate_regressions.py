import unittest

from nrw_events import report
from nrw_events.validation import canonicalize_event


def market(
    title,
    source,
    date,
    venue,
    city,
    link,
    *,
    end_date=None,
    description="",
    score=1.0,
    venue_id="",
):
    return canonicalize_event({
        "title": title,
        "source": source,
        "start_date": date,
        "end_date": end_date or date,
        "date": date,
        "venue": venue,
        "venue_id": venue_id,
        "city": city,
        "link": link,
        "description": description,
        "category": "markt flohmarkt trödelmarkt",
        "category_key": "market",
        "category_label": "Märkte & Flohmärkte",
        "score": score,
    })


class MarketDuplicateRegressionTests(unittest.TestCase):
    def test_explicit_venue_identity_survives_canonical_validation(self):
        event = market(
            "Testmarkt",
            "Veranstalter",
            "2026-08-02",
            "Testplatz",
            "Bonn",
            "https://example.test/markt",
            venue_id="testplatz-bonn",
        )

        self.assertEqual(event.get("venue_id"), "testplatz-bonn")

    def test_bad_godesberg_three_source_cluster_collapses_without_injected_identity(self):
        events = [
            market(
                "Antik- und Trödelmarkt Bad Godesberg",
                "Bonn district festivals",
                "2026-08-02",
                "Theaterplatz, Am Fronhof, Michaelshof",
                "Bad Godesberg",
                "https://www.bonn.de/presse/veranstaltungsjahr",
                score=1.33,
            ),
            market(
                "Antik- und Trödelmarkt",
                "Bad Godesberg Stadtmarketing",
                "2026-08-02",
                "Bad Godesberger Innenstadt",
                "Bonn-Bad Godesberg",
                "https://bad-godesberg.info/antikmarkt",
                score=1.1,
            ),
            market(
                "Antik- und Trödelmarkt Bad Godesberg Fußgängerzone Bonn",
                "marktcom",
                "2026-08-02",
                "Antik- und Trödelmarkt Bad Godesberg Fußgängerzone",
                "Bonn-Bad Godesberg",
                "https://www.marktcom.de/veranstaltung/antikmarkt-bad-godesberg",
                score=0.89,
            ),
        ]

        deduped = report.deduplicate(events)

        self.assertEqual(len(deduped), 1)
        self.assertEqual(deduped[0]["source"], "Bad Godesberg Stadtmarketing")
        self.assertEqual(deduped[0]["link"], "https://bad-godesberg.info/antikmarkt")

    def test_future_bad_godesberg_duplicate_from_new_source_collapses(self):
        events = [
            market(
                "Antik- und Trödelmarkt",
                "Bad Godesberg Stadtmarketing",
                "2027-05-02",
                "Bad Godesberger Innenstadt",
                "Bonn-Bad Godesberg",
                "https://bad-godesberg.info/antikmarkt-2027",
            ),
            market(
                "Antik-Trödelmarkt in der Fußgängerzone Bad Godesberg",
                "New Regional Market Service",
                "2027-05-02",
                "Theaterplatz und Michaelshof",
                "Bonn",
                "https://new-market-service.example/bad-godesberg-2027",
            ),
        ]

        deduped = report.deduplicate(events)

        self.assertEqual(len(deduped), 1)
        self.assertEqual(deduped[0]["source"], "Bad Godesberg Stadtmarketing")

    def test_recurring_market_on_a_different_date_survives(self):
        events = [
            market(
                "Antik- und Trödelmarkt",
                "Bad Godesberg Stadtmarketing",
                "2027-05-02",
                "Bad Godesberger Innenstadt",
                "Bonn-Bad Godesberg",
                "https://bad-godesberg.info/antikmarkt-mai-2027",
            ),
            market(
                "Antik- und Trödelmarkt Bad Godesberg",
                "New Regional Market Service",
                "2027-06-06",
                "Theaterplatz und Michaelshof",
                "Bonn",
                "https://new-market-service.example/bad-godesberg-juni-2027",
            ),
        ]

        self.assertEqual(len(report.deduplicate(events)), 2)

    def test_other_verified_live_market_clusters_collapse(self):
        cases = {
            "friedensplatz": [
                market(
                    "Antikmarkt Bonn",
                    "Cölln Konzept",
                    "2026-08-16",
                    "Friedensplatz, 53111 Bonn",
                    "Bonn",
                    "https://www.coelln-konzept.de/markt/antikmarkt_bonn.html",
                ),
                market(
                    "Antik-, Kunst- & Designmarkt Bonn",
                    "Rhein Antik",
                    "2026-08-16",
                    "Friedensplatz",
                    "Bonn",
                    "https://rhein-antik.de/termine/",
                ),
            ],
            "rigalsche-wiese": [
                market(
                    "Familien Flohmarkt auf der Rigal´schen Wiese",
                    "Bad Godesberg Stadtmarketing",
                    "2026-08-23",
                    "Bad Godesberger Innenstadt",
                    "Bonn-Bad Godesberg",
                    "https://bad-godesberg.info/familien-flohmarkt",
                    description="Auf der Rigal´schen Wiese darf nach Herzenslust getrödelt werden.",
                ),
                market(
                    "Familien-Ferien-Flohmarkt Bonn",
                    "marktcom",
                    "2026-08-23",
                    "Familien-Ferien-Flohmarkt",
                    "Bonn",
                    "https://www.marktcom.de/veranstaltung/familien-ferien-flohmarkt",
                    description="Auf der Rigal´schen Wiese darf nach Herzenslust getrödelt werden.",
                ),
            ],
            "hit-markt": [
                market(
                    "Trödelmarkt beim HIT-Markt",
                    "Troisdorf",
                    "2026-08-23",
                    "HIT-Markt Rotter See",
                    "Troisdorf",
                    "https://www.troisdorf.de/hit-markt",
                ),
                market(
                    "Troisdorf, Trödelmarkt beim HIT-Markt",
                    "Grote & Hiller",
                    "2026-08-23",
                    "53844 Troisdorf, Spicher Straße 101",
                    "Troisdorf",
                    "https://www.grote-hiller.de/hit-markt",
                ),
            ],
            "linz-date-range": [
                market(
                    "Antikmarkt - Linz am Rhein",
                    "Cölln Konzept",
                    "2026-08-08",
                    "53545 Linz am Rhein",
                    "Linz am Rhein",
                    "https://www.coelln-konzept.de/markt/antik_linz.html",
                    end_date="2026-08-09",
                ),
                market(
                    "Antik- und Trödelmarkt Linz am Rhein",
                    "Linz am Rhein",
                    "2026-08-08",
                    "Innenstadt Linz am Rhein",
                    "Linz am Rhein",
                    "https://www.linz.de/antikmarkt",
                ),
            ],
        }

        for name, events in cases.items():
            with self.subTest(name=name):
                self.assertEqual(len(report.deduplicate(events)), 1)

    def test_distinct_same_day_market_families_at_one_venue_survive(self):
        events = [
            market(
                "Antikmarkt am Stadtplatz",
                "Veranstalter A",
                "2026-08-16",
                "Stadtplatz",
                "Bonn",
                "https://example.test/antikmarkt",
                venue_id="stadtplatz-bonn",
            ),
            market(
                "Wochenmarkt am Stadtplatz",
                "Veranstalter B",
                "2026-08-16",
                "Stadtplatz",
                "Bonn",
                "https://example.test/wochenmarkt",
                venue_id="stadtplatz-bonn",
            ),
        ]

        self.assertEqual(len(report.deduplicate(events)), 2)

    def test_same_day_markets_in_different_cologne_districts_survive(self):
        events = [
            market(
                "Flohmarkt Köln-Nippes",
                "Cölln Konzept",
                "2026-08-16",
                "Wilhelmplatz Köln-Nippes",
                "Köln",
                "https://www.coelln-konzept.de/markt/nippes.html",
            ),
            market(
                "Trödelmarkt Köln",
                "marktcom",
                "2026-08-16",
                "Köln-Porz",
                "Köln",
                "https://www.marktcom.de/veranstaltung/troedelmarkt-koeln-porz",
            ),
        ]

        self.assertEqual(len(report.deduplicate(events)), 2)


if __name__ == "__main__":
    unittest.main()
