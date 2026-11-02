import unittest
from dataclasses import replace

from nrw_events import report
from nrw_events.identity import event_id
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
    venue_address="",
    time="",
    start_at="",
    end_at="",
    source_id="",
):
    return canonicalize_event({
        "title": title,
        "source": source,
        "start_date": date,
        "end_date": end_date or date,
        "date": date,
        "venue": venue,
        "venue_id": venue_id,
        "venue_address": venue_address,
        "city": city,
        "link": link,
        "description": description,
        "category": "markt flohmarkt trödelmarkt",
        "category_key": "market",
        "category_label": "Märkte & Flohmärkte",
        "score": score,
        "time": time,
        "start_at": start_at,
        "end_at": end_at,
        "source_id": source_id,
    })


def showing(source, start, *, venue="Kulturzentrum Brotfabrik", end="", title="Filmworkshop: Sichten, Skripten, Schneiden"):
    return canonicalize_event({
        "title": title,
        "source": source,
        "start_date": start[:10],
        "end_date": start[:10],
        "date": start[:10],
        "time": start[11:16],
        "start_at": start,
        "end_at": end,
        "venue": venue,
        "city": "Bonn",
        "link": f"https://example.test/{source}/{start}",
        "category_key": "cinema",
        "category": "kino",
        "category_label": "Kino",
        "score": 0.7,
    })


class CloseStartToleranceTests(unittest.TestCase):
    def test_brotfabrik_workshop_listed_at_15_00_and_15_30_merges(self):
        organiser = showing("Brotfabrik Bonn", "2026-10-27T15:00+01:00")
        city = showing("Bonn.de Events", "2026-10-27T15:30+01:00", end="2026-10-27T18:00+01:00")

        deduped = report.deduplicate([city, organiser])

        self.assertEqual(len(deduped), 1)
        self.assertEqual(deduped[0]["source"], "Brotfabrik Bonn")
        self.assertEqual(deduped[0]["start_at"], "2026-10-27T15:00+01:00")
        self.assertIn(event_id(city), deduped[0]["merged_event_ids"])

    def test_candlelight_shows_two_hours_apart_stay_separate(self):
        deduped = report.deduplicate([
            showing("Candlelight", "2026-11-07T18:30+01:00", title="Candlelight: Queen"),
            showing("Bonn.de Events", "2026-11-07T20:30+01:00", title="Candlelight: Queen"),
        ])

        self.assertEqual(len(deduped), 2)

    def test_gap_just_over_the_tolerance_stays_separate(self):
        deduped = report.deduplicate([
            showing("Brotfabrik Bonn", "2026-10-27T15:00+01:00"),
            showing("Bonn.de Events", "2026-10-27T16:01+01:00"),
        ])

        self.assertEqual(len(deduped), 2)

    def test_hourly_tours_listed_by_two_sources_keep_every_slot(self):
        deduped = report.deduplicate([
            showing("Museum", "2026-11-07T14:00+01:00", title="Öffentliche Führung"),
            showing("Museum", "2026-11-07T15:00+01:00", title="Öffentliche Führung"),
            showing("Bonn.de Events", "2026-11-07T14:00+01:00", title="Öffentliche Führung"),
            showing("Bonn.de Events", "2026-11-07T15:00+01:00", title="Öffentliche Führung"),
        ])

        self.assertEqual(sorted(row["start_at"][11:16] for row in deduped), ["14:00", "15:00"])

    def test_exhibition_opening_hours_do_not_absorb_a_tour(self):
        hours = replace(
            showing("Bonn.de Events", "2026-10-10T11:00+02:00", title="Menschen und Geschichten"),
            category_key="exhibition",
        )
        tour = showing("Kunstmuseum Bonn", "2026-10-10T11:15+02:00", title="Menschen und Geschichten")

        self.assertEqual(len(report.deduplicate([hours, tour])), 2)

    def test_same_source_slots_and_other_venues_stay_separate(self):
        self.assertEqual(len(report.deduplicate([
            showing("Brotfabrik Bonn", "2026-10-27T15:00+01:00"),
            showing("Brotfabrik Bonn", "2026-10-27T15:30+01:00"),
        ])), 2)
        self.assertEqual(len(report.deduplicate([
            showing("Brotfabrik Bonn", "2026-10-27T15:00+01:00"),
            showing("Bonn.de Events", "2026-10-27T15:30+01:00", venue="Haus der Springmaus"),
        ])), 2)


