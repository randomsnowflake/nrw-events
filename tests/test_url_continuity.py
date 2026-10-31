"""Published event URLs survive title cleanup and cross-source merges."""

import unittest

from nrw_events.identity import assign_event_ids
from nrw_events.identity_reconciliation import _reconcile_published_ids


def _event(title, source, **updates):
    event = {
        "title": title, "source": source, "source_id": source.casefold().replace(" ", "-"),
        "start_date": "2026-09-26", "end_date": "2026-09-26", "date": "2026-09-26",
        "start_at": "2026-09-26T11:00+02:00", "time": "11:00", "city": "Siegburg",
        "venue": "Abtei Michaelsberg", "link": "https://example.test/fuehrung", "link_kind": "detail",
        "status": "scheduled", "score": 1.0, "description": "", "price": "",
    }
    event.update(updates)
    return event


def _published(events, previous):
    return assign_event_ids(_reconcile_published_ids(events, {"events": previous}))


class UrlContinuityTests(unittest.TestCase):
    def test_cleaned_cancelled_title_keeps_its_published_id(self):
        prior = _event("ABGESAGT - Öffentliche Führung KSI und Abtei", "Siegburg",
                       status="cancelled", event_id="abgesagt-oeffentliche-fuehrung-ksi-und-abtei-f4acec7b47")
        [current] = _published([_event("Öffentliche Führung KSI und Abtei", "Siegburg", status="cancelled")], [prior])
        self.assertEqual(current["event_id"], prior["event_id"])

    def test_title_without_its_repeated_date_keeps_its_published_id(self):
        prior = _event("Quintetto Encuentro am 26.09.2026 im Löhrerhof", "SiteKit Hürth", event_id="quintetto-encuentro-old")
        [current] = _published([_event("Quintetto Encuentro im Löhrerhof", "SiteKit Hürth")], [prior])
        self.assertEqual(current["event_id"], "quintetto-encuentro-old")

    def test_merged_away_record_redirects_to_its_successor(self):
        winner_prior = _event("Rachel Joyce liest aus »Sommerhaus«", "Parkbuchhandlung", event_id="rachel-joyce-liest-winner",
                              venue="Rheinhotel Dreesen", link="https://example.test/park")
        folded_prior = _event("Rachel Joyce »Sommerhaus«", "Bonn.de Events", event_id="rachel-joyce-sommerhaus-folded",
                              venue="Rheinhotel Dreesen", link="https://example.test/bonn")
        winner = {key: value for key, value in winner_prior.items() if key != "event_id"}

        [current] = _published([winner], [winner_prior, folded_prior])

        self.assertEqual(current["event_id"], "rachel-joyce-liest-winner")
        self.assertIn("rachel-joyce-sommerhaus-folded", current["previous_event_ids"])

    def test_disappeared_record_is_not_attached_to_an_unrelated_event(self):
        gone = _event("Hauptausschuss", "SiteKit Hürth", event_id="hauptausschuss-old", venue="Rathaus",
                      link="https://example.test/hauptausschuss")
        [current] = _published([_event("Orgelkonzert", "SiteKit Hürth", venue="Rathaus",
                                       link="https://example.test/orgelkonzert")], [gone])
        self.assertNotIn("hauptausschuss-old", current["previous_event_ids"])
        self.assertNotEqual(current["event_id"], "hauptausschuss-old")

    def test_retitled_occurrence_on_its_detail_page_keeps_its_published_id(self):
        link = "https://www.bundeskunsthalle.de/veranstaltungen/detail/wednesday-late-art-nie-wieder"
        prior = _event("Wednesday Late Art – Nie Wieder!", "Bundeskunsthalle", link=link, venue="Bundeskunsthalle",
                       time="18:00–21:00", start_at="2026-10-21T18:00+02:00", start_date="2026-10-21",
                       date="2026-10-21", end_date="2026-10-21", event_id="wednesday-late-art-nie-wieder-3652fd58b4")
        retitled = {**prior, "title": "Wednesday Late Art – Meet the Real Eva Kraus!"}
        del retitled["event_id"]
        other = {**retitled, "title": "Kunstvermittlung: NIE WIEDER!", "link": "https://www.bundeskunsthalle.de/veranstaltungen/detail/10424"}

        current = _published([retitled, other], [prior])

        self.assertEqual(current[0]["event_id"], "wednesday-late-art-nie-wieder-3652fd58b4")
        self.assertNotEqual(current[1]["event_id"], prior["event_id"])

    def test_shared_overview_link_does_not_carry_a_url_to_a_new_title(self):
        prior = _event("Hauptausschuss", "SiteKit Hürth", event_id="hauptausschuss-old", link="https://example.test/kalender")
        replacement = _event("Orgelkonzert", "SiteKit Hürth", link="https://example.test/kalender")
        later = _event("Lesung", "SiteKit Hürth", link="https://example.test/kalender", start_date="2026-09-27",
                       date="2026-09-27", end_date="2026-09-27", start_at="2026-09-27T11:00+02:00")

        current = _published([replacement, later], [prior])

        self.assertNotIn("hauptausschuss-old", [event["event_id"] for event in current])

    def test_link_shared_only_in_the_previous_snapshot_carries_no_identity(self):
        link = "https://example.test/kalender"
        prior = _event("Hauptausschuss", "SiteKit Hürth", event_id="hauptausschuss-old", link=link)
        sibling = _event("Lesung", "SiteKit Hürth", event_id="lesung-old", link=link, start_date="2026-09-27",
                         date="2026-09-27", end_date="2026-09-27", start_at="2026-09-27T11:00+02:00")

        [current] = _published([_event("Orgelkonzert", "SiteKit Hürth", link=link)], [prior, sibling])

        self.assertNotEqual(current["event_id"], "hauptausschuss-old")

    def test_exhibition_run_does_not_become_an_alias_of_one_tour_day(self):
        # Production 2026-10-11: LVR's 18.10 tour inherited bonn.de's live run id.
        from nrw_events.dedup_merge import _merge_duplicate_metadata
        from nrw_events.duplicate_identity import events_are_duplicates

        run = _event("Fluchtpunkt Paris", "Bonn.de Events", source_id="bonn-de-events", event_id="fluchtpunkt-paris-767683ab38",
                     preserved_event_id="fluchtpunkt-paris-767683ab38", start_date="2026-10-15", date="2026-10-15",
                     end_date="2026-11-07", start_at="", time="", city="Bonn", venue="LVR-LandesMuseum Bonn",
                     category_key="exhibition", previous_event_ids=["fluchtpunkt-paris-34e7236751"],
                     link="https://www.bonn.de/veranstaltungskalender/veranstaltungen/hauptkalender/extern/Fluchtpunkt-Paris.php")
        tour = _event("Fluchtpunkt Paris", "LVR-LandesMuseum Bonn", source_id="lvr-landesmuseum-bonn",
                      start_date="2026-10-18", date="2026-10-18", end_date="2026-10-18",
                      start_at="2026-10-18T11:30+02:00", time="11:30", city="Bonn", venue="LVR-LandesMuseum Bonn",
                      link="https://www.shop.landesmuseum-bonn.lvr.de/#/product/event/16376?date=2026-10-18&date_id=89196")
        self.assertTrue(events_are_duplicates(run, tour))  # the syndicated-day rule still pairs them

        merged = _merge_duplicate_metadata(tour, run)
        self.assertEqual(merged.get("previous_event_ids") or [], [])

        # A same-day cross-source fold still redirects.
        day = {**run, "start_date": "2026-10-18", "date": "2026-10-18", "end_date": "2026-10-18",
               "event_id": "fluchtpunkt-paris-day", "preserved_event_id": "fluchtpunkt-paris-day", "previous_event_ids": []}
        self.assertEqual(_merge_duplicate_metadata(tour, day)["previous_event_ids"], ["fluchtpunkt-paris-day"])

        # Aliases already carried forward never shadow a live page.
        stale = {**tour, "previous_event_ids": ["fluchtpunkt-paris-c360a2fcb6", "fluchtpunkt-paris-767683ab38"]}
        published = {event["source_id"]: event for event in assign_event_ids([run, stale])}
        self.assertEqual(published["lvr-landesmuseum-bonn"]["previous_event_ids"], ["fluchtpunkt-paris-c360a2fcb6"])

    def _cancelled_last_day(self, **prior_updates):
        day = "2026-11-01"
        cancelled = _event("Hallenflohmarkt", "Halle", status="cancelled", start_date=day, end_date=day, date=day,
                           start_at="", time="")
        [natural] = assign_event_ids([cancelled])
        run = _event("Hallenflohmarkt", "Halle", event_id="hallenflohmarkt-run", start_date="2026-10-25",
                     date="2026-10-25", end_date=day, start_at="", time="",
                     previous_event_ids=[natural["event_id"]], daily_schedule=[{"date": day}])
        run.update(prior_updates)
        return cancelled, natural["event_id"], run

    def test_cancelled_last_open_date_reaches_the_published_run_page(self):
        cancelled, natural_id, run = self._cancelled_last_day()
        [current] = _published([cancelled], [run])
        self.assertEqual(current["event_id"], "hallenflohmarkt-run")
        self.assertEqual(current["status"], "cancelled")
        self.assertEqual(current["previous_event_ids"], [natural_id])

    def test_cancelled_single_date_does_not_cancel_a_run_with_open_dates(self):
        cancelled, natural_id, run = self._cancelled_last_day(
            daily_schedule=[{"date": "2026-10-31"}, {"date": "2026-11-01"}])
        [current] = _published([cancelled], [run])
        self.assertEqual(current["event_id"], natural_id)

    def test_cancelled_middle_date_does_not_cancel_a_longer_run(self):
        cancelled, natural_id, run = self._cancelled_last_day(end_date="2026-11-27")
        [current] = _published([cancelled], [run])
        self.assertEqual(current["event_id"], natural_id)

    def test_cancellation_leaves_a_run_that_is_still_published(self):
        cancelled, natural_id, run = self._cancelled_last_day()
        live = {key: value for key, value in run.items() if key != "event_id"}
        live["preserved_event_id"] = "hallenflohmarkt-run"
        published = _published([cancelled, live], [run])
        self.assertEqual({event["event_id"] for event in published}, {natural_id, "hallenflohmarkt-run"})

    def test_cancellation_ignores_another_sources_run(self):
        cancelled, natural_id, run = self._cancelled_last_day(source="Andere", source_id="andere")
        [current] = _published([cancelled], [run])
        self.assertEqual(current["event_id"], natural_id)


if __name__ == "__main__":
    unittest.main()