class SingleDayInsideRunTests(unittest.TestCase):
    RUN = ("2026-10-23", "2026-10-25")

    def run_and_day(self, *, day_source, day_venue, title="Hubertusmarkt (23.-25.10.)", day_score=1.08, time=""):
        run = market(
            title, "SiteKit regional", self.RUN[0], "Brühler Innenstadt", "Brühl",
            "https://www.bruehl.de/hubertusmarkt?day=23", end_date=self.RUN[1], score=1.08,
        )
        day = market(
            title, day_source, self.RUN[1], day_venue, "Brühl",
            "https://example.test/hubertusmarkt-sonntag", score=day_score,
            time=time, start_at=f"{self.RUN[1]}T{time}+01:00" if time else "",
        )
        return run, day

    def test_hubertusmarkt_day_row_from_same_source_folds_into_run(self):
        run, day = self.run_and_day(day_source="SiteKit regional", day_venue="")

        deduped = report.deduplicate([day, run])

        self.assertEqual(len(deduped), 1)
        self.assertEqual((deduped[0]["start_date"], deduped[0]["end_date"]), self.RUN)
        self.assertIn(event_id(day), deduped[0]["merged_event_ids"])

    def test_first_party_day_row_folds_into_the_run_despite_a_higher_score(self):
        run, day = self.run_and_day(day_source="WEPAG", day_venue="Brühler Innenstadt", day_score=2.0)

        deduped = report.deduplicate([day, run])

        self.assertEqual(len(deduped), 1)
        self.assertEqual((deduped[0]["start_date"], deduped[0]["end_date"]), self.RUN)
        self.assertIn(event_id(day), deduped[0]["merged_event_ids"])

    def test_timed_first_party_day_stays_a_separate_occurrence(self):
        run, day = self.run_and_day(day_source="Skulpturenpark", day_venue="Brühler Innenstadt", time="15:00")

        self.assertEqual(len(report.deduplicate([run, day])), 2)

    def test_same_title_at_another_venue_in_town_stays_separate(self):
        run, day = self.run_and_day(day_source="Pfarrei St. Margareta", day_venue="Pfarrzentrum", title="Weihnachtsmarkt")

        self.assertEqual(len(report.deduplicate([run, day])), 2)


class MarketDuplicateRegressionTests(unittest.TestCase):
    def test_actual_merge_records_duplicate_identity_separately_from_aliases(self):
        from dataclasses import replace

        from nrw_events.dedup_merge import _merge_duplicate_metadata
        from nrw_events.identity import event_id
        winner = market("Testmarkt", "Bonn.de Events", "2026-08-02", "Testplatz", "Bonn", "https://example.test/a")
        duplicate = market("Testmarkt", "marktcom", "2026-08-02", "Testplatz", "Bonn", "https://example.test/b")
        duplicate = replace(duplicate, previous_event_ids=["historical-alias"])
        merged = _merge_duplicate_metadata(winner, duplicate)
        self.assertIn(event_id(duplicate), merged.get("merged_event_ids"))
        self.assertNotIn("historical-alias", merged.get("merged_event_ids"))
        self.assertIn("historical-alias", merged.get("previous_event_ids"))
        replay = _merge_duplicate_metadata(merged, duplicate)
        self.assertEqual(replay.get("merged_event_ids"), merged.get("merged_event_ids"))

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

    def test_rigalsche_wiese_live_pair_keeps_first_party_facts_and_alias(self):
        primary = market(
            "Familien Flohmarkt auf der Rigal´schen Wiese",
            "Bad Godesberg Stadtmarketing",
            "2026-08-23",
            "Rigal'sche Wiese",
            "Bonn-Bad Godesberg",
            "https://bad-godesberg.info/veranstaltungen_st/familien-flohmarkt-auf-der-rigalschen-wiese",
            description=(
                "Beim Familien-Flohmarkt auf der Rigal’schen Wiese findet der Markt "
                "von 11:00 bis 17:00 Uhr statt. Der laufende Meter Standfläche kostet 10 €."
            ),
            score=1.1,
            time="11:00–17:00",
            start_at="2026-08-23T11:00+02:00",
            end_at="2026-08-23T17:00+02:00",
            source_id="bad-godesberg-stadtmarketing",
        )
        civic = market(
            "Familien Ferien Flohmarkt",
            "Bonn.de Events",
            "2026-08-23",
            "Rigal´sche Wiese",
            "Bonn",
            "https://www.bonn.de/veranstaltungskalender/veranstaltungen/hauptkalender/extern/Familien-Ferien-Flohmarkt.php",
            description="Familien-Ferien-Flohmarkt auf und rund um die Rigal´sche Wiese.",
            score=1.03,
            time="10:00–17:00",
            start_at="2026-08-23T10:00+02:00",
            end_at="2026-08-23T17:00+02:00",
            source_id="bonn-de-events",
        )

        deduped = report.deduplicate([civic, primary])

        self.assertEqual(len(deduped), 1)
        canonical = deduped[0]
        self.assertEqual(canonical["source"], "Bad Godesberg Stadtmarketing")
        self.assertEqual(canonical["time"], "11:00–17:00")
        self.assertEqual(canonical["start_at"], "2026-08-23T11:00+02:00")
        self.assertEqual(canonical["end_at"], "2026-08-23T17:00+02:00")
        self.assertEqual(canonical["link"], primary["link"])
        self.assertEqual(canonical["price"], "")
        self.assertIsNone(canonical["admission"]["isFree"])
        self.assertIn(report.event_id(civic), canonical["previous_event_ids"])

    def test_same_venue_same_day_distinct_flea_markets_with_conflicting_times_survive(self):
        events = [
            market(
                "Familien Flohmarkt",
                "Veranstalter A",
                "2026-08-23",
                "Rigal'sche Wiese",
                "Bonn-Bad Godesberg",
                "https://example.test/familien-flohmarkt",
                time="11:00–13:00",
                start_at="2026-08-23T11:00+02:00",
            ),
            market(
                "Abendflohmarkt",
                "Veranstalter B",
                "2026-08-23",
                "Rigal´sche Wiese",
                "Bonn",
                "https://example.test/abendflohmarkt",
                time="18:00–21:00",
                start_at="2026-08-23T18:00+02:00",
            ),
        ]

        self.assertEqual(len(report.deduplicate(events)), 2)

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

    def test_rauschendorf_kita_flea_market_variants_collapse(self):
        events = [
            market(
                "Sortierter Kindersachenflohmarkt im CJD Kindergarten Rauschendorf",
                "Bonn.de Events",
                "2026-09-06",
                "CJD Kindergarten Rauschendorf",
                "Königswinter",
                "https://www.bonn.de/veranstaltungskalender/extern/kindersachenflohmarkt.php",
                source_id="bonn-de-events",
                start_at="2026-09-06T10:00+02:00",
                end_at="2026-09-06T10:00+02:00",
                score=0.99,
            ),
            market(
                "Sortierter Kindersachenflohmarkt in der CJD Kita in Rauschendorf",
                "Königswinter",
                "2026-09-06",
                "CJD Kindergarten Rauschendorf",
                "Königswinter",
                "https://www.koenigswinter.de/de/veranstaltungskalender/event/143237,1081/kindersachenflohmarkt.html",
                source_id="k-nigswinter",
                start_at="2026-09-06T10:00+02:00",
                end_at="2026-09-06T13:00+02:00",
                time="10:00 bis 13:00",
                score=1.05,
            ),
        ]

        deduped = report.deduplicate(events)

        self.assertEqual(len(deduped), 1)
        self.assertEqual(deduped[0]["source"], "Königswinter")
        self.assertIn(
            "https://www.bonn.de/veranstaltungskalender/extern/kindersachenflohmarkt.php",
            deduped[0]["source_links"],
        )
        self.assertTrue(deduped[0]["previous_event_ids"])

    def test_second_market_at_the_same_kita_on_another_time_survives(self):
        events = [
            market(
                "Sortierter Kindersachenflohmarkt im CJD Kindergarten Rauschendorf",
                "Bonn.de Events",
                "2026-09-06",
                "CJD Kindergarten Rauschendorf",
                "Königswinter",
                "https://www.bonn.de/veranstaltungskalender/extern/kindersachenflohmarkt.php",
                source_id="bonn-de-events",
                start_at="2026-09-06T10:00+02:00",
                end_at="2026-09-06T13:00+02:00",
            ),
            market(
                "Bücherflohmarkt in der CJD Kita in Rauschendorf",
                "Königswinter",
                "2026-09-06",
                "CJD Kindergarten Rauschendorf",
                "Königswinter",
                "https://www.koenigswinter.de/de/veranstaltungskalender/event/143238,1081/buecherflohmarkt.html",
                source_id="k-nigswinter",
                start_at="2026-09-06T15:00+02:00",
                end_at="2026-09-06T18:00+02:00",
            ),
        ]

        self.assertEqual(len(report.deduplicate(events)), 2)

    def test_aggregator_title_as_venue_matches_organizer_by_its_own_street(self):
        # bonn.de repeats the title as place name; its structured street is the venue.
        civic = market(
            "Antik Markt Bonn Friedensplatz", "Bonn.de Events", "2026-10-18", "Antik Markt Bonn", "Bonn",
            "https://www.bonn.de/veranstaltungskalender/veranstaltungen/hauptkalender/extern/Antik-Markt-Bonn-Friedensplatz.php",
            venue_address="Friedensplatz, 53111 Bonn", time="11:00–17:00", source_id="bonn-de-events",
        )
        organizer = market(
            "Antikmarkt Bonn", "Cölln Konzept", "2026-10-18", "Friedensplatz", "Bonn",
            "https://www.coelln-konzept.de/markt/antikmarkt_bonn.html", time="11:00–17:00", source_id="c-lln-konzept",
        )
        deduped = report.deduplicate([civic, organizer])
        self.assertEqual(len(deduped), 1)
        self.assertEqual(deduped[0].get("link"), organizer.get("link"))
        # Without the street evidence the title-as-venue record stays separate.
        other_square = market(
            "Antikmarkt Bonn", "Cölln Konzept", "2026-10-18", "Marktplatz", "Bonn",
            "https://www.coelln-konzept.de/markt/other.html", time="11:00–17:00", source_id="c-lln-konzept",
        )
        self.assertEqual(len(report.deduplicate([civic, other_square])), 2)


if __name__ == "__main__":
    unittest.main()
